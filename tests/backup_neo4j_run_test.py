import gzip
import json
import tempfile
import unittest
from pathlib import Path

from scripts.backup_neo4j_run import sha256_file, stats_payload, write_jsonl_gzip
from scripts.copy_neo4j_runs import GraphStats


class BackupNeo4jRunTest(unittest.TestCase):
    def test_write_jsonl_gzip_preserves_records(self):
        records = [
            {"source_element_id": "1", "properties": {"name": "杭州"}},
            {"source_element_id": "2", "properties": {"value": 2}},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nodes.jsonl.gz"
            count = write_jsonl_gzip(path, records)
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                restored = [json.loads(line) for line in handle]

            self.assertEqual(count, 2)
            self.assertEqual(restored, records)
            self.assertEqual(len(sha256_file(path)), 64)

    def test_stats_payload_uses_json_compatible_label_groups(self):
        stats = GraphStats(
            node_counts={("AgentState",): 10, ("Run",): 1},
            relationship_counts={"IN_RUN": 10},
            boundary_relationship_counts={},
        )

        payload = stats_payload(stats)

        self.assertEqual(payload["total_nodes"], 11)
        self.assertEqual(payload["node_counts"][0]["labels"], ["AgentState"])
        json.dumps(payload)


if __name__ == "__main__":
    unittest.main()
