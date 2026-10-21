"""Bounded, manually triggered read queries; independent of simulation writes.

This is a trusted local viewer tool, not an authenticated public Cypher API.
Use a database account with read-only privileges for network deployments.
"""

import json
import math
import os
import re
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit


MAX_REQUEST_BYTES = 65536
MAX_RESULT_BYTES = 1024 * 1024
MAX_ROWS = 200
MAX_TIMEOUT_SECONDS = 15
SAFE_PROCEDURES = {"DB.LABELS", "DB.RELATIONSHIPTYPES", "DB.PROPERTYKEYS"}
SAFE_NAMESPACED_FUNCTIONS = {"POINT.DISTANCE", "POINT.WITHINBBOX"}
FORBIDDEN = set("CREATE MERGE INSERT SET DELETE DETACH REMOVE DROP ALTER RENAME "
                "LOAD FOREACH GRANT DENY REVOKE TERMINATE START STOP ENABLE DISABLE "
                "TRANSACTIONS DATABASE DATABASES SERVER SERVERS USE CYPHER".split())
# Preserve token positions so EXPLAIN/PROFILE can be replaced without touching literals.
TOKEN = re.compile(
    r"(?P<space>\s+)|(?P<line>//[^\r\n]*)|"
    r"(?P<string>'(?:\\.|''|[^'\\])*'|\"(?:\\.|\"\"|[^\"\\])*\")|"
    r"(?P<identifier>`(?:``|[^`])*`)|(?P<parameter>\$[\w]+)|"
    r"(?P<word>[^\W\d]\w*)|(?P<symbol>.)", re.DOTALL,
)


class GraphQueryError(Exception):
    def __init__(self, message, status=400, code="invalid_request"):
        super().__init__(message)
        self.status = status
        self.code = code


def read_statement(query):
    """Conservative lexical guard, followed by the database's EXPLAIN check."""
    if not isinstance(query, str) or not query.strip() or len(query) > 32000:
        raise GraphQueryError("请输入一条不超过 32000 字符的只读 Cypher。")
    tokens = []
    offset = 0
    while offset < len(query):
        if query.startswith("/*", offset):
            depth = 1
            offset += 2
            while offset < len(query) and depth:
                if query.startswith("/*", offset):
                    depth += 1
                    offset += 2
                elif query.startswith("*/", offset):
                    depth -= 1
                    offset += 2
                else:
                    offset += 1
            if depth:
                raise GraphQueryError("块注释未结束。")
            continue
        match = TOKEN.match(query, offset)
        kind = match.lastgroup
        value = match.group()
        if kind not in ("space", "line", "string"):
            if kind == "symbol" and value in ("'", '"', "`", "\\"):
                raise GraphQueryError("字符串或标识符格式不正确。")
            tokens.append((value.upper(), kind, offset, match.end()))
        offset = match.end()
    if tokens and tokens[-1][0] == ";":
        position = tokens.pop()[2]
        query = query[:position] + query[position + 1:]
    if not tokens or any(token[0] == ";" for token in tokens):
        raise GraphQueryError("每次只能提交一条查询，不能包含多条语句。")
    first = tokens[0]
    base = tokens[1:] if first[0] in ("EXPLAIN", "PROFILE") else tokens
    if not base or base[0][0] not in {"MATCH", "OPTIONAL", "RETURN", "WITH", "UNWIND", "CALL", "SHOW"}:
        raise GraphQueryError("仅支持 MATCH、RETURN、WITH、UNWIND、只读 CALL 子查询及 SHOW 索引/约束。", 403, "read_only_required")
    for index, (value, kind, _start, _end) in enumerate(tokens):
        previous = tokens[index - 1][0] if index else None
        if kind == "identifier" and "." in value and index + 1 < len(tokens) and tokens[index + 1][0] == "(":
            raise GraphQueryError("不开放以单个转义名称调用的扩展函数或过程。", 403, "read_only_required")
        if kind == "word" and value in FORBIDDEN and previous not in (".", ":"):
            raise GraphQueryError(f"查询面板不允许 {value} 写入或管理指令。", 403, "read_only_required")
        if value == "SHOW" and kind == "word":
            following = [token[0] for token in tokens[index + 1:index + 4]]
            if not any(item in ("INDEXES", "CONSTRAINTS") for item in following):
                raise GraphQueryError("SHOW 仅开放索引和约束信息。", 403, "read_only_required")
        if value == "CALL" and kind == "word" and previous not in (".", ":"):
            following = tokens[index + 1:]
            if not following or following[0][0] not in ("{", "("):
                parts = []
                for token in following:
                    if token[1] not in ("word", "identifier") and token[0] != ".":
                        break
                    parts.append(token[0].strip("`"))
                if "".join(parts) not in SAFE_PROCEDURES:
                    raise GraphQueryError("不开放任意过程调用；仅支持 CALL 子查询及 db.labels/db.relationshipTypes/db.propertyKeys。", 403, "read_only_required")
        # Extension functions can have non-transactional side effects, even in READ mode.
        if value == "(" and index >= 3 and tokens[index - 2][0] == ".":
            cursor = index - 1
            parts = [tokens[cursor][0].strip("`")]
            while cursor >= 2 and tokens[cursor - 1][0] == "." and tokens[cursor - 2][1] in ("word", "identifier"):
                parts.extend((".", tokens[cursor - 2][0].strip("`")))
                cursor -= 2
            name = "".join(reversed(parts))
            if name not in SAFE_PROCEDURES | SAFE_NAMESPACED_FUNCTIONS:
                raise GraphQueryError("不开放扩展命名空间函数或过程。", 403, "read_only_required")
    if first[0] in ("EXPLAIN", "PROFILE"):
        explain = query[:first[2]] + "EXPLAIN" + query[first[3]:]
    else:
        explain = "EXPLAIN " + query
    return query.strip(), explain


