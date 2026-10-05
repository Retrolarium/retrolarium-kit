-- Start a Doraemon game in one world through the title's chapter select.
--
-- The title starts the world ShellChapterSelectIndex names when A and B are
-- held with Start. The script presses Start on the title and answers the
-- shell's two reads for the start: the buttons (LDA $21, with A and B
-- added) and the chapter (LDX $3C, the chosen world). The world first shows
-- its status screen (the chapter, score and lives) for 90 frames; until
-- rendering comes back on after it the emulator runs unthrottled and muted.
-- DORAEMON_PLAYTEST_READY, when set, names a file written then.

local function setting(name, minimum, maximum)
    local value = tonumber(os.getenv(name) or "")
    if value == nil or value < minimum or value > maximum
            or value ~= math.floor(value) then
        error(name .. " must be in range " .. minimum .. ".." .. maximum)
    end
    return value
end

local world = setting("DORAEMON_PLAYTEST_WORLD", 1, 3)
local BUTTONS_READ = setting("DORAEMON_PLAYTEST_BUTTONS_READ", 0x8000, 0xFFFF)
local CHAPTER_READ = setting("DORAEMON_PLAYTEST_CHAPTER_READ", 0x8000, 0xFFFF)
local BUTTONS = 0x0021          -- CombinedControllerButtons
local CHAPTER = 0x003C          -- ShellChapterSelectIndex
local PPU_MASK = 0x2001

local ready_path = os.getenv("DORAEMON_PLAYTEST_READY")
local machine = manager.machine
local cpu = machine.devices[":maincpu"]
if cpu == nil or cpu.spaces["program"] == nil then
    error("MAME NES main CPU program space is unavailable")
end
local memory = cpu.spaces["program"]

local start_field = nil
for _, port in pairs(machine.ioport.ports) do
    for _, field in pairs(port.fields) do
        if string.lower(field.name or "") == "p1 start" and field.player == 0 then
            start_field = field
            break
        end
    end
    if start_field ~= nil then break end
end
if start_field == nil then
    error("MAME NES player-one Start input is unavailable")
end

local frame = 0
local starting = false      -- the shell checked the buttons for a chapter start
local entered = false       -- the shell chose the chapter
local placed = false        -- the world shows
local shown = nil           -- the frame rendering last came on
local status_done = false   -- the status screen has been shown and hidden
local entered_at = 0

