import argparse
import json
import math
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

try:
    from scripts.lade_cypher_templates import build_extended_templates
except ModuleNotFoundError:
    from lade_cypher_templates import build_extended_templates


DEFAULT_URI = "bolt://127.0.0.1:17687"
DEFAULT_USER = "neo4j"
DEFAULT_PASSWORD = "neo4j123456"
DEFAULT_LOG_PATH = Path("data/lade_cypher_benchmark_logs.jsonl")
DEFAULT_SUMMARY_PATH = Path("data/lade_cypher_benchmark_summary.json")


AUTO_PARAMS = {
    "run_id",
    "drone_id",
    "agent_id",
    "agent_index",
    "agent_number",
    "query_index",
    "query_number",
    "round_index",
    "round_number",
    "template_id",
    "template_name",
    "limit",
    "step_index",
    "start_step",
    "end_step",
    "start_time",
    "end_time",
}


@dataclass(frozen=True)
class QueryTemplate:
    id: str
    name: str
    category: str
    cypher: str
    required_params: tuple[str, ...]
    description: str


DEFAULT_TEMPLATES = {
    "q0": QueryTemplate(
        id="q0",
        name="run_label_counts",
        category="overview",
        required_params=("run_id",),
        description="统计指定 run 下各类节点数量，用于确认数据规模。",
        cypher="""
        MATCH (n)-[:IN_RUN]->(:Run {id: $run_id})
        RETURN labels(n) AS labels, count(*) AS cnt
        ORDER BY cnt DESC
        """,
    ),
    "q1": QueryTemplate(
        id="q1",
        name="run_point_lookup",
        category="point",
        required_params=("run_id",),
        description="Run 点查，主要测连接、索引和 Bolt 往返开销。",
        cypher="""
        MATCH (r:Run {id: $run_id})
        RETURN r.id AS run_id
        """,
    ),
    "q2": QueryTemplate(
        id="q2",
        name="drone_point_lookup",
        category="point",
        required_params=("run_id", "drone_id"),
        description="Drone 点查，模拟单个智能体查询自己当前状态。",
        cypher="""
        MATCH (d:Drone {run_id: $run_id, id: $drone_id})-[:IN_RUN]->(:Run {id: $run_id})
        RETURN d.id AS drone_id,
               d.status AS status,
               d.region_id AS region_id,
               d.queue_len AS queue_len,
               d.onboard_count AS onboard_count,
               d.battery_remaining_m AS battery_remaining_m,
               d.total_distance_m AS total_distance_m
        """,
    ),
    "q3": QueryTemplate(
        id="q3",
        name="agent_state_trajectory",
        category="temporal",
        required_params=("run_id", "drone_id", "start_step", "end_step"),
        description="单 agent 时间窗口轨迹查询，窗口越大返回越多。",
        cypher="""
        MATCH (a:AgentState {run_id: $run_id, drone_id: $drone_id})
        WHERE a.step_index >= $start_step AND a.step_index <= $end_step
        RETURN a.step_index AS step_index,
               a.time_minute AS time_minute,
               a.x AS x,
               a.y AS y,
               a.status AS status,
               a.queue_len AS queue_len,
               a.battery_remaining_m AS battery_remaining_m
        ORDER BY a.step_index
        """,
    ),
    "q4": QueryTemplate(
        id="q4",
        name="step_snapshot_star",
        category="star",
        required_params=("run_id", "step_index"),
        description="以单个 Step 为中心展开 AgentState，覆盖 star query。",
        cypher="""
        MATCH (s:Step {run_id: $run_id, step_index: $step_index})<-[:IN_STEP]-(a:AgentState {run_id: $run_id})
        RETURN s.step_index AS step_index,
               a.status AS status,
               count(*) AS agent_count,
               avg(a.queue_len) AS avg_queue_len,
               avg(a.onboard_count) AS avg_onboard_count,
               avg(a.battery_remaining_m) AS avg_battery_remaining_m
        ORDER BY agent_count DESC
        """,
    ),
    "q5": QueryTemplate(
        id="q5",
        name="event_time_window",
        category="temporal",
        required_params=("run_id", "start_time", "end_time", "limit"),
        description="事件时间窗口查询，覆盖近期事件检索和可选任务关联。",
        cypher="""
        MATCH (e:Event {run_id: $run_id})-[:ABOUT_DRONE]->(d:Drone)
        WHERE e.time_minute >= $start_time AND e.time_minute < $end_time
        OPTIONAL MATCH (e)-[:ABOUT_TASK]->(t:Task)
        RETURN e.time_minute AS time_minute,
               e.type AS event_type,
               d.id AS drone_id,
               t.id AS task_id,
               e.region_id AS region_id
        ORDER BY e.time_minute
        LIMIT $limit
        """,
    ),
    "q6": QueryTemplate(
        id="q6",
        name="drone_task_lifecycle",
        category="business",
        required_params=("run_id", "drone_id", "limit"),
        description="查询单个 drone 的任务分配和完成关系，业务语义强。",
        cypher="""
        MATCH (d:Drone {run_id: $run_id, id: $drone_id})-[rel:ASSIGNED|COMPLETED]->(t:Task {run_id: $run_id})
        RETURN t.id AS task_id,
               t.region_id AS region_id,
               type(rel) AS relation_type,
               rel.time_minute AS event_time_minute,
               t.release_minute AS release_minute,
               t.deadline_minute AS deadline_minute,
               t.status AS task_status,
               t.late AS late
        ORDER BY event_time_minute
        LIMIT $limit
        """,
    ),
    "q7": QueryTemplate(
        id="q7",
        name="agent_state_timeseries_aggregate",
        category="aggregate",
        required_params=("run_id", "start_step", "end_step"),
        description="AgentState 时间序列聚合，偏 BI 类扫描、group by 和排序。",
        cypher="""
        MATCH (a:AgentState {run_id: $run_id})
        WHERE a.step_index >= $start_step AND a.step_index <= $end_step
        RETURN a.step_index AS step_index,
               a.status AS status,
               count(*) AS cnt,
               avg(a.queue_len) AS avg_queue_len,
               max(a.queue_len) AS max_queue_len,
               avg(a.battery_remaining_m) AS avg_battery
        ORDER BY step_index, status
        """,
    ),
    "q8": QueryTemplate(
        id="q8",
        name="region_load_heatmap",
        category="aggregate",
        required_params=("run_id", "start_step", "end_step", "limit"),
        description="区域负载聚合，覆盖 distinct、top-k 和时间窗口。",
        cypher="""
        MATCH (a:AgentState {run_id: $run_id})
        WHERE a.step_index >= $start_step AND a.step_index <= $end_step
        RETURN a.region_id AS region_id,
               count(*) AS samples,
               count(DISTINCT a.drone_id) AS drones,
               avg(a.queue_len) AS avg_queue_len,
               avg(a.onboard_count) AS avg_onboard_count
        ORDER BY samples DESC
        LIMIT $limit
        """,
    ),
    "q9": QueryTemplate(
        id="q9",
        name="top_busy_drones",
        category="topk",
        required_params=("run_id", "limit"),
        description="Top-K 繁忙 drone 查询，测试聚合排序和热点 agent。",
        cypher="""
        MATCH (d:Drone {run_id: $run_id})-[c:COMPLETED]->(t:Task {run_id: $run_id})
        RETURN d.id AS drone_id,
               count(t) AS completed_tasks,
               sum(CASE WHEN coalesce(c.late, false) THEN 1 ELSE 0 END) AS late_tasks
        ORDER BY completed_tasks DESC, late_tasks DESC
        LIMIT $limit
        """,
    ),
    "q10": QueryTemplate(
        id="q10",
        name="event_drone_region_complex_join",
        category="join",
        required_params=("run_id", "start_time", "end_time", "limit"),
        description="复杂 join 查询，关联 Run/Event/Drone/Region/Task。",
        cypher="""
        MATCH (:Run {id: $run_id})<-[:IN_RUN]-(e:Event)-[:ABOUT_DRONE]->(d:Drone)
        MATCH (e)-[:IN_REGION]->(region:Region)
        WHERE e.time_minute >= $start_time AND e.time_minute < $end_time
        OPTIONAL MATCH (e)-[:ABOUT_TASK]->(t:Task)
        RETURN region.id AS region_id,
               e.type AS event_type,
               d.status AS drone_status,
               count(e) AS events,
               count(DISTINCT d) AS drones,
               count(DISTINCT t) AS tasks
        ORDER BY events DESC
        LIMIT $limit
        """,
    ),
    "q11": QueryTemplate(
        id="q11",
        name="event_drone_task_region_path",
        category="path",
        required_params=("run_id", "start_time", "end_time", "limit"),
        description="多跳路径查询，测试图遍历、路径过滤和中间结果规模。",
        cypher="""
        MATCH p=(e:Event {run_id: $run_id})-[:ABOUT_DRONE]->(d:Drone)-[:ASSIGNED|COMPLETED]->(t:Task)-[:BELONGS_TO]->(region:Region)
        WHERE e.time_minute >= $start_time
          AND e.time_minute < $end_time
          AND e.region_id = t.region_id
        RETURN d.id AS drone_id,
               region.id AS region_id,
               count(DISTINCT t) AS related_tasks,
               count(DISTINCT e) AS events,
               min(length(p)) AS path_len
        ORDER BY events DESC
        LIMIT $limit
        """,
    ),
    "q12": QueryTemplate(
        id="q12",
        name="unfinished_task_antijoin",
        category="antijoin",
        required_params=("run_id",),
        description="未完成或异常任务反连接查询，测试 NOT EXISTS 模式过滤。",
        cypher="""
        MATCH (t:Task {run_id: $run_id})
        WHERE NOT EXISTS {
          MATCH (:Drone {run_id: $run_id})-[:COMPLETED]->(t)
        }
        RETURN t.status AS task_status,
               count(*) AS task_count
        ORDER BY task_count DESC
        """,
    ),
}

