import json
import tempfile
import unittest
from pathlib import Path

from scripts import timed_neo4j_query


class FakeResult:
    def __iter__(self):
        return iter(
            [
                {"labels": ["Task"], "count": 3},
                {"labels": ["Event"], "count": 7},
            ]
        )


class FakeSession:
    def __init__(self):
        self.calls = []

    def run(self, query, **params):
        self.calls.append((query, params))
        return FakeResult()


class TimedNeo4jQueryTest(unittest.TestCase):
    def test_timed_query_prints_and_logs_elapsed_time(self):
        session = FakeSession()
        clock_values = iter([10.0, 10.125])

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "neo4j_query_logs.jsonl"
            rows, elapsed_ms = timed_neo4j_query.timed_query(
                session,
                name="label-counts",
                query="MATCH (n) RETURN labels(n) AS labels, count(n) AS count",
                params={"run_id": "run-a"},
                log_path=log_path,
                clock=lambda: next(clock_values),
                uri="bolt://127.0.0.1:17687",
            )

            self.assertEqual(elapsed_ms, 125.0)
            self.assertEqual(len(rows), 2)
            self.assertEqual(session.calls[0][1], {"run_id": "run-a"})
            record = json.loads(log_path.read_text(encoding="utf-8").strip())
            self.assertEqual(record["name"], "label-counts")
            self.assertEqual(record["elapsed_ms"], 125.0)
            self.assertEqual(record["row_count"], 2)
            self.assertEqual(record["params"], {"run_id": "run-a"})
            self.assertEqual(record["uri"], "bolt://127.0.0.1:17687")

    def test_parse_param_accepts_json_values(self):
        self.assertEqual(timed_neo4j_query.parse_param("limit=20"), ("limit", 20))
        self.assertEqual(timed_neo4j_query.parse_param("run_id=jilin-0925"), ("run_id", "jilin-0925"))


if __name__ == "__main__":
    unittest.main()
