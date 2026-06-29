import io
import unittest
from contextlib import redirect_stdout

from scripts import query_neo4j


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def single(self):
        return self.rows[0]

    def __iter__(self):
        return iter(self.rows)


class FakeSession:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def run(self, query, **params):
        self.calls.append((query, params))
        if "MATCH (n) RETURN count(n)" in query:
            return FakeResult([{"count": 10}])
        if "MATCH ()-[r]->() RETURN count(r)" in query:
            return FakeResult([{"count": 20}])
        if "RETURN labels(n) AS labels" in query:
            return FakeResult([{"labels": ["Task"], "count": 5}, {"labels": ["Event"], "count": 5}])
        if "RETURN type(r) AS relationship_type" in query:
            return FakeResult([{"relationship_type": "IN_RUN", "count": 8}])
        if "MATCH (run:Run)" in query:
            return FakeResult([{"run_id": "run-a"}, {"run_id": "run-b"}])
        if "MATCH (n)-[:IN_RUN]->(:Run {id: $run_id})" in query:
            return FakeResult([{"labels": ["Task"], "count": 3}])
        if "MATCH (e:Event)-[:IN_RUN]->(:Run {id: $run_id})" in query:
            return FakeResult([{"type": "task_assigned", "time": "08:00:00", "task_id": "task-1", "drone_id": "drone-1"}])
        return FakeResult([])


class FakeDriver:
    def __init__(self):
        self.session_obj = FakeSession()
        self.closed = False

    def session(self):
        return self.session_obj

    def close(self):
        self.closed = True


class QueryNeo4jTest(unittest.TestCase):
    def test_summary_prints_global_and_run_scoped_counts(self):
        driver = FakeDriver()
        output = io.StringIO()

        with redirect_stdout(output):
            query_neo4j.print_summary(driver, run_id="run-a", recent_events=1)

        text = output.getvalue()
        self.assertIn("总节点数: 10", text)
        self.assertIn("总关系数: 20", text)
        self.assertIn("Run 列表", text)
        self.assertIn("run-a", text)
        self.assertIn("指定 run: run-a", text)
        self.assertIn("task_assigned", text)


if __name__ == "__main__":
    unittest.main()
