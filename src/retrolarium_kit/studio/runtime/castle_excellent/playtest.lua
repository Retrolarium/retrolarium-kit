-- Boot an isolated Castle Excellent preview ROM through its normal room loader.
-- The preview changes only the initial-room immediate; this script supplies
-- Start and places the player after gameplay has initialized. Until then the
-- emulator runs unthrottled and muted; CASTLE_EXCELLENT_PLAYTEST_READY, when set,
-- names a file written once the player is placed.

local function setting(name, minimum, maximum)
    local value = tonumber(os.getenv(name) or "")
    if value == nil or value < minimum or value > maximum
            or value ~= math.floor(value) then
        error(name .. " must be in range " .. minimum .. ".." .. maximum)
    end
    return value
end

local room = setting("CASTLE_EXCELLENT_PLAYTEST_ROOM", 1, 100)
local player_x = setting("CASTLE_EXCELLENT_PLAYTEST_X", 0, 319)
local player_y = setting("CASTLE_EXCELLENT_PLAYTEST_Y", 0, 159)
local ready_path = os.getenv("CASTLE_EXCELLENT_PLAYTEST_READY")
local machine = manager.machine
local cpu = manager.machine.devices[":maincpu"]
if cpu == nil or cpu.spaces["program"] == nil then
    error("MAME NES main CPU program space is unavailable")
end
local memory = cpu.spaces["program"]
local function read(address) return memory:read_u8(address) end
local function write(address, value) memory:write_u8(address, value) end

local start_field = nil
for _, port in pairs(manager.machine.ioport.ports) do
    for _, field in pairs(port.fields) do
        local name = string.lower(field.default_name or "")
        if name == "p1 start" and field.player == 0 then
            start_field = field
            break
        end
    end
    if start_field ~= nil then break end
end
if start_field == nil then
    error("MAME NES player-one Start input is unavailable")
end

local frame = 0
local placed = false
-- New-game setup stores InitialPlayerY ($98) in PlayerY ($3A) after the
-- position is cleared; the player is placed the frame after it.
local initialized = false
init_tap = memory:install_write_tap(0x003a, 0x003a, "castle-excellent-point-init",
    function(_, value)
        if frame > 30 and value == 0x98 then initialized = true end
        return value
    end)
local function on_frame()
    frame = frame + 1
    -- The way to the room: fast and silent until the player is placed.
    if not placed then
        machine.video.throttled = false
        machine.sound.ui_mute = true
    end
    if frame == 30 then start_field:set_value(1) end
    if frame == 33 then start_field:clear_value() end
    if placed then return end
    if frame > 1800 then
        error("Castle Excellent gameplay did not become ready for point playtest")
    end
    -- The normal loader writes CurrentRoomNumber ($37) before gameplay.
    -- Apply the editor point once, after the new-game position setup.
    if initialized and read(0x0037) == room then
        write(0x0038, player_x % 256)
        write(0x0039, math.floor(player_x / 256))
        write(0x003a, player_y)
        placed = true
        machine.video.throttled = true
        machine.sound.ui_mute = false
        if ready_path ~= nil and ready_path ~= "" then
            local file = io.open(ready_path, "w")
            if file ~= nil then
                file:write("ready")
                file:close()
            end
        end
        print(string.format(
            "Castle Excellent point playtest ready: room=%d x=%d y=%d frame=%d",
            room, player_x, player_y, frame))
    end
end

retrolarium_castle_excellent_point_playtest = emu.register_frame_done(on_frame, "frame")
