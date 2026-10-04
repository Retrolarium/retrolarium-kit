-- Start a Pac-Man game at one round through the game's own session setup.
-- The script presses Start on the title until a one-player game begins; the
-- setup's "no round yet" value ($FF in ram_stage_p1) becomes the round before
-- the chosen one, so the round init counts up to it. Until the round is set
-- the emulator runs unthrottled and muted; PACMAN_PLAYTEST_READY, when set,
-- names a file written once the round starts.

local function setting(name, minimum, maximum)
    local value = tonumber(os.getenv(name) or "")
    if value == nil or value < minimum or value > maximum
            or value ~= math.floor(value) then
        error(name .. " must be in range " .. minimum .. ".." .. maximum)
    end
    return value
end

local STAGE = 0x0068        -- ram_stage_p1: the round, 0-based
local DEMO = 0x0048         -- ram_flag_demo

local round = setting("PACMAN_PLAYTEST_ROUND", 1, 23)
local ready_path = os.getenv("PACMAN_PLAYTEST_READY")
local machine = manager.machine
local cpu = machine.devices[":maincpu"]
if cpu == nil or cpu.spaces["program"] == nil then
    error("MAME NES main CPU program space is unavailable")
end
local memory = cpu.spaces["program"]

local start_field = nil
for _, port in pairs(machine.ioport.ports) do
    for _, field in pairs(port.fields) do
        if string.lower(field.default_name or "") == "p1 start" and field.player == 0 then
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
local entered = false       -- a real game's setup has run
local started = 0           -- the frame the round was counted up to
local placed = false
stage_tap = memory:install_write_tap(STAGE, STAGE, "pacman-round-playtest",
    function(_, value)
        if memory:read_u8(DEMO) ~= 0 then return value end
        if not entered and value == 0xff then
            entered = true
            return (round - 2) & 0xff
        end
        if entered and started == 0 and value == round - 1 then started = frame end
        return value
    end)

local function on_frame()
    frame = frame + 1
    if not placed then
        machine.video.throttled = false
        machine.sound.ui_mute = true
    end
    -- Start on the title (and out of the attract demo) until a game begins;
    -- in a game Start would pause it.
    if not entered then
        local phase = frame % 60
        if frame >= 120 and phase == 0 then start_field:set_value(1) end
        if phase == 3 then start_field:clear_value() end
    elseif frame % 60 >= 3 then
        start_field:clear_value()
    end
    if placed then return end
    if frame > 3600 then
        error("Pac-Man did not start a game for the round playtest")
    end
    if started > 0 and frame >= started + 2 then
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
        print(string.format("Pac-Man round playtest ready: round=%d frame=%d", round, frame))
    end
end

retrolarium_pacman_round_playtest = emu.register_frame_done(on_frame, "frame")
