-- Loop or record the Super Mario Bros. title demo.
-- The game reaches its demo the way it does after power-on, so the demo
-- starts in the very state it starts in on a console; outside the demo the
-- emulator runs unthrottled and muted (the picture keeps its PPU mask: the
-- title waits for a sprite-0 hit). After each run the machine powers on again,
-- so every loop is the console's first demo. SMB_DEMO_READY, when set, names
-- a file written when the first run starts.
--
-- With SMB_DEMO_RECORD set, the demo plays the player's controller through
-- its own tables, so the game runs the code it runs when it plays the steps
-- back: each frame the engine's entry compares the buttons the player holds
-- with the step it plays; when they differ (or a step reaches 255 frames)
-- the step timer and index go to zero, so the engine loads a step at once,
-- reading 255 frames and the new buttons. Every finished step is written
-- to the file as "step frames buttons", then "end" when the demo ends (Start
-- starts a game, Select returns to the title, the player dies) or MAME stops.

local function setting(name)
    local value = tonumber(os.getenv(name) or "")
    if value == nil or value < 0 or value > 0xffff or value ~= math.floor(value) then
        error(name .. " must be a 16-bit address")
    end
    return value
end

local engine = setting("SMB_DEMO_ENGINE")
local reset_title = setting("SMB_DEMO_RESET_TITLE")
local actions = setting("SMB_DEMO_ACTIONS")
local durations = setting("SMB_DEMO_DURATIONS")
local capacity = setting("SMB_DEMO_CAPACITY")
local saved_joypad = setting("SMB_DEMO_SAVED_JOYPAD")
local demo_action = setting("SMB_DEMO_ACTION")
local demo_timer = setting("SMB_DEMO_ACTION_TIMER")
local oper_mode = setting("SMB_DEMO_OPER_MODE")
local record_path = os.getenv("SMB_DEMO_RECORD")
local recording = record_path ~= nil and record_path ~= ""
local ready_path = os.getenv("SMB_DEMO_READY")

local GAMEPLAY = 0xcf -- Start and Select leave the demo
local LONGEST = 255

local machine = manager.machine
local cpu = machine.devices[":maincpu"]
if cpu == nil or cpu.spaces["program"] == nil then
    error("MAME NES main CPU program space is unavailable")
end
local memory = cpu.spaces["program"]
local pc = cpu.state["PC"]

local in_demo = false
local active = false -- recording now
local installed = nil -- the buttons of the step the engine plays
local pending = nil -- the buttons the engine loads this frame
local frames = 0 -- how long the current step has played
local output = nil

local function finish_step()
    if output ~= nil and installed ~= nil and frames > 0 then
        output:write(string.format("step %d %d\n", frames, installed))
        output:flush()
    end
end

local function close_recording()
    if output ~= nil then
        finish_step()
        output:write("end\n")
        output:close()
        output = nil
    end
    active = false
end

local function demo_over()
    in_demo = false
    if active then
        close_recording()
        print("SMB demo: recording finished")
        machine:exit()
    elseif not recording then
        machine:hard_reset()
    end
end

-- Program-space read taps see every read, dummy reads included; an opcode
-- fetch is the read made with PC at the address. Holding the handler
-- objects keeps them installed.
engine_tap = memory:install_read_tap(engine, engine, "smb-demo-engine", function(_, value)
    if pc.value ~= engine then
        return value
    end
    if not in_demo then
        in_demo = true
        machine.video.throttled = true
        machine.sound.ui_mute = false
        if ready_path ~= nil and ready_path ~= "" then
            local file = io.open(ready_path, "w")
            if file ~= nil then
                file:write("ready")
                file:close()
            end
        end
        if recording and output == nil then
            active = true
            output = assert(io.open(record_path, "w"))
            print("SMB demo: recording; press Start or stop the emulator to finish")
        end
    end
    if active then
        local live = memory:read_u8(saved_joypad) & GAMEPLAY
        local timer = memory:read_u8(demo_timer)
        if installed == nil or live ~= installed or timer == 0 then
            finish_step()
            memory:write_u8(demo_action, 0)
            memory:write_u8(demo_timer, 0)
            pending = live
            frames = 0
        end
        frames = frames + 1
    end
    return value
end)
reset_tap = memory:install_read_tap(reset_title, reset_title, "smb-demo-reset", function(_, value)
    if pc.value == reset_title and in_demo then
        demo_over()
    end
    return value
end)
if recording then
    duration_tap = memory:install_read_tap(durations, durations + capacity, "smb-demo-duration", function(_, value)
        if active then
            return LONGEST
        end
        return value
    end)
    action_tap = memory:install_read_tap(actions, actions + capacity - 1, "smb-demo-action", function(_, value)
        if not active then
            return value
        end
        if pending ~= nil then
            installed = pending
            pending = nil
        end
        return installed
    end)
    stop_subscription = emu.add_machine_stop_notifier(function()
        if active then
            close_recording()
        end
    end)
end

emu.register_frame_done(function()
    if in_demo then
        -- Start leaves the demo for a game.
        if memory:read_u8(oper_mode) ~= 0 then
            demo_over()
        end
    else
        -- The way to the demo: fast and silent.
        machine.video.throttled = false
        machine.sound.ui_mute = true
    end
end, "smb-demo-frame")

print(recording and "SMB demo: waiting for the demo to start" or "SMB demo: looping")
