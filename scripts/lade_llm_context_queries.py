import argparse
import json
import math
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

if __package__:
    from .lade_llm_context_templates import (
        BASE_TEMPORAL_SCOPES, CATALOG_VERSION, CONTEXT_PARAM_DEFAULTS, EXTENDED_QUERY_PACKS,
        add_context_arguments, add_context_params, build_extended_queries, validate_builtin_queries,
    )
else:
    from lade_llm_context_templates import (
        BASE_TEMPORAL_SCOPES, CATALOG_VERSION, CONTEXT_PARAM_DEFAULTS, EXTENDED_QUERY_PACKS,
        add_context_arguments, add_context_params, build_extended_queries, validate_builtin_queries,
    )


DEFAULT_URI = "bolt://127.0.0.1:17687"
DEFAULT_USER = "neo4j"
DEFAULT_PASSWORD = "neo4j123456"
DEFAULT_QUERY_LOG_PATH = Path("data/lade_llm_context_query_logs.jsonl")
DEFAULT_CONTEXT_LOG_PATH = Path("data/lade_llm_contexts.jsonl")
DEFAULT_SUMMARY_PATH = Path("data/lade_llm_context_summary.json")


@dataclass(frozen=True)
class ContextQuery:
    id: str
    name: str
    category: str
    required_params: tuple[str, ...]
    description: str
    cypher: str
    temporal_scope: str = "current"


