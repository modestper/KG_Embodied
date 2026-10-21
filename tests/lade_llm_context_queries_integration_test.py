"""Opt-in READ-ONLY checks against an existing delivery graph, without fixtures.

Set LADE_CONTEXT_INTEGRATION_URI, LADE_CONTEXT_RUN_ID and
LADE_CONTEXT_READ_ONLY=1. No node, relationship, index or data is created.
The old LADE_CONTEXT_ROLLBACK_FIXTURE setting no longer enables this suite.
"""

import os
import unittest
from types import SimpleNamespace

from scripts import lade_llm_context_queries as base
from scripts.lade_llm_context_templates import (
    GRAPH_EVENT_TYPES, GRAPH_NODE_PROPERTIES, GRAPH_RELATIONSHIP_PROPERTIES,
)


ENABLED = (
    bool(os.environ.get("LADE_CONTEXT_INTEGRATION_URI"))
    and bool(os.environ.get("LADE_CONTEXT_RUN_ID"))
    and os.environ.get("LADE_CONTEXT_READ_ONLY") == "1"
)


@unittest.skipUnless(ENABLED, "opt-in existing-graph read-only checks are not enabled")
class DroneContextExistingGraphTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from neo4j import GraphDatabase

        cls.run_id = os.environ["LADE_CONTEXT_RUN_ID"]
        cls.drone_id = os.environ.get("LADE_CONTEXT_DRONE_ID", "drone-1")
        cls.driver = GraphDatabase.driver(
            os.environ["LADE_CONTEXT_INTEGRATION_URI"],
            auth=(os.environ.get("NEO4J_USER", base.DEFAULT_USER),
                  os.environ.get("NEO4J_PASSWORD", base.DEFAULT_PASSWORD)),
            connection_timeout=5, max_transaction_retry_time=0,
            max_connection_pool_size=1,
        )
        cls.session = cls.driver.session(
            database=os.environ.get("NEO4J_DATABASE", "neo4j"),
            default_access_mode="READ",
        )
        cls.addClassCleanup(cls.driver.close)
        cls.addClassCleanup(cls.session.close)
        cls.args = SimpleNamespace(
            run_id=cls.run_id, step_index=int(os.environ.get("LADE_CONTEXT_STEP_INDEX", "2300")),
            time_minute=None, history_steps=20, event_window_minutes=30.0,
            deadline_horizon_minutes=60.0, limit=3, param=[],
        )
        rows, _ = cls.read(
            "MATCH (s:Step {run_id:$run_id, step_index:$step_index}) "
            "RETURN s.time_minute AS time_minute",
            run_id=cls.run_id, step_index=cls.args.step_index,
        )
        if not rows:
            raise AssertionError("requested Step does not exist in the existing graph")
        cls.args.time_minute = rows[0]["time_minute"]
        # Find representative historical events without manufacturing a fixture.
        # The CLI default remains 30 minutes; this wider window is test-only.
        cls.args.event_window_minutes = float(os.environ.get(
            "LADE_CONTEXT_EVENT_WINDOW_MINUTES", str(max(30.0, cls.args.time_minute + 1)),
        ))

    @classmethod
    def read(cls, cypher, **params):
        from neo4j import Query

        result = cls.session.run(Query(cypher, timeout=12), params)
        rows = [dict(row) for row in result]
        return rows, result.consume()

    def query(self, query_id, explain=False):
        query = base.CONTEXT_QUERIES[query_id]
        params = base.build_params(query, self.args, self.drone_id)
        return self.read(("EXPLAIN " if explain else "") + query.cypher, **params)

    def assert_no_unknown_schema(self, summary):
        unknown = {"01N50", "01N51", "01N52"}
        self.assertFalse([
            status for status in summary.gql_status_objects
            if status.gql_status in unknown
        ])

    def test_contract_uses_labels_edges_fields_and_events_present_in_this_run(self):
        labels, _ = self.read("CALL db.labels()")
        self.assertTrue(set(GRAPH_NODE_PROPERTIES) <= {row["label"] for row in labels})
        edges, _ = self.read("CALL db.relationshipTypes()")
        self.assertTrue(set(GRAPH_RELATIONSHIP_PROPERTIES) <= {row["relationshipType"] for row in edges})
        for label, fields in GRAPH_NODE_PROPERTIES.items():
            with self.subTest(label=label):
                identity = "id" if label == "Run" else "run_id"
                bound = " WITH n LIMIT 128" if label == "AgentState" else ""
                rows, _ = self.read(
                    f"MATCH (n:{label} {{{identity}:$run_id}})" + bound
                    + " UNWIND keys(n) AS key RETURN collect(DISTINCT key) AS fields",
                    run_id=self.run_id,
                )
                self.assertTrue(fields <= set(rows[0]["fields"]),
                                f"{label} missing fields: {fields - set(rows[0]['fields'])}")
        events, _ = self.read(
            "MATCH (e:Event {run_id:$run_id}) RETURN collect(DISTINCT e.type) AS types",
            run_id=self.run_id,
        )
        self.assertTrue(GRAPH_EVENT_TYPES <= set(events[0]["types"]))

    def test_all_56_templates_explain_without_unknown_schema_notifications(self):
        for query_id in base.CONTEXT_QUERIES:
            with self.subTest(query=query_id):
                _, summary = self.query(query_id, explain=True)
                self.assert_no_unknown_schema(summary)

    def test_existing_current_and_event_queries_execute_without_unknown_schema(self):
        # Sequential smoke checks, not a throughput benchmark. No full-history
        # AgentState scans are started on the deliberately unindexed baseline.
        query_ids = (
            "i0", "i3", "i4", "i5", "i12", "i17", "i18", "i19", "i20",
            "i21", "i22", "i23", "i24", "i25", "i26", "i27", "i28", "i29",
            "i30", "i31", "i33", "i37", "i38", "i39", "i40", "i41", "i43",
            "i44", "i46", "i47", "i48", "i50", "i51", "i54",
        )
        for query_id in query_ids:
            with self.subTest(query=query_id):
                rows, summary = self.query(query_id)
                self.assert_no_unknown_schema(summary)
                if query_id in {"i26", "i27", "i30", "i31", "i41", "i43", "i50", "i51", "i54"}:
                    self.assertTrue(rows, f"no representative event rows for {query_id}")

    def test_assignment_feedback_matches_actual_events_and_time_boundary(self):
        rows, _ = self.query("i50")
        self.assertTrue(rows)
        events, _ = self.read(
            "MATCH (e:Event {run_id:$run_id}) "
            "WHERE e.task_id IN $task_ids AND e.time_minute <= $time_minute "
            "AND e.type IN ['task_assigned','task_picked_up'] "
            "RETURN e.task_id AS task_id,e.drone_id AS drone_id,e.type AS type,e.time_minute AS minute",
            run_id=self.run_id, task_ids=[row["task_id"] for row in rows],
            time_minute=self.args.time_minute,
        )
        for row in rows:
            same_task = [event for event in events if event["task_id"] == row["task_id"]]
            later = [event["minute"] for event in same_task
                     if event["type"] == "task_assigned" and event["minute"] > row["assigned_at"]]
            boundary = min(later) if later else None
            pickups = [event["minute"] for event in same_task
                       if event["type"] == "task_picked_up" and event["drone_id"] == self.drone_id
                       and event["minute"] >= row["assigned_at"]
                       and (boundary is None or event["minute"] < boundary)]
            expected = min(pickups) if pickups else None
            self.assertEqual(row["picked_up_at"], expected)
            self.assertEqual(row["observation_status"],
                             "picked_up" if expected is not None else
                             "reassigned" if boundary is not None else "awaiting_pickup")

    def test_release_and_pickup_feedback_are_different_real_intervals(self):
        release_rows, _ = self.query("i51")
        pickup_rows, _ = self.query("i54")
        self.assertTrue(release_rows)
        self.assertTrue(pickup_rows)
        by_task = {row["task_id"]: row for row in pickup_rows}
        for row in release_rows:
            self.assertLessEqual(row["completed_at"], self.args.time_minute)
            self.assertEqual(row["release_to_completion_minutes"], row["completed_at"] - row["release_minute"])
            self.assertGreaterEqual(row["overdue_minutes"], 0)
            if row["task_id"] in by_task and by_task[row["task_id"]]["observed_delivery_minutes"] is not None:
                self.assertGreaterEqual(row["release_to_completion_minutes"],
                                        by_task[row["task_id"]]["observed_delivery_minutes"])


if __name__ == "__main__":
    unittest.main()
