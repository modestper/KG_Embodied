import argparse
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts import run_dynamic_sim, run_headless_sim
from simulation.delivery_sim import AuctionCoordinator, DeliveryTask, DroneAgent, SimulationEnv
from simulation.knowledge import Neo4jKnowledgeStore, TemporalKnowledgeStore
from simulation.knowledge_writer import (
    DelegatingKnowledgeStore, KnowledgeWriterConfig, KnowledgeWriterError,
    ProcessKnowledgeWriter, build_delegating_store, encode_record,
)
from simulation.ray_delivery_sim import RaySimulationEnv
from tests.knowledge_test import FakeNeo4jDriver
from tests.ray_delivery_sim_test import FakeRay


def scenario():
    tasks = [
        DeliveryTask(f"task-{index}", "吉林市", 100 * index, 0, 480, 540,
                     "aoi", "31", pickup_x=0, pickup_y=0)
        for index in (1, 2)
    ]
    drones = [DroneAgent(f"drone-{index}", 0, 0, region_id="31") for index in (1, 2)]
    return tasks, drones


class TrackingStore(TemporalKnowledgeStore):
    def __init__(self):
        super().__init__()
        self.drone_states = []

    def record_drone_state(self, step_index, now, drone):
        self.drone_states.append((step_index, drone.drone_id, drone.battery_remaining_m))


