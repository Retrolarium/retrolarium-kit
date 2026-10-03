-- Loop or record the Castle Excellent title demo.
-- The game reaches its demo the way it does after power-on: the title waits,
-- then plays the demo script in place of controller 1. Outside the demo the
-- emulator runs unthrottled and muted. After each run the machine powers on
-- again, so every loop is the console's first demo. CASTLE_DEMO_READY, when
-- set, names a file written when the demo starts.
--
-- With CASTLE_DEMO_RECORD set, the demo plays the player's controller through
-- its own script, so the game runs the code it runs when it plays the
-- commands back: the script reads, for each 12-frame command, the buttons
-- the player holds when the command starts. The controller port reads idle
-- to the game, as it does while a demo plays; its bits are collected here.
-- Every command is written to the file as "command buttons", then "end"
-- when the demo ends (all commands played, Start pressed) or MAME stops.

local function setting(name)
    local value = tonumber(os.getenv(name) or "")
    if value == nil or value < 0 or value > 0xffff or value ~= math.floor(value) then
        error(name .. " must be a 16-bit address")
    end
    return value
end

local script = setting("CASTLE_DEMO_SCRIPT")
local reader = setting("CASTLE_DEMO_READER")
local capacity = setting("CASTLE_DEMO_CAPACITY")
local demo_active = setting("CASTLE_DEMO_ACTIVE")
local record_path = os.getenv("CASTLE_DEMO_RECORD")
local recording = record_path ~= nil and record_path ~= ""
local ready_path = os.getenv("CASTLE_DEMO_READY")

local JOYPAD1 = 0x4016
local TERMINATOR = 0xff
local START = 0x08
local GAMEPLAY = 0xf3       -- the script's mask: Select and Start never play

local machine = manager.machine
local cpu = machine.devices[":maincpu"]
if cpu == nil or cpu.spaces["program"] == nil then
    error("MAME NES main CPU program space is unavailable")
end
local memory = cpu.spaces["program"]
local pc = cpu.state["PC"]
local reset = memory:read_u16(0xfffc)
-- ReadDemoController1's LDA (ptr),Y; the branch back to it also reads the
-- script's first byte (the 6502's dummy fetch), which is no command.
local script_read = reader + 10

local in_demo = false
local active = false        -- recording now
local command = -1          -- the script index being played
local value = 0             -- its buttons
local bit = 8               -- the controller bit the next port read returns
local polled = 0
local live = 0              -- the buttons the player holds
local output = nil

local function close_recording()
    if output ~= nil then
        output:write("end\n")
        output:close()
        output = nil
    end
    active = false
end

local function start_demo()
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
        print("Castle demo: recording; press Start or stop the emulator to finish")
    end
end

local function demo_over()
    in_demo = false
    if active then
        close_recording()
        print("Castle demo: recording finished")
        machine:exit()
    elseif not recording then
        machine:hard_reset()
    end
end

-- ReadDemoController1 reads the script once a frame.
script_tap = memory:install_read_tap(script, script + capacity, "castle-demo-script",
    function(offset, data)
        if pc.value ~= script_read then return data end
        if not in_demo then start_demo() end
        if not active then return data end
        local index = offset - script
        if index ~= command then
            command = index
            if index >= capacity then
                value = TERMINATOR
            else
                value = live & GAMEPLAY
                output:write(string.format("command %d\n", value))
                output:flush()
            end
        end
        return value
    end)
-- The demo ends at its terminator with a jump to Reset.
reset_tap = memory:install_read_tap(reset, reset, "castle-demo-reset",
    function(_, data)
        if pc.value == reset and in_demo then demo_over() end
        return data
    end)
if recording then
    -- A strobe write restarts the controller's serial bits.
    strobe_tap = memory:install_write_tap(JOYPAD1, JOYPAD1, "castle-demo-strobe",
        function(_, data)
            bit = 0
            polled = 0
            return data
        end)
    -- PollController1 shifts eight reads into A, B, Select, Start, Up,
    -- Down, Left, Right from bit 0 up; the game reads the port idle.
    port_tap = memory:install_read_tap(JOYPAD1, JOYPAD1, "castle-demo-port",
        function(_, data)
            if bit < 8 then
                if data & 0x03 ~= 0 then polled = polled | (1 << bit) end
                bit = bit + 1
                if bit == 8 then live = polled end
            end
            return data & 0xfc
        end)
    stop_subscription = emu.add_machine_stop_notifier(function()
        if active then close_recording() end
    end)
end

emu.register_frame_done(function()
    if in_demo then
        -- Start leaves the demo: for a game, or when recording, its end.
        if memory:read_u8(demo_active) == 0 then
            demo_over()
        elseif active and command >= 0 and live & START ~= 0 then
            demo_over()
        end
    else
        -- The way to the demo: fast and silent.
        machine.video.throttled = false
        machine.sound.ui_mute = true
    end
end, "castle-demo-frame")

print(recording and "Castle demo: waiting for the demo to start" or "Castle demo: looping")
