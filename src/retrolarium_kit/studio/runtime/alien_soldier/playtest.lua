-- Start a built Alien Soldier cartridge at a chosen stage, through the
-- game's own password screen: nothing in RAM is set that the game does not
-- set itself, except the password digits and the cursor on the screen that
-- reads them.
--
-- 1. When the boot shows the story, press Start once the story screen's
--    palette and loader allow it.
-- 2. When the title has faded in, select the password screen (game mode $44).
-- 3. When that screen has faded in, write the stage's code into the digits,
--    put the cursor on confirm and press C (the raw bit the check tests).
-- 4. On the weapon setup screen, accept each page with C once it has
--    scrolled in, and Start at its final page.
-- 5. Ready when the game runs (mode $10) at the stage asked for, with the
--    player object in place.
--
-- The kit reads the RAM addresses from the source by name and gives them in
-- ALIEN_RAM_<NAME>, with ALIEN_PLAYTEST_STAGE_INDEX (the StageTableIndex
-- wanted) and ALIEN_PLAYTEST_PASSWORD (the code, hexadecimal). Until it is
-- ready the emulator runs unthrottled and muted. ALIEN_PLAYTEST_RESULT names a
-- file to write the outcome to; ALIEN_PLAYTEST_EXIT=1 stops MAME afterwards.

local function setting(name, base)
    local value = tonumber(os.getenv(name) or "", base or 10)
    if value == nil then
        error(name .. " is missing")
    end
    return value
end

local ram = {}
for _, name in ipairs({
    "GameModeIndex",
    "GameSubstateIndex",
    "StageTableIndex",
    "DifficultyMode",
    "PasswordDigits",
    "PasswordCursorOffset",
    "PasswordCursorMoveFlag",
    "PlayerObjectType",
    "PaletteFadeMaskStatus",
    "ScenePaletteFadeOffset",
    "DataLoaderControl",
    "PaletteFadeColorOffset",
    "PaletteFadeMode",
    "SetupTransitionIndex",
    "WeaponSetupVScrollTarget",
    "SecondaryCameraYPos",
}) do
    ram[name] = setting("ALIEN_RAM_" .. name, 16) & 0xFFFFFF
end
local wanted = setting("ALIEN_PLAYTEST_STAGE_INDEX")
local password = setting("ALIEN_PLAYTEST_PASSWORD", 16)
local result_path = os.getenv("ALIEN_PLAYTEST_RESULT")
local exit_when_ready = os.getenv("ALIEN_PLAYTEST_EXIT") == "1"

local machine = manager.machine
local memory = machine.devices[":maincpu"].spaces["program"]
local function word(name)
    return memory:read_u16(ram[name])
end

-- Controller one: MAME's Mega Drive pad fields by mask (C $20, Start $80).
local pad = {}
for tag, port in pairs(machine.ioport.ports) do
    if tag:match("^:ctrl1:") then
        for _, field in pairs(port.fields) do
            pad[field.mask] = pad[field.mask] or field
        end
    end
end
local BUTTON_C, START = 0x20, 0x80
if pad[BUTTON_C] == nil or pad[START] == nil then
    error("controller one has no C or Start button")
end
local held = nil
local function press(mask)
    pad[mask]:set_value(1)
    held = mask
end
local function release()
    if held ~= nil then
        pad[held]:clear_value()
        held = nil
    end
end

local function finish(status, frame)
    if result_path ~= nil and result_path ~= "" then
        local file = assert(io.open(result_path, "w"))
        file:write(
            string.format(
                "status=%s stage_index=%d mode=%04X player=%04X frame=%d\n",
                status,
                word("StageTableIndex"),
                word("GameModeIndex"),
                word("PlayerObjectType"),
                frame
            )
        )
        file:close()
    end
    machine.video.throttled = true
    machine.sound.ui_mute = false
    if exit_when_ready or status ~= "ready" then
        machine:exit()
    end
end

machine.video.throttled = false
machine.sound.ui_mute = true
local phase, frame, story_skipped, setup_started, released = "title", 0, false, false, false
local done = false

retrolarium_alien_soldier_playtest = emu.register_frame_done(function()
    if done then
        return
    end
    frame = frame + 1
    released = held ~= nil
    release()
    local mode = word("GameModeIndex")
    if
        phase == "title"
        and mode == 0x28
        and not story_skipped
        and memory:read_u8(ram.PaletteFadeMaskStatus) & 1 == 1
        and word("ScenePaletteFadeOffset") == 0
        and word("DataLoaderControl") < 0x8000
    then
        press(START)
        story_skipped = true
    elseif phase == "title" and mode == 0x18 and word("PaletteFadeColorOffset") == 0 then
        memory:write_u16(ram.GameModeIndex, 0x44)
        memory:write_u16(ram.GameSubstateIndex, 0)
        phase = "password"
    elseif
        phase == "password"
        and mode == 0x48
        and word("PaletteFadeMode") == 0
        and word("PaletteFadeColorOffset") == 0
    then
        memory:write_u32(ram.PasswordDigits, password)
        memory:write_u16(ram.PasswordCursorOffset, 8)
        memory:write_u16(ram.PasswordCursorMoveFlag, 0)
        press(BUTTON_C)
        phase = "setup"
    elseif phase == "setup" and mode == 0x74 and word("GameSubstateIndex") == 2 and word("PaletteFadeMode") == 0 then
        local page = word("SetupTransitionIndex")
        if
            not released
            and (page == 0 or page == 2 or page == 4)
            and word("SecondaryCameraYPos") == word("WeaponSetupVScrollTarget")
        then
            press(BUTTON_C)
        elseif page == 0x0A and not setup_started then
            press(START)
            setup_started = true
        end
    elseif phase == "setup" and mode == 0x10 then
        if word("StageTableIndex") == wanted and word("PlayerObjectType") == 0x0008 then
            done = true
            finish("ready", frame)
        end
    end
    if not done and frame > 6000 then
        done = true
        finish("timeout", frame)
    end
end, "alien-soldier-playtest")
