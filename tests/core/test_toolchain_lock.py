"""The lock one kit holds while it installs a toolchain suite."""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from retrolarium_kit.core import build_toolchains
from retrolarium_kit.core.build_toolchains import BuildToolchainError, _publishing


def finished_pid() -> int:
    """The PID of a process that has already exited."""
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


class ToolchainLockTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.lock = Path(folder.name) / ".megadrive.lock"

    def hold(self, pid: int, host: str | None = None, age: float = 0.0) -> None:
        holder = {"pid": pid, "host": host or socket.gethostname(), "started": time.time() - age, "token": "x"}
        self.lock.write_text(json.dumps(holder), encoding="utf-8")
        if age:
            os.utime(self.lock, (time.time() - age, time.time() - age))

    def test_the_lock_names_its_holder_and_is_released(self) -> None:
        with _publishing(self.lock, wait=1):
            holder = json.loads(self.lock.read_text(encoding="utf-8"))
            self.assertEqual((os.getpid(), socket.gethostname()), (holder["pid"], holder["host"]))
        self.assertFalse(self.lock.exists())

    def test_a_lock_whose_process_died_is_taken_at_once(self) -> None:
        self.hold(finished_pid())
        started = time.monotonic()
        with _publishing(self.lock, wait=0.5):
            self.assertEqual(os.getpid(), json.loads(self.lock.read_text(encoding="utf-8"))["pid"])
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual([], list(self.lock.parent.glob("*.stale-*")))

    def test_a_live_holder_is_waited_for(self) -> None:
        self.hold(os.getpid())
        with self.assertRaisesRegex(BuildToolchainError, "still installing"), _publishing(self.lock, wait=0.3):
            pass
        self.assertTrue(self.lock.exists())

    def test_another_hosts_lock_waits_for_the_stale_age(self) -> None:
        self.hold(finished_pid(), host="elsewhere.invalid")
        with self.assertRaises(BuildToolchainError), _publishing(self.lock, wait=0.3):
            pass
        self.hold(os.getpid(), host="elsewhere.invalid", age=build_toolchains.LOCK_STALE_SECONDS + 5)
        with _publishing(self.lock, wait=0.3):
            pass

    def test_an_older_kits_empty_lock_waits_for_the_stale_age(self) -> None:
        self.lock.write_bytes(b"")
        with self.assertRaises(BuildToolchainError), _publishing(self.lock, wait=0.3):
            pass

    def test_a_lock_taken_over_meanwhile_is_not_released_by_its_first_holder(self) -> None:
        with _publishing(self.lock, wait=1):
            self.hold(os.getpid() + 1, host="elsewhere.invalid")
        self.assertTrue(self.lock.exists())

    def test_a_lock_that_changed_after_it_was_judged_is_put_back(self) -> None:
        self.hold(os.getpid())
        build_toolchains._take_stale_lock(self.lock, "the text of an older lock")
        self.assertEqual(os.getpid(), json.loads(self.lock.read_text(encoding="utf-8"))["pid"])

    def test_waiting_outlasts_the_stale_age(self) -> None:
        self.assertGreater(build_toolchains.LOCK_WAIT_SECONDS, build_toolchains.LOCK_STALE_SECONDS)


if __name__ == "__main__":
    unittest.main()