def json_value(value):
    """Keep graph identity and turn Neo4j-specific values into JSON-safe values."""
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if hasattr(value, "element_id") and hasattr(value, "labels"):
        return {"type": "node", "element_id": value.element_id,
                "labels": sorted(value.labels), "properties": json_value(dict(value))}
    if hasattr(value, "element_id") and hasattr(value, "start_node"):
        return {"type": "relationship", "element_id": value.element_id, "relationship_type": value.type,
                "start": value.start_node.element_id, "end": value.end_node.element_id,
                "properties": json_value(dict(value))}
    if hasattr(value, "nodes") and hasattr(value, "relationships"):
        return {"type": "path", "nodes": json_value(list(value.nodes)),
                "relationships": json_value(list(value.relationships))}
    if hasattr(value, "srid"):
        return {"type": "point", "srid": value.srid, "coordinates": list(value)}
    if isinstance(value, Mapping):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(item) for item in value]
    if isinstance(value, bytes):
        return {"type": "bytes", "hex": value.hex()}
    if hasattr(value, "iso_format"):
        return value.iso_format()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


@dataclass(frozen=True)
class GraphQuerySettings:
    uri: str = ""
    user: str = "neo4j"
    password: str = ""
    database: str | None = None
    run_id: str = ""

    @classmethod
    def from_env(cls):
        fallback_uri = os.environ.get("NEO4J_URI", "")
        if not fallback_uri and os.environ.get("KG_BACKEND", "").lower() in ("neo4j", "graph"):
            fallback_uri = "bolt://127.0.0.1:7687"
        return cls(
            uri=os.environ.get("GRAPH_QUERY_URI", fallback_uri),
            user=os.environ.get("GRAPH_QUERY_USER", os.environ.get("NEO4J_USER", "neo4j")),
            password=os.environ.get("GRAPH_QUERY_PASSWORD", os.environ.get("NEO4J_PASSWORD", "")),
            database=os.environ.get("GRAPH_QUERY_DATABASE", os.environ.get("NEO4J_DATABASE")) or None,
            run_id=os.environ.get("KG_RUN_ID", ""),
        )

    def public_config(self):
        try:
            uri = urlsplit(self.uri)
            target = f"{uri.scheme}://{uri.hostname or ''}"
            if uri.port:
                target += f":{uri.port}"
        except ValueError:
            target = "地址配置无效"
        return {"enabled": bool(self.uri), "target": target if self.uri else "未配置",
                "database": self.database or "用户默认库", "run_id": self.run_id,
                "max_rows": MAX_ROWS, "max_timeout_seconds": MAX_TIMEOUT_SECONDS,
                "max_result_bytes": MAX_RESULT_BYTES, "max_concurrent_queries": 1}


