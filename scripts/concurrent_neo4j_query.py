import argparse
import json
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path


DEFAULT_URI = "bolt://127.0.0.1:17687"
DEFAULT_USER = "neo4j"
DEFAULT_PASSWORD = "neo4j123456"
DEFAULT_LOG_PATH = Path("data/neo4j_concurrent_query_logs.jsonl")


PRESET_QUERIES = {
    "agent-state": (
        """
        MATCH (d:Drone {id: $agent_id, run_id: $run_id})
        RETURN d.id AS drone_id,
               properties(d) AS properties
        LIMIT 1
        """,
        {"run_id", "agent_id"},
    ),
    "agent-events": (
        """
        MATCH (e:Event)-[:IN_RUN]->(:Run {id: $run_id})
        WHERE e.drone_id = $agent_id
        RETURN e.type AS type,
               e.time AS time,
               e.time_minute AS time_minute,
               e.task_id AS task_id,
               e.drone_id AS drone_id
        ORDER BY e.time_minute DESC
        LIMIT $limit
        """,
        {"run_id", "agent_id", "limit"},
    ),
    "agent-neighborhood": (
        """
        MATCH (d:Drone {id: $agent_id, run_id: $run_id})
        OPTIONAL MATCH (d)-[r]-(n)
        RETURN d.id AS drone_id,
               type(r) AS relationship_type,
               labels(n) AS neighbor_labels,
               coalesce(n.id, n.task_id, n.drone_id) AS neighbor_id
        LIMIT $limit
        """,
        {"run_id", "agent_id", "limit"},
    ),
    "recent-events": (
        """
        MATCH (e:Event)-[:IN_RUN]->(:Run {id: $run_id})
        RETURN e.type AS type,
               e.time AS time,
               e.time_minute AS time_minute,
               e.task_id AS task_id,
               e.drone_id AS drone_id
        ORDER BY e.time_minute DESC
        LIMIT $limit
        """,
        {"run_id", "limit"},
    ),
    "run-summary": (
        """
        MATCH (n)-[:IN_RUN]->(:Run {id: $run_id})
        RETURN labels(n) AS labels, count(n) AS count
        ORDER BY count DESC
        """,
        {"run_id"},
    ),
    "step-summary": (
        """
        MATCH (s:Step {run_id: $run_id})
        RETURN s.step_index AS step_index,
               s.time_minute AS time_minute,
               s.completed_tasks AS completed_tasks,
               s.unfinished_tasks AS unfinished_tasks,
               s.idle_drones AS idle_drones,
               s.delivering_drones AS delivering_drones,
               s.returning_drones AS returning_drones
        ORDER BY s.step_index DESC
        LIMIT $limit
        """,
        {"run_id", "limit"},
    ),
}


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


def row_count(result):
    count = 0
    for _ in result:
        count += 1
    return count


def percentile(values, pct):
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    rank = (len(ordered) - 1) * pct / 100
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return round(ordered[int(rank)], 3)
    fraction = rank - lower
    return round(ordered[lower] * (1 - fraction) + ordered[upper] * fraction, 3)


def build_agent_id(template, agent_index, query_index):
    return template.format(
        agent=agent_index + 1,
        agent_index=agent_index,
        query=query_index + 1,
        query_index=query_index,
    )


def parse_agent_ids(raw):
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def resolve_agent_id(args, agent_index, query_index):
    explicit_ids = []
    explicit_ids.extend(getattr(args, "agent_id", []) or [])
    explicit_ids.extend(parse_agent_ids(getattr(args, "agent_ids", None)))
    if explicit_ids:
        return explicit_ids[agent_index % len(explicit_ids)]
    return build_agent_id(args.agent_id_template, agent_index, query_index)


def build_query_params(base_params, args, agent_index, query_index):
    params = dict(base_params)
    params.setdefault("limit", args.limit)
    if args.run_id:
        params.setdefault("run_id", args.run_id)
    params["agent_id"] = resolve_agent_id(args, agent_index, query_index)
    params["agent_index"] = agent_index
    params["agent_number"] = agent_index + 1
    params["query_index"] = query_index
    params["query_number"] = query_index + 1
    return params


