import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path


DEFAULT_URI = "bolt://127.0.0.1:17687"
DEFAULT_USER = "neo4j"
DEFAULT_PASSWORD = "neo4j123456"
DEFAULT_LOG_PATH = Path("data/neo4j_query_logs.jsonl")


PRESET_QUERIES = {
    "node-count": (
        "MATCH (n) RETURN count(n) AS count",
        {},
    ),
    "relationship-count": (
        "MATCH ()-[r]->() RETURN count(r) AS count",
        {},
    ),
    "label-counts": (
        """
        MATCH (n)
        RETURN labels(n) AS labels, count(n) AS count
        ORDER BY count DESC
        """,
        {},
    ),
    "relationship-counts": (
        """
        MATCH ()-[r]->()
        RETURN type(r) AS relationship_type, count(r) AS count
        ORDER BY count DESC
        """,
        {},
    ),
    "runs": (
        """
        MATCH (run:Run)
        RETURN run.id AS run_id
        ORDER BY run_id
        """,
        {},
    ),
    "run-summary": (
        """
        MATCH (n)-[:IN_RUN]->(:Run {id: $run_id})
        RETURN labels(n) AS labels, count(n) AS count
        ORDER BY count DESC
        """,
        {"run_id": None},
    ),
    "recent-events": (
        """
        MATCH (e:Event)-[:IN_RUN]->(:Run {id: $run_id})
        RETURN e.time AS time, e.type AS type, e.task_id AS task_id, e.drone_id AS drone_id
        ORDER BY e.time_minute DESC
        LIMIT $limit
        """,
        {"run_id": None, "limit": 20},
    ),
}


OVERVIEW_PRESETS = [
    "node-count",
    "relationship-count",
    "label-counts",
    "relationship-counts",
    "runs",
]


def parse_param(value):
    if "=" not in value:
        raise argparse.ArgumentTypeError("参数格式必须是 key=value")
    key, raw = value.split("=", 1)
    key = key.strip()
    if not key:
        raise argparse.ArgumentTypeError("参数名不能为空")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = raw
    return key, parsed


def row_to_dict(row):
    return dict(row)


def append_log(log_path, record):
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def timed_query(session, name, query, params=None, log_path=None, clock=time.perf_counter, uri=None):
    params = params or {}
    started_at = datetime.now().isoformat(timespec="seconds")
    start = clock()
    rows = [row_to_dict(row) for row in session.run(query, **params)]
    elapsed_ms = round((clock() - start) * 1000, 3)

    print(f"\n{name}")
    print("-" * len(name))
    print(f"耗时: {elapsed_ms:.3f} ms")
    print(f"返回行数: {len(rows)}")
    for row in rows:
        print(json.dumps(row, ensure_ascii=False, separators=(",", ":")))

    if log_path:
        append_log(
            log_path,
            {
                "time": started_at,
                "name": name,
                "elapsed_ms": elapsed_ms,
                "row_count": len(rows),
                "query": query.strip(),
                "params": params,
                "uri": uri,
            },
        )
    return rows, elapsed_ms


def make_driver(uri, user, password):
    from neo4j import GraphDatabase

    return GraphDatabase.driver(uri, auth=(user, password))


def build_preset(name, args):
    query, default_params = PRESET_QUERIES[name]
    params = {key: value for key, value in default_params.items() if value is not None}
    if "run_id" in default_params:
        if not args.run_id:
            raise SystemExit(f"preset '{name}' 需要 --run-id 或 KG_RUN_ID")
        params["run_id"] = args.run_id
    if "limit" in default_params:
        params["limit"] = args.limit
    params.update(dict(args.param))
    return query, params


def load_query(args):
    if args.cypher and args.cypher_file:
        raise SystemExit("--cypher 和 --cypher-file 只能选一个")
    if args.cypher:
        return "custom", args.cypher, dict(args.param)
    if args.cypher_file:
        return f"file:{args.cypher_file}", Path(args.cypher_file).read_text(encoding="utf-8"), dict(args.param)
    return None


def run_queries(driver, args):
    log_path = None if args.no_log else args.log_jsonl
    with driver.session() as session:
        custom_query = load_query(args)
        if custom_query:
            name, query, params = custom_query
            timed_query(session, name, query, params=params, log_path=log_path, uri=args.uri)
            return

        presets = OVERVIEW_PRESETS if args.preset == "overview" else [args.preset]
        for preset in presets:
            query, params = build_preset(preset, args)
            timed_query(session, preset, query, params=params, log_path=log_path, uri=args.uri)


def parse_args():
    parser = argparse.ArgumentParser(description="Run Neo4j Cypher queries and record elapsed time.")
    parser.add_argument("--uri", default=os.environ.get("NEO4J_URI", DEFAULT_URI))
    parser.add_argument("--user", default=os.environ.get("NEO4J_USER", DEFAULT_USER))
    parser.add_argument("--password", default=os.environ.get("NEO4J_PASSWORD", DEFAULT_PASSWORD))
    parser.add_argument("--run-id", default=os.environ.get("KG_RUN_ID"))
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument(
        "--preset",
        choices=["overview", *PRESET_QUERIES.keys()],
        default="overview",
        help="不传自定义 Cypher 时执行的内置查询",
    )
    parser.add_argument("--cypher", help="直接执行一条 Cypher 查询")
    parser.add_argument("--cypher-file", help="从文件读取 Cypher 查询")
    parser.add_argument("--param", action="append", type=parse_param, default=[], help="查询参数，格式 key=value")
    parser.add_argument("--log-jsonl", type=Path, default=DEFAULT_LOG_PATH)
    parser.add_argument("--no-log", action="store_true", help="只打印结果，不写入日志")
    return parser.parse_args()


def main():
    args = parse_args()
    driver = make_driver(args.uri, args.user, args.password)
    try:
        print(f"连接: {args.uri} user={args.user}")
        if not args.no_log:
            print(f"日志: {args.log_jsonl}")
        run_queries(driver, args)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
