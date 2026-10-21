import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts import run_headless_supervisor


class HeadlessSupervisorTest(unittest.TestCase):
    def test_child_command_forces_resume(self):
        command = run_headless_supervisor.child_command(
            ["--", "--backend", "ray", "--run-id", "run-a"]
        )

        self.assertEqual(command[0:2], [run_headless_supervisor.sys.executable, "-u"])
        self.assertIn("--resume", command)

    def test_supervisor_restarts_failed_child_then_stops_on_success(self):
        args = SimpleNamespace(
            child_args=["--", "--backend", "ray"],
            min_available_memory_gb=0,
            memory_poll_interval=0,
            restart_delay=5,
            max_restarts=0,
        )
        results = iter(
            [subprocess.CompletedProcess([], 1), subprocess.CompletedProcess([], 0)]
        )
        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            return next(results)

        with patch("builtins.print"):
            result = run_headless_supervisor.run_supervisor(
                args,
                run=fake_run,
                sleep=lambda seconds: calls.append(("sleep", seconds)),
            )

        self.assertEqual(result, 0)
        self.assertEqual(len([call for call in calls if call[0] != "sleep"]), 2)
        self.assertIn(("sleep", 5), calls)


if __name__ == "__main__":
    unittest.main()
