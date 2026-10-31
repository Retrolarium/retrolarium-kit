-- Loop or record one Alien Soldier attract demo.
-- The game reaches the demo as it does after power-on: past the story (Start
-- is pressed once the story screen takes it) and through the title,
-- whose idle timer is moved to the frame before it starts a demo, with
-- DemoRotationIndex set to the demo asked for (ALIEN_DEMO_INDEX, 0..3).
-- Until the demo starts the emulator runs unthrottled and muted; after each
-- run the machine powers on again. ALIEN_DEMO_READY, when set, names a file
-- written when the demo starts.
--
-- Recording takes over at the weapon setup, preserving only the title's
-- native input prefix. Each step is counted at the game's own VBlank input
-- consumption, never at a rendered frame: skipped rendering must not drop
-- input ticks. The same playback input write receives the live pad word.
-- Finished runs are written as "step frames held pressed".
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
    "DemoPlaybackInputWord",
    "DemoInputRunFrames",
    "ControllerHeldState",
    "ControllerPressedState",
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

local function write_ready()
    if ready_path ~= nil and ready_path ~= "" then
        local file = io.open(ready_path, "w")
        if file ~= nil then
            file:write("ready")
            file:close()
        end
    end
end

local in_demo, output, prefix = false, nil, nil
local consumed = 0
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

local pad_pressed, delivering = 0, false
if recording then
    pressed_tap = memory:install_write_tap(
        ram.ControllerPressedState,
        ram.ControllerPressedState + 1,
        "alien-soldier-demo-pressed",
        function(_, data, mask)
            if mask & 0xFF00 == 0 then
                return data
            end
            if delivering then
                delivering = false
                return data
            end
            pad_pressed = (data >> 8) & 0xFF
            -- Start confirms weapon setup. Hide it only from the earlier
            -- real-pad abort check, then deliver it through the demo reader.
            if
                memory:read_u16(ram.DemoPlaybackActive) ~= 0
                and (memory:read_u16(ram.GameModeIndex) == 0x70 or memory:read_u16(ram.GameModeIndex) == 0x74)
            then
                return data & 0x7FFF
            end
            return data
        end
    )
end

if recording then
    -- Demo_HandlePlaybackInput writes this word just before copying it to
    -- the controller. The physical pad is still in ControllerHeldState here.
    input_tap = memory:install_write_tap(
        ram.DemoPlaybackInputWord,
        ram.DemoPlaybackInputWord + 1,
        "alien-soldier-demo-input",
        function(_, data, mask)
            if mask ~= 0xFFFF or memory:read_u16(ram.DemoPlaybackActive) == 0 then
                return data
            end
            local mode = memory:read_u16(ram.GameModeIndex)
            if prefix == nil and mode == 0x70 then
                prefix = consumed
                output = assert(io.open(record_path, "w"))
                output:write(string.format("prefix %d\n", prefix))
                output:flush()
                machine.video.throttled = true
                machine.sound.ui_mute = false
                write_ready()
                print("Alien Soldier demo: recording from weapon setup")
            end
            consumed = consumed + 1
            if prefix == nil then
                return data
            end
            local word = (memory:read_u8(ram.ControllerHeldState) << 8) | pad_pressed
            local held, pressed = (word >> 8) & 0xFF, word & 0xFF
            if held & START ~= 0 and mode ~= 0x70 and mode ~= 0x74 then
                demo_over()
                return data
            end
            if held ~= step_held or pressed ~= step_pressed or step_frames == 0xFFFF then
                finish_step()
                step_held, step_pressed, step_frames = held, pressed, 0
            end
            step_frames = step_frames + 1
            -- The native reader decrements this after this write. Keep it
            -- above zero so it never advances the original stream while live.
            memory:write_u16(ram.DemoInputRunFrames, 2)
            delivering = true
            return word
        end
    )
end

retrolarium_alien_soldier_demo = emu.register_frame_done(function()
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
            if not recording then
                machine.video.throttled = true
                machine.sound.ui_mute = false
                write_ready()
            end
        end
        return
    end
    if memory:read_u16(ram.DemoPlaybackActive) == 0 then
        demo_over()
        return
    end
end, "alien-soldier-demo")

print(recording and "Alien Soldier demo: waiting for the demo to start" or "Alien Soldier demo: looping")
