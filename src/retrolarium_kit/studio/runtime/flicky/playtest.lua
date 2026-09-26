-- Boot the built Flicky (UE) cartridge into the selected round without patching ROM.
-- Addresses and mode transitions are from the canonical Flicky source runtime.

local round = tonumber(os.getenv("FLICKY_PLAYTEST_ROUND") or "")
if round == nil or round ~= math.floor(round) or round < 1 or round > 48 then
    error("FLICKY_PLAYTEST_ROUND must be an integer in 1..48")
end
local result_path = os.getenv("FLICKY_PLAYTEST_RESULT")
local exit_after_ready = os.getenv("FLICKY_PLAYTEST_EXIT") == "1"
local snapshot_path = os.getenv("FLICKY_PLAYTEST_SNAPSHOT")
if snapshot_path == "" then snapshot_path = nil end
-- Later snapshots expose cutscene/animation timing effects; keep a bounded
-- diagnostic window so a bad caller cannot leave the emulator running forever.
local snapshot_delay = tonumber(os.getenv("FLICKY_PLAYTEST_SNAPSHOT_DELAY") or "60")
if snapshot_delay == nil or snapshot_delay ~= math.floor(snapshot_delay)
        or snapshot_delay < 0 or snapshot_delay > 600 then
    error("FLICKY_PLAYTEST_SNAPSHOT_DELAY must be an integer in 0..600")
end
local sweep_text = os.getenv("FLICKY_PLAYTEST_SNAPSHOT_PHASES")
local sweep_prefix = os.getenv("FLICKY_PLAYTEST_SNAPSHOT_PREFIX")
local sweep_phases = {}
if sweep_text ~= nil and sweep_text ~= "" then
    if snapshot_path == nil or sweep_prefix == nil or sweep_prefix == "" then
        error("Flicky snapshot sweep requires a final snapshot and prefix")
    end
    for token in string.gmatch(sweep_text .. ",", "([^,]*),") do
        local phase = tonumber(token)
        if phase == nil or phase ~= math.floor(phase)
                or phase < 1 or phase >= snapshot_delay
                or sweep_phases[phase] then
            error("Flicky snapshot sweep phases must be unique integers in 1..delay-1")
        end
        sweep_phases[phase] = true
    end
end
local round_word = (math.floor(round / 10) * 16 + round % 10) * 256 + round
-- Game_PreRoundDelay enters Bonus_Init/MainLoop every fourth slot starting
-- with round 3. Those rounds never reach the ordinary Game_MainLoop ($24).
local ready_mode = round % 4 == 3 and 0x2C or 0x24

local cpu = manager.machine.devices[":maincpu"]
if cpu == nil or cpu.spaces["program"] == nil then
    error("MAME Mega Drive main CPU program space is unavailable")
end
local memory = cpu.spaces["program"]
local ram = {
    lives = 0xffd882,
    game_state = 0xffd2a0,
    round_number = 0xffd82c,
    skip_bonus = 0xffd88f,
    next_game_mode = 0xffffc0,
}
local function read_word(address) return memory:read_u16(address) end
local function write_word(address, value) memory:write_u16(address, value) end
local function read_byte(address) return memory:read_u8(address) end
local function write_byte(address, value) memory:write_u8(address, value) end

local frame = 0
local ym_trace_path = os.getenv("FLICKY_PLAYTEST_YM_TRACE")
local anim_trace_path = os.getenv("FLICKY_PLAYTEST_ANIM_TRACE")
local anim_trace_file = nil
if anim_trace_path ~= nil and anim_trace_path ~= "" then
    anim_trace_file = assert(io.open(anim_trace_path, "w"))
end
local bonus_trace_path = os.getenv("FLICKY_PLAYTEST_BONUS_TRACE")
local bonus_trace_file = nil
if bonus_trace_path ~= nil and bonus_trace_path ~= "" then
    bonus_trace_file = assert(io.open(bonus_trace_path, "w"))
end
local bonus_actor_trace_path = os.getenv("FLICKY_PLAYTEST_BONUS_ACTORS_TRACE")
local bonus_actor_trace_file = nil
if bonus_actor_trace_path ~= nil and bonus_actor_trace_path ~= "" then
    bonus_actor_trace_file = assert(io.open(bonus_actor_trace_path, "w"))
    bonus_actor_trace_file:write("phase,name,object,x,y,yfrac,animation,frame,timer,flags,mapping_hi,mapping_lo,state,yspeed_hi,yspeed_lo,airborne,side,index,delay,collision_type,caught_count,caught_bcd,chicks_remaining\n")
