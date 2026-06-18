import os
import unittest
from unittest.mock import patch

from simulation.delivery_sim import DeliveryTask, DroneAgent
from simulation.knowledge import Neo4jKnowledgeStore, TemporalKnowledgeStore, build_knowledge_store_from_env


class TemporalKnowledgeStoreTest(unittest.TestCase):
    def make_task(self, task_id="task-1", deadline=520):
        return DeliveryTask(
            task_id=task_id,
            city="重庆市",
            x=1000,
            y=0,
            pickup_x=0,
            pickup_y=0,
            release_minute=480,
            deadline_minute=deadline,
            aoi_id="aoi-1",
            region_id="10",
        )

    def test_records_assignment_and_completion_events(self):
        kg = TemporalKnowledgeStore()
        task = self.make_task()
        drone = DroneAgent("drone-1", 0, 0, region_id="10")

        kg.record_assignment(481, task, drone)
        kg.record_completion(500, task, drone)

        self.assertEqual(len(kg.events), 2)
        self.assertEqual(kg.events[0]["type"], "task_assigned")
        self.assertEqual(kg.events[1]["type"], "task_completed")

    def test_query_region_context_returns_urgent_tasks_and_candidates(self):
        kg = TemporalKnowledgeStore()
        urgent = self.make_task("urgent", deadline=490)
        relaxed = self.make_task("relaxed", deadline=700)
        drones = [
            DroneAgent("free", 0, 0, region_id="10"),
            DroneAgent("busy", 0, 0, region_id="10"),
        ]
        drones[1].queue.append(relaxed)

        context = kg.query_region_context(
            region_id="10",
            tasks=[relaxed, urgent],
            drones=drones,
            now_minute=480,
            max_tasks=1,
            max_drones=1,
        )

        self.assertEqual(context["urgent_tasks"][0]["task_id"], "urgent")
        self.assertEqual(context["candidate_drones"][0]["drone_id"], "free")

    def test_recent_performance_counts_completed_tasks(self):
        kg = TemporalKnowledgeStore()
        task = self.make_task()
        drone = DroneAgent("drone-1", 0, 0, region_id="10")
        kg.record_completion(500, task, drone)

        context = kg.query_region_context("10", [task], [drone], now_minute=510)

        self.assertEqual(context["recent_agent_performance"]["drone-1"]["completed"], 1)

class FakeNeo4jResult:
    def __init__(self, rows=None):
        self.rows = rows or []

    def data(self):
        return self.rows


class FakeNeo4jSession:
    def __init__(self, driver):
        self.driver = driver

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def run(self, query, **params):
        self.driver.calls.append((query, params))
        if "AS drone_id" in query:
            return FakeNeo4jResult(self.driver.performance_rows)
        if "RETURN e" in query:
            return FakeNeo4jResult(self.driver.event_rows)
        return FakeNeo4jResult()


class FakeNeo4jDriver:
    def __init__(self):
        self.calls = []
        self.closed = False
        self.event_rows = [
            {
                "e": {
                    "time_minute": 500,
                    "time": "08:20:00",
                    "type": "task_completed",
                    "region_id": "10",
                    "task_id": "task-1",
                    "drone_id": "drone-1",
                    "late": False,
                }
            }
        ]
        self.performance_rows = [
            {"drone_id": "drone-1", "assigned": 1, "completed": 1, "late": 0}
        ]

    def session(self):
        return FakeNeo4jSession(self)

    def close(self):
        self.closed = True


class Neo4jKnowledgeStoreTest(unittest.TestCase):
    def make_task(self, task_id="task-1", deadline=520):
        return DeliveryTask(
            task_id=task_id,
            city="重庆市",
            x=1000,
            y=0,
            pickup_x=0,
            pickup_y=0,
            release_minute=480,
            deadline_minute=deadline,
            aoi_id="aoi-1",
            region_id="10",
        )

    def test_records_assignment_as_graph_nodes_and_relationships(self):
        driver = FakeNeo4jDriver()
        kg = Neo4jKnowledgeStore(driver=driver, run_id="run-a")
        task = self.make_task()
        drone = DroneAgent("drone-1", 0, 0, region_id="10")

        kg.record_assignment(481, task, drone)

        joined_queries = "\n".join(query for query, _params in driver.calls)
        first_params = driver.calls[0][1]
        self.assertEqual(kg.run_id, "run-a")
        self.assertIn("MERGE (run:Run", joined_queries)
        self.assertIn(":IN_RUN", joined_queries)
        self.assertIn("MERGE (d:Drone", joined_queries)
        self.assertIn("MERGE (t:Task", joined_queries)
        self.assertIn("MERGE (r:Region", joined_queries)
        self.assertIn("MERGE (e:Event", joined_queries)
        self.assertIn(":ASSIGNED", joined_queries)
        self.assertEqual(first_params["run_id"], "run-a")
        self.assertEqual(first_params["run"]["id"], "run-a")
        self.assertEqual(kg.events[0]["type"], "task_assigned")

    def test_query_region_context_uses_neo4j_recent_memory(self):
        driver = FakeNeo4jDriver()
        kg = Neo4jKnowledgeStore(driver=driver, run_id="run-a")
        task = self.make_task()
        drone = DroneAgent("drone-1", 0, 0, region_id="10")

        context = kg.query_region_context("10", [task], [drone], now_minute=510)

        query_calls = [call for call in driver.calls if "MATCH (e:Event)" in call[0]]
        self.assertEqual(len(query_calls), 2)
        for query, params in query_calls:
            self.assertIn("(:Run {id: $run_id})", query)
            self.assertEqual(params["run_id"], "run-a")
        self.assertEqual(context["recent_agent_performance"]["drone-1"]["completed"], 1)
        self.assertEqual(context["recent_events"][0]["type"], "task_completed")

    def test_build_knowledge_store_from_env_creates_neo4j_store(self):
        driver = FakeNeo4jDriver()

        with patch.dict(
            os.environ,
            {
                "KG_BACKEND": "neo4j",
                "NEO4J_URI": "bolt://neo4j.example:7687",
                "NEO4J_USER": "neo4j",
                "NEO4J_PASSWORD": "secret",
                "KG_RUN_ID": "experiment-001",
            },
        ):
            kg = build_knowledge_store_from_env(driver_factory=lambda uri, auth: driver)

        self.assertIsInstance(kg, Neo4jKnowledgeStore)
        self.assertIs(kg.driver, driver)
        self.assertEqual(kg.run_id, "experiment-001")


if __name__ == "__main__":
    unittest.main()
