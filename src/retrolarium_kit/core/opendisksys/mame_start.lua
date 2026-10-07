-- Started by Retrolarium for a Famicom Disk System game on OpenDiskSysROM.
--
-- MAME knows the BIOS by Nintendo's checksums, so for any other BIOS it
-- opens with a "ROMs are incorrect" screen that waits for a key, and an
-- embedded MAME never receives one. It shows no startup screens when
-- -seconds_to_run is under 300 seconds, and none on the machines after the
-- first one in a session. The launch passes -seconds_to_run 299; this script
-- clears it and hard-resets once, so the game runs on the second machine
-- with no screen in the way and no time limit.
local seconds_to_run = manager.options.entries["seconds_to_run"]
if seconds_to_run:value() ~= 0 then
    seconds_to_run:value(0)
    manager.machine:hard_reset()
end
