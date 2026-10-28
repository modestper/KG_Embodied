import copy
import json
import os
import unittest
from unittest.mock import patch

from scripts import run_dynamic_sim, run_headless_sim
from simulation.delivery_sim import AuctionCoordinator, DeliveryTask, DroneAgent
from simulation.knowledge import Neo4jKnowledgeStore
from simulation.knowledge_state import KnowledgeStateError, decode_runtime_states
from simulation.knowledge_writer import KnowledgeWriterConfig, build_delegating_store
from simulation.ray_delivery_sim import RaySimulationEnv, _DroneActor
from tests.knowledge_test import FakeNeo4jDriver, FakeNeo4jResult, FakeNeo4jSession
from tests.ray_delivery_sim_test import FakeRay


class RuntimeSession(FakeNeo4jSession):
    def run(self, query, **params):
        if "DroneRuntimeState" not in query:
            return super().run(query, **params)
        self.driver.calls.append((query, copy.deepcopy(params)))
        database = self.driver.database
        if "UNWIND $states" in query:
            for state in params["states"]:
                key = (params["run_id"], params["execution_id"], state["drone_id"])
                previous = database.get(key)
                if previous is None or previous["version"] <= params["version"]:
                    database[key] = {
                        **copy.deepcopy(state),
                        **{field: params[field] for field in (
                            "execution_id", "version", "schema_version",
                        )},
                    }
        if "RETURN s.drone_id" in query:
            return FakeNeo4jResult([
                copy.deepcopy(database[key]) for drone_id in params["drone_ids"]
                if (key := (params["run_id"], params["execution_id"], drone_id)) in database
            ])
        return FakeNeo4jResult()


class RuntimeDriver(FakeNeo4jDriver):
    """Shared fake DB; production Neo4j encode/decode and routing still run."""
    def __init__(self, database):
        super().__init__()
        self.database = database
        self.session_options = []

    def session(self, **kwargs):
        self.session_options.append(kwargs)
        return RuntimeSession(self)


def scenario(handover=False):
    tasks = [DeliveryTask("first", "吉林市", 1000, 0, 480, 540,
                          "aoi", "31", pickup_x=0, pickup_y=0)]
    source = DroneAgent("source", 0, 0, region_id="31", speed_m_per_minute=300)
    helper = DroneAgent("helper", 100, 0, region_id="31", speed_m_per_minute=500)
    if handover:
        source.assign(tasks[0])
        source.pickup_task(tasks[0], 480)
        source.failure_mode = "motor"
    else:
        tasks.append(DeliveryTask("later", "吉林市", 1200, 0, 482, 540,
                                  "aoi", "31", pickup_x=100, pickup_y=0))
    return tasks, [source, helper]