DEFAULT_TEMPLATES.update(build_extended_templates(QueryTemplate))


TEMPLATE_PACKS = {
    "smoke": ("q0", "q1", "q2", "q13"),
    "online": (
        "q2", "q3", "q5", "q6", "q13", "q17", "q18", "q20",
        "q27", "q33", "q37", "q41",
    ),
    "analytics_suite": (
        "q7", "q8", "q9", "q10", "q11", "q14", "q16", "q21",
        "q23", "q25", "q26", "q28", "q29", "q30", "q35", "q36",
        "q38", "q40",
    ),
    "collaboration_suite": ("q32",) + tuple(f"q{number}" for number in range(42, 49)),
    "resilience_suite": ("q17", "q18", "q24", "q41", "q49", "q50", "q51"),
    "quality_suite": ("q12", "q52", "q53", "q54", "q55"),
    "all": tuple(DEFAULT_TEMPLATES),
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


def parse_csv(raw):
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def infer_required_params(cypher):
    return tuple(sorted(set(re.findall(r"\$([A-Za-z_][A-Za-z0-9_]*)", cypher))))


def normalize_template(raw):
    cypher = raw["cypher"]
    template_id = raw["id"]
    return QueryTemplate(
        id=template_id,
        name=raw.get("name", template_id),
        category=raw.get("category", "custom"),
        cypher=cypher,
        required_params=tuple(raw.get("required_params") or infer_required_params(cypher)),
        description=raw.get("description", ""),
    )


def load_template_file(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    raw_templates = payload.get("templates", payload) if isinstance(payload, dict) else payload
    if not isinstance(raw_templates, list):
        raise SystemExit("--template-file 必须是模板列表，或包含 templates 字段的 JSON 对象")
    templates = {}
    for raw in raw_templates:
        if not isinstance(raw, dict) or "id" not in raw or "cypher" not in raw:
            raise SystemExit("每个自定义模板都必须包含 id 和 cypher")
        template = normalize_template(raw)
        templates[template.id] = template
    return templates


def load_templates(args):
    templates = {} if args.replace_default_templates else dict(DEFAULT_TEMPLATES)
    if args.template_file:
        templates.update(load_template_file(args.template_file))
    return templates


def expand_template_range(item, templates):
    if "-" not in item:
        return None
    start, end = item.split("-", 1)
    if not (start.startswith("q") and end.startswith("q")):
        return None
    try:
        start_num = int(start[1:])
        end_num = int(end[1:])
    except ValueError:
        return None
    step = 1 if start_num <= end_num else -1
    ids = [f"q{number}" for number in range(start_num, end_num + step, step)]
    missing = [template_id for template_id in ids if template_id not in templates]
    if missing:
        raise SystemExit(f"模板范围包含不存在的模板: {', '.join(missing)}")
    return ids


def select_templates(raw_selection, templates, packs=TEMPLATE_PACKS):
    selected = []
    seen = set()
    items = parse_csv(raw_selection)
    if not items or items == ["all"]:
        items = list(templates)

    for item in items:
        if item == "all":
            candidates = list(templates)
        elif item in templates:
            candidates = [item]
        elif item in packs:
            candidates = [template_id for template_id in packs[item] if template_id in templates]
        else:
            range_candidates = expand_template_range(item, templates)
            if range_candidates is not None:
                candidates = range_candidates
            else:
                candidates = [
                    template_id
                    for template_id, template in templates.items()
                    if template.category == item
                ]
        if not candidates:
            raise SystemExit(f"未知查询模板或类别: {item}")
        for template_id in candidates:
            if template_id not in seen:
                selected.append(templates[template_id])
                seen.add(template_id)
    return selected


def resolve_drone_id(args, agent_index, query_index, template):
    explicit_ids = []
    explicit_ids.extend(args.drone_id or [])
    explicit_ids.extend(parse_csv(args.drone_ids))
    if explicit_ids:
        return explicit_ids[agent_index % len(explicit_ids)]
    return args.drone_id_template.format(
        agent=agent_index + 1,
        agent_index=agent_index,
        query=query_index + 1,
        query_index=query_index,
        template_id=template.id,
        template_name=template.name,
    )


def build_step_window(args, round_index):
    start_base = args.start_step
    if args.end_step is None:
        stride = args.step_stride if args.step_stride is not None else args.step_window
        start_step = start_base + round_index * stride
        end_step = start_step + max(args.step_window, 1) - 1
        return start_step, end_step

    if args.step_window <= 0:
        return start_base, args.end_step

    window = min(args.step_window, max(1, args.end_step - start_base + 1))
    max_start = args.end_step - window + 1
    available_starts = max(1, max_start - start_base + 1)
    stride = args.step_stride if args.step_stride is not None else window
    start_step = start_base + ((round_index * stride) % available_starts)
    return start_step, start_step + window - 1


def build_step_index(args, round_index):
    if args.step_index is not None:
        return args.step_index
    start_step, _ = build_step_window(args, round_index)
    return start_step


def build_time_window(args, round_index):
    start_base = args.start_time
    if args.end_time is None:
        stride = args.time_stride if args.time_stride is not None else args.time_window_minutes
        start_time = start_base + round_index * stride
        return start_time, start_time + args.time_window_minutes

    if args.time_window_minutes <= 0:
        return start_base, args.end_time

    window = min(args.time_window_minutes, max(0.001, args.end_time - start_base))
    max_start = args.end_time - window
    available_span = max(0.0, max_start - start_base)
    stride = args.time_stride if args.time_stride is not None else window
    offset = (round_index * stride) % available_span if available_span else 0.0
    start_time = start_base + offset
    return start_time, start_time + window


def build_query_params(template, args, agent_index, round_index, query_index):
    params = dict(args.param)
    params.setdefault("limit", args.limit)
    params.setdefault("agent_index", agent_index)
    params.setdefault("agent_number", agent_index + 1)
    params.setdefault("query_index", query_index)
    params.setdefault("query_number", query_index + 1)
    params.setdefault("round_index", round_index)
    params.setdefault("round_number", round_index + 1)
    params.setdefault("template_id", template.id)
    params.setdefault("template_name", template.name)

    required = set(template.required_params)
    if "run_id" in required and "run_id" not in params:
        if not args.run_id:
            raise SystemExit(f"模板 {template.id} 需要 --run-id 或 KG_RUN_ID")
        params["run_id"] = args.run_id
    if "drone_id" in required and "drone_id" not in params:
        params["drone_id"] = resolve_drone_id(args, agent_index, query_index, template)
    if "agent_id" in required and "agent_id" not in params:
        params["agent_id"] = resolve_drone_id(args, agent_index, query_index, template)
    if {"start_step", "end_step"} & required:
        start_step, end_step = build_step_window(args, round_index)
        params.setdefault("start_step", start_step)
        params.setdefault("end_step", end_step)
    if "step_index" in required and "step_index" not in params:
        params["step_index"] = build_step_index(args, round_index)
    if {"start_time", "end_time"} & required:
        start_time, end_time = build_time_window(args, round_index)
        params.setdefault("start_time", start_time)
        params.setdefault("end_time", end_time)
    return params


def validate_template_params(template, args):
    available = set(dict(args.param)) | AUTO_PARAMS
    missing = sorted(set(template.required_params) - available)
    if missing:
        raise SystemExit(f"模板 {template.id} 缺少参数生成规则或 --param: {', '.join(missing)}")
    if "run_id" in template.required_params and not args.run_id and "run_id" not in dict(args.param):
        raise SystemExit(f"模板 {template.id} 需要 --run-id 或 KG_RUN_ID")


def row_count(result):
    count = 0
    for _ in result:
        count += 1
    return count


def append_log(log_path, record, lock):
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    with lock:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def execute_query(driver, template, params, args, agent_index, round_index, query_index, clock=time.perf_counter):
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
            rows = row_count(session.run(template.cypher, **params))
    except Exception as exc:  # noqa: BLE001 - benchmark logs need the exact per-query failure.
        success = False
        error = f"{type(exc).__name__}: {exc}"
    elapsed_ms = round((clock() - start) * 1000, 3)
    record = {
        "time": started_at,
        "template_id": template.id,
        "template_name": template.name,
        "category": template.category,
        "agent_index": agent_index,
        "agent_number": agent_index + 1,
        "round_index": round_index,
        "round_number": round_index + 1,
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
    if args.include_cypher:
        record["cypher"] = template.cypher.strip()
    return record


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


def summarize_records(records, wall_ms=None):
    total = len(records)
    successes = [record for record in records if record["success"]]
    latencies = [record["elapsed_ms"] for record in successes]
    row_counts = [record["row_count"] for record in successes]
    summary = {
        "total_queries": total,
        "success_count": len(successes),
        "failure_count": total - len(successes),
        "min_ms": round(min(latencies), 3) if latencies else None,
        "avg_ms": round(sum(latencies) / len(latencies), 3) if latencies else None,
        "p50_ms": percentile(latencies, 50),
        "p95_ms": percentile(latencies, 95),
        "p99_ms": percentile(latencies, 99),
        "max_ms": round(max(latencies), 3) if latencies else None,
        "min_rows": min(row_counts) if row_counts else None,
        "avg_rows": round(sum(row_counts) / len(row_counts), 3) if row_counts else None,
        "max_rows": max(row_counts) if row_counts else None,
    }
    if wall_ms is not None:
        summary["wall_ms"] = round(wall_ms, 3)
        summary["throughput_qps"] = round(total / (wall_ms / 1000), 3) if wall_ms > 0 else None
    return summary


def group_summaries(records, key):
    grouped = {}
    for record in records:
        grouped.setdefault(record[key], []).append(record)
    return {
        group_key: summarize_records(group_records)
        for group_key, group_records in sorted(grouped.items(), key=lambda item: item[0])
    }


def build_jobs(templates, args):
    jobs = []
    query_index = 0
    for agent_index in range(args.agents):
        for round_index in range(args.queries_per_agent):
            for template in templates:
                params = build_query_params(template, args, agent_index, round_index, query_index)
                jobs.append((template, params, agent_index, round_index, query_index))
                query_index += 1
    return jobs


def run_benchmark(driver, templates, args):
    for template in templates:
        validate_template_params(template, args)

    jobs = build_jobs(templates, args)
    records = []
    log_lock = threading.Lock()
    max_workers = args.max_workers or args.agents
    wall_start = time.perf_counter()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(
                execute_query,
                driver,
                template,
                params,
                args,
                agent_index,
                round_index,
                query_index,
            )
            for template, params, agent_index, round_index, query_index in jobs
        ]
        for completed, future in enumerate(as_completed(futures), start=1):
            record = future.result()
            records.append(record)
            if args.log_jsonl:
                append_log(args.log_jsonl, record, log_lock)
            if args.progress_every and completed % args.progress_every == 0:
                print(f"进度: {completed}/{len(jobs)}")

    wall_ms = (time.perf_counter() - wall_start) * 1000
    summary = summarize_records(records, wall_ms=wall_ms)
    summary.update(
        {
            "run_id": args.run_id or dict(args.param).get("run_id"),
            "agents": args.agents,
            "queries_per_agent": args.queries_per_agent,
            "max_workers": max_workers,
            "template_count": len(templates),
            "templates": [
                {
                    "id": template.id,
                    "name": template.name,
                    "category": template.category,
                    "description": template.description,
                }
                for template in templates
            ],
            "by_template": group_summaries(records, "template_id"),
            "by_category": group_summaries(records, "category"),
        }
    )
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


def print_template_list(templates):
    print("可用查询模板")
    print("============")
    for template in templates.values():
        print(f"{template.id}\t{template.category}\t{template.name}\t{template.description}")


def print_dry_run(jobs):
    print("Dry run 查询计划")
    print("================")
    for template, params, agent_index, round_index, query_index in jobs:
        print(
            json.dumps(
                {
                    "template_id": template.id,
                    "template_name": template.name,
                    "category": template.category,
                    "agent_index": agent_index,
                    "round_index": round_index,
                    "query_index": query_index,
                    "params": params,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )


def print_summary(summary):
    print("\nLaDe Cypher 查询压测结果")
    print("=======================")
    print(f"run_id: {summary['run_id']}")
    print(f"模板数: {summary['template_count']}")
    print(f"总查询数: {summary['total_queries']}")
    print(f"成功: {summary['success_count']}")
    print(f"失败: {summary['failure_count']}")
    print(f"总耗时: {summary['wall_ms']:.3f} ms")
    print(f"吞吐: {summary['throughput_qps']:.3f} qps")
    print(f"avg: {summary['avg_ms']} ms")
    print(f"p50: {summary['p50_ms']} ms")
    print(f"p95: {summary['p95_ms']} ms")
    print(f"p99: {summary['p99_ms']} ms")
    print(f"max: {summary['max_ms']} ms")
    print("\n按模板统计")
    for template_id, item in summary["by_template"].items():
        print(
            f"{template_id}: total={item['total_queries']} "
            f"success={item['success_count']} avg={item['avg_ms']}ms "
            f"p95={item['p95_ms']}ms rows_avg={item['avg_rows']}"
        )


def positive_int(value):
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("必须是大于 0 的整数")
    return parsed


def parse_args():
    parser = argparse.ArgumentParser(description="Run a LaDe Neo4j Cypher query-template benchmark.")
    parser.add_argument("--uri", default=os.environ.get("NEO4J_URI", DEFAULT_URI))
    parser.add_argument("--user", default=os.environ.get("NEO4J_USER", DEFAULT_USER))
    parser.add_argument("--password", default=os.environ.get("NEO4J_PASSWORD", DEFAULT_PASSWORD))
    parser.add_argument("--database", default=os.environ.get("NEO4J_DATABASE"))
    parser.add_argument("--run-id", default=os.environ.get("KG_RUN_ID"))
    parser.add_argument(
        "--templates",
        default="all",
        help=(
            "逗号分隔的模板 id、范围、类别或查询包，例如 all、q0-q9、q3,q4、"
            "point,temporal、smoke、online、collaboration_suite"
        ),
    )
    parser.add_argument("--template-file", type=Path, help="自定义模板 JSON 文件")
    parser.add_argument("--replace-default-templates", action="store_true", help="只使用 --template-file 中的模板")
    parser.add_argument("--list-templates", action="store_true", help="列出可用模板后退出")
    parser.add_argument("--agents", type=positive_int, default=1, help="模拟查询客户端/智能体数量")
    parser.add_argument("--queries-per-agent", type=positive_int, default=1, help="每个 agent 对每个模板执行多少轮")
    parser.add_argument("--max-workers", type=positive_int, default=None, help="并发线程数；默认等于 --agents")
    parser.add_argument("--max-connection-pool-size", type=positive_int, default=None, help="Neo4j driver 最大连接池")
    parser.add_argument("--limit", type=positive_int, default=20)
    parser.add_argument("--drone-id-template", default="drone-{agent}", help="默认 drone id 模板")
    parser.add_argument("--drone-id", action="append", default=[], help="显式指定 drone id，可重复传入")
    parser.add_argument("--drone-ids", default=None, help="逗号分隔 drone id，数量不足时循环使用")
    parser.add_argument("--start-step", type=int, default=1)
    parser.add_argument("--end-step", type=int, default=None)
    parser.add_argument("--step-index", type=int, default=None)
    parser.add_argument("--step-window", type=int, default=100, help="step 时间窗口大小；设为 0 且有 --end-step 时使用完整窗口")
    parser.add_argument("--step-stride", type=int, default=None, help="多轮查询时 step 窗口移动步长")
    parser.add_argument("--start-time", type=float, default=0.0)
    parser.add_argument("--end-time", type=float, default=None)
    parser.add_argument("--time-window-minutes", type=float, default=1440.0)
    parser.add_argument("--time-stride", type=float, default=None)
    parser.add_argument("--param", action="append", type=parse_param, default=[], help="额外查询参数，格式 key=value")
    parser.add_argument("--log-jsonl", type=Path, default=DEFAULT_LOG_PATH, help="逐次查询延迟日志")
    parser.add_argument("--summary-json", type=Path, default=DEFAULT_SUMMARY_PATH, help="汇总统计 JSON 输出路径")
    parser.add_argument("--no-log", action="store_true", help="不写 JSONL 明细")
    parser.add_argument("--include-cypher", action="store_true", help="在每条 JSONL 日志中写入 Cypher 原文")
    parser.add_argument("--dry-run", action="store_true", help="只打印查询计划，不连接 Neo4j")
    parser.add_argument("--progress-every", type=int, default=100, help="每完成 N 次查询打印一次进度；0 表示关闭")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.no_log:
        args.log_jsonl = None
    templates = load_templates(args)
    if args.list_templates:
        print_template_list(templates)
        return

    selected_templates = select_templates(args.templates, templates)
    for template in selected_templates:
        validate_template_params(template, args)

    jobs = build_jobs(selected_templates, args)
    if args.dry_run:
        print_dry_run(jobs)
        return

    driver = make_driver(
        args.uri,
        args.user,
        args.password,
        max_connection_pool_size=args.max_connection_pool_size,
    )
    try:
        print(f"连接: {args.uri} user={args.user}")
        if args.database:
            print(f"database: {args.database}")
        if args.log_jsonl:
            print(f"逐次日志: {args.log_jsonl}")
        if args.summary_json:
            print(f"汇总日志: {args.summary_json}")
        _, summary = run_benchmark(driver, selected_templates, args)
        print_summary(summary)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
