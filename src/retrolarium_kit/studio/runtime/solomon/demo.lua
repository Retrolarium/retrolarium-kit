-- Loop or record Solomon's Key's attract demo.
-- The licence and title waits are cut short and the picture stays dark
-- outside the demo (PPU mask writes lose their show bits), so each run
-- starts on a black screen straight into the demo room. SOLOMON_DEMO_READY,
-- when set, names a file written when the first run starts.
-- With SOLOMON_DEMO_RECORD set, the demo's gameplay buttons come from the
-- player's controller instead of the recorded steps: every write to the
-- controller cache takes the live buttons and the step index stays at zero.
-- The buttons are written to the file as "tick value" lines, counting the
-- NMI frame counter's ticks from the moment the demo's input thread starts
-- (the clock its steps wait on); the run ends with "end tick" when the demo
-- ends or MAME stops.

local function setting(name)
    local value = tonumber(os.getenv(name) or "")
    if value == nil or value < 0 or value > 0xffff or value ~= math.floor(value) then
        error(name .. " must be a 16-bit address")
    end
    return value
end

local run_demo = setting("SOLOMON_DEMO_RUN")
local post_game = setting("SOLOMON_DEMO_POST_GAME")
local wait_summary = setting("SOLOMON_DEMO_WAIT_SUMMARY")
local wait_title = setting("SOLOMON_DEMO_WAIT_TITLE")
local counter_high = setting("SOLOMON_DEMO_COUNTER_HIGH")
local joypad_cached = setting("SOLOMON_DEMO_JOYPAD_CACHED")
local joypad_raw = setting("SOLOMON_DEMO_JOYPAD_RAW")
local demo_index = setting("SOLOMON_DEMO_INDEX")
local nmi_counter = setting("SOLOMON_DEMO_NMI_COUNTER")
local record_path = os.getenv("SOLOMON_DEMO_RECORD")
local recording = record_path ~= nil and record_path ~= ""
local ready_path = os.getenv("SOLOMON_DEMO_READY")

local START_SELECT = 0x30
local GAMEPLAY = 0xcf
local PPU_MASK = 0x2001
local SHOW_PICTURE = 0x18

local cpu = manager.machine.devices[":maincpu"]
if cpu == nil or cpu.spaces["program"] == nil then
    error("MAME NES main CPU program space is unavailable")
end
local memory = cpu.spaces["program"]
local pc = cpu.state["PC"]

local in_demo = false       -- between the demo thread's start and the post-game thread
local recorded = false      -- a recording ran (one run per launch)
local active = false        -- recording now
local ticks = 0
local counter = 0
local last = -1
local output = nil

local function close_recording()
    if output ~= nil then
        output:write(string.format("end %d\n", ticks))
        output:close()
        output = nil
    end
    active = false
end

-- Program-space read taps see every read, the 6502's dummy reads after
-- RTS included; an opcode fetch is the read made with PC at the address.
-- Holding the handler objects keeps them installed.
summary_tap = memory:install_read_tap(wait_summary, wait_summary, "solomon-demo-summary",
    function(_, value)
        if pc.value == wait_summary then memory:write_u8(counter_high, 1) end
        return value
    end)
title_tap = memory:install_read_tap(wait_title, wait_title, "solomon-demo-title",
    function(_, value)
        if pc.value == wait_title then memory:write_u8(counter_high, 2) end
        return value
    end)
run_tap = memory:install_read_tap(run_demo, run_demo, "solomon-demo-run",
    function(_, value)
        if pc.value ~= run_demo then return value end
        in_demo = true
        if ready_path ~= nil and ready_path ~= "" then
            local file = io.open(ready_path, "w")
            if file ~= nil then
                file:write("ready")
                file:close()
            end
            ready_path = nil
        end
        if recording and not recorded then
            recorded = true
            active = true
            ticks = 0
            counter = 0
            last = -1
            output = assert(io.open(record_path, "w"))
            print("Solomon demo: recording; press Start or stop the emulator to finish")
        end
        return value
    end)
post_game_tap = memory:install_read_tap(post_game, post_game, "solomon-demo-post-game",
    function(_, value)
        if pc.value ~= post_game then return value end
        in_demo = false
        if active then
            close_recording()
            print("Solomon demo: recording finished")
            manager.machine:exit()
        end
        return value
    end)
mask_tap = memory:install_write_tap(PPU_MASK, PPU_MASK, "solomon-demo-dark",
    function(_, data, _)
        if in_demo then return data end
        return data & ~SHOW_PICTURE
    end)

if recording then
    cache_tap = memory:install_write_tap(joypad_cached, joypad_cached, "solomon-demo-live",
        function(_, data, _)
            if not active then return data end
            local buttons = memory:read_u8(joypad_raw) & GAMEPLAY
            if buttons ~= last then
                output:write(string.format("%d %d\n", ticks, buttons))
                output:flush()
                last = buttons
            end
            return (data & START_SELECT) | buttons
        end)
    -- The NMI increments the counter; the demo thread resets it.
    tick_tap = memory:install_write_tap(nmi_counter, nmi_counter, "solomon-demo-ticks",
        function(_, data, _)
            if active and data == (counter + 1) & 0xff then ticks = ticks + 1 end
            counter = data
            return data
        end)
    stop_subscription = emu.add_machine_stop_notifier(function()
        if active then close_recording() end
    end)
end

-- The step index stays at the first step so the demo never runs out.
emu.register_frame_done(function()
    if active then memory:write_u8(demo_index, 0) end
end, "solomon-demo-frame")

print(recording and "Solomon demo: waiting for the demo to start" or "Solomon demo: looping")
