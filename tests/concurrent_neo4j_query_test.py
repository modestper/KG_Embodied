import argparse
import tempfile
import unittest
from pathlib import Path

from scripts import concurrent_neo4j_query


class FakeResult:
    def __iter__(self):
        return iter([{"id": "drone-1"}, {"id": "task-1"}])


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
        "agents": 2,
        "queries_per_agent": 2,
        "max_workers": 2,
        "limit": 20,
        "agent_id_template": "drone-{agent}",
        "agent_id": [],
        "agent_ids": None,
        "preset": "agent-state",
        "cypher": None,
        "cypher_file": None,
        "param": [],
        "log_jsonl": None,
        "summary_json": None,
        "progress_every": 0,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class ConcurrentNeo4jQueryTest(unittest.TestCase):
    def test_build_query_params_adds_agent_fields(self):
        args = make_args(agent_id_template="agent-{agent_index}-{query}")

        params = concurrent_neo4j_query.build_query_params({}, args, agent_index=3, query_index=4)

        self.assertEqual(params["run_id"], "run-a")
        self.assertEqual(params["agent_id"], "agent-3-5")
        self.assertEqual(params["agent_index"], 3)
        self.assertEqual(params["agent_number"], 4)
        self.assertEqual(params["query_index"], 4)
        self.assertEqual(params["query_number"], 5)

    def test_build_query_params_cycles_explicit_agent_ids(self):
        args = make_args(agent_ids="drone-a,drone-b")

        params = concurrent_neo4j_query.build_query_params({}, args, agent_index=3, query_index=0)

        self.assertEqual(params["agent_id"], "drone-b")

    def test_execute_query_records_elapsed_and_row_count(self):
        driver = FakeDriver()
        args = make_args()
        clock_values = iter([10.0, 10.042])

        record = concurrent_neo4j_query.execute_query(
            driver,
            "agent-state",
            "MATCH (d:Drone {id: $agent_id}) RETURN d",
            {"agent_id": "drone-1", "run_id": "run-a"},
            args,
            agent_index=0,
            query_index=0,
            clock=lambda: next(clock_values),
        )

        self.assertTrue(record["success"])
        self.assertEqual(record["elapsed_ms"], 42.0)
        self.assertEqual(record["row_count"], 2)
        self.assertEqual(record["params"]["agent_id"], "drone-1")
        self.assertIn(("session", {}), driver.calls)

    def test_run_benchmark_writes_jsonl_and_summary(self):
        driver = FakeDriver()
        with tempfile.TemporaryDirectory() as tmpdir:
            args = make_args(
                log_jsonl=Path(tmpdir) / "latency.jsonl",
                summary_json=Path(tmpdir) / "summary.json",
            )

            records, summary = concurrent_neo4j_query.run_benchmark(driver, args)

            self.assertEqual(len(records), 4)
            self.assertEqual(summary["total_queries"], 4)
            self.assertEqual(summary["success_count"], 4)
            self.assertEqual(summary["failure_count"], 0)
            self.assertEqual(len(args.log_jsonl.read_text(encoding="utf-8").splitlines()), 4)
            self.assertTrue(args.summary_json.exists())

    def test_percentile_uses_linear_interpolation(self):
        values = [10.0, 20.0, 30.0, 40.0]

        self.assertEqual(concurrent_neo4j_query.percentile(values, 50), 25.0)
        self.assertEqual(concurrent_neo4j_query.percentile(values, 95), 38.5)


if __name__ == "__main__":
    unittest.main()
