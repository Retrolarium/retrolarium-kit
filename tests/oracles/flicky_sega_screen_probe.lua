-- Capture the early canonical SEGA screen without altering the cartridge.
local prefix = os.getenv("FLICKY_SEGA_SCREEN_PREFIX")
if prefix == nil or prefix == "" then error("FLICKY_SEGA_SCREEN_PREFIX is required") end

local targets = {30, 60, 90, 120, 150, 180}
local next_target = 1
local frame = 0
local function capture_frame()
    frame = frame + 1
    local screen = manager.machine.screens:at(1)
    if screen == nil then return end
    if frame < targets[next_target] then return end
    local name = string.format("%s-%03d.png", prefix, targets[next_target])
    local failure = screen:snapshot(name)
    if failure then error("SEGA screen snapshot failed: " .. failure) end
    emu.print_info(string.format("Flicky SEGA screen: frame=%d file=%s", frame, name))
    next_target = next_target + 1
    if next_target > #targets then
        manager.machine:exit()
    end
end

retrolarium_flicky_sega_screen_probe = emu.register_frame_done(capture_frame, "frame")
