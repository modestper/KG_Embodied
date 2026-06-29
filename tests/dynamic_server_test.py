import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts import run_dynamic_sim
from scripts.run_dynamic_sim import DynamicHandler, args_from_request, format_sse, parse_args, stream_simulation
from simulation.delivery_sim import AuctionCoordinator, DeliveryTask, DroneAgent, SimulationEnv
from simulation.knowledge import TemporalKnowledgeStore


class DynamicServerTest(unittest.TestCase):
    def make_env(self):
        task = DeliveryTask(
            task_id="task-1",
            city="吉林市",
            x=100,
            y=0,
            pickup_x=0,
            pickup_y=0,
            release_minute=480,
            deadline_minute=520,
            aoi_id="aoi-1",
            region_id="31",
        )
        drone = DroneAgent("drone-1", 0, 0, region_id="31", speed_m_per_minute=100)
        kg = TemporalKnowledgeStore()
        return SimulationEnv([task], [drone], AuctionCoordinator(), step_minutes=1, knowledge_store=kg)

    def test_format_sse_serializes_event_and_payload(self):
        payload = format_sse("frame", {"time": "08:00:00"})

        self.assertTrue(payload.startswith("event: frame\n"))
        self.assertIn('"time":"08:00:00"', payload)
        self.assertTrue(payload.endswith("\n\n"))

    def test_dynamic_page_exposes_compact_region_boundary_controls(self):
        html = (run_dynamic_sim.VIEWER_ROOT / "dynamic.html").read_text(encoding="utf-8")

        for control_id in (
            "regionBoundaryMenu",
            "regionBoundaryEnabled",
            "regionBoundaryWidth",
            "regionBoundaryOpacity",
            "regionBoundaryColor",
        ):
            self.assertIn(f'id="{control_id}"', html)

    def test_stream_simulation_emits_init_frame_and_done(self):
        events = list(
            stream_simulation(
                self.make_env(),
                config={"city": "吉林市"},
                depots=[{"region_id": "31", "x": 0, "y": 0}],
                max_steps=5,
                frame_stride=1,
            )
        )
        names = [event.split("\n", 1)[0] for event in events]
        done_payload = json.loads(events[-1].split("data: ", 1)[1])

        self.assertIn("event: init", names)
        self.assertIn("event: frame", names)
        self.assertEqual(names[-1], "event: done")
        self.assertEqual(done_payload["metrics"]["completed_tasks"], 1)
        self.assertEqual(done_payload["metrics"]["unfinished_tasks"], 0)
        self.assertEqual(done_payload["reason"], "all_tasks_completed")

    def test_stream_simulation_reports_max_steps_with_unfinished_tasks(self):
        env = self.make_env()
        env.drones[0].speed_m_per_minute = 1

        events = list(
            stream_simulation(
                env,
                config={"city": "吉林市"},
                depots=[{"region_id": "31", "x": 0, "y": 0}],
                max_steps=1,
                frame_stride=1,
            )
        )
        done_payload = json.loads(events[-1].split("data: ", 1)[1])

        self.assertEqual(done_payload["reason"], "max_steps_reached")
        self.assertEqual(done_payload["metrics"]["unfinished_tasks"], 1)
        self.assertEqual(done_payload["steps_run"], 1)

    def test_stream_simulation_filters_stored_region_boundaries_to_active_regions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "region_boundaries.json"
            path.write_text(
                json.dumps(
                    {
                        "cities": {
                            "吉林市": {
                                "regions": [
                                    {"region_id": "31", "task_count": 10, "centroid": {"x": 0, "y": 0}, "polygon": [[0, 0], [1, 0], [1, 1]]},
                                    {"region_id": "88", "task_count": 8, "centroid": {"x": 10, "y": 10}, "polygon": [[10, 10], [11, 10], [11, 11]]},
                                ]
                            }
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with patch("scripts.run_dynamic_sim.REGION_BOUNDARY_PATH", path):
                events = list(
                    stream_simulation(
                        self.make_env(),
                        config={"city": "吉林市"},
                        depots=[{"region_id": "31", "x": 0, "y": 0}],
                        max_steps=1,
                        frame_stride=1,
                    )
                )

        init_payload = json.loads(events[0].split("data: ", 1)[1])
        self.assertEqual([region["region_id"] for region in init_payload["regions"]], ["31"])
        self.assertEqual(init_payload["regions"][0]["outline"], [[0, 0], [1, 0], [1, 1]])

    def test_args_from_request_overrides_runtime_fields(self):
        base_args = argparse.Namespace(
            city="Jilin",
            day="0925",
            task_limit=20,
            drone_count=3,
            step_seconds=20,
            max_steps=1000,
            frame_stride=1,
            wall_interval=0.25,
            depot_mode="region",
            seed=13,
            use_llm=False,
            vllm_api_base="http://127.0.0.1:6666/v1",
            vllm_model="qwen2.5-7b",
            llm_max_tokens=512,
            backend="local",
            ray_address="",
            ray_num_cpus=None,
        )

        args = args_from_request(
            base_args,
            "/events?city=Chongqing&task_limit=50&drone_count=8&max_steps=600&"
            "frame_stride=2&wall_interval=0.05&use_llm=1&vllm_model=test-model&llm_max_tokens=128&"
            "backend=ray&ray_address=auto&ray_num_cpus=4",
        )

        self.assertEqual(args.city, "Chongqing")
        self.assertEqual(args.task_limit, 50)
        self.assertEqual(args.drone_count, 8)
        self.assertEqual(args.max_steps, 600)
        self.assertEqual(args.frame_stride, 2)
        self.assertEqual(args.wall_interval, 0.05)
        self.assertTrue(args.use_llm)
        self.assertEqual(args.vllm_model, "test-model")
        self.assertEqual(args.llm_max_tokens, 128)
        self.assertEqual(args.backend, "ray")
        self.assertEqual(args.ray_address, "auto")
        self.assertEqual(args.ray_num_cpus, 8)

    def test_initialize_ray_if_needed_starts_ray_for_ray_backend(self):
        args = argparse.Namespace(backend="ray", ray_address="auto", ray_num_cpus=4)
        fake_ray = Mock()
        fake_ray.is_initialized.return_value = False
        initializer = getattr(run_dynamic_sim, "initialize_ray_if_needed", None)
        self.assertIsNotNone(initializer)

        with patch.dict("sys.modules", {"ray": fake_ray}):
            initializer(args)

        fake_ray.init.assert_called_once_with(address="auto", num_cpus=4)

    def test_initialize_ray_if_needed_restarts_ray_when_request_needs_more_cpus(self):
        args = argparse.Namespace(backend="ray", ray_address="", ray_num_cpus=10)
        fake_ray = Mock()
        fake_ray.is_initialized.return_value = True
        fake_ray.cluster_resources.return_value = {"CPU": 4.0}

        with patch.dict("sys.modules", {"ray": fake_ray}):
            run_dynamic_sim.initialize_ray_if_needed(args)

        fake_ray.shutdown.assert_called_once()
        fake_ray.init.assert_called_once_with(num_cpus=10)

    def test_initialize_ray_if_needed_skips_local_backend(self):
        args = argparse.Namespace(backend="local", ray_address="", ray_num_cpus=None)
        fake_ray = Mock()
        initializer = getattr(run_dynamic_sim, "initialize_ray_if_needed", None)
        self.assertIsNotNone(initializer)

        with patch.dict("sys.modules", {"ray": fake_ray}):
            initializer(args)

        fake_ray.init.assert_not_called()

    def test_parse_args_defaults_allow_jilin_demo_to_reach_late_releases(self):
        with patch("sys.argv", ["run_dynamic_sim.py"]):
            args = parse_args()

        self.assertEqual(args.max_steps, 2000)

    def test_parse_args_defaults_to_ray_backend(self):
        with patch("sys.argv", ["run_dynamic_sim.py"]):
            args = parse_args()

        self.assertEqual(args.backend, "ray")

    def test_dynamic_handler_replaces_active_runtime(self):
        previous = Mock()
        current = Mock()
        DynamicHandler.active_env = previous

        DynamicHandler.replace_active_env(current)

        previous.close.assert_called_once()
        self.assertIs(DynamicHandler.active_env, current)

        DynamicHandler.clear_active_env(current)

        current.close.assert_called_once()
        self.assertIsNone(DynamicHandler.active_env)

    def test_build_runtime_uses_knowledge_store_from_env(self):
        class CustomKnowledgeStore(TemporalKnowledgeStore):
            pass

        custom_kg = CustomKnowledgeStore()
        task = DeliveryTask(
            task_id="task-1",
            city="吉林市",
            x=100,
            y=0,
            pickup_x=0,
            pickup_y=0,
            release_minute=480,
            deadline_minute=520,
            aoi_id="aoi-1",
            region_id="31",
        )
        args = argparse.Namespace(
            city="Jilin",
            day="0925",
            task_limit=1,
            drone_count=1,
            step_seconds=20,
            max_steps=1,
            frame_stride=1,
            wall_interval=0.25,
            depot_mode="region",
            seed=13,
            use_llm=False,
            vllm_api_base="http://127.0.0.1:6666/v1",
            vllm_model="qwen2.5-7b",
            llm_max_tokens=512,
            backend="local",
            ray_address="",
            ray_num_cpus=None,
        )

        with (
            patch("scripts.run_dynamic_sim.load_delivery_tasks", return_value=[task]),
            patch("scripts.run_dynamic_sim.build_knowledge_store_from_env", return_value=custom_kg),
        ):
            env, _config, _depots = run_dynamic_sim.build_runtime(args)

        self.assertIs(env.knowledge_store, custom_kg)


if __name__ == "__main__":
    unittest.main()