def load_query(args):
    if args.cypher and args.cypher_file:
        raise SystemExit("--cypher 和 --cypher-file 只能选一个")
    if args.cypher:
        return "custom", args.cypher, set()
    if args.cypher_file:
        path = Path(args.cypher_file)
        return f"file:{path}", path.read_text(encoding="utf-8"), set()
    query, required_params = PRESET_QUERIES[args.preset]
    return args.preset, query, required_params


def validate_required_params(required_params, base_params, args):
    available = set(base_params)
    if args.run_id:
        available.add("run_id")
    available.add("agent_id")
    available.add("limit")
    missing = sorted(required_params - available)
    if missing:
        joined = ", ".join(missing)
        raise SystemExit(f"查询 preset 缺少必要参数: {joined}")


def append_log(log_path, record, lock):
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    with lock:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def execute_query(driver, query_name, query, params, args, agent_index, query_index, clock=time.perf_counter):
    started_at = datetime.now().isoformat(timespec="milliseconds")
    start = clock()
    success = True
    error = None
    rows = 0
    try:
        session_kwargs = {}
        if args.database:
            session_kwargs["database"] = args.database
        with driver.session(**session_kwargs) as session:
            rows = row_count(session.run(query, **params))
    except Exception as exc:  # noqa: BLE001 - 压测日志需要记录每次失败原因。
        success = False
        error = f"{type(exc).__name__}: {exc}"
    elapsed_ms = round((clock() - start) * 1000, 3)
    return {
        "time": started_at,
        "name": query_name,
        "agent_index": agent_index,
        "agent_number": agent_index + 1,
        "query_index": query_index,
        "query_number": query_index + 1,
        "success": success,
        "elapsed_ms": elapsed_ms,
        "row_count": rows,
        "error": error,
        "params": params,
        "uri": args.uri,
        "database": args.database,
    }


def summarize(records, wall_ms):
    total = len(records)
    successes = [record for record in records if record["success"]]
    failures = total - len(successes)
    latencies = [record["elapsed_ms"] for record in successes]
    summary = {
        "total_queries": total,
        "success_count": len(successes),
        "failure_count": failures,
        "wall_ms": round(wall_ms, 3),
        "throughput_qps": round(total / (wall_ms / 1000), 3) if wall_ms > 0 else None,
        "min_ms": round(min(latencies), 3) if latencies else None,
        "avg_ms": round(sum(latencies) / len(latencies), 3) if latencies else None,
        "p50_ms": percentile(latencies, 50),
        "p95_ms": percentile(latencies, 95),
        "p99_ms": percentile(latencies, 99),
        "max_ms": round(max(latencies), 3) if latencies else None,
    }
    return summary


def run_benchmark(driver, args):
    query_name, query, required_params = load_query(args)
    base_params = dict(args.param)
    validate_required_params(required_params, base_params, args)
    max_workers = args.max_workers or args.agents
    total_queries = args.agents * args.queries_per_agent
    log_lock = threading.Lock()
    records = []
    wall_start = time.perf_counter()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        for agent_index in range(args.agents):
            for query_index in range(args.queries_per_agent):
                params = build_query_params(base_params, args, agent_index, query_index)
                futures.append(
                    executor.submit(
                        execute_query,
                        driver,
                        query_name,
                        query,
                        params,
                        args,
                        agent_index,
                        query_index,
                    )
                )

        completed = 0
        for future in as_completed(futures):
            record = future.result()
            records.append(record)
            if args.log_jsonl:
                append_log(args.log_jsonl, record, log_lock)
            completed += 1
            if args.progress_every and completed % args.progress_every == 0:
                print(f"进度: {completed}/{total_queries}")

    wall_ms = (time.perf_counter() - wall_start) * 1000
    summary = summarize(records, wall_ms)
    if args.summary_json:
        Path(args.summary_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.summary_json).write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return records, summary


