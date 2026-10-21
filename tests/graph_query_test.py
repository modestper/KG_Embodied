import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from neo4j import Record
from neo4j.exceptions import ServiceUnavailable

from scripts.run_dynamic_sim import DynamicHandler
from simulation.graph_query import (
    GraphQueryError, GraphQueryService, GraphQuerySettings,
    MAX_REQUEST_BYTES, json_value, read_statement,
)


class ReadStatementTest(unittest.TestCase):
    def test_accepts_regular_reads_and_comments(self):
        cases = [
            "MATCH (d:Drone) RETURN d.id LIMIT $limit",
            "RETURN 'CREATE; DELETE', \"MERGE\", $create",
            "/* outer /* nested */ comment */ MATCH (n) RETURN n; // tail",
            "CALL { RETURN 1 AS x } RETURN x",
            "CALL (n) { WITH n RETURN n AS value } RETURN value",
            "CALL db.labels() YIELD label RETURN label",
            "CALL `db`.`propertyKeys`() YIELD propertyKey RETURN propertyKey",
            "RETURN point.distance(point({x: 0, y: 0}), point({x: 1, y: 1}))",
            "SHOW INDEXES YIELD name, state RETURN name, state",
            "SHOW ALL CONSTRAINTS",
            "MATCH (n) RETURN n.`create`, n.set, n.type",
            "EXPLAIN MATCH (n) RETURN n",
            "// comment\nPROFILE MATCH (n) RETURN n",
        ]
        for query in cases:
            with self.subTest(query=query):
                actual, explain = read_statement(query)
                self.assertTrue(actual)
                self.assertIn("EXPLAIN", explain)

    def test_rejects_writes_admin_and_extension_calls(self):
        cases = [
            "CREATE (n)", "MATCH (n) DELETE n", "MATCH (n) SET n.x=1 RETURN n",
            "MATCH (n) DETACH DELETE n", "MERGE (n:X) RETURN n", "DROP INDEX x",
            "RETURN 1; MATCH (n) DELETE n", "EXPLAIN CREATE (n)",
            "CALL { CREATE (n) RETURN n } RETURN n", "CALL apoc.cypher.run('RETURN 1', {})",
            "CALL apoc.load.json('https://example.com')", "RETURN apoc.load.json('file:///secret')",
            "RETURN `apoc`.`load`.`json`('x')", "CALL `apoc.load.json`", "SHOW TRANSACTIONS",
            "RETURN `apoc.load.json`('file:///x')",
            "SHOW DATABASES", "USE neo4j MATCH (n) RETURN n", "CYPHER 5 RETURN 1",
            "LOAD CSV FROM 'file:///a' AS row RETURN row", "/* unfinished", "RETURN 'unfinished",
            "RETURN 1; ;", "", None,
        ]
        for query in cases:
            with self.subTest(query=query), self.assertRaises(GraphQueryError):
                read_statement(query)

    def test_profile_is_replaced_only_in_preflight(self):
        actual, explain = read_statement("/* PROFILE */ PROFILE RETURN 'PROFILE' AS value;")
        self.assertEqual(actual, "/* PROFILE */ PROFILE RETURN 'PROFILE' AS value")
        self.assertEqual(explain, "/* PROFILE */ EXPLAIN RETURN 'PROFILE' AS value")

    def test_existing_i_queries_remain_accepted_without_being_used_as_ui_catalog(self):
        from scripts.lade_llm_context_queries import CONTEXT_QUERIES

        self.assertEqual(len(CONTEXT_QUERIES), 56)
        for item in CONTEXT_QUERIES.values():
            with self.subTest(query=item.id):
                read_statement(item.cypher)


