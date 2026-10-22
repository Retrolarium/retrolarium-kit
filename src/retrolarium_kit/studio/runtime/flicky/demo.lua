-- Loop or record one Flicky attract demo.
-- The game reaches the demo the way it does after power-on: the Sega screen,
-- the title, then Demo_Init, which here reads FLICKY_DEMO_INDEX as the demo
-- to play (each demo starts from the same state whatever ran before it).
-- Outside the demo the emulator runs unthrottled and muted. After each run
-- the machine powers on again. FLICKY_DEMO_READY, when set, names a file
-- written when the demo starts.
--
-- With FLICKY_DEMO_RECORD set, the demo plays the player's controller through
-- its own stream, so the game runs the code it runs when it plays the steps
-- back: each frame, when Demo_ReadInput reads the stream pointer, the pointer
-- reads as the stream's start, whose first pair holds the buttons the player
-- holds for one frame. The pad the game reads in VBlank stays idle, as it is
-- while a demo plays. Every finished step (at most 255 frames) is written to
-- the file as "step frames buttons", then "end" when the demo ends (the bird
-- is caught, Start is pressed) or MAME stops.

local demo = tonumber(os.getenv("FLICKY_DEMO_INDEX") or "")
if demo == nil or demo ~= math.floor(demo) or demo < 0 or demo > 3 then
    error("FLICKY_DEMO_INDEX must be 0..3")
end
local record_path = os.getenv("FLICKY_DEMO_RECORD")
local recording = record_path ~= nil and record_path ~= ""
local ready_path = os.getenv("FLICKY_DEMO_READY")

-- src/memory/ram.inc
local NEXT_GAME_MODE = 0xffffc0
local DEMO_INDEX = 0xffd890
local STREAM_PTR = 0xffd2aa
local HOLD_FRAMES = 0xffd2ac
local JOYPAD = 0xffff8e
local RAM = 0xff0000
-- Sys_GameModeTable: Demo_Init and Demo_Update.
local DEMO_INIT, DEMO_UPDATE = 0x38, 0x3c
local START = 0x80
local LONGEST = 255

local machine = manager.machine
local cpu = machine.devices[":maincpu"]
if cpu == nil or cpu.spaces["program"] == nil then
    error("MAME Mega Drive main CPU program space is unavailable")
end
local memory = cpu.spaces["program"]

local in_demo = false
local active = false -- recording now
local stream = nil -- the stream's start, as the game's 16-bit pointer
local live = 0 -- the buttons the player holds
local installed = nil -- the buttons of the step being recorded
local frames = 0 -- how long that step has played
local demo_write = false -- Demo_ReadInput's Ram_Joypad write comes next
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
        print("Flicky demo: recording finished")
        machine:exit()
    elseif not recording then
        machine:hard_reset()
    end
end

-- Only Demo_Init reads and increments Ram_DemoIndex.
index_read_tap = memory:install_read_tap(DEMO_INDEX, DEMO_INDEX + 1, "flicky-demo-index", function(_, value, mask)
    if in_demo then
        return value
    end
    return demo & mask
end)
index_write_tap = memory:install_write_tap(DEMO_INDEX, DEMO_INDEX + 1, "flicky-demo-start", function(_, value)
    -- Boot clears the index; Demo_Init writes the demo's number plus one.
    if in_demo or value == 0 then
        return value
    end
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
        print("Flicky demo: recording; press Start or stop the emulator to finish")
    end
    return value
end)

if recording then
    -- VBlank writes the pad's held and pressed bytes; the game sees them
    -- idle. Demo_ReadInput's own write passes.
    joypad_tap = memory:install_write_tap(JOYPAD, JOYPAD + 1, "flicky-demo-joypad", function(_, value, mask)
        if mask & 0xff00 ~= 0 and demo_write then
            demo_write = false
            return value
        end
        if mask & 0xff00 ~= 0 then
            live = (value >> 8) & 0xff
        end
        return value & ~mask & 0xffff
    end)
    -- Read once a frame, by Demo_ReadInput.
    pointer_tap = memory:install_read_tap(STREAM_PTR, STREAM_PTR + 1, "flicky-demo-pointer", function(_, value, mask)
        if not active or mask ~= 0xffff then
            return value
        end
        if stream == nil then
            stream = value
        end
        local buttons = live & ~START & 0xff
        if installed == nil or buttons ~= installed or frames == LONGEST then
            finish_step()
            installed = buttons
            frames = 0
        end
        frames = frames + 1
        memory:write_u8(RAM + stream, installed)
        memory:write_u8(HOLD_FRAMES, 1)
        demo_write = true
        return stream
    end)
    stop_subscription = emu.add_machine_stop_notifier(function()
        if active then
            close_recording()
        end
    end)
end

emu.register_frame_done(function()
    if in_demo then
        local mode = memory:read_u16(NEXT_GAME_MODE)
        -- The bird is caught, or Start leaves for the title.
        if mode ~= DEMO_INIT and mode ~= DEMO_UPDATE then
            demo_over()
        elseif active and stream ~= nil and live & START ~= 0 then
            demo_over()
        end
    else
        -- The way to the demo: fast and silent.
        machine.video.throttled = false
        machine.sound.ui_mute = true
    end
end, "flicky-demo-frame")

print(recording and "Flicky demo: waiting for the demo to start" or "Flicky demo: looping")
