-- Record an Alien Soldier boss fight as the game draws it: the playtest
-- script starts the stage, the player walks right (invincible, jumping now
-- and then) until the first object record holds a boss (a type its asset
-- set installs), and from then on every frame writes what the VDP changed
-- and the object records. When a fight has run its frames the boss's
-- health goes to zero, so once its record holds something else the next
-- boss or form that comes on is recorded too, until none comes or the game
-- moves on to the next stage.
--
-- The VDP is followed through the CPU's writes to its ports (DMA from RAM
-- or ROM, fills and copies included), so the record is the VDP's own state,
-- whatever drew it. ALIEN_CAPTURE_OUT names the file; its lines:
--
--   B <type>               a fight begins: the type in the first record
--   F <frame>              a frame (from the fight's start)
--   V <chunk> <hex>        32 bytes of VRAM at chunk * 32 that changed
--   C <hex> / S <hex>      CRAM (64 words) / VSRAM (40 words) when changed
--   R <hex>                the registers 0-23 when changed
--   O <slot> <hex>         a live object record: its first $60 bytes
--   E <reason>             the end: "done", "no boss", "left the stage"
--
-- The first frame of each fight carries the whole VRAM, CRAM, VSRAM and
-- registers. Settings: ALIEN_CAPTURE_FRAMES (frames a fight, default 600),
-- ALIEN_CAPTURE_FIGHTS (most fights, default 8), ALIEN_CAPTURE_WAIT (frames
-- to wait for a boss, default 12000), ALIEN_CAPTURE_POOL and
-- ALIEN_CAPTURE_POOL_END (the object records, hexadecimal),
-- ALIEN_CAPTURE_BOSSES (the boss types, hexadecimal, comma-separated) and
-- the RAM in ALIEN_RAM_<name> (BossHealth, GameModeIndex, StageTableIndex).

local here = debug.getinfo(1, "S").source:sub(2):match("^(.*)[/\\]")
dofile(here .. "/playtest.lua")

local machine = manager.machine
local mem = machine.devices[":maincpu"].spaces["program"]
local function number(name, base, default)
    local value = tonumber(os.getenv(name) or "", base or 10)
    if value == nil then
        if default == nil then
            error(name .. " is missing")
        end
        return default
    end
    return value
end
local out = assert(io.open(assert(os.getenv("ALIEN_CAPTURE_OUT"), "ALIEN_CAPTURE_OUT is missing"), "w"))
local fight_frames = number("ALIEN_CAPTURE_FRAMES", 10, 600)
local most_fights = number("ALIEN_CAPTURE_FIGHTS", 10, 8)
local wait_frames = number("ALIEN_CAPTURE_WAIT", 10, 12000)
local pool = number("ALIEN_CAPTURE_POOL", 16) & 0xFFFFFF
local pool_end = number("ALIEN_CAPTURE_POOL_END", 16) & 0xFFFFFF
local health = number("ALIEN_RAM_BossHealth", 16) & 0xFFFFFF
local mode = number("ALIEN_RAM_GameModeIndex", 16) & 0xFFFFFF
local stage = number("ALIEN_RAM_StageTableIndex", 16) & 0xFFFFFF
local wanted = number("ALIEN_PLAYTEST_STAGE_INDEX")
local bosses = {}
for value in string.gmatch(os.getenv("ALIEN_CAPTURE_BOSSES") or "", "%x+") do
    bosses[tonumber(value, 16)] = true
end

-- The VDP as the CPU leaves it.
local vram, cram, vsram, regs = {}, {}, {}, {}
for i = 0, 0xFFFF do
    vram[i] = 0
end
for i = 0, 63 do
    cram[i] = 0
end
for i = 0, 39 do
    vsram[i] = 0
end
for i = 0, 31 do
    regs[i] = 0
end
local dirty, cram_dirty, vsram_dirty, regs_dirty = {}, false, false, false
local pending, first, address, code, fill = false, 0, 0, 0, false

local function poke_vram(at, value)
    at = at & 0xFFFF
    if vram[at] ~= value then
        vram[at] = value
        dirty[at >> 5] = true
    end
end

local function write_word(value)
    local target = code & 0xF
    if target == 1 then
        local at = address & 0xFFFE
        poke_vram(at, (value >> 8) & 0xFF)
        poke_vram(at + 1, value & 0xFF)
    elseif target == 3 then
        cram[(address >> 1) & 63] = value & 0xEEE
        cram_dirty = true
    elseif target == 5 then
        vsram[(address >> 1) % 40] = value & 0x7FF
        vsram_dirty = true
    end
    address = (address + regs[15]) & 0xFFFF
end

local function command()
    if code & 0x20 == 0 or regs[1] & 0x10 == 0 then
        return
    end
    local kind = regs[23] >> 6
    local length = regs[19] | (regs[20] << 8)
    if length == 0 then
        length = 0x10000
    end
    if kind < 2 then
        local source = ((regs[21] | (regs[22] << 8) | ((regs[23] & 0x7F) << 16)) << 1) & 0xFFFFFF
        for _ = 1, length do
            write_word(mem:read_u16(source))
            source = (source + 2) & 0xFFFFFF
        end
    elseif kind == 2 then
        fill = true
    else
        local source = regs[21] | (regs[22] << 8)
        for _ = 1, length do
            poke_vram(address, vram[source & 0xFFFF])
            source = source + 1
            address = (address + regs[15]) & 0xFFFF
        end
    end
end

capture_tap = mem:install_write_tap(0xC00000, 0xC00007, "capture", function(offset, data, mask)
    if offset & 6 < 4 then
        pending = false
        if fill then
            fill = false
            write_word(data)
            local length = regs[19] | (regs[20] << 8)
            if length == 0 then
                length = 0x10000
            end
            for _ = 1, length - 1 do
                poke_vram(address, (data >> 8) & 0xFF)
                address = (address + regs[15]) & 0xFFFF
            end
        else
            write_word(data)
        end
    elseif pending then
        pending = false
        address = (first & 0x3FFF) | ((data & 3) << 14)
        code = ((first >> 14) & 3) | ((data >> 2) & 0x3C)
        command()
    elseif data & 0xC000 == 0x8000 then
        local register = (data >> 8) & 0x1F
        if regs[register] ~= data & 0xFF then
            regs[register] = data & 0xFF
            regs_dirty = true
        end
    else
        pending = true
        first = data
        address = (address & 0xC000) | (data & 0x3FFF)
        code = (code & 0x3C) | ((data >> 14) & 3)
    end
    return data
end)

local function hex(list, from, to, format)
    local parts = {}
    for i = from, to do
        parts[#parts + 1] = string.format(format, list[i])
    end
    return table.concat(parts)
end

local function write_frame(whole)
    if whole then
        for chunk = 0, 0x7FF do
            dirty[chunk] = true
        end
    end
    for chunk = 0, 0x7FF do
        if dirty[chunk] then
            out:write(string.format("V %X %s\n", chunk, hex(vram, chunk * 32, chunk * 32 + 31, "%02X")))
        end
    end
    dirty = {}
    if whole or cram_dirty then
        out:write("C " .. hex(cram, 0, 63, "%04X") .. "\n")
    end
    if whole or vsram_dirty then
        out:write("S " .. hex(vsram, 0, 39, "%04X") .. "\n")
    end
    if whole or regs_dirty then
        out:write("R " .. hex(regs, 0, 23, "%02X") .. "\n")
    end
    cram_dirty, vsram_dirty, regs_dirty = false, false, false
    for record = pool, pool_end - 1, 0x60 do
        if mem:read_u16(record) ~= 0 then
            local bytes = {}
            for i = 0, 0x5F do
                bytes[i] = mem:read_u8(record + i)
            end
            out:write(string.format("O %d %s\n", (record - pool) // 0x60, hex(bytes, 0, 0x5F, "%02X")))
        end
    end
end

local pad = {}
for tag, port in pairs(machine.ioport.ports) do
    if tag:match("^:ctrl1:") then
        for name, field in pairs(port.fields) do
            pad[name] = field
        end
    end
end
local right, jumps = nil, {}
for name, field in pairs(pad) do
    if name:match(" Right$") then
        right = field
    end
    if name:match(" B$") or name:match(" C$") then
        jumps[#jumps + 1] = field
    end
end

-- waiting: walking to a boss; fighting: recording; dying: until the boss
-- put down leaves its record.
local state, frames, fights, waited, running, killed = "waiting", 0, 0, 0, false, 0
local function finish(reason)
    out:write("E " .. reason .. "\n")
    out:close()
    machine:exit()
end

capture_frame = emu.register_frame_done(function()
    if not running then
        if mem:read_u16(mode) == 0x10 and mem:read_u16(stage) == wanted then
            running = true
            machine.video.throttled = false
        end
        return
    end
    -- Putting a stage's last boss down moves the game on to the next stage.
    if mem:read_u16(mode) ~= 0x10 or mem:read_u16(stage) ~= wanted then
        return finish(fights == 0 and "left the stage" or "done")
    end
    if state ~= "fighting" then
        waited = waited + 1
        if right then
            right:set_value(1)
        end
        for _, field in ipairs(jumps) do
            field:set_value((waited % 40) < 12 and 1 or 0)
        end
        local kind = mem:read_u16(pool)
        if state == "dying" and kind ~= killed then
            state = "waiting"
        end
        if state == "waiting" and bosses[kind] then
            if right then
                right:set_value(0)
            end
            for _, field in ipairs(jumps) do
                field:set_value(0)
            end
            state, frames, waited = "fighting", 0, 0
            fights = fights + 1
            out:write(string.format("B %X\n", mem:read_u16(pool)))
            out:write("F 0\n")
            write_frame(true)
        elseif waited > wait_frames then
            return finish(fights == 0 and "no boss" or "done")
        end
        return
    end
    frames = frames + 1
    if frames >= fight_frames then
        mem:write_u16(health, 0)
        if fights >= most_fights then
            return finish("done")
        end
        state, waited, killed = "dying", 0, mem:read_u16(pool)
        return
    end
    out:write(string.format("F %d\n", frames))
    write_frame(false)
end)
