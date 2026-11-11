"""Single-node Ray context benchmark with one distinct physical core per actor.

Ray reserves one logical CPU resource per actor. Linux thread affinities enforce
one selected hardware CPU per actor; no two actors use SMT siblings of one core.
Affinity does not exclude unrelated processes or Ray services from these cores.
"""

import argparse
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

# Use a stable package name so Ray workers can deserialize queries in script mode.
REPO_ROOT = Path(__file__).resolve().parents[1]
if not __package__:
    sys.path.insert(0, str(REPO_ROOT))
from scripts import lade_llm_context_queries as shared


DEFAULT_WORKERS = 100
DEFAULT_QUERY_LOG_PATH = Path("data/lade_llm_context_ray_query_logs.jsonl")
DEFAULT_CONTEXT_LOG_PATH = Path("data/lade_llm_context_ray_contexts.jsonl")
DEFAULT_SUMMARY_PATH = Path("data/lade_llm_context_ray_summary.json")
CPU_TOPOLOGY_ROOT = Path("/sys/devices/system/cpu")


def physical_core(cpu_id, topology_root=CPU_TOPOLOGY_ROOT):
    try:
        root = Path(topology_root) / f"cpu{cpu_id}" / "topology"
        return (int((root / "physical_package_id").read_text()),
                int((root / "core_id").read_text()))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"无法读取 CPU {cpu_id} 的物理核心拓扑：{exc}") from exc


def parse_cpu_ids(raw):
    ids = []
    try:
        for part in raw.split(","):
            bounds = part.strip().split("-")
            if len(bounds) == 1:
                ids.append(int(bounds[0]))
            elif len(bounds) == 2:
                start, end = map(int, bounds)
                if end < start:
                    raise ValueError("倒序范围")
                ids.extend(range(start, end + 1))
            else:
                raise ValueError("无效范围")
        if any(cpu < 0 for cpu in ids) or len(ids) != len(set(ids)):
            raise ValueError("CPU 编号为负数或重复")
    except ValueError as exc:
        raise SystemExit("--cpu-ids 需要互不重复的 CPU 编号或范围，例如 0-3,8,10") from exc
    return ids


def choose_cpu_ids(workers, raw=None, *, allowed_cpus=None, topology_root=CPU_TOPOLOGY_ROOT):
    if not hasattr(os, "sched_setaffinity") or not Path("/proc/self/task").is_dir():
        raise SystemExit("CPU 绑定需要支持 sched_setaffinity 和 /proc 的 Linux 环境")
    allowed = set(os.sched_getaffinity(0) if allowed_cpus is None else allowed_cpus)
    if raw is not None:
        chosen = parse_cpu_ids(raw)
        if len(chosen) != workers:
            raise SystemExit(f"--cpu-ids 需要恰好 {workers} 个 CPU 编号，实际 {len(chosen)} 个")
        if set(chosen) - allowed:
            raise SystemExit(f"指定的 CPU 不在当前允许范围内：{sorted(set(chosen) - allowed)}")
        cores = [physical_core(cpu, topology_root) for cpu in chosen]
        if len(set(cores)) != workers:
            raise SystemExit("指定 CPU 包含同一物理核心的超线程；请为每个 Actor 选择不同物理核心")
        return chosen
    chosen, seen = [], set()
    for cpu in sorted(allowed):
        core = physical_core(cpu, topology_root)
        if core not in seen:
            chosen.append(cpu)
            seen.add(core)
    if len(chosen) < workers:
        raise SystemExit(f"需要 {workers} 个不同物理核心，当前允许范围内只有 {len(chosen)} 个；请减小 --max-workers")
    return chosen[:workers]


def pin_actor_threads(cpu_id):
    """Pin existing Ray background threads as well as the actor execution thread.

    sched_setaffinity(0, ...) alone only changes the calling thread on Linux.
    New threads inherit the creating thread's mask. Recheck to cover startup races.
    """
    expected = {cpu_id}
    for _ in range(8):
        for path in Path("/proc/self/task").iterdir():
            try:
                os.sched_setaffinity(int(path.name), expected)
            except ProcessLookupError:
                pass
        masks = []
        for path in Path("/proc/self/task").iterdir():
            try:
                masks.append(os.sched_getaffinity(int(path.name)))
            except ProcessLookupError:
                pass
        if masks and all(mask == expected for mask in masks):
            return len(masks)
    raise RuntimeError(f"Actor 线程未能全部绑定到 CPU {cpu_id}")


