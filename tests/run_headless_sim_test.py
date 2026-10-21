import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts import run_headless_sim


class FakeTask:
    def __init__(self):
        self.task_id = "task-1"
        self.status = "waiting"
        self.completed_at = None
        self.deadline_minute = 100


class FakeDrone:
    total_distance_m = 0


class FakeEnvironment:
    def __init__(self, complete_at=3):
        self.tasks = [FakeTask()]
        self.drones = [FakeDrone(), FakeDrone()]
        self.frames = []
        self.now_minute = 0
        self.complete_at = complete_at
        self.snapshot_calls = 0
        self.recorded_step_indexes = []
        self.knowledge_store = SimpleNamespace(latest_step=lambda: None)

    def snapshot(self, _completed):
        self.snapshot_calls += 1
        return {"large": "frame"}

    def step_once(self):
        self.now_minute += 1
        frame = self.snapshot([])
        self.frames.append(frame)
        self.recorded_step_indexes.append(len(self.frames))
        if len(self.frames) >= self.complete_at:
            self.tasks[0].status = "completed"

    def is_complete(self):
        return all(task.status == "completed" for task in self.tasks)

    def checkpoint_state(self):
        return {"now_minute": self.now_minute, "fake": True}

    def restore_checkpoint_state(self, state):
        self.now_minute = state["now_minute"]