class GraphQueryServiceTest(unittest.TestCase):
    def make_service(self, values=(1, 2), query_type="r"):
        self.summary = SimpleNamespace(
            result_available_after=2, result_consumed_after=3, database="neo4j",
            gql_status_objects=[SimpleNamespace(is_notification=True, gql_status="01N52",
                                               status_description="unknown property", raw_severity="WARNING",
                                               position={"line": 1, "column": 3})],
        )
        self.result = MagicMock()
        self.result.keys.return_value = ["value"]
        self.result.__iter__.return_value = iter(Record([("value", value)]) for value in values)
        self.result.consume.return_value = self.summary
        self.tx = MagicMock()
        self.tx.__enter__.return_value = self.tx
        preflight = Mock()
        preflight.consume.return_value = SimpleNamespace(query_type=query_type)
        self.tx.run.side_effect = [preflight, self.result]
        self.session = MagicMock()
        self.session.__enter__.return_value = self.session
        self.session.begin_transaction.return_value = self.tx
        self.driver = Mock()
        self.driver.session.return_value = self.session
        return GraphQueryService(GraphQuerySettings(uri="bolt://localhost:61687", password="secret"), self.driver)

    def test_read_uses_preflight_and_rollback_and_returns_separate_timings(self):
        service = self.make_service()
        result = service.execute({"query": "RETURN $value AS value", "parameters": {"value": 1}})
        self.assertEqual(result["rows"], [[1], [2]])
        self.assertEqual(result["columns"], ["value"])
        self.assertEqual(result["timing"]["server_reported_ms"], 5)
        self.assertEqual(result["notifications"][0]["severity"], "WARNING")
        self.assertEqual(self.tx.run.call_args_list[0].args[0], "EXPLAIN RETURN $value AS value")
        self.assertEqual(self.tx.run.call_args_list[1].kwargs["parameters"], {"value": 1})
        self.tx.rollback.assert_called_once()
        self.tx.commit.assert_not_called()
        self.assertEqual(self.session.begin_transaction.call_args.kwargs["timeout"], 5.0)
        self.assertGreaterEqual(result["timing"]["backend_total_ms"], result["timing"]["query_client_ms"])

    def test_write_is_rejected_before_opening_session(self):
        service = self.make_service()
        with self.assertRaises(GraphQueryError):
            service.execute({"query": "CREATE (n)"})
        self.driver.session.assert_not_called()

    def test_database_preflight_is_authoritative(self):
        service = self.make_service(query_type="rw")
        with self.assertRaises(GraphQueryError) as caught:
            service.execute({"query": "RETURN 1"})
        self.assertEqual(caught.exception.status, 403)
        self.assertEqual(self.tx.run.call_count, 1)

    def test_result_row_cap_still_consumes_summary(self):
        service = self.make_service()
        result = service.execute({"query": "RETURN 1", "max_rows": 1})
        self.assertEqual(result["rows"], [[1]])
        self.assertTrue(result["truncated"])
        self.result.consume.assert_called_once()

    def test_byte_cap_and_unknown_server_timings(self):
        service = self.make_service(values=("large value",))
        self.summary.result_available_after = None
        with patch("simulation.graph_query.MAX_RESULT_BYTES", 2):
            result = service.execute({"query": "RETURN 1"})
        self.assertTrue(result["truncated"])
        self.assertEqual(result["row_count"], 0)
        self.assertIsNone(result["timing"]["server_reported_ms"])

    def test_rejects_invalid_payload_and_limits(self):
        service = self.make_service()
        cases = [None, [], {"query": "RETURN 1", "parameters": []},
                 {"query": "RETURN 1", "max_rows": 201}, {"query": "RETURN 1", "max_rows": True},
                 {"query": "RETURN 1", "timeout_seconds": 16}, {"query": "RETURN 1", "timeout_seconds": float("nan")},
                 {"query": "RETURN 1", "parameters": {"bad": float("inf")}}]
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(GraphQueryError):
                service.execute(payload)
        self.driver.session.assert_not_called()

    def test_no_config_or_parallel_query_is_refused(self):
        with self.assertRaises(GraphQueryError) as caught:
            GraphQueryService(GraphQuerySettings()).execute({"query": "RETURN 1"})
        self.assertEqual(caught.exception.status, 503)
        service = self.make_service()
        with service._slot, self.assertRaises(GraphQueryError) as caught:
            service.execute({"query": "RETURN 1"})
        self.assertEqual(caught.exception.status, 429)

    def test_connection_error_does_not_leak_credentials_and_releases_slot(self):
        service = self.make_service()
        self.tx.run.side_effect = ServiceUnavailable("bolt://user:secret@host")
        with self.assertRaises(GraphQueryError) as caught:
            service.execute({"query": "RETURN 1"})
        self.assertNotIn("secret", str(caught.exception))
        self.assertEqual(caught.exception.status, 503)
        self.assertTrue(service._slot.acquire(blocking=False))
        service._slot.release()

    def test_public_config_and_environment_fallback(self):
        with patch.dict("os.environ", {"NEO4J_URI": "bolt://host:1234", "NEO4J_PASSWORD": "writer-secret",
                                      "GRAPH_QUERY_URI": "bolt://reader:embedded-secret@host:61687",
                                      "GRAPH_QUERY_USER": "reader", "GRAPH_QUERY_PASSWORD": "reader-secret"}, clear=True):
            settings = GraphQuerySettings.from_env()
        self.assertEqual(settings.user, "reader")
        self.assertEqual(settings.password, "reader-secret")
        config = settings.public_config()
        self.assertEqual(config["target"], "bolt://host:61687")
        self.assertNotIn("secret", json.dumps(config))
        with patch.dict("os.environ", {"KG_BACKEND": "neo4j"}, clear=True):
            self.assertEqual(GraphQuerySettings.from_env().uri, "bolt://127.0.0.1:7687")

    def test_serializes_real_neo4j_graph_temporal_and_spatial_values(self):
        from neo4j.graph import Graph, Node, Path, Relationship
        from neo4j.spatial import CartesianPoint
        from neo4j.time import Date

        graph = Graph()
        start = Node(graph, "n1", 1, ["Drone"], {"id": "drone-1"})
        end = Node(graph, "n2", 2, ["Run"], {"id": "run-1"})
        edge = Relationship(graph, "r1", 1, {})
        edge._start_node, edge._end_node = start, end
        result = json_value({"node": start, "edge": edge, "path": Path(start, edge),
                             "date": Date(2026, 10, 21), "point": CartesianPoint((1, 2)), "nan": float("nan")})
        self.assertEqual(result["node"]["properties"]["id"], "drone-1")
        self.assertEqual(result["edge"]["start"], "n1")
        self.assertEqual(result["path"]["type"], "path")
        self.assertEqual(result["point"]["coordinates"], [1, 2])
        self.assertEqual(result["date"], "2026-10-21")
        json.dumps(result, allow_nan=False)


