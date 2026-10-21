import argparse
import unittest

from scripts import lade_llm_context_queries as base
from scripts import lade_llm_context_queries_stages as stages


class FakeSummary:
    result_available_after = 7
    result_consumed_after = 3


class FakeResult:
    def __iter__(self):
        return iter([{"name": "row-a"}, {"name": "row-b"}])

    def consume(self):
        return FakeSummary()


class FakeSession:
    def __init__(self):
        self.closed = False

    def run(self, query, **params):
        return FakeResult()

    def close(self):
        self.closed = True


class FakeDriver:
    def __init__(self):
        self.session_instance = FakeSession()

    def session(self, **kwargs):
        return self.session_instance


def make_args(**overrides):
    values = {
        "uri": "bolt://127.0.0.1:17687",
        "database": None,
        "run_id": "run-a",
        "drone_id": [],
        "drone_ids": "drone-1",
        "step_index": 100,
        "time_minute": 600.0,
        "history_steps": 20,
        "event_window_minutes": 30.0,
        "deadline_horizon_minutes": 60.0,
        "limit": 10,
        "repeat": 1,
        "max_workers": 1,
        "param": [],
        "query_log_jsonl": None,
        "context_jsonl": None,
        "summary_json": None,
        "include_cypher": False,
        "print_context": False,
        "progress_every": 0,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class LaDeLlmContextQueryStagesTest(unittest.TestCase):
    def test_extended_scenario_preserves_scopes_and_stage_measurements(self):
        queries = base.select_queries("energy_safety")
        context, records = stages.execute_context(FakeDriver(), queries, make_args(), "drone-1", 0)
        self.assertEqual(len(records), 8)
        self.assertEqual(context["query_temporal_scopes"]["i38"], "current")
        self.assertTrue(all(record["success"] for record in records))
        self.assertTrue(all(record["server_query_ms"] == 10 for record in records))
        self.assertTrue(all(record["temporal_scope"] for record in records))

    def test_server_timing_sums_bolt_fields(self):
        available, consumed, total, supported = stages.server_timing(FakeSummary())

        self.assertEqual(available, 7)
        self.assertEqual(consumed, 3)
        self.assertEqual(total, 10)
        self.assertTrue(supported)

    def test_execute_context_records_all_stage_groups(self):
        driver = FakeDriver()
        query = base.CONTEXT_QUERIES["i0"]

        context, records = stages.execute_context(
            driver,
            [query],
            make_args(),
            "drone-1",
            0,
        )

        self.assertTrue(driver.session_instance.closed)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["server_query_ms"], 10)
        self.assertEqual(records[0]["row_count"], 2)
        self.assertTrue(records[0]["server_timing_supported"])
        for field in stages.DIRECT_QUERY_STAGE_FIELDS:
            self.assertIsNotNone(records[0][field])
        self.assertIn("worker_queue_ms", context["timings_ms"])
        self.assertEqual(context["timings_ms"]["queries_server_sum_ms"], 10)

    def test_summarize_reports_overall_and_per_query_averages(self):
        records = []
        for value in (10.0, 20.0):
            record = {
                "query_id": "i0",
                "success": True,
                "server_timing_supported": True,
                "row_count": 1,
            }
            for field in stages.DIRECT_QUERY_STAGE_FIELDS:
                record[field] = value
            for field in stages.SERVER_QUERY_STAGE_FIELDS:
                record[field] = value / 2
            for field in stages.ESTIMATED_QUERY_STAGE_FIELDS:
                record[field] = value / 2
            records.append(record)
        context_timing = {field: 1.0 for field in stages.CONTEXT_STAGE_FIELDS}
        contexts = [{"run_id": "run-a", "timings_ms": context_timing}]

        summary = stages.summarize(records, contexts, 100.0, {"wall_ms": 100.0})

        self.assertEqual(summary["direct_stage_ms"]["client_query_total_ms"]["avg_ms"], 15.0)
        self.assertEqual(
            summary["by_query"]["i0"]["server_stage_ms"]["server_query_ms"]["avg_ms"],
            7.5,
        )
        self.assertEqual(summary["throughput_qps"], 20.0)


if __name__ == "__main__":
    unittest.main()
