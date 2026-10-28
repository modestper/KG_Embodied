"""Opt-in real Ray processes and isolated Neo4j write integration checks."""

import os
import tempfile
import unittest
from uuid import uuid4

from simulation.delivery_sim import AuctionCoordinator
from simulation.knowledge import TemporalKnowledgeStore
from simulation.knowledge_writer import KnowledgeWriterConfig, build_delegating_store
from simulation.ray_delivery_sim import RaySimulationEnv
from scripts.run_dynamic_sim import close_env
from tests.knowledge_writer_test import scenario


class RayIntegrationBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import ray

        cls.ray = ray
        cls.temp = tempfile.TemporaryDirectory(prefix="lade-kg-ray-", dir="/tmp")
        try:
            ray.init(address="local", num_cpus=2, include_dashboard=False,
                     object_store_memory=80 * 1024 * 1024, _temp_dir=cls.temp.name,
                     _node_ip_address="127.0.0.1")
        except Exception:
            ray.shutdown()
            cls.temp.cleanup()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.ray.shutdown()
        cls.temp.cleanup()


@unittest.skipUnless(os.environ.get("LADE_KG_RAY_INTEGRATION") == "1", "set LADE_KG_RAY_INTEGRATION=1")
class KnowledgeWriterRayIntegrationTest(RayIntegrationBase):
    def test_both_modes_match_baseline_in_real_ray_processes(self):
        expected_frame = None
        for mode in ("main", "ray", "process"):
            with self.subTest(mode=mode):
                config = KnowledgeWriterConfig(backend="memory", run_id=f"real-ray-{mode}", record_agent_state=True)
                store = TemporalKnowledgeStore() if mode == "main" else build_delegating_store(mode, config, self.ray)
                tasks, drones = scenario()
                env = None
                try:
                    env = RaySimulationEnv(tasks, drones, AuctionCoordinator(), step_minutes=1,
                                           knowledge_store=store, ray_module=self.ray)
                    frame = env.step_once()
                    if expected_frame is None:
                        expected_frame = frame
                    else:
                        self.assertEqual(frame, expected_frame)
                    if mode != "main":
                        status = store.writer.status()
                        self.assertNotEqual(status["pid"], os.getpid())
                        self.assertEqual(status["run_id"], config.run_id)
                        self.assertEqual(status["steps"], 1)
                    actor_statuses = self.ray.get([actor.knowledge_status.remote() for actor in env.actors.values()])
                    for status in actor_statuses:
                        self.assertNotEqual(status["pid"], os.getpid())
                        self.assertEqual(status["writes_enabled"], mode == "ray")
                        self.assertEqual(status["events"], 3 if mode == "ray" else 0)
                finally:
                    if env is not None:
                        close_env(env)
                    else:
                        store.close()


NEO4J_WRITE_ENABLED = bool(os.environ.get("LADE_KG_WRITER_INTEGRATION_URI")) and os.environ.get("LADE_KG_WRITER_INTEGRATION_WRITE") == "1"