class GraphQueryHTTPTest(unittest.TestCase):
    def test_routes_body_limits_origin_checks_and_simulation_independence(self):
        service = Mock()
        service.config.return_value = {"enabled": True, "target": "bolt://localhost:61687"}
        service.execute.return_value = {"ok": True, "rows": [[1]], "timing": {"query_client_ms": 1}}

        class Handler(DynamicHandler):
            query_service = service

            def log_message(self, *args):
                pass

        marker = object()
        Handler.active_env = marker
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()

        def request(method, path, body=None, headers=None):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            connection.request(method, path, body, headers or {})
            response = connection.getresponse()
            status = response.status
            data = json.loads(response.read())
            connection.close()
            return status, data

        try:
            self.assertEqual(request("GET", "/api/graph-query/config")[0], 200)
            self.assertEqual(request("POST", "/api/graph-query", '{"query":"RETURN 1"}',
                                     {"Content-Type": "application/json"})[0], 200)
            self.assertIs(Handler.active_env, marker)
            self.assertEqual(service.execute.call_count, 1)
            self.assertEqual(request("POST", "/api/graph-query", "{}", {"Content-Type": "text/plain"})[0], 415)
            self.assertEqual(request("POST", "/api/graph-query", "{}", {"Content-Type": "application/json", "Origin": "https://foreign.example"})[0], 403)
            self.assertEqual(request("POST", "/api/graph-query", "x" * (MAX_REQUEST_BYTES + 1), {"Content-Type": "application/json"})[0], 413)
            self.assertEqual(request("POST", "/api/graph-query", "not json", {"Content-Type": "application/json"})[0], 400)
            self.assertEqual(request("POST", "/unknown", "{}")[0], 404)
            service.execute.side_effect = GraphQueryError("busy", 429, "query_busy")
            self.assertEqual(request("POST", "/api/graph-query", "{}", {"Content-Type": "application/json"})[0], 429)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_page_contains_manual_inputs_and_separate_timing_labels(self):
        from scripts.run_dynamic_sim import VIEWER_ROOT

        html = (VIEWER_ROOT / "dynamic.html").read_text(encoding="utf-8")
        for element in ("graphQueryPanel", "graphQueryCypher", "graphQueryParams", "graphQuerySubmit",
                        "graphQueryDbMs", "graphQueryClientMs", "graphQueryBrowserMs", "graphQueryExport"):
            self.assertIn(f'id="{element}"', html)


if __name__ == "__main__":
    unittest.main()
