"""Process-based context benchmark using the shared i0-i55 template catalog.

Each spawned worker owns one Neo4j driver and executes one context at a time.
Queries within a context remain sequential, as in the thread-based entry point.
Only the parent process writes output files. Run from the repository root:

    ./run_in_lade.sh python scripts/lade_llm_context_queries_process.py --help
"""

import argparse
import json
import multiprocessing
import os
import threading
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from multiprocessing.util import Finalize
from pathlib import Path

if __package__:
    from . import lade_llm_context_queries as shared
else:
    import lade_llm_context_queries as shared


DEFAULT_QUERY_LOG_PATH = Path("data/lade_llm_context_process_query_logs.jsonl")
DEFAULT_CONTEXT_LOG_PATH = Path("data/lade_llm_context_process_contexts.jsonl")
DEFAULT_SUMMARY_PATH = Path("data/lade_llm_context_process_summary.json")

_worker_driver = None
_worker_queries = None
_worker_args = None


def initialize_worker(queries, args, driver_factory=None):
    """Create the driver inside the child; never pickle a driver or session."""
    global _worker_driver, _worker_queries, _worker_args
    factory = driver_factory if driver_factory is not None else shared.make_driver
    _worker_driver = factory(
        args.uri, args.user, args.password,
        max_connection_pool_size=args.max_connection_pool_size,
    )
    _worker_queries = queries
    _worker_args = args
    # multiprocessing workers do not reliably run ordinary atexit callbacks.
    Finalize(None, _worker_driver.close, exitpriority=10)


def execute_context_job(drone_id, context_index):
    context, records = shared.execute_context(
        _worker_driver, _worker_queries, _worker_args, drone_id, context_index,
    )
    pid = os.getpid()
    context.update(concurrency_mode="process", worker_pid=pid)
    for record in records:
        record.update(concurrency_mode="process", worker_pid=pid)
    return context, records