class RayContextWorker:
    def __init__(self, worker_index, cpu_id, queries, args, driver_factory=None):
        self.worker_index = worker_index
        self.cpu_id = cpu_id
        self.thread_count = pin_actor_threads(cpu_id)
        self.queries = queries
        self.args = args
        self.pid = os.getpid()
        factory = shared.make_driver if driver_factory is None else driver_factory
        self.driver = factory(args.uri, args.user, args.password,
                              max_connection_pool_size=args.max_connection_pool_size)

    def ready(self):
        import ray

        self.thread_count = pin_actor_threads(self.cpu_id)
        socket, core = physical_core(self.cpu_id)
        return {"worker_index": self.worker_index, "worker_pid": self.pid,
                "cpu_id": self.cpu_id, "affinity_cpus": sorted(os.sched_getaffinity(0)),
                "physical_socket": socket, "physical_core": core,
                "ray_assigned_resources": ray.get_runtime_context().get_assigned_resources(),
                "pinned_thread_count": self.thread_count}

    def execute(self, drone_id, context_index):
        pin_actor_threads(self.cpu_id)
        context, records = shared.execute_context(self.driver, self.queries, self.args, drone_id, context_index)
        metadata = {"concurrency_mode": "ray", "worker_pid": self.pid,
                    "worker_index": self.worker_index, "cpu_id": self.cpu_id,
                    "affinity_cpus": sorted(os.sched_getaffinity(0))}
        if metadata["affinity_cpus"] != [self.cpu_id]:
            raise RuntimeError("Actor 执行线程的 CPU 绑定发生变化")
        context.update(metadata)
        for record in records:
            record.update(metadata)
        return context, records

    def close(self):
        if self.driver is not None:
            self.driver.close()
            self.driver = None


def load_ray():
    try:
        import ray
    except ImportError as exc:
        raise SystemExit("当前 Python 环境未安装 Ray，请在有 Ray 的环境运行") from exc
    return ray


