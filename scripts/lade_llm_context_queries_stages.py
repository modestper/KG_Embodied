import argparse
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

if __package__:
    from . import lade_llm_context_queries as base
else:
    import lade_llm_context_queries as base


DEFAULT_QUERY_LOG_PATH = Path("data/lade_llm_context_query_stage_logs.jsonl")
DEFAULT_CONTEXT_LOG_PATH = Path("data/lade_llm_context_stage_contexts.jsonl")
DEFAULT_SUMMARY_PATH = Path("data/lade_llm_context_stage_summary.json")

DIRECT_QUERY_STAGE_FIELDS = (
    "params_build_ms",
    "run_to_header_ms",
    "fetch_records_ms",
    "summary_consume_ms",
    "row_conversion_ms",
    "record_build_ms",
    "client_query_total_ms",
    "query_pipeline_total_ms",
)

SERVER_QUERY_STAGE_FIELDS = (
    "server_available_ms",
    "server_consumed_ms",
    "server_query_ms",
)

ESTIMATED_QUERY_STAGE_FIELDS = (
    "pre_first_nonserver_estimate_ms",
    "post_first_nonserver_estimate_ms",
    "client_nonserver_estimate_ms",
)

CONTEXT_STAGE_FIELDS = (
    "worker_queue_ms",
    "session_create_ms",
    "session_close_ms",
    "queries_client_sum_ms",
    "queries_server_sum_ms",
    "context_total_ms",
)

QUERY_STAGE_LABELS = {
    "params_build_ms": "构造查询参数",
    "run_to_header_ms": "session.run 到结果头",
    "fetch_records_ms": "拉取并解码全部记录",
    "summary_consume_ms": "取得 ResultSummary",
    "row_conversion_ms": "Record 转 Python dict",
    "record_build_ms": "构造查询日志记录",
    "client_query_total_ms": "客户端单查询总时间",
    "query_pipeline_total_ms": "含参数构造的查询流水线总时间",
    "server_available_ms": "服务端首结果可用",
    "server_consumed_ms": "服务端消费剩余结果",
    "server_query_ms": "服务端查询处理总时间",
    "pre_first_nonserver_estimate_ms": "首结果前非服务端组合估算",
    "post_first_nonserver_estimate_ms": "首结果后非服务端组合估算",
    "client_nonserver_estimate_ms": "客户端非服务端组合估算",
}

CONTEXT_STAGE_LABELS = {
    "worker_queue_ms": "线程池等待",
    "session_create_ms": "创建 Session 对象",
    "session_close_ms": "关闭 Session 对象",
    "queries_client_sum_ms": "context 内客户端查询时间之和",
    "queries_server_sum_ms": "context 内服务端查询时间之和",
    "context_total_ms": "context 总时间",
}


def elapsed_ms(start, end):
    return round((end - start) * 1000, 3)


def difference_ms(total, contained):
    if total is None or contained is None:
        return None
    return round(total - contained, 3)


def server_timing(summary):
    available = getattr(summary, "result_available_after", None)
    consumed = getattr(summary, "result_consumed_after", None)
    supported = available is not None and consumed is not None
    total = available + consumed if supported else None
    return available, consumed, total, supported


def empty_query_timings():
    return {
        field: None
        for field in (
            *DIRECT_QUERY_STAGE_FIELDS,
            *SERVER_QUERY_STAGE_FIELDS,
            *ESTIMATED_QUERY_STAGE_FIELDS,
        )
    }


