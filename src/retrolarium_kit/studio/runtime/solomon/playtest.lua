-- Start an authored Solomon's Key room through the game's normal room loader.
-- The ROM is never patched: only the emulated CurrentRoom byte is selected.

local function setting(name, minimum, maximum)
    local value = tonumber(os.getenv(name) or "")
    if value == nil or value < minimum or value > maximum or value ~= math.floor(value) then
        error(name .. " must be an integer in " .. minimum .. ".." .. maximum)
    end
    return value
end

local room = setting("SOLOMON_PLAYTEST_ROOM", 0, 52)
local room_load = setting("SOLOMON_PLAYTEST_ROOM_LOAD", 0x8000, 0xffff)
local gameplay = setting("SOLOMON_PLAYTEST_GAMEPLAY", 0x8000, 0xffff)
local current_room = setting("SOLOMON_PLAYTEST_CURRENT_ROOM", 0, 0x07ff)
local start_frame = setting("SOLOMON_PLAYTEST_START_FRAME", 1, 3598)
local ready_frames = setting("SOLOMON_PLAYTEST_READY_FRAMES", start_frame + 2, 3600)
local exit_after_ready = os.getenv("SOLOMON_PLAYTEST_EXIT") == "1"
local result_path = os.getenv("SOLOMON_PLAYTEST_RESULT")

local cpu = manager.machine.devices[":maincpu"]
if cpu == nil or cpu.spaces["program"] == nil then
    error("MAME NES main CPU program space is unavailable")
end
local memory = cpu.spaces["program"]
local function read(address)
    return memory:read_u8(address)
end
local function write(address, value)
    memory:write_u8(address, value)
end

local start_field = nil
for _, port in pairs(manager.machine.ioport.ports) do
    for _, field in pairs(port.fields) do
        if string.lower(field.default_name or "") == "p1 start" and field.player == 0 then
            start_field = field
            break
        end
    end
    if start_field ~= nil then
        break
    end
end
if start_field == nil then
    error("MAME NES player-one Start input is unavailable")
end

local frame = 0
local selected = false
local ready = false
local reported = false
local room_load_hits = 0
local gameplay_hits = 0

local function report(status)
    if reported then
        return
    end
    reported = true
    local line = string.format(
        "status=%s requested_room=%02x current_room=%02x " .. "room_loads=%d gameplay_hits=%d frame=%d",
        status,
        room,
        read(current_room),
        room_load_hits,
        gameplay_hits,
        frame
    )
    print("Solomon point playtest: " .. line)
    if result_path ~= nil and result_path ~= "" then
        local output = assert(io.open(result_path .. ".tmp", "w"))
        output:write(line .. "\n")
        output:close()
        assert(os.rename(result_path .. ".tmp", result_path))
    end
end

-- Program-space read taps include opcode fetches on the NES CPU. Holding the
-- handler objects prevents collection from removing the taps.
local room_load_tap = memory:install_read_tap(room_load, room_load, "solomon-room-load", function(_, value)
    room_load_hits = room_load_hits + 1
    if not selected then
        write(current_room, room)
        selected = true
    end
    return value
end)
local gameplay_tap = memory:install_read_tap(gameplay, gameplay, "solomon-gameplay", function(_, value)
    gameplay_hits = gameplay_hits + 1
    if selected and read(current_room) == room then
        ready = true
    end
    return value
end)

local function on_frame()
    frame = frame + 1
    if frame == start_frame or frame == start_frame + 1 then
        start_field:set_value(1)
    elseif frame == start_frame + 2 then
        start_field:clear_value()
    end
    if ready and frame > start_frame + 2 then
        report("ready")
        if exit_after_ready then
            manager.machine:exit()
        end
    elseif frame >= ready_frames then
        report(selected and "gameplay-timeout" or "room-load-timeout")
        if exit_after_ready then
            manager.machine:exit()
        end
    end
end

retrolarium_solomon_room_load_tap = room_load_tap
retrolarium_solomon_gameplay_tap = gameplay_tap
retrolarium_solomon_point_playtest = emu.register_frame_done(on_frame, "frame")
