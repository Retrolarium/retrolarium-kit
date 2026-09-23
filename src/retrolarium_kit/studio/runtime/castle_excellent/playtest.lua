-- Boot an isolated Castle preview ROM through its normal room loader.
-- The preview changes only the initial-room immediate; this script supplies
-- Start and places the player after gameplay has initialized.

local function setting(name, minimum, maximum)
    local value = tonumber(os.getenv(name) or "")
    if value == nil or value < minimum or value > maximum
            or value ~= math.floor(value) then
        error(name .. " must be in range " .. minimum .. ".." .. maximum)
    end
    return value
end

local room = setting("CASTLE_PLAYTEST_ROOM", 1, 100)
local player_x = setting("CASTLE_PLAYTEST_X", 0, 319)
local player_y = setting("CASTLE_PLAYTEST_Y", 0, 159)
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
local function on_frame()
    frame = frame + 1
    if frame == 30 then start_field:set_value(1) end
    if frame == 33 then start_field:clear_value() end
    if placed then return end
    if frame > 900 then
        error("Castle gameplay did not become ready for point playtest")
    end
    -- The normal loader writes CurrentRoomNumber ($37) before gameplay.
    -- Wait beyond the title transition, then apply the editor point once.
    if frame >= 90 and read(0x0037) == room then
        write(0x0038, player_x % 256)
        write(0x0039, math.floor(player_x / 256))
        write(0x003a, player_y)
        placed = true
        print(string.format(
            "Castle point playtest ready: room=%d x=%d y=%d frame=%d",
            room, player_x, player_y, frame))
    end
end

retrolarium_castle_point_playtest = emu.register_frame_done(on_frame, "frame")