CONTEXT_QUERIES = {
    "i0": ContextQuery(
        id="i0",
        name="current_drone_state",
        category="self",
        required_params=("run_id", "drone_id"),
        description="读取 drone 当前属性，作为 LLM 决策的基础自我状态。",
        cypher="""
        MATCH (d:Drone {run_id: $run_id, id: $drone_id})
        RETURN d.id AS drone_id,
               d.status AS status,
               d.region_id AS region_id,
               d.x AS x,
               d.y AS y,
               d.queue_len AS queue_len,
               d.onboard_count AS onboard_count,
               d.battery_remaining_m AS battery_remaining_m,
               d.total_distance_m AS total_distance_m,
               d.updated_at AS updated_at
        LIMIT 1
        """,
    ),
    "i1": ContextQuery(
        id="i1",
        name="latest_agent_state",
        category="self",
        required_params=("run_id", "drone_id", "step_index"),
        description="读取不晚于当前 step 的最新 AgentState，避免只依赖 Drone 快照。",
        cypher="""
        MATCH (a:AgentState {run_id: $run_id, drone_id: $drone_id})
        WHERE a.step_index <= $step_index
        RETURN a.step_index AS step_index,
               a.time_minute AS time_minute,
               a.drone_id AS drone_id,
               a.region_id AS region_id,
               a.x AS x,
               a.y AS y,
               a.status AS status,
               a.queue_len AS queue_len,
               a.onboard_count AS onboard_count,
               a.battery_remaining_m AS battery_remaining_m,
               a.total_distance_m AS total_distance_m
        ORDER BY a.step_index DESC
        LIMIT 1
        """,
    ),
    "i2": ContextQuery(
        id="i2",
        name="recent_self_trajectory",
        category="history",
        required_params=("run_id", "drone_id", "start_step", "step_index", "limit"),
        description="读取最近若干 step 的自身轨迹和状态变化，给 LLM 提供短期记忆。",
        cypher="""
        MATCH (a:AgentState {run_id: $run_id, drone_id: $drone_id})
        WHERE a.step_index >= $start_step AND a.step_index <= $step_index
        RETURN a.step_index AS step_index,
               a.time_minute AS time_minute,
               a.x AS x,
               a.y AS y,
               a.status AS status,
               a.queue_len AS queue_len,
               a.onboard_count AS onboard_count,
               a.battery_remaining_m AS battery_remaining_m
        ORDER BY a.step_index DESC
        LIMIT $limit
        """,
    ),
    "i3": ContextQuery(
        id="i3",
        name="active_tasks_for_drone",
        category="task",
        required_params=("run_id", "drone_id", "limit"),
        description="读取当前 drone 已分配但未完成的任务，辅助 LLM 判断下一步行动。",
        cypher="""
        MATCH (d:Drone {run_id: $run_id, id: $drone_id})-[rel:ASSIGNED|CARRYING]->(t:Task {run_id: $run_id})
        WHERE coalesce(t.status, "") <> "completed"
          AND coalesce(rel.active, true) = true
          AND (t.assigned_to = $drone_id OR t.onboard_drone_id = $drone_id
               OR (t.assigned_to IS NULL AND t.onboard_drone_id IS NULL))
        WITH t,
             collect(DISTINCT type(rel)) AS relation_types,
             min(coalesce(rel.time_minute, rel.pickup_time_minute, t.release_minute)) AS relation_time_minute
        RETURN t.id AS task_id,
               t.status AS task_status,
               t.region_id AS region_id,
               t.release_minute AS release_minute,
               t.deadline_minute AS deadline_minute,
               t.pickup_x AS pickup_x,
               t.pickup_y AS pickup_y,
               t.dropoff_x AS dropoff_x,
               t.dropoff_y AS dropoff_y,
               t.late AS late,
               relation_types AS relation_types,
               relation_time_minute AS relation_time_minute
        ORDER BY t.deadline_minute ASC, relation_time_minute ASC
        LIMIT $limit
        """,
    ),
    "i4": ContextQuery(
        id="i4",
        name="recent_drone_events",
        category="event",
        required_params=("run_id", "drone_id", "start_time", "time_minute", "limit"),
        description="读取当前 drone 的近期事件，告诉 LLM 刚刚发生过什么。",
        cypher="""
        MATCH (e:Event {run_id: $run_id})
        WHERE e.drone_id = $drone_id
          AND e.time_minute >= $start_time AND e.time_minute <= $time_minute
        OPTIONAL MATCH (e)-[:ABOUT_TASK]->(t:Task)
        RETURN e.time_minute AS time_minute,
               e.time AS time,
               e.type AS event_type,
               e.region_id AS region_id,
               t.id AS task_id,
               e.drone_id AS actor_drone_id,
               e.queue_len AS queue_len,
               e.onboard_count AS onboard_count,
               e.late AS late
        ORDER BY e.time_minute DESC
        LIMIT $limit
        """,
    ),
    "i5": ContextQuery(
        id="i5",
        name="recent_self_performance",
        category="event",
        required_params=("run_id", "drone_id", "start_time", "time_minute"),
        description="把当前 drone 近期分配、取件、完成、超时次数压缩成 LLM 易用摘要。",
        cypher="""
        MATCH (e:Event {run_id: $run_id, drone_id: $drone_id})
        WHERE e.time_minute >= $start_time AND e.time_minute <= $time_minute
        RETURN sum(CASE WHEN e.type = "task_assigned" THEN 1 ELSE 0 END) AS assigned,
               sum(CASE WHEN e.type = "task_picked_up" THEN 1 ELSE 0 END) AS picked_up,
               sum(CASE WHEN e.type = "task_completed" THEN 1 ELSE 0 END) AS completed,
               sum(CASE WHEN coalesce(e.late, false) THEN 1 ELSE 0 END) AS late,
               count(e) AS total_events
        """,
    ),
    "i6": ContextQuery(
        id="i6",
        name="current_region_load",
        category="region",
        required_params=("run_id", "drone_id", "step_index"),
        description="读取当前 drone 所在区域在当前 step 的 agent 状态分布。",
        cypher="""
        MATCH (self:AgentState {run_id: $run_id, drone_id: $drone_id})
        WHERE self.step_index <= $step_index
        WITH self
        ORDER BY self.step_index DESC
        LIMIT 1
        MATCH (a:AgentState {run_id: $run_id, region_id: self.region_id, step_index: self.step_index})
        RETURN self.step_index AS step_index,
               self.region_id AS region_id,
               a.status AS status,
               count(*) AS agent_count,
               avg(a.queue_len) AS avg_queue_len,
               max(a.queue_len) AS max_queue_len,
               avg(a.battery_remaining_m) AS avg_battery_remaining_m
        ORDER BY agent_count DESC
        """,
    ),
    "i7": ContextQuery(
        id="i7",
        name="nearby_drones_same_region",
        category="coordination",
        required_params=("run_id", "drone_id", "step_index", "limit"),
        description="读取同一区域其他 drone 的状态并按队列/电量排序；距离候选请用 i32。",
        cypher="""
        MATCH (self:AgentState {run_id: $run_id, drone_id: $drone_id})
        WHERE self.step_index <= $step_index
        WITH self
        ORDER BY self.step_index DESC
        LIMIT 1
        MATCH (a:AgentState {run_id: $run_id, region_id: self.region_id, step_index: self.step_index})
        WHERE a.drone_id <> $drone_id
        RETURN a.drone_id AS drone_id,
               a.status AS status,
               a.x AS x,
               a.y AS y,
               a.queue_len AS queue_len,
               a.onboard_count AS onboard_count,
               a.battery_remaining_m AS battery_remaining_m
        ORDER BY a.queue_len ASC, a.battery_remaining_m DESC
        LIMIT $limit
        """,
    ),
    "i8": ContextQuery(
        id="i8",
        name="known_open_tasks_in_region",
        category="task",
        required_params=("run_id", "drone_id", "step_index", "time_minute", "deadline_horizon_minutes", "limit"),
        description="读取当前区域内知识图谱已经知道的未完成且临近截止任务。",
        cypher="""
        MATCH (self:AgentState {run_id: $run_id, drone_id: $drone_id})
        WHERE self.step_index <= $step_index
        WITH self
        ORDER BY self.step_index DESC
        LIMIT 1
        MATCH (t:Task {run_id: $run_id, region_id: self.region_id})
        WHERE coalesce(t.status, "") <> "completed"
          AND t.release_minute <= $time_minute
          AND t.deadline_minute <= $time_minute + $deadline_horizon_minutes
        RETURN t.id AS task_id,
               t.status AS task_status,
               t.region_id AS region_id,
               t.release_minute AS release_minute,
               t.deadline_minute AS deadline_minute,
               t.pickup_x AS pickup_x,
               t.pickup_y AS pickup_y,
               t.dropoff_x AS dropoff_x,
               t.dropoff_y AS dropoff_y,
               t.assigned_to AS assigned_to,
               t.onboard_drone_id AS onboard_drone_id
        ORDER BY t.deadline_minute ASC, t.release_minute ASC
        LIMIT $limit
        """,
    ),
    "i9": ContextQuery(
        id="i9",
        name="latest_global_step_summary",
        category="global",
        required_params=("run_id", "step_index"),
        description="读取不晚于当前 step 的全局 Step 摘要，给 LLM 提供系统态势。",
        cypher="""
        MATCH (s:Step {run_id: $run_id})
        WHERE s.step_index <= $step_index
        RETURN s.step_index AS step_index,
               s.time_minute AS time_minute,
               s.completed_tasks AS completed_tasks,
               s.unfinished_tasks AS unfinished_tasks,
               s.idle_drones AS idle_drones,
               s.delivering_drones AS delivering_drones,
               s.returning_drones AS returning_drones,
               s.charging_drones AS charging_drones,
               s.low_battery_drones AS low_battery_drones,
               s.total_distance_m AS total_distance_m
        ORDER BY s.step_index DESC
        LIMIT 1
        """,
    ),
    "i10": ContextQuery(
        id="i10",
        name="region_recent_events",
        category="region",
        required_params=("run_id", "drone_id", "step_index", "start_time", "time_minute", "limit"),
        description="读取当前区域近期事件，帮助 LLM 了解局部拥堵、完成和异常情况。",
        cypher="""
        MATCH (self:AgentState {run_id: $run_id, drone_id: $drone_id})
        WHERE self.step_index <= $step_index
        WITH self
        ORDER BY self.step_index DESC
        LIMIT 1
        MATCH (e:Event {run_id: $run_id, region_id: self.region_id})
        WHERE e.time_minute >= $start_time AND e.time_minute <= $time_minute
        RETURN e.time_minute AS time_minute,
               e.type AS event_type,
               e.drone_id AS drone_id,
               e.task_id AS task_id,
               e.region_id AS region_id,
               e.late AS late
        ORDER BY e.time_minute DESC
        LIMIT $limit
        """,
    ),
    "i11": ContextQuery(
        id="i11",
        name="global_region_bottlenecks",
        category="global",
        required_params=("run_id", "step_index", "limit"),
        description="按区域聚合当前 step 的全局瓶颈，适合给 LLM 一个简短全局负载视角。",
        cypher="""
        MATCH (s:Step {run_id: $run_id})
        WHERE s.step_index <= $step_index
        WITH s
        ORDER BY s.step_index DESC
        LIMIT 1
        MATCH (a:AgentState {run_id: $run_id, step_index: s.step_index})
        RETURN a.region_id AS region_id,
               count(*) AS agent_count,
               sum(CASE WHEN a.status = "delivering" THEN 1 ELSE 0 END) AS delivering,
               sum(CASE WHEN a.status = "idle" THEN 1 ELSE 0 END) AS idle,
               avg(a.queue_len) AS avg_queue_len,
               max(a.queue_len) AS max_queue_len,
               avg(a.battery_remaining_m) AS avg_battery_remaining_m
        ORDER BY max_queue_len DESC, agent_count DESC
        LIMIT $limit
        """,
    ),
}