end
local sfx_code_text = os.getenv("FLICKY_PLAYTEST_SFX")
local sfx_code = nil
if sfx_code_text ~= nil and sfx_code_text ~= "" then
    sfx_code = tonumber(sfx_code_text)
    if sfx_code == nil or sfx_code ~= math.floor(sfx_code)
            or (sfx_code ~= 0x90 and sfx_code ~= 0x91
                and sfx_code ~= 0x92 and sfx_code ~= 0x93
                and sfx_code ~= 0x94 and sfx_code ~= 0x95
                and sfx_code ~= 0x96 and sfx_code ~= 0x97
                and sfx_code ~= 0x98) then
        error("FLICKY_PLAYTEST_SFX must name a supported SFX command")
    end
end
local ym_trace_file = nil
local ym_trace_tap = nil
local z80 = nil
if (ym_trace_path ~= nil and ym_trace_path ~= "") or sfx_code ~= nil then
    z80 = manager.machine.devices[":genesis_snd_z80"]
    if z80 == nil or z80.spaces["program"] == nil then
        error("MAME Mega Drive sound CPU program space is unavailable")
    end
end
if ym_trace_path ~= nil and ym_trace_path ~= "" then
    ym_trace_file = assert(io.open(ym_trace_path, "w"))
    ym_trace_tap = z80.spaces["program"]:install_write_tap(
        0x4000, 0x4003, "flicky_ym2612_trace",
        function(address, data, mask)
            ym_trace_file:write(string.format("%d,%04X,%02X,%02X\n",
                frame, address, data, mask))
        end)
end
local selected = false
local reported = false
local ready_frame = nil
local snapshot_done = false
local function report(status)
    if reported then return end
    reported = true
    local line = string.format(
        "status=%s round=%d round_word=%04X mode=%02X game_state=%04X lives=%d frame=%d",
        status, round, read_word(ram.round_number), read_word(ram.next_game_mode),
        read_word(ram.game_state), read_byte(ram.lives), frame)
    print("Flicky round playtest: " .. line)
    if result_path ~= nil and result_path ~= "" then
        local output = assert(io.open(result_path .. ".tmp", "w"))
        output:write(line .. "\n")
        output:close()
        assert(os.rename(result_path .. ".tmp", result_path))
    end
end

