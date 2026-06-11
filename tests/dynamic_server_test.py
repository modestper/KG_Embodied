import argparse
import json
import unittest

from scripts.run_dynamic_sim import args_from_request, format_sse, stream_simulation
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
        )

        args = args_from_request(
            base_args,
            "/events?city=Chongqing&task_limit=50&drone_count=8&max_steps=600&"
            "frame_stride=2&wall_interval=0.05&use_llm=1&vllm_model=test-model&llm_max_tokens=128",
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


if __name__ == "__main__":
    unittest.main()
