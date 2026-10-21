import argparse
import json
import re
import tempfile
import unittest
from pathlib import Path

from scripts import lade_llm_context_queries
from scripts import lade_llm_context_queries_single as single
from scripts.lade_llm_context_templates import (
    CATALOG_VERSION, EXTENDED_QUERY_PACKS, builtin_schema_violations, validate_builtin_queries,
)


class FakeResult:
    def __iter__(self):
        return iter(
            [
                {"name": "row-a", "value": 1},
                {"name": "row-b", "value": 2},
            ]
        )


class FakeSession:
    def __init__(self, calls, rows=None):
        self.calls = calls
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def run(self, query, **params):
        self.calls.append((query, params))
        if self.rows is not None:
            return iter(self.rows)
        return FakeResult()


class FakeDriver:
    def __init__(self, rows=None):
        self.calls = []
        self.rows = rows

    def session(self, **kwargs):
        self.calls.append(("session", kwargs))
        return FakeSession(self.calls, rows=self.rows)


def make_args(**overrides):
    values = {
        "uri": "bolt://127.0.0.1:17687",
        "database": None,
        "run_id": "run-a",
        "queries": "core",
        "query_file": None,
        "replace_default_queries": False,
        "list_queries": False,
        "drone_id": [],
        "drone_ids": "drone-1,drone-2",
        "step_index": 100,
        "time_minute": 600.0,
        "history_steps": 20,
        "event_window_minutes": 30.0,
        "deadline_horizon_minutes": 60.0,
        "limit": 10,
        "repeat": 1,
        "max_workers": 1,
        "max_connection_pool_size": None,
        "param": [],
        "query_log_jsonl": None,
        "context_jsonl": None,
        "summary_json": None,
        "no_query_log": False,
        "no_context_log": False,
        "include_cypher": False,
        "print_context": False,
        "dry_run": False,
        "progress_every": 0,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class LaDeLlmContextQueriesTest(unittest.TestCase):
    def test_extended_catalog_is_drone_scoped_and_builds_all_params(self):
        queries = lade_llm_context_queries.select_queries("all")
        self.assertEqual([q.id for q in queries], [f"i{number}" for number in range(56)])
        self.assertEqual(len({q.name for q in queries}), 56)
        covered = {query_id for pack in EXTENDED_QUERY_PACKS.values() for query_id in pack}
        self.assertTrue({f"i{number}" for number in range(12, 56)} <= covered)
        for query in queries:
            with self.subTest(query=query.id):
                self.assertEqual(set(query.required_params), set(lade_llm_context_queries.infer_required_params(query.cypher)))
                self.assertIsNone(re.search(r"\b(CREATE|MERGE|SET|DELETE|REMOVE|DROP)\b", query.cypher, re.IGNORECASE))
                params = lade_llm_context_queries.build_params(query, make_args(), "drone-7")
                self.assertTrue(set(query.required_params) <= set(params))
                self.assertEqual(params["drone_id"], "drone-7")
                if int(query.id[1:]) >= 12:
                    self.assertIn("drone_id", query.required_params)
                    self.assertIn("run_id", query.required_params)

    def test_single_entry_uses_same_catalog_and_context_parameters(self):
        for query_id, query in lade_llm_context_queries.CONTEXT_QUERIES.items():
            other = single.CONTEXT_QUERIES[query_id]
            self.assertEqual((query.name, query.cypher, query.temporal_scope),
                             (other.name, other.cypher, other.temporal_scope))
        self.assertEqual(single.QUERY_PACKS, lade_llm_context_queries.QUERY_PACKS)

    def test_all_builtins_stay_within_observed_graph_contract(self):
        for query in lade_llm_context_queries.CONTEXT_QUERIES.values():
            with self.subTest(query=query.id):
                self.assertEqual(builtin_schema_violations(query.cypher), [])
                for absent in ("TAKES_OVER", "ASSISTS", "HANDOVER_TO", "failure_mode",
                               "takeover_mode", "source_drone_id", "helper_drone_id",
                               "assistance_reason", "previous_assignees", "handover_x"):
                    self.assertNotIn(absent, query.cypher)

    def test_schema_guard_rejects_invented_nodes_edges_properties_and_events(self):
        for cypher in (
            "MATCH (x:TaskState) RETURN x",
            "MATCH (:UnknownDepot) RETURN 1",
            "MATCH (x:Drone)-[:ASSISTS]->(y:Drone) RETURN x",
            "MATCH (x:Drone) RETURN x.failure_mode",
            "MATCH (x:Task {handover_x:1}) RETURN x",
            'MATCH (e:Event {type:"task_taken_over"}) RETURN e',
            'MATCH (e:Event) WHERE e.type IN ["assist_dispatched"] RETURN e',
        ):
            with self.subTest(cypher=cypher):
                self.assertTrue(builtin_schema_violations(cypher))

    def test_schema_guard_stops_invalid_builtin_catalog_before_database_access(self):
        invalid = lade_llm_context_queries.ContextQuery(
            id="i999", name="invalid", category="test", required_params=(),
            description="invalid", cypher="MATCH (n:InventedNode) RETURN n",
        )
        with self.assertRaisesRegex(ValueError, "i999"):
            validate_builtin_queries([invalid])

    def test_unsupported_handover_pack_is_not_advertised_as_current_schema(self):
        self.assertNotIn("handover", lade_llm_context_queries.QUERY_PACKS)
        self.assertIn("task_progress", lade_llm_context_queries.QUERY_PACKS)

    def test_scenario_selection_deduplicates_overlapping_queries(self):
        queries = lade_llm_context_queries.select_queries("task_progress,request_help,energy_safety,i1,i50")
        ids = [query.id for query in queries]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("i26", ids)
        self.assertIn("i38", ids)
        self.assertIn("i50", ids)

    def test_extended_params_have_defaults_and_param_overrides_win(self):
        query = lade_llm_context_queries.CONTEXT_QUERIES["i32"]
        args = make_args(radius_m=1200.0, min_battery_m=4000.0, param=[("radius_m", 2000.0)])
        params = lade_llm_context_queries.build_params(query, args, "drone-7")
        self.assertEqual(params["radius_m"], 2000.0)
        self.assertEqual(params["min_battery_m"], 4000.0)
        self.assertEqual(params["max_state_age_steps"], 10)

    def test_invalid_spatial_and_freshness_params_fail_before_query(self):
        query = lade_llm_context_queries.CONTEXT_QUERIES["i32"]
        for key, value in (("radius_m", -1), ("radius_m", float("inf")),
                           ("radius_m", float("nan")), ("min_battery_m", True),
                           ("max_state_age_steps", 1.5)):
            with self.subTest(key=key, value=value), self.assertRaises(SystemExit):
                lade_llm_context_queries.build_params(query, make_args(param=[(key, value)]), "drone-7")

    def test_extended_execution_preserves_all_results_and_temporal_scopes(self):
        queries = lade_llm_context_queries.select_queries("all")
        context, records = lade_llm_context_queries.execute_context(
            FakeDriver(), queries, make_args(), "drone-7", 0,
        )
        self.assertEqual(len(records), 56)
        self.assertEqual(context["catalog_version"], CATALOG_VERSION)
        self.assertTrue(all(record["catalog_version"] == CATALOG_VERSION for record in records))
        self.assertEqual(len(context["queries"]), 56)
        self.assertEqual(context["query_temporal_scopes"]["i26"], "event_window")
        self.assertEqual(context["query_temporal_scopes"]["i38"], "current")
        self.assertTrue(all(record["success"] for record in records))
        self.assertTrue(all(record["temporal_scope"] for record in records))

    def test_select_queries_supports_pack_range_and_category(self):
        core = lade_llm_context_queries.select_queries("core")
        ranged = lade_llm_context_queries.select_queries("i0-i2")
        self_queries = lade_llm_context_queries.select_queries("self")

        self.assertEqual([query.id for query in core], ["i0", "i1", "i3", "i4", "i5", "i6", "i9"])
        self.assertEqual([query.id for query in ranged], ["i0", "i1", "i2"])
        self.assertEqual([query.id for query in self_queries], ["i0", "i1"])

    def test_queries_avoid_unsupported_between_syntax(self):
        for query in lade_llm_context_queries.CONTEXT_QUERIES.values():
            self.assertNotIn(" BETWEEN ", query.cypher.upper(), query.id)

    def test_build_params_adds_immediate_windows(self):
        args = make_args(step_index=100, time_minute=600.0, history_steps=20, event_window_minutes=45.0)
        query = lade_llm_context_queries.CONTEXT_QUERIES["i2"]

        params = lade_llm_context_queries.build_params(query, args, "drone-7")

        self.assertEqual(params["run_id"], "run-a")
        self.assertEqual(params["drone_id"], "drone-7")
        self.assertEqual(params["step_index"], 100)
        self.assertEqual(params["start_step"], 81)
        self.assertEqual(params["time_minute"], 600.0)
        self.assertEqual(params["start_time"], 555.0)

    def test_execute_context_returns_llm_context_and_metrics(self):
        driver = FakeDriver()
        args = make_args(drone_ids="drone-1")
        queries = lade_llm_context_queries.select_queries("i0,i1")
        clock_values = iter([10.0, 10.01, 10.02, 10.03, 10.04, 10.05])

        context, records = lade_llm_context_queries.execute_context(
            driver,
            queries,
            args,
            "drone-1",
            context_index=0,
            clock=lambda: next(clock_values),
        )

        self.assertEqual(context["drone_id"], "drone-1")
        self.assertEqual(set(context["queries"]), {"current_drone_state", "latest_agent_state"})
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["elapsed_ms"], 10.0)
        self.assertEqual(records[0]["row_count"], 2)
        self.assertIn(("session", {}), driver.calls)

    def test_fetch_step_time_minute_reads_step_node(self):
        driver = FakeDriver(rows=[{"time_minute": 488}])
        args = make_args(time_minute=None, step_index=100)

        time_minute = lade_llm_context_queries.fetch_step_time_minute(driver, args)

        self.assertEqual(time_minute, 488.0)
        _, params = driver.calls[1]
        self.assertEqual(params["run_id"], "run-a")
        self.assertEqual(params["step_index"], 100)

    def test_fetch_step_time_minute_fails_when_step_missing(self):
        driver = FakeDriver(rows=[])
        args = make_args(time_minute=None, step_index=999)

        with self.assertRaises(SystemExit):
            lade_llm_context_queries.fetch_step_time_minute(driver, args)

    def test_resolve_time_args_skips_fetch_when_queries_do_not_need_time(self):
        driver = FakeDriver(rows=[])
        args = make_args(time_minute=None, step_index=None, queries="i0")
        queries = lade_llm_context_queries.select_queries("i0")

        resolved = lade_llm_context_queries.resolve_time_args(driver, args, queries)

        self.assertIsNone(resolved.time_minute)
        self.assertEqual(driver.calls, [])

    def test_run_context_benchmark_writes_logs_and_summary(self):
        driver = FakeDriver()
        with tempfile.TemporaryDirectory() as tmpdir:
            args = make_args(
                drone_ids="drone-1,drone-2",
                queries="i0,i1",
                query_log_jsonl=Path(tmpdir) / "query.jsonl",
                context_jsonl=Path(tmpdir) / "context.jsonl",
                summary_json=Path(tmpdir) / "summary.json",
            )
            queries = lade_llm_context_queries.select_queries(args.queries)

            contexts, records, summary = lade_llm_context_queries.run_context_benchmark(driver, queries, args)

            self.assertEqual(len(contexts), 2)
            self.assertEqual(len(records), 4)
            self.assertEqual(summary["context_count"], 2)
            self.assertEqual(summary["total_queries"], 4)
            self.assertEqual(len(args.query_log_jsonl.read_text(encoding="utf-8").splitlines()), 4)
            self.assertEqual(len(args.context_jsonl.read_text(encoding="utf-8").splitlines()), 2)
            self.assertTrue(args.summary_json.exists())

    def test_load_custom_queries_infers_params(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "queries.json"
            path.write_text(
                json.dumps(
                    {
                        "queries": [
                            {
                                "id": "custom",
                                "cypher": "MATCH (n {run_id: $run_id, id: $id}) RETURN n LIMIT $limit",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            queries = lade_llm_context_queries.load_custom_queries(path)

        self.assertEqual(queries["custom"].required_params, ("id", "limit", "run_id"))


if __name__ == "__main__":
    unittest.main()
