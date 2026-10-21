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
        kg.record_pickup(482, task, drone)
        kg.record_completion(500, task, drone)

        self.assertEqual(len(kg.events), 3)
        self.assertEqual(kg.events[0]["type"], "task_assigned")
        self.assertEqual(kg.events[1]["type"], "task_picked_up")
        self.assertEqual(kg.events[2]["type"], "task_completed")

    def test_records_drone_events_and_step_summaries(self):
        kg = TemporalKnowledgeStore()
        drone = DroneAgent("drone-1", 0, 0, region_id="10", battery_remaining_m=10_000)

        kg.record_drone_event(481, "drone_low_battery", drone)
        kg.record_step(
            step_index=1,
            time_minute=481,
            tasks=[],
            drones=[drone],
            completed_this_step=[],
        )

        self.assertEqual(kg.events[0]["type"], "drone_low_battery")
        self.assertEqual(kg.steps[0]["step_index"], 1)
        self.assertEqual(kg.steps[0]["low_battery_drones"], 1)

    def test_step_summary_uses_dynamic_nearest_depot_threshold(self):
        kg = TemporalKnowledgeStore()
        drone = DroneAgent(
            "drone-1",
            0,
            0,
            region_id="10",
            battery_remaining_m=15_000,
            depots=[{"region_id": "near", "x": 10_000, "y": 0}],
        )

        kg.record_step(
            step_index=1,
            time_minute=481,
            tasks=[],
            drones=[drone],
            completed_this_step=[],
        )

        self.assertEqual(drone.return_battery_threshold(), 15_000)
        self.assertEqual(kg.steps[0]["low_battery_drones"], 1)

    def test_records_assistance_event_with_source_and_helper(self):
        kg = TemporalKnowledgeStore()
        task = self.make_task()
        source = DroneAgent("source", 0, 0, region_id="10")
        helper = DroneAgent("helper", 10, 0, region_id="10")

        kg.record_assistance_event(
            481,
            "assist_dispatched",
            task,
            source,
            helper_drone=helper,
            reason="failure",
        )

        self.assertEqual(kg.events[0]["source_drone_id"], "source")
        self.assertEqual(kg.events[0]["helper_drone_id"], "helper")
        self.assertEqual(kg.events[0]["reason"], "failure")

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

    def consume(self):
        return None


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


class FakeIndexSession:
    def __init__(self, driver):
        self.driver = driver

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def run(self, query, **params):
        self.driver.calls.append((query, params))
        if "SHOW INDEXES" in query:
            self.driver.show_count += 1
            if (
                self.driver.online_after_show_count is not None
                and self.driver.show_count >= self.driver.online_after_show_count
            ):
                for index in self.driver.indexes:
                    if index["state"] == "POPULATING":
                        index["state"] = "ONLINE"
            return FakeNeo4jResult([dict(index) for index in self.driver.indexes])
        for name, properties in self.driver.index_specs.items():
            if f"CREATE INDEX {name}" in query:
                self.driver.indexes.append(
                    {
                        "name": name,
                        "state": "ONLINE",
                        "type": "RANGE",
                        "entityType": "NODE",
                        "labelsOrTypes": ["AgentState"],
                        "properties": list(properties),
                        "failureMessage": "",
                    }
                )
                break
        return FakeNeo4jResult()


