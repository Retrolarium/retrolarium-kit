-- The game's own sound driver as the reference for the kit's sequencer
-- (tools/alien_soldier_sound_check.py): every write it makes to the YM2612,
-- from the 68000 through the Z80 bus and from the Z80 itself, and to the PSG,
-- tagged with the driver update it happens in.
--
-- The game's own sound requests are dropped (the queue at $FFF80A only takes
-- the ones this script puts there), so nothing but the requested sound plays.
-- CAPTURE_PLAN: "<request hex>:<updates>,..." played one after another, each
-- after a stop-all request ($04) and a quiet gap; CAPTURE_START: the frame of
-- the first. Lines:
--   S <request> <update>                 a request goes into the queue
--   F <update> <port> <register> <value> a 68000 write to the YM2612
--   Z <update> <port> <register> <value> a Z80 write other than a DAC level
--   D <update> <count>                   DAC levels the Z80 wrote before it
--   P <update> <value>                   a PSG byte
--   E <request>                          its recording ends
local machine = manager.machine
local main = machine.devices[":maincpu"].spaces["program"]
local z80 = machine.devices[":genesis_snd_z80"].spaces["program"]
local out = assert(io.open(os.getenv("CAPTURE_OUT"), "w"))

local QUEUE, BUSY = 0xFFF80A, 0xFFF745
local GAP = 40

local plan = {}
for request, updates in string.gmatch(os.getenv("CAPTURE_PLAN") or "", "(%x+):(%d+)") do
    plan[#plan + 1] = { request = tonumber(request, 16), updates = tonumber(updates) }
end
local start = tonumber(os.getenv("CAPTURE_START") or "200")

local update = 0
local injecting = false
local recording = false

queue_tap = main:install_write_tap(QUEUE, QUEUE + 3, "queue", function(offset, data, mask)
    if injecting then
        return data
    end
    return data & ~mask
end)

-- The VBlank handler sets SoundUpdateBusy before each driver update.
busy_tap = main:install_write_tap(BUSY & ~1, BUSY | 1, "busy", function(offset, data, mask)
    if mask & 0x00FF ~= 0 and data & 0xFF == 1 then
        update = update + 1
    end
    return data
end)

local latch = { [0] = 0, [1] = 0 }
ym_tap = main:install_write_tap(0xA04000, 0xA04003, "ym", function(offset, data, mask)
    local port = (offset >> 1) & 1
    if mask & 0xFF00 ~= 0 then
        latch[port] = (data >> 8) & 0xFF
    end
    if mask & 0x00FF ~= 0 and recording then
        out:write(string.format("F %d %d %02X %02X\n", update, port, latch[port], data & 0xFF))
    end
    return data
end)

psg_tap = main:install_write_tap(0xC00010, 0xC00011, "psg", function(offset, data, mask)
    if recording and mask & 0x00FF ~= 0 then
        out:write(string.format("P %d %02X\n", update, data & 0xFF))
    end
    return data
end)

local levels = 0
local zlatch = { [0] = 0, [1] = 0 }
z80_tap = z80:install_write_tap(0x4000, 0x4003, "zym", function(offset, data, mask)
    local port = (offset >> 1) & 1
    if offset & 1 == 0 then
        zlatch[port] = data & 0xFF
    elseif recording and port == 0 and zlatch[0] == 0x2A then
        levels = levels + 1
    elseif recording then
        if levels > 0 then
            out:write(string.format("D %d %d\n", update, levels))
        end
        levels = 0
        out:write(string.format("Z %d %d %02X %02X\n", update, port, zlatch[port], data & 0xFF))
    end
    return data
end)

local function inject(request)
    injecting = true
    main:write_u8(QUEUE, request)
    injecting = false
end

local frames, step, phase, until_update = 0, 0, "wait", 0
frame_cb = emu.register_frame_done(function()
    frames = frames + 1
    if phase == "wait" then
        if frames < start then
            return
        end
        step = step + 1
        if step > #plan then
            out:close()
            machine:exit()
            return
        end
        inject(0x04)
        phase, until_update = "gap", update + GAP
    elseif phase == "gap" then
        if update < until_update then
            return
        end
        local item = plan[step]
        out:write(string.format("S %02X %d\n", item.request, update))
        recording = true
        inject(item.request)
        phase, until_update = "play", update + item.updates
    elseif phase == "play" then
        if update < until_update then
            return
        end
        recording = false
        if levels > 0 then
            out:write(string.format("D %d %d\n", update, levels))
        end
        levels = 0
        out:write(string.format("E %02X\n", plan[step].request))
        out:flush()
        phase = "wait"
    end
end)
