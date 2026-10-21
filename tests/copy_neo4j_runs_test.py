import unittest

from scripts import copy_neo4j_runs


class CopyNeo4jRunsTest(unittest.TestCase):
    def test_quote_identifier_escapes_backticks(self):
        self.assertEqual(copy_neo4j_runs.quote_identifier("a`b"), "`a``b`")

    def test_normalize_run_ids_keeps_order_and_removes_duplicates(self):
        self.assertEqual(
            copy_neo4j_runs.normalize_run_ids(["run-b", " run-a ", "run-b"]),
            ["run-b", "run-a"],
        )

    def test_build_composite_uniqueness_constraint(self):
        statement = copy_neo4j_runs.build_constraint_statement(
            {
                "name": "task_key",
                "type": "UNIQUENESS",
                "entityType": "NODE",
                "labelsOrTypes": ["Task"],
                "properties": ["id", "run_id"],
            }
        )
        self.assertEqual(
            statement,
            "CREATE CONSTRAINT `task_key` IF NOT EXISTS "
            "FOR (n:`Task`) REQUIRE (n.`id`, n.`run_id`) IS UNIQUE",
        )

    def test_build_range_index_skips_constraint_backing_index(self):
        statement = copy_neo4j_runs.build_index_statement(
            {
                "name": "owned",
                "type": "RANGE",
                "entityType": "NODE",
                "labelsOrTypes": ["Task"],
                "properties": ["id"],
                "owningConstraint": "task_key",
            }
        )
        self.assertIsNone(statement)

    def test_build_relationship_range_index(self):
        statement = copy_neo4j_runs.build_index_statement(
            {
                "name": "assigned_time",
                "type": "RANGE",
                "entityType": "RELATIONSHIP",
                "labelsOrTypes": ["ASSIGNED"],
                "properties": ["time_minute"],
                "owningConstraint": None,
            }
        )
        self.assertEqual(
            statement,
            "CREATE RANGE INDEX `assigned_time` IF NOT EXISTS "
            "FOR ()-[n:`ASSIGNED`]-() ON (n.`time_minute`)",
        )

    def test_compare_stats_detects_differences(self):
        source = copy_neo4j_runs.GraphStats(
            {("Drone",): 2}, {"IN_RUN": 2}, {}
        )
        target = copy_neo4j_runs.GraphStats(
            {("Drone",): 1}, {"IN_RUN": 2}, {}
        )
        self.assertEqual(len(copy_neo4j_runs.compare_stats(source, target)), 1)

    def test_parse_args_defaults_to_preview(self):
        args = copy_neo4j_runs.parse_args(["--run-id", "run-a"])
        self.assertFalse(args.apply)
        self.assertFalse(args.verify_only)
        self.assertEqual(args.source_uri, "bolt://127.0.0.1:17687")
        self.assertEqual(args.target_uri, "bolt://127.0.0.1:37687")


if __name__ == "__main__":
    unittest.main()