class FakeIndexDriver:
    def __init__(self, indexes=None, online_after_show_count=None):
        self.calls = []
        self.indexes = list(indexes or [])
        self.show_count = 0
        self.online_after_show_count = online_after_show_count
        self.index_specs = {
            "lade_agent_state_run_drone_step": ("run_id", "drone_id", "step_index"),
            "lade_agent_state_run_region_step": ("run_id", "region_id", "step_index"),
            "lade_agent_state_run_step": ("run_id", "step_index"),
        }

    def session(self):
        return FakeIndexSession(self)


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
        self.assertIn("battery_capacity_m", first_params["drone"])
        self.assertIn("battery_remaining_m", first_params["drone"])
        self.assertIn("battery_consumed_m", first_params["drone"])
        self.assertIn("queue_capacity", first_params["drone"])
        self.assertIn("cargo_capacity", first_params["drone"])
        self.assertIn("onboard_count", first_params["drone"])
        self.assertIn("picked_up", first_params["task"])
        self.assertIn("pickup_time", first_params["task"])
        self.assertIn("onboard_drone_id", first_params["task"])
        self.assertEqual(kg.events[0]["type"], "task_assigned")

    def test_event_writes_are_idempotent_for_ray_step_replay(self):
        driver = FakeNeo4jDriver()
        kg = Neo4jKnowledgeStore(driver=driver, run_id="run-a")
        task = self.make_task()
        drone = DroneAgent("drone-1", 0, 0, region_id="10")

        kg.record_assignment(481.25, task, drone)
        first_event_id = driver.calls[0][1]["event_id"]
        kg.record_assignment(481.25, task, drone)
        second_event_id = driver.calls[2][1]["event_id"]

        self.assertEqual(first_event_id, second_event_id)
        self.assertEqual(
            first_event_id,
            "run-a:task_assigned:481.25:drone-1:task-1",
        )
        event_queries = [query for query, _params in driver.calls if "MERGE (e:Event" in query]
        self.assertEqual(len(event_queries), 2)
        self.assertTrue(all("MERGE (e)-[:ABOUT_DRONE]->(d)" in query for query in event_queries))
        self.assertTrue(all("CREATE (e)-[" not in query for query in event_queries))

    def test_records_pickup_carrying_relation_and_completion_deactivates_it(self):
        driver = FakeNeo4jDriver()
        kg = Neo4jKnowledgeStore(driver=driver, run_id="run-a")
        task = self.make_task()
        drone = DroneAgent("drone-1", 0, 0, region_id="10")
        task.status = "picked_up"
        task.picked_up = True
        task.pickup_time = 482
        task.onboard_drone_id = drone.drone_id
        drone.onboard_tasks.append(task)

        kg.record_pickup(482, task, drone)
        kg.record_completion(500, task, drone)

        joined_queries = "\n".join(query for query, _params in driver.calls)
        self.assertIn(":CARRYING", joined_queries)
        self.assertIn("active = false", joined_queries)
        self.assertEqual(kg.events[0]["type"], "task_picked_up")

    def test_records_drone_charged_event_and_step_node_to_neo4j(self):
        driver = FakeNeo4jDriver()
        kg = Neo4jKnowledgeStore(driver=driver, run_id="run-a")
        drone = DroneAgent("drone-1", 0, 0, region_id="10")

        kg.record_drone_event(481, "drone_charged", drone)
        kg.record_step(1, 481, [], [drone], [])

        joined_queries = "\n".join(query for query, _params in driver.calls)
        step_params = [params for query, params in driver.calls if "MERGE (s:Step" in query][0]
        self.assertIn("MERGE (s:Step", joined_queries)
        self.assertEqual(step_params["step"]["id"], "run-a-1")
        self.assertEqual(kg.events[0]["type"], "drone_charged")

    def test_records_assistance_events_and_relationships_to_neo4j(self):
        driver = FakeNeo4jDriver()
        kg = Neo4jKnowledgeStore(driver=driver, run_id="run-a")
        task = self.make_task()
        task.assistance_reason = "failure"
        task.assistance_source_drone_id = "source"
        task.handover_from_drone_id = "source"
        task.handover_to_drone_id = "helper"
        task.handover_x = 100
        task.handover_y = 200
        task.takeover_count = 1
        task.previous_assignees = ["source"]
        source = DroneAgent("source", 100, 200, region_id="10")
        helper = DroneAgent("helper", 0, 0, region_id="10")

        kg.record_assistance_event(
            481,
            "assist_dispatched",
            task,
            source,
            helper_drone=helper,
            reason="failure",
        )

        joined_queries = "\n".join(query for query, _params in driver.calls)
        self.assertIn(":ASSISTS", joined_queries)
        self.assertIn(":HANDOVER_TO", joined_queries)
        self.assertIn(":TAKES_OVER", joined_queries)
        self.assertIn(":ABOUT_SOURCE_DRONE", joined_queries)
        event_call = next(
            params for query, params in driver.calls if "MERGE (e:Event" in query
        )
        self.assertEqual(event_call["task"]["handover_from_drone_id"], "source")
        self.assertEqual(event_call["task"]["takeover_count"], 1)

    def test_agent_state_nodes_are_not_written_by_default(self):
        driver = FakeNeo4jDriver()
        kg = Neo4jKnowledgeStore(driver=driver, run_id="run-a")
        drones = [
            DroneAgent("drone-1", 10, 20, region_id="10"),
            DroneAgent("drone-2", 30, 40, region_id="10"),
        ]

        kg.record_step(1, 481, [], drones, [])

        joined_queries = "\n".join(query for query, _params in driver.calls)
        self.assertNotIn("AgentState", joined_queries)
        self.assertTrue(all("agent_states" not in params for _query, params in driver.calls))

    def test_agent_state_nodes_are_written_in_batch_when_enabled(self):
        driver = FakeNeo4jDriver()
        kg = Neo4jKnowledgeStore(driver=driver, run_id="run-a", record_agent_state=True)
        queued_task = self.make_task("queued")
        onboard_task = self.make_task("onboard")
        onboard_task.picked_up = True
        onboard_task.status = "picked_up"
        drone = DroneAgent(
            "drone-1",
            10,
            20,
            region_id="10",
            battery_remaining_m=42_000,
            total_distance_m=8_500,
        )
        drone.queue.append(queued_task)
        drone.onboard_tasks.append(onboard_task)

        kg.record_step(2, 482, [], [drone], [])

        agent_state_calls = [
            (query, params)
            for query, params in driver.calls
            if "MERGE (a:AgentState" in query
        ]
        self.assertEqual(len(agent_state_calls), 1)
        query, params = agent_state_calls[0]
        self.assertIn("UNWIND $agent_states AS state", query)
        self.assertEqual(params["run_id"], "run-a")
        self.assertEqual(params["step_id"], "run-a-2")
        self.assertEqual(len(params["agent_states"]), 1)
        state = params["agent_states"][0]
        self.assertEqual(state["id"], "run-a-2-drone-1")
        self.assertEqual(state["run_id"], "run-a")
        self.assertEqual(state["step_index"], 2)
        self.assertEqual(state["time_minute"], 482)
        self.assertEqual(state["drone_id"], "drone-1")
        self.assertEqual(state["region_id"], "10")
        self.assertEqual(state["x"], 10)
        self.assertEqual(state["y"], 20)
        self.assertEqual(state["status"], "idle")
        self.assertEqual(state["battery_remaining_m"], 42_000)
        self.assertEqual(state["queue_len"], 1)
        self.assertEqual(state["onboard_count"], 1)
        self.assertEqual(state["total_distance_m"], 8_500)
        self.assertIn("run_id: $run_id", query)
        self.assertIn("drone_id: state.drone_id", query)
        self.assertIn("step_index: state.step_index", query)

    def test_auto_create_agent_state_indexes_when_missing(self):
        driver = FakeIndexDriver()

        Neo4jKnowledgeStore(
            driver=driver,
            run_id="run-a",
            record_agent_state=True,
            auto_create_indexes=True,
            index_poll_interval_seconds=0,
        )

        create_queries = [query for query, _params in driver.calls if "CREATE INDEX" in query]
        self.assertEqual(len(create_queries), 3)
        self.assertEqual(
            {index["name"] for index in driver.indexes},
            set(driver.index_specs),
        )

    def test_auto_create_agent_state_indexes_reuses_equivalent_indexes(self):
        indexes = [
            {
                "name": f"custom_{number}",
                "state": "ONLINE",
                "type": "RANGE",
                "entityType": "NODE",
                "labelsOrTypes": ["AgentState"],
                "properties": list(properties),
                "failureMessage": "",
            }
            for number, properties in enumerate(FakeIndexDriver().index_specs.values(), start=1)
        ]
        driver = FakeIndexDriver(indexes=indexes)

        Neo4jKnowledgeStore(
            driver=driver,
            run_id="run-a",
            record_agent_state=True,
            auto_create_indexes=True,
            index_poll_interval_seconds=0,
        )

        self.assertFalse(any("CREATE INDEX" in query for query, _params in driver.calls))

    def test_auto_create_agent_state_indexes_waits_for_populating_index(self):
        indexes = [
            {
                "name": f"custom_{number}",
                "state": "POPULATING" if number == 1 else "ONLINE",
                "type": "RANGE",
                "entityType": "NODE",
                "labelsOrTypes": ["AgentState"],
                "properties": list(properties),
                "failureMessage": "",
            }
            for number, properties in enumerate(FakeIndexDriver().index_specs.values(), start=1)
        ]
        driver = FakeIndexDriver(indexes=indexes, online_after_show_count=2)

        kg = Neo4jKnowledgeStore(
            driver=driver,
            run_id="run-a",
            record_agent_state=True,
            auto_create_indexes=True,
            index_poll_interval_seconds=0,
        )

        self.assertEqual(driver.show_count, 2)
        self.assertEqual(kg.agent_state_index_names[0], "custom_1")

    def test_auto_create_agent_state_indexes_rejects_failed_equivalent_index(self):
        driver = FakeIndexDriver(
            indexes=[
                {
                    "name": "failed_agent_state_index",
                    "state": "FAILED",
                    "type": "RANGE",
                    "entityType": "NODE",
                    "labelsOrTypes": ["AgentState"],
                    "properties": ["run_id", "drone_id", "step_index"],
                    "failureMessage": "population failed",
                }
            ]
        )

        with self.assertRaisesRegex(RuntimeError, "population failed"):
            Neo4jKnowledgeStore(
                driver=driver,
                run_id="run-a",
                record_agent_state=True,
                auto_create_indexes=True,
                index_poll_interval_seconds=0,
            )

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
        driver = FakeIndexDriver()

        with patch.dict(
            os.environ,
            {
                "KG_BACKEND": "neo4j",
                "NEO4J_URI": "bolt://neo4j.example:7687",
                "NEO4J_USER": "neo4j",
                "NEO4J_PASSWORD": "secret",
                "KG_RUN_ID": "experiment-001",
                "KG_RECORD_AGENT_STATE": "1",
                "KG_AUTO_CREATE_INDEXES": "1",
            },
        ):
            kg = build_knowledge_store_from_env(driver_factory=lambda uri, auth: driver)

        self.assertIsInstance(kg, Neo4jKnowledgeStore)
        self.assertIs(kg.driver, driver)
        self.assertEqual(kg.run_id, "experiment-001")
        self.assertTrue(kg.record_agent_state)
        self.assertTrue(kg.auto_create_indexes)
        self.assertEqual(
            {index["name"] for index in driver.indexes},
            set(driver.index_specs),
        )


if __name__ == "__main__":
    unittest.main()
