"""CPU allocation and opt-in real Ray actor checks without database access."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import lade_llm_context_queries as shared
from scripts import lade_llm_context_queries_ray as benchmark


class FakeSession:
    def __init__(self, driver):
        self.driver = driver
        self.index = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, cypher, **params):
        self.index += 1
        if cypher == "FAIL":
            raise RuntimeError("intentional query failure")
        return iter([{"pid": os.getpid(), "driver_pid": self.driver.pid,
                      "cpu_ids": sorted(os.sched_getaffinity(0)),
                      "query_index": self.index, "session_index": self.driver.sessions}])


class FakeDriver:
    def __init__(self, close_log):
        self.pid = os.getpid()
        self.close_log = close_log
        self.sessions = 0

    def __getstate__(self):
        raise AssertionError("Driver must remain inside its actor")

    def session(self, **kwargs):
        self.sessions += 1
        return FakeSession(self)

    def close(self):
        with open(self.close_log, "a", encoding="utf-8") as handle:
            handle.write(f"{self.pid}\n")


class FakeDriverFactory:
    def __init__(self, close_log):
        self.close_log = close_log

    def __call__(self, uri, user, password, max_connection_pool_size=None):
        if max_connection_pool_size != 1:
            raise AssertionError("Expected one connection per actor")
        return FakeDriver(self.close_log)


class RayContextQueryTest(unittest.TestCase):
    def test_default_has_100_actors_and_100_drone_ids(self):
        args = benchmark.parse_args([])
        self.assertEqual(args.max_workers, 100)
        self.assertEqual(shared.resolve_drone_ids(args), [f"drone-{i}" for i in range(1, 101)])
        self.assertEqual(args.max_connection_pool_size, 1)
        self.assertNotEqual(args.summary_json, shared.DEFAULT_SUMMARY_PATH)

    def test_explicit_drone_selection_is_preserved(self):
        args = benchmark.parse_args(["--max-workers", "2", "--drone-id", "drone-7",
                                     "--no-query-log", "--no-context-log"])
        self.assertEqual(shared.resolve_drone_ids(args), ["drone-7"])
        self.assertIsNone(args.query_log_jsonl)
        self.assertIsNone(args.context_jsonl)

    def test_cpu_selection_skips_smt_siblings_and_rejects_unavailable_cores(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # Logical CPUs 0/2 share physical core 0; 1/3 share core 1.
            for cpu in range(4):
                topology = root / f"cpu{cpu}" / "topology"
                topology.mkdir(parents=True)
                (topology / "physical_package_id").write_text("0")
                (topology / "core_id").write_text(str(cpu % 2))
            options = {"allowed_cpus": {0, 1, 2, 3}, "topology_root": root}
            self.assertEqual(benchmark.choose_cpu_ids(2, **options), [0, 1])
            self.assertEqual(benchmark.choose_cpu_ids(2, "2-3", **options), [2, 3])
            with self.assertRaisesRegex(SystemExit, "超线程"):
                benchmark.choose_cpu_ids(2, "0,2", **options)
            with self.assertRaisesRegex(SystemExit, "只有 2"):
                benchmark.choose_cpu_ids(3, **options)
            with self.assertRaisesRegex(SystemExit, "允许范围"):
                benchmark.choose_cpu_ids(2, "0,4", **options)
            with self.assertRaisesRegex(SystemExit, "恰好 2"):
                benchmark.choose_cpu_ids(2, "0", **options)

    def test_invalid_cpu_ranges_are_rejected(self):
        for value in ("0,0", "3-1", "-1", "a", "0-1-2", ""):
            with self.subTest(value=value), self.assertRaises(SystemExit):
                benchmark.parse_cpu_ids(value)

    def test_missing_query_parameters_fail_before_ray_startup(self):
        args = benchmark.parse_args(["--run-id", "offline", "--max-workers", "2"])
        args.step_index = None
        with patch.object(benchmark, "load_ray") as load:
            with self.assertRaisesRegex(SystemExit, "step_index"):
                benchmark.run_context_benchmark(shared.select_queries("i1"), args)
            load.assert_not_called()

    def test_dry_run_does_not_load_ray_or_connect(self):
        argv = ["ray.py", "--dry-run", "--queries", "all", "--drone-id", "drone-1",
                "--run-id", "offline", "--step-index", "100", "--time-minute", "600"]
        with patch("sys.argv", argv), patch.object(benchmark, "load_ray") as load, \
                patch.object(shared, "make_driver") as driver, patch("builtins.print") as output:
            benchmark.main()
            self.assertEqual(output.call_count, 56)
            load.assert_not_called()
            driver.assert_not_called()

    def test_list_queries_does_not_load_ray_or_connect(self):
        with patch("sys.argv", ["ray.py", "--list-queries"]), \
                patch.object(benchmark, "load_ray") as load, \
                patch.object(shared, "make_driver") as driver, patch("builtins.print"):
            benchmark.main()
            load.assert_not_called()
            driver.assert_not_called()


@unittest.skipUnless(os.environ.get("LADE_CONTEXT_RAY_TEST") == "1", "set LADE_CONTEXT_RAY_TEST=1 for real actors")
class RayContextQueryIntegrationTest(unittest.TestCase):
    def test_two_real_actors_use_distinct_cores_and_preserve_results(self):
        import ray

        parent_affinity = os.sched_getaffinity(0)
        with tempfile.TemporaryDirectory(prefix="lade-ray-query-test-", dir="/tmp") as directory:
            root = Path(directory)
            args = benchmark.parse_args([
                "--run-id", "offline", "--max-workers", "2", "--repeat", "3",
                "--step-index", "100", "--time-minute", "600", "--progress-every", "0",
                "--context-jsonl", str(root / "contexts.jsonl"),
                "--query-log-jsonl", str(root / "queries.jsonl"),
                "--summary-json", str(root / "summary.json"),
            ])
            queries = shared.select_queries("all")
            queries.append(shared.ContextQuery(
                id="custom_fail", name="custom_fail", category="custom",
                required_params=(), description="offline failure", cypher="FAIL"))
            close_log = root / "closed.txt"
            contexts, records, summary = benchmark.run_context_benchmark(
                queries, args, driver_factory=FakeDriverFactory(str(close_log)))
            self.assertFalse(ray.is_initialized())
            self.assertEqual(os.sched_getaffinity(0), parent_affinity)
            self.assertEqual(len(contexts), 6)
            self.assertEqual(len(records), 6 * 57)
            self.assertEqual(summary["success_count"], 6 * 56)
            self.assertEqual(summary["failure_count"], 6)
            self.assertEqual(summary["started_worker_count"], 2)
            self.assertEqual(summary["observed_worker_count"], 2)
            self.assertEqual(len(set(summary["cpu_ids"])), 2)
            self.assertNotIn(os.getpid(), summary["worker_pids"])
            self.assertEqual(summary["cleanup_errors"], [])
            self.assertFalse(summary["system_cpu_exclusivity"])
            for info in summary["worker_info"]:
                self.assertEqual(info["affinity_cpus"], [info["cpu_id"]])
                self.assertEqual(info["ray_assigned_resources"].get("CPU"), 1)
                self.assertGreater(info["pinned_thread_count"], 1)
            self.assertEqual(len({(i["physical_socket"], i["physical_core"]) for i in summary["worker_info"]}), 2)
            sessions = []
            for context in contexts:
                self.assertEqual(context["affinity_cpus"], [context["cpu_id"]])
                for index, query in enumerate(queries[:-1], start=1):
                    row = context["queries"][query.name][0]
                    self.assertEqual(row["pid"], context["worker_pid"])
                    self.assertEqual(row["driver_pid"], context["worker_pid"])
                    self.assertEqual(row["cpu_ids"], [context["cpu_id"]])
                    self.assertEqual(row["query_index"], index)
                    sessions.append(row["session_index"])
                self.assertEqual(context["queries"]["custom_fail"], [])
            self.assertGreater(max(sessions), 1)
            for record in records:
                self.assertEqual(record["concurrency_mode"], "ray")
                self.assertEqual(record["affinity_cpus"], [record["cpu_id"]])
                if record["query_id"] == "custom_fail":
                    self.assertIn("intentional query failure", record["error"])
            self.assertEqual(sorted(map(int, close_log.read_text().splitlines())), summary["worker_pids"])
            self.assertEqual([json.loads(line) for line in args.context_jsonl.read_text().splitlines()], contexts)
            self.assertEqual([json.loads(line) for line in args.query_log_jsonl.read_text().splitlines()], records)
            self.assertEqual(json.loads(args.summary_json.read_text()), summary)
            self.assertAlmostEqual(sum(summary[k] for k in ("ray_startup_ms", "query_phase_wall_ms", "ray_cleanup_ms")),
                                   summary["wall_ms"], delta=0.005)


if __name__ == "__main__":
    unittest.main()