-- A point inside the world (DORAEMON_PLAYTEST_POINT, empty for its start),
-- set by answering the world's own start-up writes, once each:
--   "camera X Y PX PY A" World 1's city: World1_InitializeDemoState's camera
--                       cells ($5B/$5C), player position ($75/$76) and area
--                       palette ($29, 1 on the mountain);
--   "stage N"           World 2: the stage index Bank1_World2Main clears;
--   "room N"            World 3: the room Bank2_World3Main sets to 0 and the
--                       pass hoop's room the respawn then loads (the game
--                       picks a passable position in it); answered until the
--                       world shows.
--   "underground R X Y CX A"  World 1's underground: on the city's first
--                       frame World1_TryEnterManhole sees A on a manhole
--                       (buttons and flag reads), World1_EnterManhole stores
--                       room R + 8 as the manhole, and the room profile's
--                       player position, camera X and axis position become
--                       X, Y, CX and A. The world shows from the room.
-- DORAEMON_PLAYTEST_WRITES names the PCs of those stores, comma-separated.
local point = {}
for word in string.gmatch(os.getenv("DORAEMON_PLAYTEST_POINT") or "", "%S+") do point[#point + 1] = word end
local writes = {}
for pc in string.gmatch(os.getenv("DORAEMON_PLAYTEST_WRITES") or "", "%d+") do writes[#writes + 1] = tonumber(pc) end
local overrides = {}        -- address -> {pc, value}
if point[1] == "camera" and #point == 6 and #writes == 5 then
    overrides[0x5B] = {writes[1], tonumber(point[2])}
    overrides[0x5C] = {writes[2], tonumber(point[3])}
    overrides[0x75] = {writes[3], tonumber(point[4])}
    overrides[0x76] = {writes[4], tonumber(point[5])}
    overrides[0x29] = {writes[5], tonumber(point[6])}
elseif point[1] == "stage" and #writes >= 1 then
    overrides[0xA9] = {writes[1], tonumber(point[2])}
elseif point[1] == "room" and #writes >= 1 then
    overrides[0xDF] = {writes, tonumber(point[2]), sticky = true}
elseif point[1] == "underground" and #point == 6 and #writes == 7 then
    overrides[0x81] = {writes[3], tonumber(point[2]) + 8}
    overrides[0x75] = {writes[4], tonumber(point[3])}
    overrides[0x76] = {writes[5], tonumber(point[4])}
    overrides[0x5B] = {writes[6], tonumber(point[5])}
    overrides[0x89] = {writes[7], tonumber(point[6])}
elseif point[1] ~= nil then
    error("DORAEMON_PLAYTEST_POINT is not a camera, stage or room point")
end
local underground = point[1] == "underground"
local dived = false         -- the underground room is set up
point_taps = {}
if underground then
    -- The manhole entry reads: A held and the "on a manhole" flag, once.
    local pressed = false
    point_taps[#point_taps + 1] = memory:install_read_tap(0x21, 0x21, "doraemon-playtest-dive-buttons",
        function(_, value)
            if not entered or pressed or cpu.state["PC"].value ~= writes[1] then return value end
            pressed = true
            return value | 0x80
        end)
    point_taps[#point_taps + 1] = memory:install_read_tap(0x81, 0x81, "doraemon-playtest-dive-flag",
        function(_, value)
            if not pressed or overrides[0x81].done or cpu.state["PC"].value ~= writes[2] then return value end
            return 1
        end)
end
for address, override in pairs(overrides) do
    point_taps[#point_taps + 1] = memory:install_write_tap(address, address, "doraemon-playtest-point",
        function(_, value)
            if not entered or override.done or placed then return value end
            local pc = cpu.state["PC"].value
            local pcs = type(override[1]) == "table" and override[1] or {override[1]}
            for _, wanted in ipairs(pcs) do
                if pc == wanted then
                    override.done = not override.sticky
                    if address == 0x89 then dived = true end
                    return override[2]
                end
            end
            return value
        end)
end

buttons_tap = memory:install_read_tap(BUTTONS, BUTTONS, "doraemon-playtest-buttons",
    function(_, value)
        if entered or cpu.state["PC"].value ~= BUTTONS_READ then return value end
        starting = true
        return value | 0xC0
    end)

chapter_tap = memory:install_read_tap(CHAPTER, CHAPTER, "doraemon-playtest-chapter",
    function(_, value)
        if entered or not starting or cpu.state["PC"].value ~= CHAPTER_READ then return value end
        entered = true
        entered_at = frame
        return world - 1
    end)

mask_tap = memory:install_write_tap(PPU_MASK, PPU_MASK, "doraemon-playtest-mask",
    function(_, value)
        if not entered or placed then return value end
        local on = value & 0x18 ~= 0
        if not on then
            -- A screen that stayed a second was the status screen.
            if shown ~= nil and frame - shown >= 60 then status_done = true end
            shown = nil
        elseif shown == nil then
            shown = frame
        end
        if on and (status_done or frame - entered_at > 600) and (dived or not underground) then
            placed = true
            machine.video.throttled = true
            machine.sound.ui_mute = false
            if ready_path ~= nil and ready_path ~= "" then
                local file = io.open(ready_path, "w")
                if file ~= nil then
                    file:write("ready")
                    file:close()
                end
            end
        end
        return value
    end)

local function on_frame()
    frame = frame + 1
    if placed then return end
    machine.video.throttled = false
    machine.sound.ui_mute = true
    -- Start skips the intro, then starts the game on the title.
    if not entered then
        local phase = frame % 40
        if frame >= 60 and phase == 0 then start_field:set_value(1) end
        if phase == 3 then start_field:clear_value() end
    else
        start_field:clear_value()
    end
    if frame > 3600 then
        error("Doraemon did not start a game for the playtest")
    end
end

retrolarium_doraemon_playtest = emu.register_frame_done(on_frame, "frame")