local function on_frame()
    frame = frame + 1
    local mode = read_word(ram.next_game_mode)
    if not selected then
        if mode == 0x44 then
            -- Global boot and tile upload finished; skip the Sega timer.
            write_word(ram.next_game_mode, 0)
        elseif mode == 0x04 and read_word(ram.round_number) == 0x0101
                and read_byte(ram.lives) == 3
                and read_byte(ram.skip_bonus) == 1 then
            -- Title_Init established the ordinary new-game defaults.
            write_word(ram.round_number, round_word)
            write_byte(ram.lives, 3)
            write_byte(ram.skip_bonus, 1)
            write_word(ram.next_game_mode, 0x18)
            selected = true
        end
    elseif mode == ready_mode and read_word(ram.round_number) == round_word then
        report("ready")
        if ready_frame == nil then ready_frame = frame end
        if exit_after_ready and snapshot_path == nil then manager.machine:exit() end
    end
    if sfx_code ~= nil and ready_frame ~= nil and frame == ready_frame + 5 then
        local sound_memory = z80.spaces["program"]
        if sound_memory:read_u8(0x1C0A) ~= 0 then
            error("Flicky Z80 SFX slot 0 is occupied")
        end
        sound_memory:write_u8(0x1C0A, sfx_code)
    end
    if anim_trace_file ~= nil then
        -- src/memory/ram.inc: Ram_UIAnimState and Ram_ArrowAnimState.
        -- Three bytes each: frame index, countdown, completion flag.
        anim_trace_file:write(string.format("%d,%04X,%02X,%02X,%02X,%02X,%02X,%02X\n",
            frame, mode,
            read_byte(0xFFD254), read_byte(0xFFD255), read_byte(0xFFD256),
            read_byte(0xFFD258), read_byte(0xFFD259), read_byte(0xFFD25A)))
    end
    if bonus_trace_file ~= nil and ready_frame ~= nil
            and frame <= ready_frame + snapshot_delay then
        -- src/memory/ram.inc: Ram_BonusChickSlots=$FFC080; each slot is $40.
        -- Obj_BonusChick stores its table-derived countdown at slot+$3A.
        -- Slot 0 state/X/Y/X-speed live at +$3C/+$30/+$24/+$34 respectively.
        bonus_trace_file:write(string.format("%d,%04X,%04X,%04X,%04X,%04X,%04X,%04X,%04X,%04X\n",
            frame - ready_frame, mode,
            read_word(0xFFC080), read_word(0xFFC0C0), read_word(0xFFC0FA),
            read_word(0xFFC0BC), read_word(0xFFC0B0), read_word(0xFFC0A4),
            read_word(0xFFC0B4), read_word(0xFFC0B6)))
    end
    if bonus_actor_trace_file ~= nil and ready_frame ~= nil
            and frame <= ready_frame + snapshot_delay then
            -- src/memory/ram.inc and Sprite_RenderObject in src/game/world.s.
            -- Screen X/Y retain the Mega Drive hardware +$80 origin.
            local actors = {
                {"player", 0xFFC580},
                {"held", 0xFFC040},
                {"tiger0", 0xFFC5C0}, {"tiger1", 0xFFC600},
                {"seesaw0", 0xFFC640}, {"seesaw1", 0xFFC680},
            }
            for index = 0, 19 do
                table.insert(actors, {string.format("chick%02d", index),
                    0xFFC080 + index * 0x40})
            end
            for _, actor in ipairs(actors) do
                local name, base = actor[1], actor[2]
                bonus_actor_trace_file:write(string.format(
                    "%d,%s,%04X,%04X,%04X,%04X,%04X,%02X,%02X,%02X,%04X,%04X,%04X,%04X,%04X,%02X,%02X,%02X,%04X,%02X,%02X,%02X,%02X\n",
                    frame - ready_frame, name, read_word(base), read_word(base + 0x20),
                    read_word(base + 0x24), read_word(base + 0x26), read_word(base + 0x06),
                    read_byte(base + 0x10), read_byte(base + 0x11),
                    read_byte(base + 0x02), read_word(base + 0x0C),
                    read_word(base + 0x0E), read_word(base + 0x3C),
                    read_word(base + 0x2C), read_word(base + 0x2E),
                    read_byte(base + 0x38), read_byte(base + 0x39),
                    read_byte(base + 0x3E), read_word(base + 0x3A),
                    read_byte(base + 0x04), read_byte(0xFFD28E),
                    read_byte(0xFFD28F), read_byte(0xFFD883)))
            end
            bonus_actor_trace_file:flush()
    end
    if ready_frame ~= nil and sweep_phases[frame - ready_frame] then
        local screen = manager.machine.screens:at(1)
        if screen == nil then error("MAME Mega Drive screen is unavailable") end
        local path = string.format("%s-%03d.png", sweep_prefix, frame - ready_frame)
        local failure = screen:snapshot(path)
        if failure ~= nil then
            error("Flicky sweep screenshot failed: " .. tostring(failure))
        end
    end
    if ready_frame ~= nil and snapshot_path ~= nil and not snapshot_done
            and frame >= ready_frame + snapshot_delay then
        local screen = manager.machine.screens:at(1)
        if screen == nil then error("MAME Mega Drive screen is unavailable") end
        local failure = screen:snapshot(snapshot_path)
        if failure ~= nil then
            error("Flicky screenshot failed: " .. tostring(failure))
        end
        print(string.format("Flicky VDP snapshot: frame=%d mode=%02X round_word=%04X",
            frame, read_word(ram.next_game_mode), read_word(ram.round_number)))
        snapshot_done = true
        if ym_trace_tap ~= nil then ym_trace_file:flush() end
        if anim_trace_file ~= nil then anim_trace_file:flush() end
        if bonus_trace_file ~= nil then bonus_trace_file:flush() end
        if exit_after_ready then manager.machine:exit() end
    end
    if frame >= 1200 and not reported then
        report(selected and "round-timeout" or "boot-timeout")
        if exit_after_ready then manager.machine:exit() end
    end
end

retrolarium_flicky_round_playtest = emu.register_frame_done(on_frame, "frame")