class KnowledgeStateTest(unittest.TestCase):
    def make_env(self, mode="main", source="neo4j", handover=False, database=None):
        database = {} if database is None else database
        fake_ray = FakeRay()

        def build_store(config, bootstrap=False):
            return Neo4jKnowledgeStore(driver=RuntimeDriver(database), run_id=config.run_id)

        config = KnowledgeWriterConfig(run_id="same-run", auto_create_indexes=False)
        with patch.object(KnowledgeWriterConfig, "build_store", build_store):
            if mode == "main":
                store = build_store(config)
            else:
                # Synchronous fake actor exercises the real write executor and
                # main reader; the opt-in integration test covers the OS worker.
                store = build_delegating_store("ray", config, fake_ray)
                if mode == "process":
                    store.mode = "process"
                    store.actor_config = None
            tasks, drones = scenario(handover)
            env = RaySimulationEnv(tasks, drones, AuctionCoordinator(), step_minutes=1,
                                   knowledge_store=store, ray_module=fake_ray,
                                   state_source=source)
        self.addCleanup(store.close)
        self.addCleanup(env.close)
        return env, database

    def test_frames_events_and_task_progress_match_ray_for_all_write_modes(self):
        for handover in (False, True):
            baseline, _ = self.make_env(source="ray", handover=handover)
            expected = [baseline.step_once() for _ in range(7)]
            for mode in ("main", "ray", "process"):
                with self.subTest(mode=mode, handover=handover):
                    with patch.object(_DroneActor, "snapshot", side_effect=AssertionError("Ray snapshot read")):
                        env, _ = self.make_env(mode=mode, handover=handover)
                        actual = [env.step_once() for _ in range(7)]
                    self.assertEqual(actual, expected)
                    self.assertTrue(env.is_complete())
                    self.assertEqual(env.knowledge_store.events, baseline.knowledge_store.events)

    def test_main_read_connection_executes_only_reads_in_offloaded_modes(self):
        for mode in ("ray", "process"):
            with self.subTest(mode=mode):
                env, _ = self.make_env(mode=mode)
                env.step_once()
                driver = env.knowledge_store.reader.driver
                self.assertTrue(driver.calls)
                for query, _ in driver.calls:
                    self.assertIn("MATCH (s:DroneRuntimeState", query)
                    self.assertNotIn("MERGE", query)
                    self.assertNotIn("SET ", query)
                    self.assertNotIn("CREATE", query)
                self.assertTrue(all(options == {"default_access_mode": "READ"}
                                    for options in driver.session_options))

    def test_planner_uses_neo4j_payload_instead_of_actor_snapshot(self):
        env, database = self.make_env()
        key = ("same-run", env.execution_id, "source")
        state = json.loads(database[key]["payload_json"])
        state["snapshot"]["battery_remaining_m"] = 123
        database[key]["payload_json"] = json.dumps(state)
        env._sync_drones()
        self.assertEqual(env.drone_by_id("source").battery_remaining_m, 123)
        self.assertNotEqual(env.actors["source"].instance.drone.battery_remaining_m, 123)

    def test_task_notifications_use_graph_payload(self):
        env, _ = self.make_env()
        env.release_and_assign()
        task = env.tasks[0]
        env._apply_assigned_task({"task_id": task.task_id, "status": "completed"})
        self.assertEqual(task.status, "assigned")
        self.assertIsNotNone(task.assigned_to)

    def test_actor_owned_write_returns_ids_after_persisting_complete_state(self):
        env, database = self.make_env(mode="ray")
        context = env._next_state_context(["source"])
        result = env.actors["source"].instance.step(1, 481, 1, state_context=context)
        self.assertEqual(result["drone"], {"drone_id": "source"})
        self.assertNotIn("_runtime_state", result)
        state = json.loads(database[("same-run", env.execution_id, "source")]["payload_json"])
        self.assertIn("battery_remaining_m", state["snapshot"])

    def test_missing_and_stale_states_fail_without_ray_fallback(self):
        for stale in (False, True):
            with self.subTest(stale=stale):
                env, database = self.make_env()
                key = ("same-run", env.execution_id, "source")
                if stale:
                    database[key]["version"] = -1
                else:
                    del database[key]
                with patch.object(_DroneActor, "snapshot", side_effect=AssertionError("fallback")):
                    with self.assertRaises(KnowledgeStateError):
                        env.step_once()

    def test_restore_and_restart_isolate_runtime_views(self):
        env, database = self.make_env()
        env.step_once()
        checkpoint = env.checkpoint_state()
        previous_execution = env.execution_id
        expected = env.step_once()
        with patch.object(_DroneActor, "snapshot", side_effect=AssertionError("snapshot")):
            env.restore_checkpoint_state(checkpoint)
            self.assertNotEqual(env.execution_id, previous_execution)
            self.assertEqual(env.step_once(), expected)
            another, _ = self.make_env(database=database)
            self.assertNotEqual(env.execution_id, another.execution_id)
            self.assertEqual(another.drone_by_id("source").position, (0, 0))

    def test_failed_assistance_restores_source_via_graph(self):
        env, _ = self.make_env(handover=True)
        env.actors["helper"].instance.drone.queue_capacity = 0
        # The persisted helper snapshot still admits selection; actor rejects it.
        env.coordinate_assistance()
        self.assertEqual([task.task_id for task in env.drone_by_id("source").queue], ["first"])
        self.assertIsNone(env.drone_by_id("helper").takeover_mode)
        self.assertEqual(env.drone_by_id("source").onboard_tasks[0].task_id, "first")

    def test_schema_and_identity_validation(self):
        env, database = self.make_env()
        row = next(iter(database.values()))
        expected = {row["drone_id"]: 0}
        for field, value in (("schema_version", 9), ("execution_id", "old"),
                             ("drone_id", "alien"), ("payload_json", "{")):
            with self.subTest(field=field):
                with self.assertRaises(KnowledgeStateError):
                    decode_runtime_states([{**row, field: value}], env.execution_id, expected)
        with self.assertRaises(KnowledgeStateError):
            decode_runtime_states([row, row], env.execution_id, expected)
        payload = json.loads(row["payload_json"])
        del payload["snapshot"]["queue_payload"]
        with self.assertRaises(KnowledgeStateError):
            decode_runtime_states([{**row, "payload_json": json.dumps(payload)}], env.execution_id, expected)

    def test_invalid_configs_fail_before_loading_tasks(self):
        for backend, kg_backend in (("local", "neo4j"), ("ray", "memory")):
            with patch.dict(os.environ, {"KG_BACKEND": kg_backend}):
                args = type("Args", (), {"state_source": "neo4j", "backend": backend})()
                with self.assertRaisesRegex(ValueError, "requires"):
                    run_dynamic_sim.build_runtime(args)

    def test_entrypoints_accept_state_source(self):
        with patch.dict(os.environ, {"KG_STATE_SOURCE": "neo4j"}):
            with patch("sys.argv", ["run_dynamic_sim.py"]):
                self.assertEqual(run_dynamic_sim.parse_args().state_source, "neo4j")
            args = run_headless_sim.parse_args(["--run-id", "state-test"])
            self.assertEqual(args.state_source, "neo4j")
            self.assertEqual(run_dynamic_sim.args_from_request(args, "/events?state_source=ray").state_source, "ray")


@unittest.skipUnless(os.environ.get("LADE_KG_STATE_EXPLAIN_URI"), "set LADE_KG_STATE_EXPLAIN_URI for read-only EXPLAIN")
class KnowledgeStateExplainIntegrationTest(unittest.TestCase):
    def test_runtime_queries_compile_without_executing_writes(self):
        from neo4j import GraphDatabase
        from simulation.knowledge_state import runtime_state
        from simulation.ray_delivery_sim import drone_snapshot

        fake = RuntimeDriver({})
        store = Neo4jKnowledgeStore(driver=fake, run_id="state-explain-only")
        context = {"execution_id": "explain-only", "version": 0,
                   "step_index": 0, "time_minute": 480}
        store.publish_runtime_states(context, [runtime_state(drone_snapshot(scenario()[1][0]))])
        store.query_runtime_states(context["execution_id"], {"source": 0})
        with GraphDatabase.driver(
            os.environ["LADE_KG_STATE_EXPLAIN_URI"],
            auth=(os.environ.get("NEO4J_USER", "neo4j"), os.environ.get("NEO4J_PASSWORD", "")),
        ) as driver:
            with driver.session() as session:
                for query, parameters in fake.calls:
                    with self.subTest(query=query):
                        session.run("EXPLAIN " + query, **parameters).consume()


if __name__ == "__main__":
    unittest.main()
