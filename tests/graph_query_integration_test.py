"""Opt-in reads against an existing graph; never starts a simulation or writes."""

import http.client
import json
import os
import threading
import unittest
from http.server import ThreadingHTTPServer

from scripts.run_dynamic_sim import DynamicHandler
from simulation.graph_query import GraphQueryService, GraphQuerySettings


@unittest.skipUnless(os.environ.get("GRAPH_QUERY_INTEGRATION_URI"), "set GRAPH_QUERY_INTEGRATION_URI for read-only live tests")
class GraphQueryIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = GraphQueryService(GraphQuerySettings(
            uri=os.environ["GRAPH_QUERY_INTEGRATION_URI"],
            user=os.environ.get("GRAPH_QUERY_USER", os.environ.get("NEO4J_USER", "neo4j")),
            password=os.environ.get("GRAPH_QUERY_PASSWORD", os.environ.get("NEO4J_PASSWORD", "")),
            database=os.environ.get("GRAPH_QUERY_DATABASE", "neo4j"),
        ))

    @classmethod
    def tearDownClass(cls):
        cls.service.close()

    def test_actual_read_summaries_graph_values_and_metadata(self):
        for query in (
            "RETURN 1 AS value, date('2026-10-21') AS day, point({x: 1, y: 2}) AS location",
            "MATCH (r:Run) RETURN r.id AS run_id LIMIT 3",
            "MATCH p=(d:Drone)-[edge:IN_RUN]->(r:Run) RETURN d, edge, p LIMIT 1",
            "SHOW INDEXES YIELD name, state RETURN name, state LIMIT 20",
            "CALL db.labels() YIELD label RETURN label LIMIT 20",
        ):
            with self.subTest(query=query):
                result = self.service.execute({"query": query, "timeout_seconds": 5, "max_rows": 20})
                self.assertTrue(result["ok"])
                self.assertGreater(result["row_count"], 0)
                self.assertEqual(result["database"], "neo4j")
                self.assertIn("query_client_ms", result["timing"])
                json.dumps(result, allow_nan=False)

    def test_http_to_actual_graph_without_starting_events(self):
        service = self.service

        class Handler(DynamicHandler):
            query_service = service

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=15)
        try:
            payload = {"query": "MATCH (r:Run) RETURN r.id AS run_id LIMIT $limit", "parameters": {"limit": 2}}
            connection.request("POST", "/api/graph-query", json.dumps(payload), {"Content-Type": "application/json"})
            response = connection.getresponse()
            result = json.loads(response.read())
            self.assertEqual(response.status, 200, result)
            self.assertTrue(result["ok"])
            self.assertGreater(result["row_count"], 0)
            self.assertLessEqual(result["row_count"], 2)
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
