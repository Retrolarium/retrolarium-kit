-- Loop or record one Alien Soldier attract demo.
-- The game reaches the demo as it does after power-on: past the story (Start
-- is pressed once the story screen takes it) and through the title,
-- whose idle timer is moved to the frame before it starts a demo, with
-- DemoRotationIndex set to the demo asked for (ALIEN_DEMO_INDEX, 0..3).
-- Until the demo starts the emulator runs unthrottled and muted; after each
-- run the machine powers on again. ALIEN_DEMO_READY, when set, names a file
-- written when the demo starts.
--
-- With ALIEN_DEMO_RECORD set, the controller plays the demo once the stage
-- runs (game mode $10): the stream itself starts the game from the title and
-- through the weapon setup, so it plays on until then, and the file's first
-- line says for how many frames ("prefix frames"). From then on, every frame
-- the step the game reads holds the buttons pressed now (and the ones newly
-- pressed), and never runs out, so the game runs the code it runs when it
-- plays a stream back. Each finished step is written as "step frames held
-- pressed", then "end" when the demo ends ($1000 frames, or Start) or MAME
-- stops.
--
-- The kit reads the RAM addresses from the source by name (ALIEN_RAM_<NAME>).
-- The buttons are the game's own pad bits (Up $01 ... Start $80).

local function setting(name, base)
    local value = tonumber(os.getenv(name) or "", base or 10)
    if value == nil then
        error(name .. " is missing")
    end
    return value
end

local demo = setting("ALIEN_DEMO_INDEX")
if demo < 0 or demo > 3 then
    error("ALIEN_DEMO_INDEX must be 0..3")
end
local ram = {}
for _, name in ipairs({
    "GameModeIndex",
    "FrameCounter",
    "DemoRotationIndex",
    "DemoPlaybackActive",
    "DemoCurrentInputWord",
    "DemoInputRunFrames",
    "ControllerHeldState",
    "PaletteFadeMaskStatus",
    "ScenePaletteFadeOffset",
    "DataLoaderControl",
}) do
    ram[name] = setting("ALIEN_RAM_" .. name, 16) & 0xFFFFFF
end
local record_path = os.getenv("ALIEN_DEMO_RECORD")
local recording = record_path ~= nil and record_path ~= ""
local ready_path = os.getenv("ALIEN_DEMO_READY")

local machine = manager.machine
local memory = machine.devices[":maincpu"].spaces["program"]
local START = 0x80
local start_field = nil
for tag, port in pairs(machine.ioport.ports) do
    if tag:match("^:ctrl1:") then
        for _, field in pairs(port.fields) do
            if field.mask == START then
                start_field = field
            end
        end
    end
end
local start_held = false

-- The buttons the player holds: the first write of a frame to
-- ControllerHeldState is VBlank's read of the pad; a demo writes after it.
local live, captured = 0, false
if recording then
    held_tap = memory:install_write_tap(
        ram.ControllerHeldState,
        ram.ControllerHeldState + 1,
        "alien-soldier-pad",
        function(offset, data, mask)
            -- The held byte is the high half of its word.
            if not captured and mask & 0xFF00 ~= 0 then
                live = (data >> 8) & 0xFF
                captured = true
            end
            return data
        end
    )
end

local in_demo, previous, output, prefix = false, 0, nil, nil
local step_held, step_pressed, step_frames = nil, nil, 0

local function finish_step()
    if output ~= nil and step_held ~= nil and step_frames > 0 then
        output:write(string.format("step %d %d %d\n", step_frames, step_held, step_pressed))
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
end

local function demo_over()
    in_demo = false
    if recording then
        close_recording()
        machine:exit()
    else
        machine:hard_reset()
    end
end

if recording then
    stop_subscription = emu.add_machine_stop_notifier(close_recording)
end

retrolarium_alien_soldier_demo = emu.register_frame_done(function()
    captured = false
    local mode = memory:read_u16(ram.GameModeIndex)
    if start_held then
        start_field:clear_value()
        start_held = false
    end
    if not in_demo then
        machine.video.throttled = false
        machine.sound.ui_mute = true
        if
            mode == 0x28
            and start_field ~= nil
            and memory:read_u8(ram.PaletteFadeMaskStatus) & 1 == 1
            and memory:read_u16(ram.ScenePaletteFadeOffset) == 0
            and memory:read_u16(ram.DataLoaderControl) < 0x8000
        then
            start_field:set_value(1)
            start_held = true
        elseif mode == 0x18 and memory:read_u16(ram.DemoPlaybackActive) == 0 then
            memory:write_u16(ram.DemoRotationIndex, demo * 2)
            if memory:read_u16(ram.FrameCounter) < 0x6F0 then
                memory:write_u16(ram.FrameCounter, 0x6F0)
            end
        elseif memory:read_u16(ram.DemoPlaybackActive) ~= 0 then
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
            if recording then
                output = assert(io.open(record_path, "w"))
                print("Alien Soldier demo: recording; press Start or stop the emulator to finish")
            end
        end
        return
    end
    if memory:read_u16(ram.DemoPlaybackActive) == 0 then
        demo_over()
        return
    end
    if recording and prefix == nil then
        -- The stream still plays the title and the weapon setup.
        demo_frames = (demo_frames or 0) + 1
        if mode == 0x10 then
            prefix = demo_frames
            output:write(string.format("prefix %d\n", prefix))
        end
    end
    if recording and prefix ~= nil then
        local held = live
        if held & START ~= 0 then
            demo_over()
            return
        end
        local pressed = held & ~previous & 0xFF
        previous = held
        if held ~= step_held or pressed ~= step_pressed or step_frames == 0xFFFF then
            finish_step()
            step_held, step_pressed, step_frames = held, pressed, 0
        end
        step_frames = step_frames + 1
        memory:write_u16(ram.DemoCurrentInputWord, (held << 8) | pressed)
        memory:write_u16(ram.DemoInputRunFrames, 2)
    end
end, "alien-soldier-demo")

print(recording and "Alien Soldier demo: waiting for the demo to start" or "Alien Soldier demo: looping")
