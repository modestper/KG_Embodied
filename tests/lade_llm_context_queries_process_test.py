"""Exercise real spawned workers without connecting to an experiment database."""

import json
import multiprocessing
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import lade_llm_context_queries as shared
from scripts import lade_llm_context_queries_process as process


class FakeSession:
    def __init__(self, driver):
        self.driver = driver
        self.query_index = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, cypher, **params):
        self.query_index += 1
        if cypher == "FAIL":
            raise RuntimeError("intentional query failure")
        return iter([{
            "pid": os.getpid(),
            "driver_pid": self.driver.pid,
            "session_index": self.driver.session_index,
            "query_index": self.query_index,
        }])


class FakeDriver:
    def __init__(self, barrier, close_log):
        self.pid = os.getpid()
        self.barrier = barrier
        self.close_log = close_log
        self.session_index = 0

    def __getstate__(self):
        raise AssertionError("A driver must never be sent between processes")

    def session(self, **kwargs):
        if self.session_index == 0:
            # Each of two workers must enter a session before either can finish.
            self.barrier.wait(timeout=20)
        self.session_index += 1
        return FakeSession(self)

    def close(self):
        with open(self.close_log, "a", encoding="utf-8") as handle:
            handle.write(f"{os.getpid()}\n")


class FakeDriverFactory:
    def __init__(self, barrier, close_log):
        self.barrier = barrier
        self.close_log = close_log

    def __call__(self, uri, user, password, max_connection_pool_size=None):
        if max_connection_pool_size != 1:
            raise AssertionError("Expected a per-process connection pool of size 1")
        return FakeDriver(self.barrier, self.close_log)


def make_args(*extra):
    return process.parse_args([
        "--run-id", "offline-test", "--drone-ids", "drone-1,drone-2",
        "--step-index", "100", "--time-minute", "600",
        "--max-workers", "2", "--repeat", "2", "--progress-every", "0",
        *extra,
    ])


class ProcessContextBenchmarkTest(unittest.TestCase):
    def test_real_processes_preserve_catalog_failures_outputs_and_close_drivers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = make_args(
                "--context-jsonl", str(root / "contexts.jsonl"),
                "--query-log-jsonl", str(root / "queries.jsonl"),
                "--summary-json", str(root / "summary.json"),
            )
            queries = shared.select_queries("all")
            self.assertEqual(len(queries), 56)
            # Custom queries must also survive spawn pickling and record failures.
            queries.append(shared.ContextQuery(
                id="custom_fail", name="custom_fail", category="custom",
                required_params=(), description="offline failure", cypher="FAIL",
            ))
            close_log = root / "closed.txt"
            factory = FakeDriverFactory(multiprocessing.get_context("spawn").Barrier(2), str(close_log))
            contexts, records, summary = process.run_context_benchmark(queries, args, driver_factory=factory)

            self.assertEqual(len(contexts), 4)
            self.assertEqual(len(records), 4 * 57)
            self.assertEqual(summary["success_count"], 4 * 56)
            self.assertEqual(summary["failure_count"], 4)
            self.assertEqual(summary["observed_worker_count"], 2)
            self.assertNotIn(os.getpid(), summary["worker_pids"])
            self.assertEqual(summary["process_start_method"], "spawn")
            self.assertEqual({c["context_index"] for c in contexts}, {0, 1, 2, 3})
            self.assertEqual(sorted(c["drone_id"] for c in contexts), ["drone-1"] * 2 + ["drone-2"] * 2)
            session_indices = []
            for context in contexts:
                self.assertEqual(context["concurrency_mode"], "process")
                for index, query in enumerate(queries[:-1], start=1):
                    row = context["queries"][query.name][0]
                    self.assertEqual(row["pid"], context["worker_pid"])
                    self.assertEqual(row["driver_pid"], context["worker_pid"])
                    self.assertEqual(row["query_index"], index)
                    session_indices.append(row["session_index"])
                self.assertEqual(context["queries"]["custom_fail"], [])
            self.assertGreater(max(session_indices), 1)  # A worker reuses its driver.
            for record in records:
                self.assertIn(record["worker_pid"], summary["worker_pids"])
                if record["query_id"] == "custom_fail":
                    self.assertFalse(record["success"])
                    self.assertIn("intentional query failure", record["error"])

            logged_contexts = [json.loads(line) for line in args.context_jsonl.read_text().splitlines()]
            logged_records = [json.loads(line) for line in args.query_log_jsonl.read_text().splitlines()]
            self.assertEqual(logged_contexts, contexts)
            self.assertEqual(logged_records, records)
            self.assertEqual(json.loads(args.summary_json.read_text()), summary)
            self.assertEqual(sorted(map(int, close_log.read_text().splitlines())), summary["worker_pids"])

    def test_missing_parameters_fail_before_starting_processes(self):
        args = make_args()
        args.step_index = None
        with patch.object(process, "ProcessPoolExecutor") as pool:
            with self.assertRaisesRegex(SystemExit, "step_index"):
                process.run_context_benchmark(shared.select_queries("i1"), args)
            pool.assert_not_called()

    def test_cli_retains_thread_entry_options_and_uses_separate_outputs(self):
        args = process.parse_args([])
        with patch("sys.argv", ["lade_llm_context_queries.py"]):
            original = shared.parse_args()
        self.assertEqual(set(vars(args)), set(vars(original)))
        for option in ("query_log_jsonl", "context_jsonl", "summary_json"):
            self.assertNotEqual(getattr(args, option), getattr(original, option))
        self.assertEqual(args.max_connection_pool_size, 1)
        disabled = process.parse_args(["--no-query-log", "--no-context-log"])
        self.assertIsNone(disabled.query_log_jsonl)
        self.assertIsNone(disabled.context_jsonl)

    def test_dry_run_uses_shared_templates_without_a_driver_or_pool(self):
        argv = ["process.py", "--dry-run", "--queries", "all", "--run-id", "offline",
                "--step-index", "10", "--time-minute", "10"]
        with patch("sys.argv", argv), patch.object(shared, "make_driver") as driver, \
                patch.object(process, "ProcessPoolExecutor") as pool, patch("builtins.print") as output:
            process.main()
            driver.assert_not_called()
            pool.assert_not_called()
            self.assertEqual(output.call_count, 56)
            ids = [json.loads(call.args[0])["query_id"] for call in output.call_args_list]
            self.assertEqual(ids, [f"i{index}" for index in range(56)])

    def test_list_queries_does_not_start_processes_or_connect(self):
        with patch("sys.argv", ["process.py", "--list-queries"]), \
                patch.object(shared, "make_driver") as driver, \
                patch.object(process, "ProcessPoolExecutor") as pool, patch("builtins.print"):
            process.main()
            driver.assert_not_called()
            pool.assert_not_called()


if __name__ == "__main__":
    unittest.main()