CONTEXT_QUERIES = {
    query_id: replace(query, temporal_scope=BASE_TEMPORAL_SCOPES[query_id])
    for query_id, query in CONTEXT_QUERIES.items()
}
CONTEXT_QUERIES.update(build_extended_queries(ContextQuery))
validate_builtin_queries(CONTEXT_QUERIES.values())


QUERY_PACKS = {
    "core": ("i0", "i1", "i3", "i4", "i5", "i6", "i9"),
    "decision": ("i0", "i1", "i2", "i3", "i4", "i5", "i6", "i8", "i9"),
    "coordination": ("i1", "i6", "i7", "i10", "i11"),
    "diagnostic": ("i0", "i1", "i2", "i4", "i5", "i9", "i11"),
    "all": tuple(CONTEXT_QUERIES),
}
QUERY_PACKS.update(EXTENDED_QUERY_PACKS)


AUTO_PARAMS = {
    "run_id",
    "drone_id",
    "step_index",
    "start_step",
    "time_minute",
    "start_time",
    "limit",
    "deadline_horizon_minutes",
} | set(CONTEXT_PARAM_DEFAULTS)


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


def parse_csv(raw):
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def infer_required_params(cypher):
    return tuple(sorted(set(re.findall(r"\$([A-Za-z_][A-Za-z0-9_]*)", cypher))))


