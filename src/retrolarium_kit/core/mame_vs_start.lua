-- Started by Retrolarium for a Vs. System game on MAME's arcade driver.
--
-- The cabinet's coin slot becomes free play, so Start (Enter in the studio)
-- begins a game the way it does on a console; right Shift still drops a coin.
--
-- MAME knows the board's ROM chips by their checksums, so an edited build
-- opens with a "ROMs are incorrect" screen that waits for a key, and an
-- embedded MAME never receives one. It shows no startup screens when
-- -seconds_to_run is under 300 seconds, and none on the machines after the
-- first one in a session. The launch passes -seconds_to_run 299; this script
-- clears it and hard-resets once, so the game runs on the second machine
-- with no screen in the way and no time limit. It runs again on that
-- machine, where it only sets free play.
for _, port in pairs(manager.machine.ioport.ports) do
    for name, field in pairs(port.fields) do
        if name == "Coinage" then
            for value, setting in pairs(field.settings) do
                if setting == "Free Play" then
                    field.user_value = value
                end
            end
        end
    end
end

local seconds_to_run = manager.options.entries["seconds_to_run"]
if seconds_to_run:value() ~= 0 then
    seconds_to_run:value(0)
    manager.machine:hard_reset()
end