class KnowledgeWriterTest(unittest.TestCase):
    def test_payload_freezes_state_before_later_mutation(self):
        tasks, drones = scenario()
        drones[0].assign(tasks[0])
        command = encode_record("record_assignment", 480, tasks[0], drones[0])
        tasks[0].previous_assignees.append("later")
        drones[0].queue.clear()
        self.assertEqual(command["args"][1]["previous_assignees"], [])
        self.assertEqual(len(command["args"][2]["queue"]), 1)

    def test_process_is_independent_and_close_drains_pending_writes(self):
        config = KnowledgeWriterConfig(backend="memory", run_id="process-test")
        writer = ProcessKnowledgeWriter(config, queue_size=2, timeout=10)
        tasks, drones = scenario()
        try:
            self.assertNotEqual(writer.info["pid"], os.getpid())
            for _ in range(20):
                writer.submit(encode_record("record_assignment", 480, tasks[0], drones[0]))
        finally:
            writer.close()
        self.assertEqual(writer.final_status["events"], 20)
        self.assertEqual(writer.process.returncode, 0)
        self.assertFalse(writer._sender.is_alive())
        self.assertFalse(writer._reader.is_alive())
        writer.close()

    def test_worker_write_failure_is_reported_and_child_reaped(self):
        writer = ProcessKnowledgeWriter(KnowledgeWriterConfig(backend="memory"), timeout=5)
        writer.submit({"method": "unsupported_write", "args": []})
        with self.assertRaises(KnowledgeWriterError):
            writer.flush()
        with self.assertRaises(KnowledgeWriterError):
            writer.close()
        self.assertIsNotNone(writer.process.poll())

    def test_unexpected_worker_exit_is_reported(self):
        writer = ProcessKnowledgeWriter(KnowledgeWriterConfig(backend="memory"), timeout=5)
        writer.process.kill()
        writer.process.wait(timeout=5)
        try:
            with self.assertRaises(KnowledgeWriterError):
                writer.flush()
        finally:
            with self.assertRaises(KnowledgeWriterError):
                writer.close()

    def test_process_mode_preserves_local_events_and_step_metrics(self):
        tasks, drones = scenario()
        store = build_delegating_store("process", KnowledgeWriterConfig(backend="memory"))
        env = SimulationEnv(tasks, drones, AuctionCoordinator(), step_minutes=1, knowledge_store=store)
        try:
            env.step_once()
            store.flush()
            status = store.writer.status()
            self.assertEqual(status["events"], len(store.events))
            self.assertEqual(status["steps"], 1)
            self.assertEqual(store.steps[0]["unfinished_tasks"], 0)
        finally:
            store.close()

    def test_parent_neo4j_connection_only_reads_and_flushes_before_query(self):
        driver = FakeNeo4jDriver()
        flushed = []
        submitted = []
        writer = SimpleNamespace(submit=submitted.append, flush=lambda: flushed.append(True), close=lambda: None)
        config = KnowledgeWriterConfig(run_id="read-only-parent")
        with patch("neo4j.GraphDatabase.driver", return_value=driver):
            store = DelegatingKnowledgeStore(config, "process", writer)
        tasks, drones = scenario()
        try:
            store.record_assignment(480, tasks[0], drones[0])
            self.assertEqual(driver.calls, [])
            store.query_region_context("31", tasks, drones, 480)
            self.assertTrue(flushed)
            self.assertEqual(len(submitted), 1)
            self.assertEqual(len(driver.calls), 2)
            for query, _ in driver.calls:
                self.assertTrue(query.lstrip().startswith("MATCH"))
        finally:
            store.close()

    def test_handover_frames_and_local_history_match_in_all_write_modes(self):
        expected = None
        for mode in ("main", "ray", "process"):
            with self.subTest(mode=mode):
                fake_ray = FakeRay()
                store = TemporalKnowledgeStore() if mode == "main" else build_delegating_store(
                    mode, KnowledgeWriterConfig(backend="memory"), ray_module=fake_ray,
                )
                task = DeliveryTask("handover", "吉林市", 1000, 0, 480, 540,
                                    "aoi", "31", pickup_x=0, pickup_y=0)
                source = DroneAgent("source", 0, 0, region_id="31")
                source.assign(task)
                source.pickup_task(task, 480)
                source.failure_mode = "motor"
                helper = DroneAgent("helper", 100, 0, region_id="31", speed_m_per_minute=500)
                env = RaySimulationEnv([task], [source, helper], AuctionCoordinator(),
                                       step_minutes=1, knowledge_store=store, ray_module=fake_ray)
                try:
                    frames = []
                    for _ in range(4):
                        frames.append(env.step_once())
                        if env.is_complete():
                            break
                    self.assertTrue(env.is_complete())
                    actual = {"frames": frames, "events": store.events, "steps": store.steps}
                    if expected is None:
                        expected = actual
                    else:
                        self.assertEqual(actual, expected)
                finally:
                    env.close()
                    store.close()

    def test_ray_mode_routes_local_records_to_drone_actors_and_summary_to_writer(self):
        stores = []

        def make_store(config, bootstrap=False):
            store = TrackingStore()
            store.run_id = config.run_id
            stores.append(store)
            return store

        fake_ray = FakeRay()
        config = KnowledgeWriterConfig(backend="memory", run_id="ray-test", record_agent_state=True)
        with patch.object(KnowledgeWriterConfig, "build_store", make_store):
            store = build_delegating_store("ray", config, ray_module=fake_ray)
            tasks, drones = scenario()
            env = RaySimulationEnv(tasks, drones, AuctionCoordinator(), step_minutes=1,
                                   knowledge_store=store, ray_module=fake_ray)
            try:
                env.step_once()
                store.flush()
                coordinator_store = store.writer.actor.instance.executor.store
                actor_stores = [actor.instance.knowledge_store for actor in env.actors.values()]
                self.assertEqual([len(item.events) for item in actor_stores], [3, 3])
                self.assertEqual([len(item.drone_states) for item in actor_stores], [1, 1])
                self.assertEqual(len(coordinator_store.steps), 1)
                self.assertEqual(coordinator_store.events, [])
                self.assertEqual(store.reader.events, [])
                self.assertEqual(store.reader.steps, [])
                self.assertEqual(len(store.events), 6)
                self.assertTrue(env.is_complete())
                self.assertEqual(fake_ray.remote_options[0], {"num_cpus": 0})
            finally:
                env.close()
                store.close()

    def test_ray_assistance_is_written_by_coordinator(self):
        fake_ray = FakeRay()
        config = KnowledgeWriterConfig(backend="memory", run_id="assist-test")
        store = build_delegating_store("ray", config, ray_module=fake_ray)
        tasks, drones = scenario()
        drones[0].assign(tasks[0])
        drones[0].failure_mode = "motor"
        env = RaySimulationEnv(tasks[:1], drones, AuctionCoordinator(), step_minutes=1,
                               knowledge_store=store, ray_module=fake_ray)
        try:
            env.step_once()
            store.flush()
            events = store.writer.actor.instance.executor.store.events
            self.assertEqual([event["type"] for event in events], [
                "task_assistance_requested", "assist_dispatched", "task_taken_over",
            ])
            self.assertEqual(tasks[0].assigned_to, "drone-2")
            self.assertEqual(len([event for event in store.events if event["type"] == "task_assigned"]), 1)
        finally:
            env.close()
            store.close()

    def test_actor_states_do_not_publish_global_step_until_coordinator_commit(self):
        driver = FakeNeo4jDriver()
        kg = Neo4jKnowledgeStore(driver=driver, run_id="run", record_agent_state=True)
        tasks, drones = scenario()
        kg.record_drone_state(1, 481, drones[0])
        self.assertFalse(any("MERGE (s:Step" in query for query, _ in driver.calls))
        self.assertTrue(any("MERGE (a:AgentState" in query for query, _ in driver.calls))
        step = kg.step_summary(1, 481, tasks, drones, [])
        kg.record_step_data(step, external_agent_states=True)
        query, params = driver.calls[-1]
        self.assertIn("MERGE (s:Step", query)
        self.assertIn("MERGE (a)-[:IN_STEP]->(s)", query)
        self.assertEqual(params["step_index"], 1)

    def test_actor_events_do_not_rewrite_shared_run_and_region_properties(self):
        driver = FakeNeo4jDriver()
        kg = Neo4jKnowledgeStore(driver=driver, run_id="actor-owned")
        kg.actor_owned = True
        tasks, drones = scenario()
        kg.record_assignment(480, tasks[0], drones[0])
        query = driver.calls[0][0]
        self.assertIn("ON CREATE SET run += $run", query)
        self.assertIn("ON CREATE SET r += $region", query)

    def test_checkpoint_barrier_drains_events_before_publishing_next_step(self):
        class DelayedWriter:
            def __init__(self):
                self.commands = []
                self.applied = []

            def submit(self, command):
                self.commands.append(command)

            def flush(self):
                self.applied.extend(self.commands)
                self.commands.clear()

            def close(self):
                self.flush()

        writer = DelayedWriter()
        config = KnowledgeWriterConfig(backend="memory")
        store = DelegatingKnowledgeStore(config, "process", writer)
        tasks, drones = scenario()
        env = RaySimulationEnv(tasks, drones, AuctionCoordinator(), step_minutes=1,
                               knowledge_store=store, ray_module=FakeRay())
        checkpoints = []

        def checkpoint(_env, step, _completed):
            self.assertEqual(len(writer.applied), 6)
            self.assertFalse(any(command["method"] == "record_step_data" for command in writer.applied))
            checkpoints.append(step)

        env.before_step_commit = checkpoint
        try:
            env.step_once()
            self.assertEqual(checkpoints, [1])
            self.assertEqual([command["method"] for command in writer.commands], ["record_step_data"])
        finally:
            env.close()
            store.close()

    def test_dynamic_and_headless_flags_select_write_mode(self):
        with patch("sys.argv", ["run_dynamic_sim.py", "--kg-write-mode", "ray"]):
            self.assertEqual(run_dynamic_sim.parse_args().kg_write_mode, "ray")
        self.assertEqual(run_headless_sim.parse_args(["--kg-write-mode", "process"]).kg_write_mode, "process")
        with patch.dict(os.environ, {"KG_WRITE_MODE": "process"}):
            with patch("sys.argv", ["run_dynamic_sim.py"]):
                self.assertEqual(run_dynamic_sim.parse_args().kg_write_mode, "process")

    def test_unsupported_modes_fail_before_loading_data(self):
        with patch("scripts.run_dynamic_sim.load_delivery_tasks") as loader:
            with self.assertRaisesRegex(ValueError, "requires --backend ray"):
                run_dynamic_sim.build_runtime(argparse.Namespace(kg_write_mode="ray", backend="local"))
            with patch.dict(os.environ, {"KG_BACKEND": "memory"}):
                with self.assertRaisesRegex(ValueError, "KG_BACKEND=neo4j"):
                    run_dynamic_sim.build_runtime(argparse.Namespace(kg_write_mode="process", backend="ray"))
        loader.assert_not_called()


if __name__ == "__main__":
    unittest.main()