def run_context_benchmark(queries, args, *, driver_factory=None, cpu_ids=None):
    drone_ids = shared.resolve_drone_ids(args)
    for drone_id in drone_ids:
        for query in queries:
            shared.build_params(query, args, drone_id)
    # Always validate explicit inputs, including the internal testing override.
    cpu_ids = choose_cpu_ids(args.max_workers, args.cpu_ids if cpu_ids is None else ",".join(map(str, cpu_ids)))
    ray = load_ray()
    if ray.is_initialized():
        raise RuntimeError("该入口需要独立本地 Ray runtime；当前 Python 已连接 Ray，请使用独立进程运行")
    jobs = iter((drone_id, repeat * len(drone_ids) + index)
                for repeat in range(args.repeat) for index, drone_id in enumerate(drone_ids))
    total_jobs = len(drone_ids) * args.repeat
    actors, worker_info, contexts, records = [], [], [], []
    cleanup_errors = []
    log_lock = threading.Lock()
    ray_temp_dir = tempfile.mkdtemp(prefix="lade-context-ray-", dir="/tmp")
    wall_start = time.perf_counter()
    query_phase_start = query_phase_end = None
    try:
        # Force a new local runtime; never join/stop an existing simulation cluster.
        # The absolute local repo path avoids packaging/uploading experiment data.
        pythonpath = os.pathsep.join(filter(None, (str(REPO_ROOT), os.environ.get("PYTHONPATH"))))
        ray.init(address="local", num_cpus=args.max_workers, include_dashboard=False,
                 object_store_memory=128 * 1024 * 1024, _temp_dir=ray_temp_dir,
                 _node_ip_address="127.0.0.1", log_to_driver=False,
                 runtime_env={"env_vars": {"PYTHONPATH": pythonpath, "OMP_NUM_THREADS": "1",
                                           "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}})
        actor_class = ray.remote(num_cpus=1, max_concurrency=1, max_restarts=0, max_task_retries=0)(RayContextWorker)
        for index, cpu in enumerate(cpu_ids):
            actors.append(actor_class.remote(index, cpu, queries, args, driver_factory))
        worker_info = ray.get([actor.ready.remote() for actor in actors], timeout=args.startup_timeout)
        if len({info["worker_pid"] for info in worker_info}) != args.max_workers:
            raise RuntimeError("Ray 未创建预期数量的独立 Actor 进程")
        for cpu, info in zip(cpu_ids, worker_info):
            if info["affinity_cpus"] != [cpu]:
                raise RuntimeError(f"Actor CPU 绑定校验失败：{info}")
        # All actors are ready before submitting any query. Keep only one call per actor in flight.
        query_phase_start = time.perf_counter()
        pending = {}
        for actor in actors:
            job = next(jobs, None)
            if job is not None:
                pending[actor.execute.remote(*job)] = actor
        while pending:
            ready, _ = ray.wait(list(pending), num_returns=1)
            ref = ready[0]
            actor = pending.pop(ref)
            context, query_records = ray.get(ref)
            contexts.append(context)
            records.extend(query_records)
            shared.append_jsonl(args.context_jsonl, context, log_lock)
            shared.append_jsonl(args.query_log_jsonl, query_records, log_lock)
            if args.print_context:
                print(json.dumps(context, ensure_ascii=False, indent=2))
            if args.progress_every and len(contexts) % args.progress_every == 0:
                print(f"进度: {len(contexts)}/{total_jobs} contexts")
            job = next(jobs, None)
            if job is not None:
                pending[actor.execute.remote(*job)] = actor
        query_phase_end = time.perf_counter()
    finally:
        close_refs = []
        for actor in actors:
            try:
                close_refs.append(actor.close.remote())
            except Exception as exc:
                cleanup_errors.append(type(exc).__name__)
        if close_refs:
            try:
                ray.get(close_refs, timeout=10)
            except Exception as exc:
                cleanup_errors.append(type(exc).__name__)
        for actor in actors:
            try:
                ray.kill(actor, no_restart=True)
            except Exception as exc:
                cleanup_errors.append(type(exc).__name__)
        ray.shutdown()
    wall_end = time.perf_counter()
    wall_ms = (wall_end - wall_start) * 1000
    summary = shared.summarize(records, contexts, wall_ms)
    summary.update(
        concurrency_mode="ray", max_workers=args.max_workers, ray_cpus_per_actor=1,
        started_worker_count=len(worker_info), worker_info=worker_info, cpu_ids=cpu_ids,
        observed_worker_count=len({c["worker_pid"] for c in contexts}),
        worker_pids=sorted({c["worker_pid"] for c in contexts}),
        max_connection_pool_size_per_process=args.max_connection_pool_size,
        cpu_binding="distinct_physical_cores", system_cpu_exclusivity=False,
        ray_startup_ms=round((query_phase_start - wall_start) * 1000, 3),
        query_phase_wall_ms=round((query_phase_end - query_phase_start) * 1000, 3),
        ray_cleanup_ms=round((wall_end - query_phase_end) * 1000, 3),
        ray_temp_dir=ray_temp_dir, cleanup_errors=cleanup_errors,
    )
    if args.summary_json:
        path = Path(args.summary_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return contexts, records, summary


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Ray 多进程 LLM 上下文查询；默认 100 个 Actor，每个绑定不同物理核心。",
        epilog="num_cpus=1 预留 Ray 资源；CPU affinity 约束执行位置，不能排除其他系统进程。")
    parser.add_argument("--uri", default=os.environ.get("NEO4J_URI", shared.DEFAULT_URI))
    parser.add_argument("--user", default=os.environ.get("NEO4J_USER", shared.DEFAULT_USER))
    parser.add_argument("--password", default=os.environ.get("NEO4J_PASSWORD", shared.DEFAULT_PASSWORD))
    parser.add_argument("--database", default=os.environ.get("NEO4J_DATABASE"))
    parser.add_argument("--run-id", default=os.environ.get("KG_RUN_ID"))
    parser.add_argument("--queries", default="core", help="查询包、id、范围或类别，例如 all、i0-i5")
    parser.add_argument("--query-file", type=Path)
    parser.add_argument("--replace-default-queries", action="store_true")
    parser.add_argument("--list-queries", action="store_true")
    parser.add_argument("--drone-id", action="append", default=[])
    parser.add_argument("--drone-ids", help="逗号分隔 drone id；未指定时生成 drone-1 至 drone-N，N 为进程数")
    parser.add_argument("--step-index", type=int, default=shared.env_int("KG_STEP_INDEX"))
    parser.add_argument("--time-minute", type=float, default=shared.env_float("KG_TIME_MINUTE"))
    parser.add_argument("--history-steps", type=shared.positive_int, default=20)
    parser.add_argument("--event-window-minutes", type=float, default=30.0)
    parser.add_argument("--deadline-horizon-minutes", type=float, default=60.0)
    shared.add_context_arguments(parser)
    parser.add_argument("--limit", type=shared.positive_int, default=10)
    parser.add_argument("--repeat", type=shared.positive_int, default=1)
    parser.add_argument("--max-workers", type=shared.positive_int, default=DEFAULT_WORKERS,
                        help="Actor 工作进程数，默认 100；每进程预留 1 个 Ray CPU 并绑定不同物理核心")
    parser.add_argument("--cpu-ids", help="指定逻辑 CPU 编号，例如 0-99；数量须等于进程数且属于不同物理核心")
    parser.add_argument("--startup-timeout", type=shared.positive_int, default=180,
                        help="等待全部 Actor 初始化的超时秒数，默认 180")
    parser.add_argument("--max-connection-pool-size", type=shared.positive_int, default=1, help="每个 Actor 的连接池上限，默认 1")
    parser.add_argument("--param", action="append", type=shared.parse_param, default=[])
    parser.add_argument("--query-log-jsonl", type=Path, default=DEFAULT_QUERY_LOG_PATH)
    parser.add_argument("--context-jsonl", type=Path, default=DEFAULT_CONTEXT_LOG_PATH)
    parser.add_argument("--summary-json", type=Path, default=DEFAULT_SUMMARY_PATH)
    parser.add_argument("--no-query-log", action="store_true")
    parser.add_argument("--no-context-log", action="store_true")
    parser.add_argument("--include-cypher", action="store_true")
    parser.add_argument("--print-context", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="打印查询计划，不启动 Ray、不连接数据库")
    parser.add_argument("--progress-every", type=shared.non_negative_int, default=20)
    args = parser.parse_args(argv)
    if not args.drone_id and not args.drone_ids:
        args.drone_ids = ",".join(f"drone-{i + 1}" for i in range(args.max_workers))
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
    selected = shared.select_queries(args.queries, queries=queries)
    if args.dry_run:
        shared.print_dry_run(selected, args)
        return
    if not args.run_id:
        raise SystemExit("需要 --run-id 或 KG_RUN_ID")
    cpu_ids = choose_cpu_ids(args.max_workers, args.cpu_ids)
    if args.time_minute is None and shared.queries_require_params(selected, {"time_minute", "start_time"}):
        driver = shared.make_driver(args.uri, args.user, args.password, max_connection_pool_size=1)
        try:
            shared.resolve_time_args(driver, args, selected)
        finally:
            driver.close()
    print(f"连接: {args.uri} user={args.user} database={args.database}")
    print(f"Ray Actor 数: {args.max_workers}，每 Actor 预留 1 CPU；绑定 CPU: {cpu_ids}")
    print("每个 Actor 使用不同物理核心；其他系统进程仍可能在这些核心上运行。")
    for label, path in (("查询日志", args.query_log_jsonl), ("上下文日志", args.context_jsonl), ("汇总日志", args.summary_json)):
        if path:
            print(f"{label}: {path}")
    _, _, summary = run_context_benchmark(selected, args, cpu_ids=cpu_ids)
    print(f"已启动 Actor: {summary['started_worker_count']}，有查询返回的 Actor: {summary['observed_worker_count']}")
    shared.print_summary(summary)


if __name__ == "__main__":
    main()
