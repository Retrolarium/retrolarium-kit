-- Play a Retrolarium movie in MAME and read RAM cells after chosen frames
-- (retrolarium_kit.core.movies writes the files this reads).
--
-- RL_MOVIE_INPUT   runs of frames: "<count> <pad one mask> <pad two mask>
--                  <command>" (command 0 none, 1 soft reset, 2 power cycle)
-- RL_MOVIE_READS   "<frame> <space> <address> <length> <name>" lines
-- RL_MOVIE_OUTPUT  written when the movie ends: "<frame> <name> <hex>"
-- RL_MOVIE_OFFSET  idle frames before the movie's first frame
-- RL_MOVIE_FRAMES  the movie's length
-- RL_MOVIE_STAY    1: keep MAME running after the movie (someone watches)
--
-- Frame n's input is set when frame n-1 is done, so the game reads it while
-- frame n runs; frame 1's input is set before the first frame.

local machine = manager.machine

local function env(name)
    local value = os.getenv(name)
    if value == nil or value == "" then error(name .. " is not set") end
    return value
end

local runs = {}
for line in io.lines(env("RL_MOVIE_INPUT")) do
    local count, one, two, command = line:match("^(%d+) (%d+) (%d+) (%d+)$")
    if count == nil then error("bad input line: " .. line) end
    runs[#runs + 1] = {tonumber(count), tonumber(one), tonumber(two), tonumber(command)}
end

local reads = {}
for line in io.lines(env("RL_MOVIE_READS")) do
    local frame, space, address, length, name = line:match("^(%d+) (%S+) (%d+) (%d+) (%S+)$")
    if frame == nil then error("bad read line: " .. line) end
    frame = tonumber(frame)
    reads[frame] = reads[frame] or {}
    table.insert(reads[frame], {space, tonumber(address), tonumber(length), name})
end

local output_path = env("RL_MOVIE_OUTPUT")
local offset = tonumber(env("RL_MOVIE_OFFSET"))
local total = tonumber(env("RL_MOVIE_FRAMES"))
local stay = os.getenv("RL_MOVIE_STAY") == "1"
local done = false

-- The pads' fields by mask: NES joypads or Mega Drive pads.
local pads = {{}, {}}
for tag, port in pairs(machine.ioport.ports) do
    local index = tag:match("^:ctrl(%d):")
    index = tonumber(index)
    if index == 1 or index == 2 then
        for _, field in pairs(port.fields) do
            pads[index][field.mask] = pads[index][field.mask] or field
        end
    end
end
if next(pads[1]) == nil then error("no controller one on this machine") end

local function set_pad(index, mask)
    for bit, field in pairs(pads[index]) do
        if mask & bit ~= 0 then field:set_value(1) else field:clear_value() end
    end
end

local cpu = machine.devices[":maincpu"]
local function space(name)
    local found = cpu.spaces[name]
    if found == nil then error("no address space " .. name) end
    return found
end

local results = {}
local function take(frame)
    for _, item in ipairs(reads[frame] or {}) do
        local memory = space(item[1])
        local bytes = {}
        for i = 0, item[3] - 1 do
            bytes[#bytes + 1] = string.format("%02x", memory:read_u8(item[2] + i))
        end
        results[#results + 1] = string.format("%d %s %s", frame, item[4], table.concat(bytes))
    end
end

local function finish()
    local file = io.open(output_path, "w")
    file:write(table.concat(results, "\n"), "\n")
    file:close()
    done = true
    if not stay then machine:exit() end
end

-- Walk the runs frame by frame.
local run_index, run_left = 1, runs[1] and runs[1][1] or 0
local function frame_input()
    local run = runs[run_index]
    local result = run
    run_left = run_left - 1
    if run_left == 0 then
        run_index = run_index + 1
        run_left = runs[run_index] and runs[run_index][1] or 0
    end
    return result
end

local idle = offset
local frame = 0          -- frames of the movie applied so far
local first_of_run = true

local function apply_next()
    if frame >= total then return end
    local run = frame_input()
    frame = frame + 1
    if run[4] == 1 then machine:soft_reset() elseif run[4] == 2 then machine:hard_reset() end
    set_pad(1, run[2])
    if next(pads[2]) ~= nil then set_pad(2, run[3]) end
end

if idle == 0 then apply_next() end

local function step()
    if done then return end
    if idle > 0 then
        idle = idle - 1
        if idle == 0 then apply_next() end
        return
    end
    take(frame)
    if frame >= total then
        finish()
        return
    end
    apply_next()
end

-- An error inside the notifier would only be logged while MAME runs on, so
-- report it in the output file and stop.
frame_subscription = emu.add_machine_frame_notifier(function()
    local ok, problem = pcall(step)
    if not ok then
        local file = io.open(output_path .. ".error", "w")
        if file ~= nil then
            file:write(tostring(problem), "\n")
            file:close()
        end
        machine:exit()
    end
end)