def json_safe(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if hasattr(value, "data"):
        return json_safe(value.data())
    return str(value)


def row_to_dict(row):
    return json_safe(dict(row))


def expand_range(item, queries):
    if "-" not in item:
        return None
    start, end = item.split("-", 1)
    if not (start.startswith("i") and end.startswith("i")):
        return None
    try:
        start_num = int(start[1:])
        end_num = int(end[1:])
    except ValueError:
        return None
    step = 1 if start_num <= end_num else -1
    ids = [f"i{number}" for number in range(start_num, end_num + step, step)]
    missing = [query_id for query_id in ids if query_id not in queries]
    if missing:
        raise SystemExit(f"查询范围包含不存在的模板: {', '.join(missing)}")
    return ids


def select_queries(raw_selection, queries=CONTEXT_QUERIES, packs=QUERY_PACKS):
    selected_ids = []
    seen = set()
    items = parse_csv(raw_selection) or ["core"]
    for item in items:
        if item in packs:
            candidates = list(packs[item])
        elif item in queries:
            candidates = [item]
        else:
            range_candidates = expand_range(item, queries)
            if range_candidates is not None:
                candidates = range_candidates
            else:
                candidates = [
                    query_id
                    for query_id, query in queries.items()
                    if query.category == item
                ]
        if not candidates:
            raise SystemExit(f"未知即时查询模板、查询包或类别: {item}")
        for query_id in candidates:
            if query_id not in seen:
                selected_ids.append(query_id)
                seen.add(query_id)
    return [queries[query_id] for query_id in selected_ids]


def load_custom_queries(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    raw_queries = payload.get("queries", payload) if isinstance(payload, dict) else payload
    if not isinstance(raw_queries, list):
        raise SystemExit("--query-file 必须是查询列表，或包含 queries 字段的 JSON 对象")
    custom = {}
    for raw in raw_queries:
        if not isinstance(raw, dict) or "id" not in raw or "cypher" not in raw:
            raise SystemExit("每个自定义查询都必须包含 id 和 cypher")
        cypher = raw["cypher"]
        query_id = raw["id"]
        custom[query_id] = ContextQuery(
            id=query_id,
            name=raw.get("name", query_id),
            category=raw.get("category", "custom"),
            required_params=tuple(raw.get("required_params") or infer_required_params(cypher)),
            description=raw.get("description", ""),
            cypher=cypher,
            temporal_scope=raw.get("temporal_scope", "current"),
        )
    return custom


def load_queries(args):
    queries = {} if args.replace_default_queries else dict(CONTEXT_QUERIES)
    if args.query_file:
        queries.update(load_custom_queries(args.query_file))
    return queries


def resolve_drone_ids(args):
    drone_ids = []
    drone_ids.extend(args.drone_id or [])
    drone_ids.extend(parse_csv(args.drone_ids))
    return drone_ids or ["drone-1"]


def build_params(query, args, drone_id):
    params = dict(args.param)
    add_context_params(params, args)
    params.setdefault("run_id", args.run_id)
    params.setdefault("drone_id", drone_id)
    params.setdefault("limit", args.limit)
    params.setdefault("deadline_horizon_minutes", args.deadline_horizon_minutes)
    if args.step_index is not None:
        params.setdefault("step_index", args.step_index)
        params.setdefault("start_step", max(0, args.step_index - args.history_steps + 1))
    if args.time_minute is not None:
        params.setdefault("time_minute", args.time_minute)
        params.setdefault("start_time", max(0.0, args.time_minute - args.event_window_minutes))

    missing = sorted(
        param
        for param in query.required_params
        if param not in params or params[param] is None
    )
    if missing:
        raise SystemExit(f"查询 {query.id} 缺少必要参数: {', '.join(missing)}")
    return params


def fetch_step_time_minute(driver, args):
    if args.time_minute is not None:
        return args.time_minute
    if not args.run_id:
        raise SystemExit("需要 --run-id 或 KG_RUN_ID")
    if args.step_index is None:
        raise SystemExit("需要 --step-index；脚本会自动从 Step 节点读取对应 time_minute")

    session_kwargs = {}
    if args.database:
        session_kwargs["database"] = args.database
    with driver.session(**session_kwargs) as session:
        result = session.run(
            """
            MATCH (s:Step {step_index: $step_index})-[:IN_RUN]->(:Run {id: $run_id})
            RETURN s.time_minute AS time_minute
            ORDER BY s.time_minute DESC
            LIMIT 1
            """,
            run_id=args.run_id,
            step_index=args.step_index,
        )
        rows = [row_to_dict(row) for row in result]
    if not rows:
        raise SystemExit(f"找不到 run_id={args.run_id} step_index={args.step_index} 对应的 Step 节点")
    time_minute = rows[0].get("time_minute")
    if time_minute is None:
        raise SystemExit(f"Step 节点缺少 time_minute: run_id={args.run_id} step_index={args.step_index}")
    return float(time_minute)


def queries_require_params(queries, params):
    required = set()
    for query in queries:
        required.update(query.required_params)
    return bool(required & set(params))


def resolve_time_args(driver, args, queries):
    if queries_require_params(queries, {"time_minute", "start_time"}):
        args.time_minute = fetch_step_time_minute(driver, args)
    return args


def execute_one(session, query, params, clock=time.perf_counter):
    start = clock()
    rows = [row_to_dict(row) for row in session.run(query.cypher, **params)]
    elapsed_ms = round((clock() - start) * 1000, 3)
    return rows, elapsed_ms


def execute_context(driver, queries, args, drone_id, context_index, clock=time.perf_counter):
    started_at = datetime.now().isoformat(timespec="milliseconds")
    context_start = clock()
    query_records = []
    context = {
        "catalog_version": CATALOG_VERSION,
        "time": started_at,
        "run_id": args.run_id,
        "drone_id": drone_id,
        "step_index": args.step_index,
        "time_minute": args.time_minute,
        "context_index": context_index,
        "queries": {},
        "query_temporal_scopes": {query.id: query.temporal_scope for query in queries},
    }
    session_kwargs = {}
    if args.database:
        session_kwargs["database"] = args.database

    with driver.session(**session_kwargs) as session:
        for query_index, query in enumerate(queries):
            params = build_params(query, args, drone_id)
            success = True
            error = None
            rows = []
            elapsed_ms = 0.0
            query_start = clock()
            try:
                rows = [row_to_dict(row) for row in session.run(query.cypher, **params)]
                elapsed_ms = round((clock() - query_start) * 1000, 3)
            except Exception as exc:  # noqa: BLE001 - context logs must preserve per-query failures.
                success = False
                error = f"{type(exc).__name__}: {exc}"
                elapsed_ms = round((clock() - query_start) * 1000, 3)

            context["queries"][query.name] = rows
            record = {
                "catalog_version": CATALOG_VERSION,
                "time": datetime.now().isoformat(timespec="milliseconds"),
                "run_id": args.run_id,
                "drone_id": drone_id,
                "step_index": args.step_index,
                "time_minute": args.time_minute,
                "context_index": context_index,
                "query_index": query_index,
                "query_id": query.id,
                "query_name": query.name,
                "category": query.category,
                "temporal_scope": query.temporal_scope,
                "success": success,
                "elapsed_ms": elapsed_ms,
                "row_count": len(rows),
                "error": error,
                "params": params,
                "uri": args.uri,
                "database": args.database,
            }
            if args.include_cypher:
                record["cypher"] = query.cypher.strip()
            query_records.append(record)

    context["total_elapsed_ms"] = round((clock() - context_start) * 1000, 3)
    context["query_metrics"] = [
        {
            "query_id": record["query_id"],
            "query_name": record["query_name"],
            "category": record["category"],
            "temporal_scope": record["temporal_scope"],
            "success": record["success"],
            "elapsed_ms": record["elapsed_ms"],
            "row_count": record["row_count"],
            "error": record["error"],
        }
        for record in query_records
    ]
    return context, query_records


def append_jsonl(path, records, lock):
    if not path:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(records, dict):
        records = [records]
    with lock:
        with path.open("a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


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


def summarize(records, contexts, wall_ms):
    successes = [record for record in records if record["success"]]
    latencies = [record["elapsed_ms"] for record in successes]
    context_latencies = [context["total_elapsed_ms"] for context in contexts]
    summary = {
        "catalog_version": CATALOG_VERSION,
        "run_id": contexts[0]["run_id"] if contexts else None,
        "context_count": len(contexts),
        "total_queries": len(records),
        "success_count": len(successes),
        "failure_count": len(records) - len(successes),
        "wall_ms": round(wall_ms, 3),
        "throughput_qps": round(len(records) / (wall_ms / 1000), 3) if wall_ms > 0 else None,
        "query_avg_ms": round(sum(latencies) / len(latencies), 3) if latencies else None,
        "query_p50_ms": percentile(latencies, 50),
        "query_p95_ms": percentile(latencies, 95),
        "query_p99_ms": percentile(latencies, 99),
        "context_avg_ms": round(sum(context_latencies) / len(context_latencies), 3) if context_latencies else None,
        "context_p95_ms": percentile(context_latencies, 95),
        "by_query": group_summaries(records, "query_id"),
        "by_category": group_summaries(records, "category"),
    }
    return summary


def summarize_group(records):
    successes = [record for record in records if record["success"]]
    latencies = [record["elapsed_ms"] for record in successes]
    return {
        "total_queries": len(records),
        "success_count": len(successes),
        "failure_count": len(records) - len(successes),
        "avg_ms": round(sum(latencies) / len(latencies), 3) if latencies else None,
        "p95_ms": percentile(latencies, 95),
        "max_ms": round(max(latencies), 3) if latencies else None,
        "avg_rows": round(sum(record["row_count"] for record in successes) / len(successes), 3)
        if successes
        else None,
    }


def group_summaries(records, key):
    grouped = {}
    for record in records:
        grouped.setdefault(record[key], []).append(record)
    return {
        group_key: summarize_group(group_records)
        for group_key, group_records in sorted(grouped.items(), key=lambda item: item[0])
    }


def run_context_benchmark(driver, queries, args):
    drone_ids = resolve_drone_ids(args)
    contexts = []
    query_records = []
    log_lock = threading.Lock()
    max_workers = args.max_workers or len(drone_ids)
    jobs = [
        (drone_id, repeat_index * len(drone_ids) + drone_index)
        for repeat_index in range(args.repeat)
        for drone_index, drone_id in enumerate(drone_ids)
    ]
    wall_start = time.perf_counter()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(execute_context, driver, queries, args, drone_id, context_index)
            for drone_id, context_index in jobs
        ]
        for completed, future in enumerate(as_completed(futures), start=1):
            context, records = future.result()
            contexts.append(context)
            query_records.extend(records)
            append_jsonl(args.context_jsonl, context, log_lock)
            append_jsonl(args.query_log_jsonl, records, log_lock)
            if args.print_context:
                print(json.dumps(context, ensure_ascii=False, indent=2))
            if args.progress_every and completed % args.progress_every == 0:
                print(f"进度: {completed}/{len(jobs)} contexts")

    wall_ms = (time.perf_counter() - wall_start) * 1000
    summary = summarize(query_records, contexts, wall_ms)
    if args.summary_json:
        Path(args.summary_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.summary_json).write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return contexts, query_records, summary


def make_driver(uri, user, password, max_connection_pool_size=None):
    from neo4j import GraphDatabase

    kwargs = {}
    if max_connection_pool_size:
        kwargs["max_connection_pool_size"] = max_connection_pool_size
    return GraphDatabase.driver(uri, auth=(user, password), **kwargs)


def print_query_list(queries):
    print(f"查询目录版本: {CATALOG_VERSION}")
    print("可用 LLM 即时上下文查询")
    print("======================")
    for query in queries.values():
        print(f"{query.id}\t{query.category}\t{query.temporal_scope}\t{query.name}\t{query.description}")
    print("\n查询包")
    print("======")
    for pack, query_ids in QUERY_PACKS.items():
        print(f"{pack}\t{','.join(query_ids)}")


def print_dry_run(queries, args):
    if args.step_index is not None and args.time_minute is None:
        raise SystemExit("--dry-run 不连接 Neo4j，自动解析 time_minute 前请先显式传 --time-minute")
    for drone_id in resolve_drone_ids(args):
        for query in queries:
            params = build_params(query, args, drone_id)
            print(
                json.dumps(
                    {
                        "catalog_version": CATALOG_VERSION,
                        "query_id": query.id,
                        "query_name": query.name,
                        "category": query.category,
                        "temporal_scope": query.temporal_scope,
                        "drone_id": drone_id,
                        "params": params,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )


def print_summary(summary):
    print(f"查询目录版本: {summary['catalog_version']}")
    print("\nLaDe LLM 前置上下文查询结果")
    print("==========================")
    print(f"run_id: {summary['run_id']}")
    print(f"context 数: {summary['context_count']}")
    print(f"总查询数: {summary['total_queries']}")
    print(f"成功: {summary['success_count']}")
    print(f"失败: {summary['failure_count']}")
    print(f"总耗时: {summary['wall_ms']:.3f} ms")
    print(f"吞吐: {summary['throughput_qps']:.3f} qps")
    print(f"单查询 avg/p95/p99: {summary['query_avg_ms']} / {summary['query_p95_ms']} / {summary['query_p99_ms']} ms")
    print(f"单 context avg/p95: {summary['context_avg_ms']} / {summary['context_p95_ms']} ms")
    print("\n按查询统计")
    for query_id, item in summary["by_query"].items():
        print(
            f"{query_id}: total={item['total_queries']} success={item['success_count']} "
            f"avg={item['avg_ms']}ms p95={item['p95_ms']}ms rows_avg={item['avg_rows']}"
        )


def positive_int(value):
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("必须是大于 0 的整数")
    return parsed


def non_negative_int(value):
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("必须是非负整数")
    return parsed


def env_int(name):
    value = os.environ.get(name)
    return int(value) if value not in (None, "") else None


def env_float(name):
    value = os.environ.get(name)
    return float(value) if value not in (None, "") else None


def parse_args():
    parser = argparse.ArgumentParser(description="Run immediate Neo4j context queries before LLM calls.")
    parser.add_argument("--uri", default=os.environ.get("NEO4J_URI", DEFAULT_URI))
    parser.add_argument("--user", default=os.environ.get("NEO4J_USER", DEFAULT_USER))
    parser.add_argument("--password", default=os.environ.get("NEO4J_PASSWORD", DEFAULT_PASSWORD))
    parser.add_argument("--database", default=os.environ.get("NEO4J_DATABASE"))
    parser.add_argument("--run-id", default=os.environ.get("KG_RUN_ID"))
    parser.add_argument("--queries", default="core", help="查询包、查询 id、范围或类别，例如 core、decision、i0-i5、self,task")
    parser.add_argument("--query-file", type=Path, help="自定义即时查询 JSON 文件")
    parser.add_argument("--replace-default-queries", action="store_true", help="只使用 --query-file 中的查询")
    parser.add_argument("--list-queries", action="store_true", help="列出可用查询和查询包后退出")
    parser.add_argument("--drone-id", action="append", default=[], help="显式指定 drone id，可重复传入")
    parser.add_argument("--drone-ids", default=None, help="逗号分隔 drone id")
    parser.add_argument("--step-index", type=int, default=env_int("KG_STEP_INDEX"))
    parser.add_argument("--time-minute", type=float, default=env_float("KG_TIME_MINUTE"))
    parser.add_argument("--history-steps", type=positive_int, default=20)
    parser.add_argument("--event-window-minutes", type=float, default=30.0)
    parser.add_argument("--deadline-horizon-minutes", type=float, default=60.0)
    add_context_arguments(parser)
    parser.add_argument("--limit", type=positive_int, default=10)
    parser.add_argument("--repeat", type=positive_int, default=1, help="每个 drone 生成多少次上下文")
    parser.add_argument("--max-workers", type=positive_int, default=None, help="并发 context 数；默认等于 drone 数")
    parser.add_argument("--max-connection-pool-size", type=positive_int, default=None)
    parser.add_argument("--param", action="append", type=parse_param, default=[], help="额外查询参数，格式 key=value")
    parser.add_argument("--query-log-jsonl", type=Path, default=DEFAULT_QUERY_LOG_PATH, help="逐条查询耗时日志")
    parser.add_argument("--context-jsonl", type=Path, default=DEFAULT_CONTEXT_LOG_PATH, help="LLM 上下文 JSONL 输出")
    parser.add_argument("--summary-json", type=Path, default=DEFAULT_SUMMARY_PATH, help="汇总统计 JSON")
    parser.add_argument("--no-query-log", action="store_true")
    parser.add_argument("--no-context-log", action="store_true")
    parser.add_argument("--include-cypher", action="store_true")
    parser.add_argument("--print-context", action="store_true", help="把上下文 JSON 打印到 stdout")
    parser.add_argument("--dry-run", action="store_true", help="只打印查询计划，不连接 Neo4j")
    parser.add_argument("--progress-every", type=non_negative_int, default=20)
    return parser.parse_args()


def main():
    args = parse_args()
    if args.no_query_log:
        args.query_log_jsonl = None
    if args.no_context_log:
        args.context_jsonl = None

    queries = load_queries(args)
    if args.list_queries:
        print_query_list(queries)
        return

    selected_queries = select_queries(args.queries, queries=queries)
    if args.dry_run:
        print_dry_run(selected_queries, args)
        return

    if not args.run_id:
        raise SystemExit("需要 --run-id 或 KG_RUN_ID")

    driver = make_driver(
        args.uri,
        args.user,
        args.password,
        max_connection_pool_size=args.max_connection_pool_size,
    )
    try:
        resolve_time_args(driver, args, selected_queries)
        print(f"连接: {args.uri} user={args.user}")
        print(f"step_index={args.step_index} time_minute={args.time_minute}")
        if args.database:
            print(f"database: {args.database}")
        if args.query_log_jsonl:
            print(f"查询日志: {args.query_log_jsonl}")
        if args.context_jsonl:
            print(f"上下文日志: {args.context_jsonl}")
        if args.summary_json:
            print(f"汇总日志: {args.summary_json}")
        _, _, summary = run_context_benchmark(driver, selected_queries, args)
        print_summary(summary)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