def make_args(**overrides):
    values = {
        "backend": "local",
        "kg_backend": "neo4j",
        "run_id": "headless-test",
        "record_agent_state": True,
        "max_steps": 10,
        "progress_every": 2,
        "wall_interval": 0.0,
        "city": "Hangzhou",
        "day": "0621",
        "task_limit": 1,
        "drone_count": 2,
        "step_seconds": 6,
        "depot_mode": "region",
        "seed": 13,
        "use_llm": False,
        "checkpoint_path": None,
        "checkpoint_enabled": False,
        "resume": False,
        "overwrite_checkpoint": False,
        "ray_recovery_attempts": 0,
        "ray_recovery_delay": 0.0,
        "ray_min_available_memory_gb": 0.0,
        "ray_memory_poll_interval": 0.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class HeadlessSimulationTest(unittest.TestCase):
    def test_step_counter_counts_without_retaining_frames(self):
        counter = run_headless_sim.StepCounter()

        counter.append({"large": "payload"})
        counter.append(None)

        self.assertEqual(len(counter), 2)
        self.assertFalse(hasattr(counter, "items"))

    def test_resume_accepts_checkpoint_one_step_ahead_of_neo4j(self):
        env = FakeEnvironment()
        env.knowledge_store = SimpleNamespace(
            latest_step=lambda: {"step_index": 9, "time_minute": 480.9}
        )
        checkpoint = {
            "step_index": 10,
            "now_minute": 481.0,
            "config": {"step_seconds": 6},
        }

        position = run_headless_sim.verify_resume_position(env, checkpoint)

        self.assertEqual(position, "checkpoint_ahead")

    def test_checkpoint_ahead_step_is_committed_from_restored_state(self):
        env = FakeEnvironment()
        calls = []
        env.knowledge_store = SimpleNamespace(
            record_step=lambda *args: calls.append(args)
        )
        checkpoint = {
            "step_index": 10,
            "now_minute": 481.0,
            "state": {"completed_this_step_ids": ["task-1"]},
        }

        run_headless_sim.commit_checkpoint_ahead_step(env, checkpoint)

        self.assertEqual(calls[0][0:2], (10, 481.0))
        self.assertEqual(calls[0][4], env.tasks)

    def test_run_stops_when_tasks_complete_without_building_frames(self):
        env = FakeEnvironment(complete_at=3)
        args = make_args()
        config = {"backend": "local", "city": "Hangzhou", "day": "0621"}

        with (
            patch("scripts.run_headless_sim.initialize_ray_if_needed"),
            patch("scripts.run_headless_sim.build_runtime", return_value=(env, config, [])),
            patch("scripts.run_headless_sim.close_env") as close_env,
            patch("builtins.print"),
        ):
            result = run_headless_sim.run_headless(args)

        self.assertEqual(result["reason"], "all_tasks_completed")
        self.assertEqual(result["steps_run"], 3)
        self.assertEqual(result["expected_agent_states"], 6)
        self.assertEqual(env.recorded_step_indexes, [1, 2, 3])
        self.assertEqual(env.snapshot_calls, 0)
        close_env.assert_called_once_with(env)

    def test_ray_recovery_retries_when_actor_restore_is_oom_killed(self):
        class OutOfMemoryError(RuntimeError):
            pass

        class RecoveringEnvironment:
            def __init__(self):
                self.step_calls = 0
                self.restore_calls = 0
                self.close_calls = 0

            def step_once(self):
                self.step_calls += 1
                if self.step_calls == 1:
                    raise OutOfMemoryError("step OOM")
                return "recovered"

            def restore_checkpoint_state(self, _state):
                self.restore_calls += 1
                if self.restore_calls == 1:
                    raise OutOfMemoryError("restore OOM")

            def close(self):
                self.close_calls += 1

        env = RecoveringEnvironment()
        args = make_args(
            backend="ray",
            ray_recovery_attempts=2,
            ray_recovery_delay=0,
        )

        result = run_headless_sim.step_with_ray_recovery(
            env,
            args,
            {"step_index": 4157, "state": {}},
        )

        self.assertEqual(result, "recovered")
        self.assertEqual(env.restore_calls, 2)
        self.assertEqual(env.close_calls, 2)

    def test_run_sleeps_after_each_step_when_wall_interval_is_set(self):
        env = FakeEnvironment(complete_at=3)
        args = make_args(wall_interval=0.25)
        config = {"backend": "local", "city": "Hangzhou", "day": "0621"}

        with (
            patch("scripts.run_headless_sim.initialize_ray_if_needed"),
            patch("scripts.run_headless_sim.build_runtime", return_value=(env, config, [])),
            patch("scripts.run_headless_sim.close_env"),
            patch("scripts.run_headless_sim.time.sleep") as sleep,
            patch("builtins.print"),
        ):
            run_headless_sim.run_headless(args)

        self.assertEqual(sleep.call_count, 3)
        sleep.assert_called_with(0.25)

    def test_run_stops_at_max_steps_when_tasks_remain(self):
        env = FakeEnvironment(complete_at=20)
        args = make_args(max_steps=4, progress_every=0)
        config = {"backend": "local", "city": "Hangzhou", "day": "0621"}

        with (
            patch("scripts.run_headless_sim.initialize_ray_if_needed"),
            patch("scripts.run_headless_sim.build_runtime", return_value=(env, config, [])),
            patch("scripts.run_headless_sim.close_env"),
            patch("builtins.print"),
        ):
            result = run_headless_sim.run_headless(args)

        self.assertEqual(result["reason"], "max_steps_reached")
        self.assertEqual(result["steps_run"], 4)
        self.assertEqual(env.snapshot_calls, 0)

    def test_parse_args_uses_million_run_defaults(self):
        env_keys = [
            "CITY",
            "DAY",
            "TASK_LIMIT",
            "DRONE_COUNT",
            "STEP_SECONDS",
            "MAX_STEPS",
            "KG_BACKEND",
            "KG_RUN_ID",
            "KG_RECORD_AGENT_STATE",
        ]
        with patch.dict(os.environ, {key: "" for key in env_keys}, clear=False):
            args = run_headless_sim.parse_args([])

        self.assertEqual(args.city, "Hangzhou")
        self.assertEqual(args.day, "0621")
        self.assertEqual(args.task_limit, 10424)
        self.assertEqual(args.drone_count, 100)
        self.assertEqual(args.max_steps, 10000)
        self.assertEqual(args.backend, "ray")
        self.assertEqual(args.kg_backend, "neo4j")
        self.assertTrue(args.record_agent_state)
        self.assertEqual(args.wall_interval, 0.25)
        self.assertTrue(args.checkpoint_enabled)
        self.assertFalse(args.resume)
        self.assertEqual(args.ray_recovery_attempts, 5)


if __name__ == "__main__":
    unittest.main()
