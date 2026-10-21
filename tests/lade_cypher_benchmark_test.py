import argparse
import json
import re
import tempfile
import unittest
from pathlib import Path

from scripts import lade_cypher_benchmark


class FakeResult:
    def __iter__(self):
        return iter([{"id": "row-1"}, {"id": "row-2"}])


class FakeSession:
    def __init__(self, calls):
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def run(self, query, **params):
        self.calls.append((query, params))
        return FakeResult()


class FakeDriver:
    def __init__(self):
        self.calls = []

    def session(self, **kwargs):
        self.calls.append(("session", kwargs))
        return FakeSession(self.calls)


def make_args(**overrides):
    values = {
        "uri": "bolt://127.0.0.1:17687",
        "database": None,
        "run_id": "run-a",
        "templates": "q2",
        "template_file": None,
        "replace_default_templates": False,
        "list_templates": False,
        "agents": 2,
        "queries_per_agent": 1,
        "max_workers": 1,
        "max_connection_pool_size": None,
        "limit": 20,
        "drone_id_template": "drone-{agent}",
        "drone_id": [],
        "drone_ids": None,
        "start_step": 1,
        "end_step": 1000,
        "step_index": None,
        "step_window": 100,
        "step_stride": None,
        "start_time": 0.0,
        "end_time": 1440.0,
        "time_window_minutes": 60.0,
        "time_stride": None,
        "param": [],
        "log_jsonl": None,
        "summary_json": None,
        "no_log": False,
        "include_cypher": False,
        "dry_run": False,
        "progress_every": 0,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class LaDeCypherBenchmarkTest(unittest.TestCase):
    def test_select_templates_supports_range_and_category(self):
        templates = lade_cypher_benchmark.DEFAULT_TEMPLATES

        ranged = lade_cypher_benchmark.select_templates("q0-q2", templates)
        temporal = lade_cypher_benchmark.select_templates("temporal", templates)

        self.assertEqual([template.id for template in ranged], ["q0", "q1", "q2"])
        self.assertEqual(
            [template.id for template in temporal],
            ["q3", "q5", "q14", "q19", "q21", "q22"],
        )

    def test_default_catalog_contains_56_contiguous_read_only_templates(self):
        templates = lade_cypher_benchmark.DEFAULT_TEMPLATES

        self.assertEqual(len(templates), 56)
        self.assertEqual(list(templates), [f"q{number}" for number in range(56)])
        self.assertEqual(len({template.name for template in templates.values()}), 56)
        for template in templates.values():
            self.assertIn("run_id", template.required_params)
            self.assertNotIn(" BETWEEN ", template.cypher.upper(), template.id)
            self.assertEqual(
                set(template.required_params),
                set(lade_cypher_benchmark.infer_required_params(template.cypher)),
            )
            self.assertTrue(set(template.required_params) <= lade_cypher_benchmark.AUTO_PARAMS)
            self.assertIsNone(
                re.search(r"\b(CREATE|MERGE|SET|DELETE|REMOVE|DROP)\b", template.cypher, re.IGNORECASE),
                template.id,
            )

    def test_select_templates_supports_workload_pack_and_extended_range(self):
        templates = lade_cypher_benchmark.DEFAULT_TEMPLATES

        collaboration = lade_cypher_benchmark.select_templates(
            "collaboration_suite",
            templates,
        )
        quality = lade_cypher_benchmark.select_templates("q52-q55", templates)

        self.assertEqual(
            [template.id for template in collaboration],
            ["q32"] + [f"q{number}" for number in range(42, 49)],
        )
        self.assertEqual(
            [template.id for template in quality],
            ["q52", "q53", "q54", "q55"],
        )

    def test_build_query_params_generates_drone_and_step_window(self):
        args = make_args(drone_ids="drone-a,drone-b", step_stride=50)
        template = lade_cypher_benchmark.DEFAULT_TEMPLATES["q3"]

        params = lade_cypher_benchmark.build_query_params(
            template,
            args,
            agent_index=3,
            round_index=2,
            query_index=7,
        )

        self.assertEqual(params["run_id"], "run-a")
        self.assertEqual(params["drone_id"], "drone-b")
        self.assertEqual(params["start_step"], 101)
        self.assertEqual(params["end_step"], 200)
        self.assertEqual(params["query_number"], 8)

    def test_build_query_params_generates_time_window(self):
        args = make_args(start_time=480.0, end_time=720.0, time_window_minutes=30.0, time_stride=15.0)
        template = lade_cypher_benchmark.DEFAULT_TEMPLATES["q5"]

        params = lade_cypher_benchmark.build_query_params(
            template,
            args,
            agent_index=0,
            round_index=2,
            query_index=0,
        )

        self.assertEqual(params["start_time"], 510.0)
        self.assertEqual(params["end_time"], 540.0)

    def test_execute_query_records_elapsed_row_count_and_template(self):
        driver = FakeDriver()
        args = make_args()
        template = lade_cypher_benchmark.DEFAULT_TEMPLATES["q2"]
        clock_values = iter([10.0, 10.025])

        record = lade_cypher_benchmark.execute_query(
            driver,
            template,
            {"run_id": "run-a", "drone_id": "drone-1"},
            args,
            agent_index=0,
            round_index=0,
            query_index=0,
            clock=lambda: next(clock_values),
        )

        self.assertTrue(record["success"])
        self.assertEqual(record["template_id"], "q2")
        self.assertEqual(record["elapsed_ms"], 25.0)
        self.assertEqual(record["row_count"], 2)
        self.assertIn(("session", {}), driver.calls)

    def test_run_benchmark_writes_jsonl_and_summary(self):
        driver = FakeDriver()
        with tempfile.TemporaryDirectory() as tmpdir:
            args = make_args(
                templates="q1,q2",
                agents=2,
                queries_per_agent=2,
                log_jsonl=Path(tmpdir) / "latency.jsonl",
                summary_json=Path(tmpdir) / "summary.json",
            )
            templates = lade_cypher_benchmark.select_templates(
                args.templates,
                lade_cypher_benchmark.DEFAULT_TEMPLATES,
            )

            records, summary = lade_cypher_benchmark.run_benchmark(driver, templates, args)

            self.assertEqual(len(records), 8)
            self.assertEqual(summary["total_queries"], 8)
            self.assertEqual(summary["success_count"], 8)
            self.assertEqual(summary["failure_count"], 0)
            self.assertEqual(set(summary["by_template"]), {"q1", "q2"})
            self.assertEqual(len(args.log_jsonl.read_text(encoding="utf-8").splitlines()), 8)
            self.assertTrue(args.summary_json.exists())

    def test_load_template_file_infers_required_params(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "templates.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "id": "custom",
                            "cypher": "MATCH (n {id: $id}) RETURN n LIMIT $limit",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            templates = lade_cypher_benchmark.load_template_file(path)

        self.assertEqual(templates["custom"].required_params, ("id", "limit"))


if __name__ == "__main__":
    unittest.main()
