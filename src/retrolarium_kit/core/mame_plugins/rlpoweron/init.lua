-- Fill work RAM at power-on the way the emulator a movie was recorded in does,
-- before the CPU runs its first instruction (a movie script starts a frame
-- too late for that). retrolarium_kit.core.movies sets:
--
-- RL_MOVIE_RAM_FILL   the fill as hex bytes, repeated ("00000000ffffffff":
--                     FCEUX's default)
-- RL_MOVIE_RAM_SIZE   how many bytes from address 0 of the main CPU (0x800)
local exports = {
    name = "rlpoweron",
    version = "1",
    description = "Retrolarium: work RAM as the recording emulator left it at power-on",
    license = "GPL-3.0-or-later",
    author = { name = "Retrolarium" },
}

local plugin = exports
local subscription

function plugin.startplugin()
    local fill = os.getenv("RL_MOVIE_RAM_FILL")
    local size = tonumber(os.getenv("RL_MOVIE_RAM_SIZE") or "0")
    if fill == nil or fill == "" or size == 0 then
        return
    end
    local bytes = {}
    for pair in fill:gmatch("%x%x") do
        bytes[#bytes + 1] = tonumber(pair, 16)
    end
    local filled = false
    subscription = emu.add_machine_reset_notifier(function()
        if filled then
            return
        end
        filled = true
        local space = manager.machine.devices[":maincpu"].spaces["program"]
        for address = 0, size - 1 do
            space:write_u8(address, bytes[address % #bytes + 1])
        end
    end)
end

return exports