def make_driver(uri, user, password, max_connection_pool_size=None):
    from neo4j import GraphDatabase

    kwargs = {}
    if max_connection_pool_size:
        kwargs["max_connection_pool_size"] = max_connection_pool_size
    return GraphDatabase.driver(uri, auth=(user, password), **kwargs)


def print_summary(summary):
    print("\n并发查询压测结果")
    print("================")
    print(f"总查询数: {summary['total_queries']}")
    print(f"成功: {summary['success_count']}")
    print(f"失败: {summary['failure_count']}")
    print(f"总耗时: {summary['wall_ms']:.3f} ms")
    print(f"吞吐: {summary['throughput_qps']:.3f} qps")
    print(f"min: {summary['min_ms']} ms")
    print(f"avg: {summary['avg_ms']} ms")
    print(f"p50: {summary['p50_ms']} ms")
    print(f"p95: {summary['p95_ms']} ms")
    print(f"p99: {summary['p99_ms']} ms")
    print(f"max: {summary['max_ms']} ms")


def parse_args():
    parser = argparse.ArgumentParser(description="Run concurrent Neo4j Cypher query benchmark for LaDe KG.")
    parser.add_argument("--uri", default=os.environ.get("NEO4J_URI", DEFAULT_URI))
    parser.add_argument("--user", default=os.environ.get("NEO4J_USER", DEFAULT_USER))
    parser.add_argument("--password", default=os.environ.get("NEO4J_PASSWORD", DEFAULT_PASSWORD))
    parser.add_argument("--database", default=os.environ.get("NEO4J_DATABASE"))
    parser.add_argument("--run-id", default=os.environ.get("KG_RUN_ID"))
    parser.add_argument("--agents", type=int, default=100, help="模拟智能体数量")
    parser.add_argument("--queries-per-agent", type=int, default=1, help="每个智能体执行多少次查询")
    parser.add_argument("--max-workers", type=int, default=None, help="线程池大小；默认等于 --agents")
    parser.add_argument("--max-connection-pool-size", type=int, default=None, help="Neo4j driver 最大连接池")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--agent-id-template", default="drone-{agent}", help="例如 drone-{agent} 或 agent-{agent_index}")
    parser.add_argument("--agent-id", action="append", default=[], help="显式指定 agent_id，可重复传入；数量不足时循环使用")
    parser.add_argument("--agent-ids", default=None, help="逗号分隔的 agent_id 列表；数量不足时循环使用")
    parser.add_argument(
        "--preset",
        choices=sorted(PRESET_QUERIES.keys()),
        default="agent-state",
        help="不传自定义 Cypher 时执行的内置查询",
    )
    parser.add_argument("--cypher", help="直接执行一条 Cypher 查询")
    parser.add_argument("--cypher-file", help="从文件读取 Cypher 查询")
    parser.add_argument("--param", action="append", type=parse_param, default=[], help="查询参数，格式 key=value")
    parser.add_argument("--log-jsonl", type=Path, default=DEFAULT_LOG_PATH, help="逐次查询延迟日志")
    parser.add_argument("--summary-json", type=Path, default=None, help="汇总统计 JSON 输出路径")
    parser.add_argument("--progress-every", type=int, default=100, help="每完成 N 次查询打印一次进度；0 表示关闭")
    return parser.parse_args()


def main():
    args = parse_args()
    driver = make_driver(
        args.uri,
        args.user,
        args.password,
        max_connection_pool_size=args.max_connection_pool_size,
    )
    try:
        print(f"连接: {args.uri} user={args.user}")
        print(f"日志: {args.log_jsonl}")
        records, summary = run_benchmark(driver, args)
        print_summary(summary)
        if summary["failure_count"]:
            first_error = next(record["error"] for record in records if not record["success"])
            print(f"首个错误: {first_error}")
    finally:
        driver.close()


if __name__ == "__main__":
    main()
