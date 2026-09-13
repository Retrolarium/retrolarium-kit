-- Enter a validated SMB1 JU area and place Hero at the editor point in MAME.
-- The host supplies only the fixed SMB1_PLAYTEST_* environment settings.

local function setting(name, minimum, maximum)
    local value = tonumber(os.getenv(name) or "")
    if value == nil or value < minimum or value > maximum then
        error(name .. " must be in range " .. minimum .. ".." .. maximum)
    end
    return value
end

local function toggle(name)
    local value = os.getenv(name)
    if value == "1" then return true end
    if value == "0" then return false end
    error(name .. " must be 0 or 1")
end

local area_pointer = setting("SMB1_PLAYTEST_AREA", 0, 127)
local world = setting("SMB1_PLAYTEST_WORLD", 0, 7)
local level = setting("SMB1_PLAYTEST_LEVEL", 0, 7)
local page = setting("SMB1_PLAYTEST_PAGE", 0, 31)
local player_x = setting("SMB1_PLAYTEST_X", 0, 255)
local player_y = setting("SMB1_PLAYTEST_Y", 32, 239)
local theme = os.getenv("SMB1_PLAYTEST_THEME")
if theme ~= "day" and theme ~= "night" then
    error("SMB1_PLAYTEST_THEME must be day or night")
end
local day_color = setting("SMB1_PLAYTEST_DAY_COLOR", 0, 63)
local night_color = setting("SMB1_PLAYTEST_NIGHT_COLOR", 0, 63)
local infinite_time = toggle("SMB1_PLAYTEST_INFINITE_TIME")
local invincible = toggle("SMB1_PLAYTEST_INVINCIBLE")
local wrap_pits = toggle("SMB1_PLAYTEST_WRAP_PITS")
local sfx_channel = os.getenv("SMB1_PLAYTEST_SFX_CHANNEL")
if sfx_channel ~= "none" and sfx_channel ~= "square1"
        and sfx_channel ~= "square2" and sfx_channel ~= "noise" then
    error("SMB1_PLAYTEST_SFX_CHANNEL must be none, square1, square2, or noise")
end
local sfx_value = setting("SMB1_PLAYTEST_SFX_VALUE", 0, 255)
if (sfx_channel == "none") ~= (sfx_value == 0) then
    error("SMB1 playtest SFX channel/value combination is invalid")
end

local cpu = manager.machine.devices[":maincpu"]
if cpu == nil or cpu.spaces["program"] == nil then
    error("MAME NES main CPU program space is unavailable")
end
local memory = cpu.spaces["program"]
local function read(address) return memory:read_u8(address) end
local function write(address, value) memory:write_u8(address, value) end

local ram = {
    engine = 0x000e, player_page = 0x006d, player_x = 0x0086,
    player_y_high = 0x00b5, player_y = 0x00ce, player_state = 0x001d,
    player_x_speed = 0x0057, player_y_speed = 0x009f,
    player_y_fraction = 0x0433, injury_timer = 0x079e,
    timer_control = 0x0747, timer_expired = 0x0759, scroll_lock = 0x0723,
    death_music = 0x0712, event_music = 0x00fc,
    screen_page = 0x071a, screen_x = 0x071c, halfway_page = 0x075b,
    level = 0x075c, world = 0x075f, area_number = 0x0760,
    area_pointer = 0x0750, entrance_page = 0x0751, alternate_entrance = 0x0752,
    player_size = 0x0754, player_status = 0x0756,
    operating_mode = 0x0770, operating_task = 0x0772,
    square1_sfx_queue = 0x00ff, square2_sfx_queue = 0x00fe,
    noise_sfx_queue = 0x00fd,
}

local sfx_queue = {
    square1 = ram.square1_sfx_queue,
    square2 = ram.square2_sfx_queue,
    noise = ram.noise_sfx_queue,
}

local function keep_time()
    if not infinite_time then return end
    write(0x07f8, 9)
    write(0x07f9, 9)
    write(0x07fa, 9)
    write(ram.timer_expired, 0)
end

local function clear_death()
    write(ram.timer_control, 0)
    write(ram.timer_expired, 0)
    write(ram.death_music, 0)
    write(ram.event_music, 0)
end

local function rescue_damage(status, size, x_speed)
    if not invincible then return end
    local engine = read(ram.engine)
    if engine ~= 0x0a and engine ~= 0x0b then return end
    write(ram.engine, 0x08)
    write(ram.player_state, 0x01)
    write(ram.player_status, status)
    write(ram.player_size, size)
    write(ram.player_x_speed, x_speed)
    write(ram.player_y_speed, 0)
    write(ram.player_y_fraction, 0)
    write(ram.injury_timer, 0x20)
    clear_death()
end

local function rescue_pit()
    if not wrap_pits or read(ram.player_y_high) < 2 then return end
    write(ram.player_y_high, 1)
    write(ram.player_y, 0x30)
    write(ram.player_state, 0x01)
    write(ram.player_y_speed, 0)
    write(ram.player_y_fraction, 0)
    write(ram.scroll_lock, 0)
    write(ram.engine, 0x08)
    clear_death()
end

local phase = "boot"
local phase_frames = 0

local function begin_area()
    write(ram.world, world)
    write(ram.level, level)
    write(ram.area_number, 0)
    write(ram.area_pointer, area_pointer)
    write(ram.halfway_page, 0)
    write(ram.entrance_page, page)
    write(ram.alternate_entrance, 1)
    write(ram.player_size, 1)
    write(ram.player_status, 0)
    write(ram.operating_task, 0)
    write(ram.operating_mode, 1)
end

local function place_player()
    write(ram.screen_page, page)
    write(ram.screen_x, 0)
    write(ram.player_page, page)
    write(ram.player_x, player_x)
    write(ram.player_y_high, 1)
    write(ram.player_y, player_y)
    read(0x2002)
    write(0x2006, 0x3f)
    write(0x2006, 0x00)
    write(0x2007, theme == "day" and day_color or night_color)
end

local function on_frame()
    phase_frames = phase_frames + 1
    if phase == "boot" then
        if read(ram.operating_mode) == 0 and read(ram.operating_task) == 3 then
            begin_area()
            phase = "loading"
            phase_frames = 0
        elseif phase_frames > 600 then
            error("SMB1 title initialization did not become ready")
        end
        return
    end
    if phase == "loading" then
        keep_time()
        if read(ram.operating_mode) == 1 and read(ram.operating_task) == 3
                and read(ram.engine) >= 7 then
            place_player()
            phase = "running"
            phase_frames = 0
        elseif phase_frames > 600 then
            error("SMB1 gameplay did not become ready for point playtest")
        end
        return
    end

    local status = read(ram.player_status)
    local size = read(ram.player_size)
    local x_speed = read(ram.player_x_speed)
    keep_time()
    if phase_frames == 20 and sfx_channel ~= "none" then
        write(sfx_queue[sfx_channel], sfx_value)
    end
    rescue_damage(status, size, x_speed)
    rescue_pit()
end

retrolarium_smb1_point_playtest = emu.register_frame_done(on_frame, "frame")
