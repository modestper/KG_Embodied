import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import urllib.request

from simulation.delivery_sim import AuctionCoordinator, DeliveryTask, DroneAgent
from simulation.knowledge import TemporalKnowledgeStore
from simulation.planner import LLMPlanner, RegionLLMCoordinator, RuleBasedPlanner, build_planner_from_env


class PlannerTest(unittest.TestCase):
    def make_task(self):
        return DeliveryTask(
            task_id="task-1",
            city="重庆市",
            x=1200,
            y=0,
            pickup_x=100,
            pickup_y=0,
            release_minute=480,
            deadline_minute=540,
            aoi_id="aoi-1",
            region_id="10",
        )

    def test_rule_based_planner_returns_numeric_cost(self):
        drone = DroneAgent("drone-1", 0, 0, region_id="10")
        task = self.make_task()

        cost = RuleBasedPlanner().estimate_cost(drone, task, now_minute=480)

        self.assertGreater(cost, 0)
        self.assertLess(cost, 10_000)

    def test_llm_planner_parses_cost_from_openai_compatible_response(self):
        response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {"accept": True, "cost": 123.5, "reason": "near region depot"}
                        )
                    }
                }
            ]
        }
        planner = LLMPlanner(
            api_key="test-key",
            model="test-model",
            request_func=lambda request: response,
            max_tokens=128,
        )

        cost = planner.estimate_cost(DroneAgent("drone-1", 0, 0), self.make_task(), 480)

        self.assertEqual(cost, 123.5)

    def test_llm_planner_request_includes_max_tokens(self):
        planner = LLMPlanner(api_key="test-key", model="test-model", max_tokens=128)
        request = planner.build_request(DroneAgent("drone-1", 0, 0), self.make_task(), 480)

        self.assertEqual(request["max_tokens"], 128)

    def test_llm_planner_rejects_task_with_large_cost(self):
        response = {"choices": [{"message": {"content": '{"accept": false, "cost": 1}'}}]}
        planner = LLMPlanner(api_key="test-key", model="test-model", request_func=lambda request: response)

        cost = planner.estimate_cost(DroneAgent("drone-1", 0, 0), self.make_task(), 480)

        self.assertGreaterEqual(cost, 1_000_000)

    def test_build_planner_from_env_defaults_to_rule_based(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsInstance(build_planner_from_env(), RuleBasedPlanner)

    def test_build_planner_from_env_uses_llm_when_enabled(self):
        env = {
            "DRONE_PLANNER": "llm",
            "LLM_API_KEY": "key",
            "LLM_MODEL": "model",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertIsInstance(build_planner_from_env(), LLMPlanner)

    def test_build_planner_from_env_supports_vllm_without_api_key(self):
        env = {
            "DRONE_PLANNER": "llm",
            "VLLM_API_BASE": "http://127.0.0.1:8001/v1",
            "VLLM_MODEL": "qwen2.5",
        }
        with patch.dict(os.environ, env, clear=True):
            planner = build_planner_from_env()

        self.assertIsInstance(planner, LLMPlanner)
        self.assertEqual(planner.api_base, "http://127.0.0.1:8001/v1")
        self.assertEqual(planner.model, "qwen2.5")
        self.assertIsNone(planner.api_key)

    def test_llm_planner_omits_auth_header_without_api_key(self):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["headers"] = dict(request.header_items())

            class Response:
                def __enter__(self):
                    return self

                def __exit__(self, *_):
                    return False

                def read(self):
                    return b'{"choices":[{"message":{"content":"{\\"accept\\":true,\\"cost\\":1}"}}]}'

            return Response()

        planner = LLMPlanner(api_key=None, model="m", api_base="http://vllm.local/v1")
        with patch.object(urllib.request, "urlopen", fake_urlopen):
            planner.estimate_cost(DroneAgent("drone-1", 0, 0), self.make_task(), 480)

        self.assertNotIn("Authorization", captured["headers"])

    def test_region_llm_coordinator_assigns_batch_response(self):
        task = self.make_task()
        drone = DroneAgent("drone-1", 0, 0, region_id="10")
        response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {"assignments": [{"task_id": task.task_id, "drone_id": drone.drone_id}]}
                        )
                    }
                }
            ]
        }
        coordinator = RegionLLMCoordinator(
            api_key=None,
            model="m",
            api_base="http://vllm.local/v1",
            request_func=lambda request: response,
        )

        assignments = coordinator.assign([task], [drone], now_minute=480)

        self.assertEqual(assignments, {task.task_id: drone.drone_id})
        self.assertEqual(task.assigned_to, drone.drone_id)
        self.assertEqual(drone.queue[0].task_id, task.task_id)

    def test_region_llm_coordinator_falls_back_on_bad_response(self):
        task = self.make_task()
        near = DroneAgent("near", task.pickup_x - 1, task.pickup_y, region_id="10")
        far = DroneAgent("far", task.pickup_x - 1000, task.pickup_y, region_id="10")
        coordinator = RegionLLMCoordinator(
            api_key=None,
            model="m",
            api_base="http://vllm.local/v1",
            request_func=lambda request: {"choices": [{"message": {"content": "not json"}}]},
            fallback=AuctionCoordinator(),
        )

        assignments = coordinator.assign([task], [far, near], now_minute=480)

        self.assertEqual(assignments, {task.task_id: "near"})

    def test_region_llm_coordinator_includes_kg_context(self):
        captured = {}
        task = self.make_task()
        drone = DroneAgent("drone-1", 0, 0, region_id="10")
        kg = TemporalKnowledgeStore()
        kg.record_completion(470, task, drone)

        def fake_request(request):
            captured["request"] = request
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"assignments": [{"task_id": task.task_id, "drone_id": drone.drone_id}]}
                            )
                        }
                    }
                ]
            }

        coordinator = RegionLLMCoordinator(
            api_key=None,
            model="m",
            api_base="http://vllm.local/v1",
            request_func=fake_request,
            knowledge_store=kg,
            max_tokens=321,
        )

        coordinator.assign([task], [drone], now_minute=480)
        payload = json.loads(captured["request"]["messages"][1]["content"])

        self.assertEqual(captured["request"]["max_tokens"], 321)
        self.assertIn("kg_context", payload)
        self.assertIn("drone-1", payload["kg_context"]["recent_agent_performance"])

    def test_region_llm_coordinator_writes_jsonl_log(self):
        task = self.make_task()
        drone = DroneAgent("drone-1", 0, 0, region_id="10")
        response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {"assignments": [{"task_id": task.task_id, "drone_id": drone.drone_id}]}
                        )
                    }
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "llm_test.jsonl"
            coordinator = RegionLLMCoordinator(
                api_key=None,
                model="m",
                api_base="http://vllm.local/v1",
                request_func=lambda request: response,
                log_path=log_path,
            )
            coordinator.assign([task], [drone], now_minute=480)

            records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(records[0]["event"], "llm_call")
        self.assertEqual(records[0]["request"]["model"], "m")
        self.assertEqual(records[0]["response"], response)
        self.assertFalse(records[0]["used_fallback"])


if __name__ == "__main__":
    unittest.main()
