-- Loop Doraemon's World 1 attract demo of a build, or record a new one.
--
-- The boot and the title run unthrottled and muted; World 1's demo
-- (World1_DemoEntry pointing $A6/$A7 at the stream) runs at speed. When it
-- starts, the machine's state is saved; when it ends, that state comes back,
-- so the demo plays again at once and the attract mode never moves on to
-- the other worlds' demos, which have no stream to edit.
-- DORAEMON_DEMO_READY, when set, names a file written when the first World 1
-- demo starts.
--
-- With DORAEMON_DEMO_RECORD set, the demo follows controller one instead of
-- the stream: each update's write of the stream's buttons to
-- CombinedControllerButtons (STA $21 in the reader) writes the pressed
-- buttons, and the stream's hold counter is kept from running out. The
-- runs are written to the file as "<buttons> <updates>" lines (buttons
-- masked with $CF, at most 256 updates a run); recording ends when the demo
-- does (Start, or the game ends it) or the stream's space is full, and MAME
-- exits.

local function setting(name, minimum, maximum)
    local value = tonumber(os.getenv(name) or "")
    if value == nil or value < minimum or value > maximum
            or value ~= math.floor(value) then
        error(name .. " must be in range " .. minimum .. ".." .. maximum)
    end
    return value
end

local ENTRY = setting("DORAEMON_DEMO_ENTRY", 0x8000, 0xFFFF)
local READER = setting("DORAEMON_DEMO_READER", 0x8000, 0xFFFF)
local STEPS = setting("DORAEMON_DEMO_STEPS", 1, 1024)
local STREAM_HIGH_WRITE = ENTRY + 6     -- STA $A7 of the stream's address
local BUTTONS_WRITE = READER + 46       -- STA CombinedControllerButtons
local BUTTONS = 0x0021                  -- CombinedControllerButtons
local DEMO = 0x0027                     -- DemoModeActive
local POINTER_HIGH = 0x00A7
local HOLD = 0x00A8

local record_path = os.getenv("DORAEMON_DEMO_RECORD") or ""
local recording = record_path ~= ""
local ready_path = os.getenv("DORAEMON_DEMO_READY")
local machine = manager.machine
local cpu = machine.devices[":maincpu"]
if cpu == nil or cpu.spaces["program"] == nil then
    error("MAME NES main CPU program space is unavailable")
end
local memory = cpu.spaces["program"]

local frame = 0
local playing = false       -- World 1's demo is on
local last_update = nil     -- frame of the demo's last update
local ready = false
local runs = {}             -- recorded {buttons, updates}
local finished = false
local LOOP_STATE = "retrolarium-doraemon-demo"
local saved = false         -- the state at the demo's start is saved

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
    print(string.format("Doraemon demo recorded: %d runs", #runs))
    machine:exit()
end

-- The demo is over: back to its start, so World 1's demo plays again
-- instead of the other worlds'.
local function replay()
    if recording or not saved then return end
    machine:load(LOOP_STATE)
    playing = true
    last_update = nil
end

entry_tap = memory:install_write_tap(POINTER_HIGH, POINTER_HIGH, "doraemon-demo-entry",
    function(_, value)
        if cpu.state["PC"].value == STREAM_HIGH_WRITE then
            playing = true
            last_update = nil
            if not recording and not saved then
                machine:save(LOOP_STATE)
                saved = true
            end
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

demo_tap = memory:install_write_tap(DEMO, DEMO, "doraemon-demo-flag",
    function(_, value)
        if value == 0 and playing then
            playing = false
            if recording then finish() else replay() end
        end
        return value
    end)

buttons_tap = memory:install_write_tap(BUTTONS, BUTTONS, "doraemon-demo-buttons",
    function(_, value)
        if not playing or cpu.state["PC"].value ~= BUTTONS_WRITE then return value end
        last_update = frame
        if not recording or finished then return value end
        -- The reader has just checked the real pad for Start and Select;
        -- it is still in place until this write.
        local held = memory:read_u8(BUTTONS) & 0xCF
        memory:write_u8(HOLD, 2)
        local run = runs[#runs]
        if run ~= nil and run[1] == held and run[2] < 256 then
            run[2] = run[2] + 1
            -- Stop recording ends MAME without warning: keep the file current.
            if run[2] % 30 == 0 then write_runs() end
        else
            if #runs >= STEPS then
                finish()
                return value
            end
            runs[#runs + 1] = {held, 1}
            write_runs()
        end
        return held
    end)

local function on_frame()
    frame = frame + 1
    -- Start leaves the demo for the shell without clearing DemoModeActive;
    -- a demo whose updates stop for a second is over.
    if playing and last_update ~= nil and frame - last_update > 60 then
        playing = false
        if recording then finish() else replay() end
    end
    local fast = not playing
    machine.video.throttled = not fast
    machine.sound.ui_mute = fast
    if frame > 60 * 60 * 15 and recording and not finished then finish() end
end

retrolarium_doraemon_demo = emu.register_frame_done(on_frame, "frame")