class GraphQueryService:
    def __init__(self, settings=None, driver=None):
        self.settings = settings or GraphQuerySettings.from_env()
        self._driver = driver
        self._slot = threading.Lock()

    def config(self):
        return self.settings.public_config()

    def close(self):
        # Wait for the bounded in-flight transaction before closing its driver.
        with self._slot:
            if self._driver is not None:
                self._driver.close()
                self._driver = None

    def execute(self, payload):
        started = time.perf_counter()
        if not isinstance(payload, dict):
            raise GraphQueryError("请求必须是 JSON 对象。")
        query, explain = read_statement(payload.get("query"))
        parameters = payload.get("parameters", {})
        if not isinstance(parameters, dict):
            raise GraphQueryError("parameters 必须是 JSON 对象。")
        try:
            json.dumps(parameters, allow_nan=False)
        except (TypeError, ValueError, RecursionError):
            raise GraphQueryError("查询参数必须是合法 JSON，不能包含 NaN 或 Infinity。") from None
        max_rows = payload.get("max_rows", 100)
        timeout = payload.get("timeout_seconds", 5)
        if type(max_rows) is not int or not 1 <= max_rows <= MAX_ROWS:
            raise GraphQueryError(f"展示行数必须是 1–{MAX_ROWS} 的整数。")
        if type(timeout) not in (int, float) or not 0 < timeout <= MAX_TIMEOUT_SECONDS:
            raise GraphQueryError(f"事务超时必须大于 0 且不超过 {MAX_TIMEOUT_SECONDS} 秒。")
        if not self.settings.uri:
            raise GraphQueryError("查询数据库未配置，请设置 GRAPH_QUERY_URI 或 NEO4J_URI 后启动页面服务。", 503, "not_configured")
        if not self._slot.acquire(blocking=False):
            raise GraphQueryError("已有一个手动查询正在执行，请稍后再试。", 429, "query_busy")
        try:
            from neo4j import GraphDatabase, READ_ACCESS
            from neo4j.exceptions import DriverError, Neo4jError

            try:
                if self._driver is None:
                    self._driver = GraphDatabase.driver(
                        self.settings.uri, auth=(self.settings.user, self.settings.password),
                        max_connection_pool_size=1, connection_timeout=5,
                        connection_acquisition_timeout=5,
                    )
                with self._driver.session(database=self.settings.database, default_access_mode=READ_ACCESS,
                                          fetch_size=100) as session:
                    with session.begin_transaction(timeout=float(timeout), metadata={"source": "dynamic_viewer_manual_query"}) as tx:
                        validation = tx.run(explain, parameters=parameters).consume()
                        if validation.query_type != "r":
                            raise GraphQueryError("数据库识别为非只读查询，已拒绝执行。", 403, "read_only_required")
                        validation_ms = (time.perf_counter() - started) * 1000
                        query_started = time.perf_counter()
                        result = tx.run(query, parameters=parameters)
                        columns = list(result.keys())
                        rows = []
                        result_bytes = 0
                        truncated = False
                        for record in result:
                            if len(rows) >= max_rows:
                                truncated = True
                                break
                            row = [json_value(value) for value in record.values()]
                            size = len(json.dumps(row, ensure_ascii=False, allow_nan=False).encode("utf-8"))
                            if result_bytes + size > MAX_RESULT_BYTES:
                                truncated = True
                                break
                            result_bytes += size
                            rows.append(row)
                        # DISCARD the rest but obtain the real execution summary. A display
                        # cap is NOT a substitute for LIMIT and does not cap DB scan work.
                        summary = result.consume()
                        query_ms = (time.perf_counter() - query_started) * 1000
                        # Even with lexical + EXPLAIN checks, never commit this transaction.
                        tx.rollback()
                first_ms = summary.result_available_after
                consumed_ms = summary.result_consumed_after
                notifications = []
                for status in summary.gql_status_objects:
                    if status.is_notification:
                        notifications.append({"code": status.gql_status,
                                              "description": status.status_description,
                                              "severity": status.raw_severity,
                                              "position": json_value(status.position)})
                return {
                    "ok": True, "executed_at": datetime.now(timezone.utc).isoformat(),
                    "target": self.config()["target"], "database": summary.database,
                    "columns": columns, "rows": rows, "row_count": len(rows), "truncated": truncated,
                    "notifications": notifications,
                    "timing": {"validation_ms": round(validation_ms, 3),
                               "query_client_ms": round(query_ms, 3),
                               "server_available_ms": first_ms, "server_consumed_ms": consumed_ms,
                               "server_reported_ms": (first_ms + consumed_ms) if first_ms is not None and consumed_ms is not None else None,
                               "backend_total_ms": round((time.perf_counter() - started) * 1000, 3)},
                }
            except GraphQueryError:
                raise
            except Neo4jError as error:
                # Do not return a driver exception string: it may include connection credentials.
                code = str(getattr(error, "code", "Neo4jError"))
                detail = str(error.message or "请检查语法、参数、权限或事务超时。")
                for secret in (self.settings.password, self.settings.uri):
                    if secret:
                        detail = detail.replace(secret, "[已隐藏]")
                raise GraphQueryError(f"Neo4j 拒绝查询（{code}）：{detail[:2000]}", 400, code) from None
            except (DriverError, OSError, ValueError):
                raise GraphQueryError("无法连接查询数据库，请检查服务端地址、账号和连接状态。", 503, "database_unavailable") from None
        finally:
            self._slot.release()