@unittest.skipUnless(NEO4J_WRITE_ENABLED, "opt-in isolated Neo4j write tests are not enabled")
class KnowledgeWriterNeo4jIntegrationTest(RayIntegrationBase):
    def test_main_reads_runtime_states_with_every_write_mode(self):
        from neo4j import GraphDatabase

        uri = os.environ["LADE_KG_WRITER_INTEGRATION_URI"]
        user = os.environ.get("NEO4J_USER", "neo4j")
        password = os.environ.get("NEO4J_PASSWORD", "")
        driver = GraphDatabase.driver(uri, auth=(user, password))
        baseline_tasks, baseline_drones = scenario()
        baseline = RaySimulationEnv(baseline_tasks, baseline_drones, AuctionCoordinator(),
                                    step_minutes=1, ray_module=self.ray)
        try:
            expected = baseline.step_once()
        finally:
            baseline.close()
        try:
            for mode in ("main", "ray", "process"):
                with self.subTest(mode=mode):
                    run_id = f"kg-state-it-{uuid4()}"
                    config = KnowledgeWriterConfig(uri=uri, user=user, password=password,
                                                   run_id=run_id, auto_create_indexes=False)
                    store = config.build_store(bootstrap=True) if mode == "main" else build_delegating_store(mode, config, self.ray)
                    tasks, drones = scenario()
                    env = None
                    try:
                        env = RaySimulationEnv(tasks, drones, AuctionCoordinator(), step_minutes=1,
                                               knowledge_store=store, ray_module=self.ray,
                                               state_source="neo4j")
                        self.assertEqual(env.step_once(), expected)
                        states = store.query_runtime_states(env.execution_id, env._expected_versions)
                        self.assertEqual(len(states), 2)
                        self.assertEqual(sum(len(state["snapshot"]["completed_ids"])
                                             for state in states.values()), 2)
                        self.assertEqual(env.tasks[0].status, "completed")
                    finally:
                        if env is not None:
                            close_env(env)
                        else:
                            store.close()
                        with driver.session() as session:
                            session.run("MATCH (n {run_id:$run_id}) DETACH DELETE n", run_id=run_id).consume()
                            session.run("MATCH (r:Run {id:$run_id}) DETACH DELETE r", run_id=run_id).consume()
        finally:
            driver.close()

    def test_both_modes_persist_same_business_graph_as_baseline(self):
        from neo4j import GraphDatabase

        uri = os.environ["LADE_KG_WRITER_INTEGRATION_URI"]
        user = os.environ.get("NEO4J_USER", "neo4j")
        password = os.environ.get("NEO4J_PASSWORD", "")
        driver = GraphDatabase.driver(uri, auth=(user, password))
        expected = None
        try:
            for mode in ("main", "ray", "process"):
                with self.subTest(mode=mode):
                    run_id = f"kg-writer-it-{uuid4()}"
                    config = KnowledgeWriterConfig(uri=uri, user=user, password=password,
                                                   run_id=run_id, record_agent_state=True)
                    store = config.build_store(bootstrap=True) if mode == "main" else build_delegating_store(mode, config, self.ray)
                    tasks, drones = scenario()
                    env = None
                    try:
                        env = RaySimulationEnv(tasks, drones, AuctionCoordinator(), step_minutes=1,
                                               knowledge_store=store, ray_module=self.ray)
                        env.step_once()
                        flush = getattr(store, "flush", None)
                        if flush:
                            flush()
                        with driver.session() as session:
                            counts = dict(session.run(
                                "MATCH (n {run_id:$run_id}) RETURN labels(n)[0] AS label, count(*) AS count",
                                run_id=run_id,
                            ).values())
                            links = session.run(
                                "MATCH (a:AgentState {run_id:$run_id})-[:IN_STEP]->(s:Step {run_id:$run_id}) RETURN count(*) AS count",
                                run_id=run_id,
                            ).single()["count"]
                            task_states = session.run(
                                "MATCH (t:Task {run_id:$run_id}) RETURN t.id, t.status, t.assigned_to ORDER BY t.id",
                                run_id=run_id,
                            ).values()
                        actual = {"counts": counts, "links": links, "tasks": task_states}
                        self.assertEqual(counts["Event"], 6)
                        self.assertEqual(counts["AgentState"], 2)
                        self.assertEqual(links, 2)
                        if expected is None:
                            expected = actual
                        else:
                            self.assertEqual(actual, expected)
                    finally:
                        if env is not None:
                            close_env(env)
                        else:
                            store.close()
                        with driver.session() as session:
                            session.run("MATCH (n {run_id:$run_id}) DETACH DELETE n", run_id=run_id).consume()
                            session.run("MATCH (r:Run {id:$run_id}) DETACH DELETE r", run_id=run_id).consume()
        finally:
            driver.close()


if __name__ == "__main__":
    unittest.main()