def run_context_benchmark(queries, args, *, driver_factory=None):
    """Run resolved queries; optional picklable driver_factory supports offline tests.

    wall_ms includes worker startup, IPC, parent logging and pool shutdown.
    Query/context latency retains the shared entry point's timing boundaries.
    """
    drone_ids = shared.resolve_drone_ids(args)
    # Validate in the parent so missing parameters cannot kill a worker midway.
    for drone_id in drone_ids:
        for query in queries:
            shared.build_params(query, args, drone_id)

    max_workers = args.max_workers or len(drone_ids)
    jobs = [
        (drone_id, repeat_index * len(drone_ids) + drone_index)
        for repeat_index in range(args.repeat)
        for drone_index, drone_id in enumerate(drone_ids)
    ]
    contexts = []
    query_records = []
    # The shared JSONL writer requires a lock, used only in the parent here.
    log_lock = threading.Lock()
    wall_start = time.perf_counter()
    with ProcessPoolExecutor(
        max_workers=max_workers,
        mp_context=multiprocessing.get_context("spawn"),
        initializer=initialize_worker,
        initargs=(queries, args, driver_factory),
    ) as executor:
        futures = [executor.submit(execute_context_job, *job) for job in jobs]
        for completed, future in enumerate(as_completed(futures), start=1):
            context, records = future.result()
            contexts.append(context)
            query_records.extend(records)
            shared.append_jsonl(args.context_jsonl, context, log_lock)
            shared.append_jsonl(args.query_log_jsonl, records, log_lock)
            if args.print_context:
                print(json.dumps(context, ensure_ascii=False, indent=2))
            if args.progress_every and completed % args.progress_every == 0:
                print(f"进度: {completed}/{len(jobs)} contexts")

    wall_ms = (time.perf_counter() - wall_start) * 1000
    summary = shared.summarize(query_records, contexts, wall_ms)
    worker_pids = sorted({context["worker_pid"] for context in contexts})
    summary.update(
        concurrency_mode="process",
        process_start_method="spawn",
        max_workers=max_workers,
        worker_pids=worker_pids,
        observed_worker_count=len(worker_pids),
        max_connection_pool_size_per_process=args.max_connection_pool_size,
    )
    if args.summary_json:
        path = Path(args.summary_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return contexts, query_records, summary


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="使用独立进程并发执行 LaDe LLM 前置上下文查询（共享 i0-i55 模板）。",
        epilog="每个进程独立维护连接池，包内查询顺序执行；默认日志与线程版分开保存。",
    )
    parser.add_argument("--uri", default=os.environ.get("NEO4J_URI", shared.DEFAULT_URI))
    parser.add_argument("--user", default=os.environ.get("NEO4J_USER", shared.DEFAULT_USER))
    parser.add_argument("--password", default=os.environ.get("NEO4J_PASSWORD", shared.DEFAULT_PASSWORD))
    parser.add_argument("--database", default=os.environ.get("NEO4J_DATABASE"))
    parser.add_argument("--run-id", default=os.environ.get("KG_RUN_ID"))
    parser.add_argument("--queries", default="core", help="查询包、id、范围或类别，例如 core、decision、i0-i5、self,task")
    parser.add_argument("--query-file", type=Path, help="自定义即时查询 JSON 文件")
    parser.add_argument("--replace-default-queries", action="store_true", help="只使用 --query-file 中的查询")
    parser.add_argument("--list-queries", action="store_true", help="列出可用查询和查询包后退出")
    parser.add_argument("--drone-id", action="append", default=[], help="显式指定 drone id，可重复传入")
    parser.add_argument("--drone-ids", default=None, help="逗号分隔 drone id")
    parser.add_argument("--step-index", type=int, default=shared.env_int("KG_STEP_INDEX"))
    parser.add_argument("--time-minute", type=float, default=shared.env_float("KG_TIME_MINUTE"))
    parser.add_argument("--history-steps", type=shared.positive_int, default=20)
    parser.add_argument("--event-window-minutes", type=float, default=30.0)
    parser.add_argument("--deadline-horizon-minutes", type=float, default=60.0)
    shared.add_context_arguments(parser)
    parser.add_argument("--limit", type=shared.positive_int, default=10)
    parser.add_argument("--repeat", type=shared.positive_int, default=1, help="每个 drone 生成多少次上下文")
    parser.add_argument("--max-workers", type=shared.positive_int, default=None, help="最大工作进程数；默认等于 drone 数")
    parser.add_argument("--max-connection-pool-size", type=shared.positive_int, default=1,
                        help="每个工作进程的连接池上限，默认 1；不是所有进程的总上限")
    parser.add_argument("--param", action="append", type=shared.parse_param, default=[], help="额外查询参数，格式 key=value")
    parser.add_argument("--query-log-jsonl", type=Path, default=DEFAULT_QUERY_LOG_PATH, help="逐条查询耗时日志")
    parser.add_argument("--context-jsonl", type=Path, default=DEFAULT_CONTEXT_LOG_PATH, help="LLM 上下文 JSONL 输出")
    parser.add_argument("--summary-json", type=Path, default=DEFAULT_SUMMARY_PATH, help="汇总统计 JSON")
    parser.add_argument("--no-query-log", action="store_true")
    parser.add_argument("--no-context-log", action="store_true")
    parser.add_argument("--include-cypher", action="store_true")
    parser.add_argument("--print-context", action="store_true", help="把上下文 JSON 打印到 stdout")
    parser.add_argument("--dry-run", action="store_true", help="只打印查询计划，不启动进程池、不连接 Neo4j")
    parser.add_argument("--progress-every", type=shared.non_negative_int, default=20)
    args = parser.parse_args(argv)
    if args.no_query_log:
        args.query_log_jsonl = None
    if args.no_context_log:
        args.context_jsonl = None
    return args


def main():
    args = parse_args()
    queries = shared.load_queries(args)
    if args.list_queries:
        shared.print_query_list(queries)
        return
    selected_queries = shared.select_queries(args.queries, queries=queries)
    if args.dry_run:
        shared.print_dry_run(selected_queries, args)
        return
    if not args.run_id:
        raise SystemExit("需要 --run-id 或 KG_RUN_ID")

    # Resolve the time once, then close the parent driver before spawning workers.
    if args.time_minute is None and shared.queries_require_params(selected_queries, {"time_minute", "start_time"}):
        driver = shared.make_driver(args.uri, args.user, args.password, max_connection_pool_size=1)
        try:
            shared.resolve_time_args(driver, args, selected_queries)
        finally:
            driver.close()

    max_workers = args.max_workers or len(shared.resolve_drone_ids(args))
    print(f"连接: {args.uri} user={args.user}")
    print(f"并发方式: process (spawn)，最大工作进程数: {max_workers}")
    print(f"每进程连接池上限: {args.max_connection_pool_size}")
    print(f"step_index={args.step_index} time_minute={args.time_minute}")
    if args.database:
        print(f"database: {args.database}")
    for label, path in (("查询日志", args.query_log_jsonl), ("上下文日志", args.context_jsonl), ("汇总日志", args.summary_json)):
        if path:
            print(f"{label}: {path}")
    _, _, summary = run_context_benchmark(selected_queries, args)
    print(f"实际返回结果的工作进程数: {summary['observed_worker_count']}，PID: {summary['worker_pids']}")
    shared.print_summary(summary)


if __name__ == "__main__":
    main()
