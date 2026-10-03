-- Loop or record Solomon's Key's attract demo.
-- The game reaches its demo the way it does after power-on (licence, title,
-- then the demo), so the demo starts in the very state it starts in on a
-- console; outside the demo the emulator runs unthrottled and muted and the
-- picture stays dark (PPU mask writes lose their show bits), which makes
-- that wait a moment of black. After each run the machine powers on again,
-- so every loop is the console's first demo. SOLOMON_DEMO_READY, when set,
-- names a file written when the first run starts.
--
-- With SOLOMON_DEMO_RECORD set, the demo plays the player's controller
-- through its own step tables (at SOLOMON_DEMO_DURATIONS, SOLOMON_DEMO_STEPS
-- long), so the game runs the very code it runs when it plays the steps
-- back. Each poll reads a duration: "wait" (255) while the buttons stay as
-- they are, and the frame counter less one when they change (or the counter
-- is about to wrap), which puts the new buttons, read from the value table,
-- in at once. The step index stays at zero. Every step is written to the
-- file as "step duration buttons"; when the demo ends (Start) or MAME
-- stops, "hold ticks" gives the time since the last step, then "end".

local function setting(name)
    local value = tonumber(os.getenv(name) or "")
    if value == nil or value < 0 or value > 0xffff or value ~= math.floor(value) then
        error(name .. " must be a 16-bit address")
    end
    return value
end

local run_demo = setting("SOLOMON_DEMO_RUN")
local post_game = setting("SOLOMON_DEMO_POST_GAME")
local joypad_raw = setting("SOLOMON_DEMO_JOYPAD_RAW")
local demo_index = setting("SOLOMON_DEMO_INDEX")
local nmi_counter = setting("SOLOMON_DEMO_NMI_COUNTER")
local durations = tonumber(os.getenv("SOLOMON_DEMO_DURATIONS") or "")
local steps = tonumber(os.getenv("SOLOMON_DEMO_STEPS") or "")
local record_path = os.getenv("SOLOMON_DEMO_RECORD")
local recording = record_path ~= nil and record_path ~= ""
local ready_path = os.getenv("SOLOMON_DEMO_READY")

local GAMEPLAY = 0xcf
local WAIT = 255            -- the counter never passes it
local PPU_MASK = 0x2001
local SHOW_PICTURE = 0x18

local machine = manager.machine
local cpu = machine.devices[":maincpu"]
if cpu == nil or cpu.spaces["program"] == nil then
    error("MAME NES main CPU program space is unavailable")
end
local memory = cpu.spaces["program"]
local pc = cpu.state["PC"]

local in_demo = false       -- between the demo thread's start and the post-game thread
local active = false        -- recording now
local installed = 0         -- the buttons the demo thread put in last
local pending = nil         -- {duration, buttons} the current poll puts in
local output = nil

local function close_recording()
    if output ~= nil then
        -- The ticks since the last step: the demo goes on that long.
        output:write(string.format("hold %d\n", memory:read_u8(nmi_counter)))
        output:write("end\n")
        output:close()
        output = nil
    end
    active = false
end

-- Program-space read taps see every read, the 6502's dummy reads after
-- RTS included; an opcode fetch is the read made with PC at the address.
-- Holding the handler objects keeps them installed.
run_tap = memory:install_read_tap(run_demo, run_demo, "solomon-demo-run",
    function(_, value)
        if pc.value ~= run_demo or in_demo then return value end
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
            active = true
            installed = 0
            pending = nil
            output = assert(io.open(record_path, "w"))
            print("Solomon demo: recording; press Start or stop the emulator to finish")
        end
        return value
    end)
post_game_tap = memory:install_read_tap(post_game, post_game, "solomon-demo-post-game",
    function(_, value)
        if pc.value ~= post_game or not in_demo then return value end
        in_demo = false
        if active then
            close_recording()
            print("Solomon demo: recording finished")
            machine:exit()
        elseif not recording then
            machine:hard_reset()
        end
        return value
    end)
mask_tap = memory:install_write_tap(PPU_MASK, PPU_MASK, "solomon-demo-dark",
    function(_, data, _)
        if in_demo then return data end
        return data & ~SHOW_PICTURE
    end)

if recording then
    if durations == nil or steps == nil or steps < 1 then
        error("recording needs SOLOMON_DEMO_DURATIONS and SOLOMON_DEMO_STEPS")
    end
    duration_tap = memory:install_read_tap(durations, durations + steps - 1, "solomon-demo-duration",
        function(_, value)
            if not active then return value end
            local buttons = memory:read_u8(joypad_raw) & GAMEPLAY
            local counter = memory:read_u8(nmi_counter)
            if counter >= 1 and (buttons ~= installed or counter >= WAIT) then
                pending = {counter - 1, buttons}
                return counter - 1
            end
            pending = nil
            return WAIT
        end)
    value_tap = memory:install_read_tap(durations + steps, durations + 2 * steps - 1, "solomon-demo-value",
        function(_, value)
            if not active or pending == nil then return value end
            local step = pending
            pending = nil
            installed = step[2]
            output:write(string.format("step %d %d\n", step[1], step[2]))
            output:flush()
            return step[2]
        end)
    stop_subscription = emu.add_machine_stop_notifier(function()
        if active then close_recording() end
    end)
end

emu.register_frame_done(function()
    if in_demo then
        -- The step index stays at the first step so the demo never runs out.
        if active then memory:write_u8(demo_index, 0) end
    else
        -- The way to the demo: fast and silent.
        machine.video.throttled = false
        machine.sound.ui_mute = true
    end
end, "solomon-demo-frame")

print(recording and "Solomon demo: waiting for the demo to start" or "Solomon demo: looping")
