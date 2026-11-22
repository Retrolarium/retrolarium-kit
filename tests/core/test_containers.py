from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from retrolarium_kit.core import containers
from retrolarium_kit.core.containers import ContainerError, docker_run_command
from retrolarium_kit.core.errors import KitError


class DockerRunCommandTests(unittest.TestCase):
    def mount(self, command: list[str]) -> str:
        return command[command.index("--mount") + 1]

    def test_a_comma_and_a_space_stay_in_the_quoted_source(self) -> None:
        project = Path("/home/me/games, old/project")
        command = docker_run_command("image:1", project, ["make", "all"])
        self.assertEqual(f'type=bind,"source={project}",target=/work', self.mount(command))
        self.assertEqual(["--workdir", "/work", "image:1", "make", "all"], command[-5:])
        self.assertNotIn("sh", command)

    def test_a_quote_or_a_line_break_in_the_path_is_refused(self) -> None:
        for path in ('/home/me/"quoted"', "/home/me/two\nlines"):
            with self.subTest(path=path), self.assertRaises(ContainerError):
                docker_run_command("image:1", Path(path), ["make"])
        self.assertTrue(issubclass(ContainerError, KitError))

    def test_an_option_is_not_an_image(self) -> None:
        with self.assertRaises(ContainerError):
            docker_run_command("--privileged", Path("/work"), ["make"])

    @unittest.skipIf(not hasattr(containers.os, "getuid"), "POSIX only")
    def test_posix_runs_the_container_as_the_calling_user(self) -> None:
        project = Path("/p")
        with (
            mock.patch.object(containers.os, "name", "posix"),
            mock.patch.object(containers.os, "getuid", return_value=1000),
            mock.patch.object(containers.os, "getgid", return_value=1001),
        ):
            command = docker_run_command("image:1", project, ["make"], name="build-1")
        self.assertEqual(["docker", "run", "--rm", "--name", "build-1", "--user", "1000:1001"], command[:7])

    def test_windows_leaves_ownership_to_docker_desktop(self) -> None:
        project = Path("/p")
        with mock.patch.object(containers.os, "name", "nt"):
            command = docker_run_command("image:1", project, ["make"])
        self.assertNotIn("--user", command)


if __name__ == "__main__":
    unittest.main()
