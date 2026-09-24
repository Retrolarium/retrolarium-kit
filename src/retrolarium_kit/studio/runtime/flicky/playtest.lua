-- Boot the built Flicky (UE) cartridge into the selected round without patching ROM.
-- Addresses and mode transitions are from the canonical Flicky source runtime.

local round = tonumber(os.getenv("FLICKY_PLAYTEST_ROUND") or "")
if round == nil or round ~= math.floor(round) or round < 1 or round > 48 then
    error("FLICKY_PLAYTEST_ROUND must be an integer in 1..48")
end
local result_path = os.getenv("FLICKY_PLAYTEST_RESULT")
local exit_after_ready = os.getenv("FLICKY_PLAYTEST_EXIT") == "1"
local round_word = (math.floor(round / 10) * 16 + round % 10) * 256 + round

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
local selected = false
local reported = false
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
    elseif mode == 0x24 and read_word(ram.round_number) == round_word then
        report("ready")
        if exit_after_ready then manager.machine:exit() end
    end
    if frame >= 1200 and not reported then
        report(selected and "round-timeout" or "boot-timeout")
        if exit_after_ready then manager.machine:exit() end
    end
end

retrolarium_flicky_round_playtest = emu.register_frame_done(on_frame, "frame")
