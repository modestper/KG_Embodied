import argparse
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from scripts import build_neo4j_scale_dataset


CSV_TEXT = """order_id,region_id,city,courier_id,lng,lat,aoi_id,aoi_type,accept_time,accept_gps_time,accept_gps_lng,accept_gps_lat,delivery_time,delivery_gps_time,delivery_gps_lng,delivery_gps_lat,ds
3322376,31,Jilin,4849,126.56526,43.84112,94,14,09-25 08:08:00,09-25 08:08:00,126.56614,43.87092,09-25 11:32:00,09-25 11:32:00,126.56919,43.84248,0925
3322377,32,Jilin,4850,126.56526,43.84112,95,14,09-25 08:09:00,09-25 08:09:00,126.56614,43.87092,09-25 11:33:00,09-25 11:33:00,126.56919,43.84248,0925
"""


def make_args(**overrides):
    values = {
        "csv_path": None,
        "city": "Jilin",
        "run_id": "run-scale",
        "id_prefix": "scale-",
        "limit": None,
        "batch_size": 1,
        "dry_run": True,
        "create_schema": False,
        "database": None,
        "progress_every": 0,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class BuildNeo4jScaleDatasetTest(unittest.TestCase):
    def test_parse_time_to_minute(self):
        self.assertEqual(build_neo4j_scale_dataset.parse_time_to_minute("09-25 08:08:00"), 488)
        self.assertIsNone(build_neo4j_scale_dataset.parse_time_to_minute(""))

    def test_row_to_task_uses_prefixed_ids(self):
        row = {
            "order_id": "3322376",
            "region_id": "31",
            "city": "Jilin",
            "courier_id": "4849",
            "accept_time": "09-25 08:08:00",
            "delivery_time": "09-25 11:32:00",
            "accept_gps_lng": "126.56614",
            "accept_gps_lat": "43.87092",
            "delivery_gps_lng": "126.56919",
            "delivery_gps_lat": "43.84248",
        }

        task = build_neo4j_scale_dataset.row_to_task(row, 1, "run-scale", "scale-")

        self.assertEqual(task["id"], "scale-task-3322376-1")
        self.assertEqual(task["courier_id"], "scale-courier-4849")
        self.assertEqual(task["accept_minute"], 488)
        self.assertEqual(task["delivery_minute"], 692)
        self.assertEqual(task["pickup_lng"], 126.56614)

    def test_batched_csv_tasks_reads_limit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "delivery.csv"
            path.write_text(CSV_TEXT, encoding="utf-8")

            batches = list(
                build_neo4j_scale_dataset.batched_csv_tasks(
                    path,
                    run_id="run-scale",
                    limit=1,
                    batch_size=10,
                    id_prefix="scale-",
                )
            )

        self.assertEqual(len(batches), 1)
        self.assertEqual(len(batches[0]), 1)
        self.assertEqual(batches[0][0]["id"], "scale-task-3322376-1")

    def test_dry_run_prints_estimated_scale(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "delivery.csv"
            path.write_text(CSV_TEXT, encoding="utf-8")
            args = make_args(csv_path=path, limit=2)
            output = io.StringIO()

            with redirect_stdout(output):
                build_neo4j_scale_dataset.import_csv(None, args)

        text = output.getvalue()
        self.assertIn("模式: dry-run", text)
        self.assertIn("任务行数: 2", text)
        self.assertIn("预计 Event 节点: 4", text)

    def test_estimate_scale_counts_task_and_event_nodes(self):
        estimate = build_neo4j_scale_dataset.estimate_scale(333_334)

        self.assertEqual(estimate["tasks"], 333_334)
        self.assertEqual(estimate["events"], 666_668)
        self.assertEqual(estimate["minimum_nodes_without_region_courier"], 1_000_003)


if __name__ == "__main__":
    unittest.main()