def execute_context(
    driver,
    queries,
    args,
    drone_id,
    context_index,
    submitted_at=None,
    clock=time.perf_counter,
):
    context_start = clock()
    worker_queue_ms = elapsed_ms(submitted_at, context_start) if submitted_at is not None else None
    started_at = datetime.now().isoformat(timespec="milliseconds")
    query_records = []
    context = {
        "catalog_version": base.CATALOG_VERSION,
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

    session_create_start = clock()
    session = driver.session(**session_kwargs)
    session_create_ms = elapsed_ms(session_create_start, clock())
    session_close_ms = None

    try:
        for query_index, query in enumerate(queries):
            pipeline_start = clock()
            params_start = pipeline_start
            params = base.build_params(query, args, drone_id)
            params_build_ms = elapsed_ms(params_start, clock())

            success = True
            error = None
            rows = []
            timings = empty_query_timings()
            timings["params_build_ms"] = params_build_ms
            query_start = clock()

            try:
                run_start = query_start
                result = session.run(query.cypher, **params)
                run_end = clock()
                timings["run_to_header_ms"] = elapsed_ms(run_start, run_end)

                fetch_start = run_end
                raw_rows = list(result)
                fetch_end = clock()
                timings["fetch_records_ms"] = elapsed_ms(fetch_start, fetch_end)

                summary_start = fetch_end
                summary = result.consume()
                summary_end = clock()
                timings["summary_consume_ms"] = elapsed_ms(summary_start, summary_end)

                available, consumed, server_total, supported = server_timing(summary)
                timings["server_available_ms"] = available
                timings["server_consumed_ms"] = consumed
                timings["server_query_ms"] = server_total

                conversion_start = summary_end
                rows = [base.row_to_dict(row) for row in raw_rows]
                conversion_end = clock()
                timings["row_conversion_ms"] = elapsed_ms(conversion_start, conversion_end)
                timings["client_query_total_ms"] = elapsed_ms(query_start, conversion_end)
                timings["query_pipeline_total_ms"] = elapsed_ms(pipeline_start, conversion_end)

                timings["pre_first_nonserver_estimate_ms"] = difference_ms(
                    timings["run_to_header_ms"],
                    available,
                )
                timings["post_first_nonserver_estimate_ms"] = difference_ms(
                    timings["fetch_records_ms"],
                    consumed,
                )
                timings["client_nonserver_estimate_ms"] = difference_ms(
                    timings["client_query_total_ms"],
                    server_total,
                )
            except Exception as exc:  # noqa: BLE001 - preserve each query failure in the output.
                success = False
                error = f"{type(exc).__name__}: {exc}"
                failure_end = clock()
                timings["client_query_total_ms"] = elapsed_ms(query_start, failure_end)
                timings["query_pipeline_total_ms"] = elapsed_ms(pipeline_start, failure_end)
                supported = False

            context["queries"][query.name] = rows
            record_start = clock()
            record = {
                "catalog_version": base.CATALOG_VERSION,
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
                "server_timing_supported": supported,
                "row_count": len(rows),
                "error": error,
                "params": params,
                "uri": args.uri,
                "database": args.database,
                **timings,
            }
            if args.include_cypher:
                record["cypher"] = query.cypher.strip()
            record["record_build_ms"] = elapsed_ms(record_start, clock())
            query_records.append(record)
    finally:
        session_close_start = clock()
        session.close()
        session_close_ms = elapsed_ms(session_close_start, clock())

    context_total_ms = elapsed_ms(context_start, clock())
    successful = [record for record in query_records if record["success"]]
    server_values = [
        record["server_query_ms"]
        for record in successful
        if record["server_query_ms"] is not None
    ]
    context["timings_ms"] = {
        "worker_queue_ms": worker_queue_ms,
        "session_create_ms": session_create_ms,
        "session_close_ms": session_close_ms,
        "queries_client_sum_ms": round(
            sum(record["client_query_total_ms"] for record in successful),
            3,
        ),
        "queries_server_sum_ms": round(sum(server_values), 3) if server_values else None,
        "context_total_ms": context_total_ms,
    }
    context["query_metrics"] = [
        {
            "query_id": record["query_id"],
            "query_name": record["query_name"],
            "temporal_scope": record["temporal_scope"],
            "success": record["success"],
            "row_count": record["row_count"],
            "client_query_total_ms": record["client_query_total_ms"],
            "server_query_ms": record["server_query_ms"],
            "error": record["error"],
        }
        for record in query_records
    ]
    return context, query_records


def metric_summary(items, fields):
    output = {}
    for field in fields:
        values = [item[field] for item in items if item.get(field) is not None]
        output[field] = {
            "count": len(values),
            "avg_ms": round(sum(values) / len(values), 3) if values else None,
            "p50_ms": base.percentile(values, 50),
            "p95_ms": base.percentile(values, 95),
            "max_ms": round(max(values), 3) if values else None,
        }
    return output


def summarize_query_group(records):
    successes = [record for record in records if record["success"]]
    timing_supported = [record for record in successes if record["server_timing_supported"]]
    return {
        "total_queries": len(records),
        "success_count": len(successes),
        "failure_count": len(records) - len(successes),
        "server_timing_count": len(timing_supported),
        "avg_rows": round(sum(record["row_count"] for record in successes) / len(successes), 3)
        if successes
        else None,
        "direct_stage_ms": metric_summary(successes, DIRECT_QUERY_STAGE_FIELDS),
        "server_stage_ms": metric_summary(timing_supported, SERVER_QUERY_STAGE_FIELDS),
        "estimated_stage_ms": metric_summary(timing_supported, ESTIMATED_QUERY_STAGE_FIELDS),
    }


def group_query_summaries(records):
    grouped = {}
    for record in records:
        grouped.setdefault(record["query_id"], []).append(record)
    return {
        query_id: summarize_query_group(group_records)
        for query_id, group_records in sorted(grouped.items())
    }


def summarize(records, contexts, wall_ms, harness_timings):
    successes = [record for record in records if record["success"]]
    timing_supported = [record for record in successes if record["server_timing_supported"]]
    context_timings = [context["timings_ms"] for context in contexts]
    return {
        "run_id": contexts[0]["run_id"] if contexts else None,
        "catalog_version": base.CATALOG_VERSION,
        "timing_model": {
            "direct": "客户端 perf_counter 实测；run_to_header 与 fetch_records 内含服务端处理时间。",
            "server": "Neo4j/Bolt 返回的整数毫秒 t_first 与 t_last。",
            "estimated": "客户端阶段减去对应服务端时间；混合连接池、建连、网络和 Driver 开销，可能因毫秒取整出现负值。",
            "session": "session_create 仅表示创建 Driver Session 对象；物理连接通常在首次 session.run 中延迟获取。",
        },
        "context_count": len(contexts),
        "total_queries": len(records),
        "success_count": len(successes),
        "failure_count": len(records) - len(successes),
        "server_timing_count": len(timing_supported),
        "wall_ms": round(wall_ms, 3),
        "throughput_qps": round(len(records) / (wall_ms / 1000), 3) if wall_ms > 0 else None,
        "direct_stage_ms": metric_summary(successes, DIRECT_QUERY_STAGE_FIELDS),
        "server_stage_ms": metric_summary(timing_supported, SERVER_QUERY_STAGE_FIELDS),
        "estimated_stage_ms": metric_summary(timing_supported, ESTIMATED_QUERY_STAGE_FIELDS),
        "context_stage_ms": metric_summary(context_timings, CONTEXT_STAGE_FIELDS),
        "harness_timings_ms": harness_timings,
        "by_query": group_query_summaries(records),
    }


def run_context_benchmark(driver, queries, args):
    drone_ids = base.resolve_drone_ids(args)
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
    submission_start = wall_start
    output_write_ms = 0.0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        for drone_id, context_index in jobs:
            submitted_at = time.perf_counter()
            futures.append(
                executor.submit(
                    execute_context,
                    driver,
                    queries,
                    args,
                    drone_id,
                    context_index,
                    submitted_at,
                )
            )
        submission_end = time.perf_counter()

        for completed, future in enumerate(as_completed(futures), start=1):
            context, records = future.result()
            contexts.append(context)
            query_records.extend(records)
            write_start = time.perf_counter()
            base.append_jsonl(args.query_log_jsonl, records, log_lock)
            base.append_jsonl(args.context_jsonl, context, log_lock)
            output_write_ms += (time.perf_counter() - write_start) * 1000
            if args.print_context:
                print(json.dumps(context, ensure_ascii=False, indent=2))
            if args.progress_every and completed % args.progress_every == 0:
                print(f"进度: {completed}/{len(jobs)} contexts")

    wall_ms = (time.perf_counter() - wall_start) * 1000
    harness_timings = {
        "job_submission_ms": round((submission_end - submission_start) * 1000, 3),
        "job_submission_avg_per_job_ms": round(
            (submission_end - submission_start) * 1000 / len(jobs),
            3,
        )
        if jobs
        else None,
        "output_write_ms": round(output_write_ms, 3),
        "output_write_avg_per_context_ms": round(output_write_ms / len(contexts), 3)
        if contexts
        else None,
        "wall_ms": round(wall_ms, 3),
    }
    summary = summarize(query_records, contexts, wall_ms, harness_timings)
    if args.summary_json:
        Path(args.summary_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.summary_json).write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return contexts, query_records, summary


def print_metric_group(title, metrics, fields, labels=None):
    print(f"\n{title}")
    print("-" * len(title))
    for field in fields:
        item = metrics[field]
        label = labels.get(field, field) if labels else field
        print(
            f"{field:<38} avg={str(item['avg_ms']):>9} ms  "
            f"p95={str(item['p95_ms']):>9} ms  count={item['count']}"
        )
        if label != field:
            print(f"  └─ {label}")


def print_summary(summary):
    print(f"查询目录版本: {summary['catalog_version']}")
    print("\nLaDe LLM 查询分阶段计时结果")
    print("==========================")
    print(f"run_id: {summary['run_id']}")
    print(f"context 数: {summary['context_count']}")
    print(f"查询总数/成功/失败: {summary['total_queries']} / {summary['success_count']} / {summary['failure_count']}")
    print(f"具有服务端计时的查询数: {summary['server_timing_count']}")
    print(f"整轮 wall: {summary['wall_ms']:.3f} ms")
    print(f"吞吐: {summary['throughput_qps']:.3f} qps")

    print_metric_group(
        "客户端直接测量阶段",
        summary["direct_stage_ms"],
        DIRECT_QUERY_STAGE_FIELDS,
        QUERY_STAGE_LABELS,
    )
    print_metric_group(
        "Neo4j 服务端报告阶段（嵌套在客户端阶段中）",
        summary["server_stage_ms"],
        SERVER_QUERY_STAGE_FIELDS,
        QUERY_STAGE_LABELS,
    )
    print_metric_group(
        "非服务端组合估算",
        summary["estimated_stage_ms"],
        ESTIMATED_QUERY_STAGE_FIELDS,
        QUERY_STAGE_LABELS,
    )
    print_metric_group(
        "context 与调度阶段",
        summary["context_stage_ms"],
        CONTEXT_STAGE_FIELDS,
        CONTEXT_STAGE_LABELS,
    )

    harness = summary["harness_timings_ms"]
    print("\n整轮测试框架时间")
    print("----------------")
    print(
        f"提交任务总计/每 job 平均: {harness['job_submission_ms']} / "
        f"{harness['job_submission_avg_per_job_ms']} ms"
    )
    print(
        f"写输出总计/每 context 平均: {harness['output_write_ms']} / "
        f"{harness['output_write_avg_per_context_ms']} ms"
    )

    print("\n按查询 ID 的阶段平均值")
    print("----------------------")
    for query_id, item in summary["by_query"].items():
        direct = item["direct_stage_ms"]
        server = item["server_stage_ms"]
        estimated = item["estimated_stage_ms"]
        print(
            f"{query_id}: params={direct['params_build_ms']['avg_ms']} ms, "
            f"run_to_header={direct['run_to_header_ms']['avg_ms']} ms, "
            f"fetch={direct['fetch_records_ms']['avg_ms']} ms, "
            f"summary={direct['summary_consume_ms']['avg_ms']} ms, "
            f"convert={direct['row_conversion_ms']['avg_ms']} ms"
        )
        print(
            f"    server_first={server['server_available_ms']['avg_ms']} ms, "
            f"server_last={server['server_consumed_ms']['avg_ms']} ms, "
            f"server_total={server['server_query_ms']['avg_ms']} ms"
        )
        print(
            f"    client_total={direct['client_query_total_ms']['avg_ms']} ms, "
            f"pipeline_total={direct['query_pipeline_total_ms']['avg_ms']} ms, "
            f"record={direct['record_build_ms']['avg_ms']} ms, "
            f"nonserver_est={estimated['client_nonserver_estimate_ms']['avg_ms']} ms"
        )


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run LaDe Neo4j context queries and record observable timing stages."
    )
    parser.add_argument("--uri", default=os.environ.get("NEO4J_URI", base.DEFAULT_URI))
    parser.add_argument("--user", default=os.environ.get("NEO4J_USER", base.DEFAULT_USER))
    parser.add_argument("--password", default=os.environ.get("NEO4J_PASSWORD", base.DEFAULT_PASSWORD))
    parser.add_argument("--database", default=os.environ.get("NEO4J_DATABASE"))
    parser.add_argument("--run-id", default=os.environ.get("KG_RUN_ID"))
    parser.add_argument("--queries", default="core", help="查询包、查询 id、范围或类别")
    parser.add_argument("--query-file", type=Path, help="自定义即时查询 JSON 文件")
    parser.add_argument("--replace-default-queries", action="store_true")
    parser.add_argument("--list-queries", action="store_true")
    parser.add_argument("--drone-id", action="append", default=[])
    parser.add_argument("--drone-ids", default=None)
    parser.add_argument("--step-index", type=int, default=base.env_int("KG_STEP_INDEX"))
    parser.add_argument("--time-minute", type=float, default=base.env_float("KG_TIME_MINUTE"))
    parser.add_argument("--history-steps", type=base.positive_int, default=20)
    parser.add_argument("--event-window-minutes", type=float, default=30.0)
    parser.add_argument("--deadline-horizon-minutes", type=float, default=60.0)
    base.add_context_arguments(parser)
    parser.add_argument("--limit", type=base.positive_int, default=10)
    parser.add_argument("--repeat", type=base.positive_int, default=1)
    parser.add_argument("--max-workers", type=base.positive_int, default=None)
    parser.add_argument("--max-connection-pool-size", type=base.positive_int, default=None)
    parser.add_argument("--param", action="append", type=base.parse_param, default=[])
    parser.add_argument("--query-log-jsonl", type=Path, default=DEFAULT_QUERY_LOG_PATH)
    parser.add_argument("--context-jsonl", type=Path, default=DEFAULT_CONTEXT_LOG_PATH)
    parser.add_argument("--summary-json", type=Path, default=DEFAULT_SUMMARY_PATH)
    parser.add_argument("--no-query-log", action="store_true")
    parser.add_argument("--no-context-log", action="store_true")
    parser.add_argument("--include-cypher", action="store_true")
    parser.add_argument("--print-context", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--progress-every", type=base.non_negative_int, default=20)
    return parser.parse_args()


def main():
    args = parse_args()
    if args.no_query_log:
        args.query_log_jsonl = None
    if args.no_context_log:
        args.context_jsonl = None

    queries = base.load_queries(args)
    if args.list_queries:
        base.print_query_list(queries)
        return

    selected_queries = base.select_queries(args.queries, queries=queries)
    if args.dry_run:
        base.print_dry_run(selected_queries, args)
        return
    if not args.run_id:
        raise SystemExit("需要 --run-id 或 KG_RUN_ID")

    driver = base.make_driver(
        args.uri,
        args.user,
        args.password,
        max_connection_pool_size=args.max_connection_pool_size,
    )
    try:
        base.resolve_time_args(driver, args, selected_queries)
        print(f"连接: {args.uri} user={args.user}")
        print(f"step_index={args.step_index} time_minute={args.time_minute}")
        if args.database:
            print(f"database: {args.database}")
        if args.query_log_jsonl:
            print(f"查询阶段日志: {args.query_log_jsonl}")
        if args.context_jsonl:
            print(f"context 日志: {args.context_jsonl}")
        if args.summary_json:
            print(f"汇总日志: {args.summary_json}")
        _, _, summary = run_context_benchmark(driver, selected_queries, args)
        print_summary(summary)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
