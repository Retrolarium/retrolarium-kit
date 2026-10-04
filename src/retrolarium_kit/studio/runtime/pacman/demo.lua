-- Loop the Pac-Man attract demo of a build, or record a new one.
--
-- The title and the character introduction run unthrottled and muted; each
-- time the demo's round starts (the session clears ram_demo_direction_index
-- while ram_flag_demo is set) the game runs at speed. PACMAN_DEMO_READY,
-- when set, names a file written when the first demo starts.
--
-- With PACMAN_DEMO_RECORD set, the demo's Pac-Man follows controller one
-- instead of the table: each read of a pair's direction (LDA table+1,Y in
-- loc_demo_direction_script) returns the pressed direction, or the last
-- one. The first 255 updates keep the table's first direction, as the
-- engine always plays it. The runs are written to the file as
-- "<direction> <updates>" lines (direction 0 up, 1 left, 2 down, 3 right,
-- at most 256 updates a run); recording ends when the demo ends (Pac-Man's
-- one life, or Start) or the table is full, and MAME exits.

local function setting(name, minimum, maximum)
    local value = tonumber(os.getenv(name) or "")
    if value == nil or value < minimum or value > maximum
            or value ~= math.floor(value) then
        error(name .. " must be in range " .. minimum .. ".." .. maximum)
    end
    return value
end

local SCRIPT = setting("PACMAN_DEMO_SCRIPT", 0xC000, 0xFFFF)
local TABLE = setting("PACMAN_DEMO_TABLE", 0xC000, 0xFFFF)
local PAIRS = setting("PACMAN_DEMO_PAIRS", 1, 127)
local DIRECTION_READ = SCRIPT + 17      -- LDA table+1,Y
local DEMO = 0x0048                     -- ram_flag_demo
local BUTTONS = 0x004D                  -- ram_btn_1p
local INDEX = 0x00E4                    -- ram_demo_direction_index
local FIRST_UPDATES = 255

local record_path = os.getenv("PACMAN_DEMO_RECORD") or ""
local recording = record_path ~= ""
local ready_path = os.getenv("PACMAN_DEMO_READY")
local machine = manager.machine
local cpu = machine.devices[":maincpu"]
if cpu == nil or cpu.spaces["program"] == nil then
    error("MAME NES main CPU program space is unavailable")
end
local memory = cpu.spaces["program"]

local frame = 0
local playing = false       -- a demo round is on
local last_update = 0       -- frame of the last direction read
local ready = false
local updates = 0           -- direction reads in this demo
local runs = {}             -- recorded {direction, updates}
local finished = false

local function write_runs()
    local file = io.open(record_path, "w")
    if file == nil then return end
    for _, run in ipairs(runs) do file:write(string.format("%d %d\n", run[1], run[2])) end
    file:close()
end

local function finish()
    if finished then return end
    finished = true
    write_runs()
    print(string.format("Pac-Man demo recorded: %d runs after the first step", #runs))
    machine:exit()
end

index_tap = memory:install_write_tap(INDEX, INDEX, "pacman-demo-start",
    function(_, value)
        if value == 0 and memory:read_u8(DEMO) ~= 0 and not playing then
            playing = true
            updates = 0
            last_update = frame
            if not ready and ready_path ~= nil and ready_path ~= "" then
                local file = io.open(ready_path, "w")
                if file ~= nil then
                    file:write("ready")
                    file:close()
                end
            end
            ready = true
        end
        return value
    end)

local held = nil
direction_tap = memory:install_read_tap(TABLE, TABLE + 0x101, "pacman-demo-direction",
    function(_, value)
        if cpu.state["PC"].value ~= DIRECTION_READ or not playing then return value end
        last_update = frame
        updates = updates + 1
        if not recording or finished then return value end
        if updates <= FIRST_UPDATES then
            held = value & 3
            return value
        end
        local buttons = memory:read_u8(BUTTONS)
        -- tbl_dpad_to_direction: right, left, down, up from the high bit.
        if buttons & 0x80 ~= 0 then held = 3
        elseif buttons & 0x40 ~= 0 then held = 1
        elseif buttons & 0x20 ~= 0 then held = 2
        elseif buttons & 0x10 ~= 0 then held = 0 end
        local run = runs[#runs]
        if run ~= nil and run[1] == held and run[2] < 256 then
            run[2] = run[2] + 1
        else
            if #runs >= PAIRS - 1 then
                finish()
                return value
            end
            runs[#runs + 1] = {held, 1}
            write_runs()
        end
        return (value & 0xFC) | held
    end)

local function on_frame()
    frame = frame + 1
    -- A demo is over when its Pac-Man has not moved for two seconds (it
    -- waits about four at READY, before the first update).
    if playing and updates > 0 and frame - last_update > 120 then
        playing = false
        if recording then finish() end
    end
    local fast = not playing
    machine.video.throttled = not fast
    machine.sound.ui_mute = fast
    if frame > 60 * 60 * 10 and recording and not finished then finish() end
end

retrolarium_pacman_demo = emu.register_frame_done(on_frame, "frame")
