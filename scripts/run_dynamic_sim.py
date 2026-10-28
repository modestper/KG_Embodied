import argparse
import json
import logging
import os
import re
import sys
import threading
import time
from datetime import datetime
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

VIEWER_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = VIEWER_ROOT.parents[0]
LLM_LOG_ROOT = VIEWER_ROOT / "data" / "llm_logs"
REGION_BOUNDARY_PATH = VIEWER_ROOT / "data" / "region_boundaries.json"
sys.path.insert(0, str(VIEWER_ROOT))

from scripts.build_delivery_sim import CITY_FILES
from simulation.delivery_sim import (
    SimulationEnv,
    build_depot,
    build_region_depots,
    load_fixed_region_depots,
    load_delivery_tasks,
    make_drones,
    make_region_drones,
)
from simulation.planner import build_coordinator_from_env, build_planner_from_env
from simulation.knowledge import build_knowledge_store_from_env
from simulation.knowledge_writer import WRITE_MODES, build_delegating_store
from simulation.knowledge_state import STATE_SOURCES
from simulation.graph_query import GraphQueryError, GraphQueryService, MAX_REQUEST_BYTES


def format_sse(event, payload):
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"


def step_once(env):
    if hasattr(env, "step_once"):
        return env.step_once()
    env.release_and_assign()
    completed_this_step = []
    next_time = env.now_minute + env.step_minutes
    for drone in env.drones:
        completed_tasks = drone.step(env.step_minutes, next_time)
        for task in completed_tasks:
            env.knowledge_store.record_completion(next_time, task, drone)
        completed_this_step.extend(completed_tasks)
    env.now_minute = next_time
    frame = env.snapshot(completed_this_step)
    env.frames.append(frame)
    return frame


def metrics(env):
    completed = [task for task in env.tasks if task.status == "completed"]
    late = [task for task in completed if task.completed_at and task.completed_at > task.deadline_minute]
    return {
        "total_tasks": len(env.tasks),
        "completed_tasks": len(completed),
        "unfinished_tasks": len(env.tasks) - len(completed),
        "late_tasks": len(late),
        "total_distance_m": round(sum(drone.total_distance_m for drone in env.drones), 1),
        "drones": len(env.drones),
    }


def task_payload(task):
    return {
        "id": task.task_id,
        "city": task.city,
        "region_id": task.region_id,
        "pickup_x": task.pickup_x,
        "pickup_y": task.pickup_y,
        "x": task.x,
        "y": task.y,
        "release": task.release_minute,
        "deadline": task.deadline_minute,
    }


def active_region_boundaries(city, tasks):
    active_region_ids = {task.region_id for task in tasks}
    boundaries = load_region_boundaries(city)
    return [
        {
            "region_id": region["region_id"],
            "task_count": region["task_count"],
            "centroid": region["centroid"],
            "outline": region["polygon"],
        }
        for region in boundaries
        if region["region_id"] in active_region_ids
    ]


def load_region_boundaries(city):
    if not REGION_BOUNDARY_PATH.exists():
        return []
    payload = json.loads(REGION_BOUNDARY_PATH.read_text(encoding="utf-8"))
    return payload.get("cities", {}).get(city, {}).get("regions", [])


def stream_simulation(env, config, depots, max_steps, frame_stride=1):
    yield format_sse(
        "init",
        {
            "config": config,
            "depots": depots,
            "regions": active_region_boundaries(config.get("city"), env.tasks),
            "tasks": [task_payload(task) for task in env.tasks],
            "metrics": metrics(env),
            "step_seconds": round(env.step_minutes * 60),
        },
    )
    steps_run = 0
    for step in range(max_steps):
        frame = step_once(env)
        steps_run = step + 1
        if step % frame_stride == 0:
            yield format_sse(
                "frame",
                {
                    "frame_index": step,
                    "frame": frame,
                    "metrics": metrics(env),
                    "kg_events": env.knowledge_store.events[-10:],
                    "llm_events": llm_events(env)[-5:],
                },
            )
        if env.is_complete():
            break
    flush = getattr(env.knowledge_store, "flush", None)
    if flush is not None:
        flush()
    yield format_sse(
        "done",
        {
            "metrics": metrics(env),
            "kg_events": env.knowledge_store.events[-20:],
            "llm_events": llm_events(env)[-10:],
            "reason": "all_tasks_completed" if env.is_complete() else "max_steps_reached",
            "steps_run": steps_run,
            "final_time": env.snapshot([])["time_label"],
        },
    )


def llm_events(env):
    return getattr(env.coordinator, "llm_events", [])


def build_runtime(args):
    state_source = getattr(args, "state_source", os.environ.get("KG_STATE_SOURCE", "ray"))
    if state_source not in STATE_SOURCES:
        raise ValueError(f"Unsupported state source: {state_source}")
    if state_source == "neo4j":
        if args.backend != "ray":
            raise ValueError("--state-source neo4j requires --backend ray")
        if os.environ.get("KG_BACKEND", "memory").lower() not in {"neo4j", "graph"}:
            raise ValueError("--state-source neo4j requires KG_BACKEND=neo4j")
    write_mode = getattr(args, "kg_write_mode", os.environ.get("KG_WRITE_MODE", "main"))
    if write_mode not in WRITE_MODES:
        raise ValueError(f"Unsupported KG write mode: {write_mode}")
    if write_mode == "ray" and args.backend != "ray":
        raise ValueError("--kg-write-mode ray requires --backend ray")
    if write_mode != "main" and os.environ.get("KG_BACKEND", "memory").lower() not in {"neo4j", "graph"}:
        raise ValueError("Offloaded KG writes require KG_BACKEND=neo4j")
    args.llm_log_path = str(make_llm_log_path(args)) if args.use_llm else None
    day_filter = None if args.day in ("all", "ALL", "*", "") else args.day
    delivery_file = CITY_FILES[args.city]
    tasks = load_delivery_tasks(
        DATA_ROOT / "delivery" / delivery_file,
        city=args.city,
        day=day_filter,
        limit=args.task_limit,
        sample_seed=args.seed,
    )
    region_depots = build_region_depots(tasks)
    charging_depots = load_fixed_region_depots(args.city)
    depot = build_depot(tasks)
    if args.depot_mode == "region":
        drones = make_region_drones(args.drone_count, region_depots, capacity=6, seed=args.seed)
    else:
        drones = make_drones(args.drone_count, depot, capacity=6)
    with planner_environment(args):
        planner = build_planner_from_env()
        for drone in drones:
            drone.planner = planner
        coordinator = build_coordinator_from_env()
    if write_mode == "main":
        knowledge_store = build_knowledge_store_from_env()
    else:
        ray_module = None
        if write_mode == "ray":
            initialize_ray_if_needed(args)
            import ray as ray_module
        knowledge_store = build_delegating_store(write_mode, ray_module=ray_module)
    try:
        if args.backend == "ray":
            from simulation.ray_delivery_sim import RaySimulationEnv

            env = RaySimulationEnv(
                tasks,
                drones,
                coordinator,
                step_minutes=args.step_seconds / 60,
                knowledge_store=knowledge_store,
                ray_address=args.ray_address or None,
                ray_num_cpus=args.ray_num_cpus,
                charging_depots=list(charging_depots.values()),
                state_source=state_source,
            )
        else:
            env = SimulationEnv(
                tasks,
                drones,
                coordinator,
                step_minutes=args.step_seconds / 60,
                knowledge_store=knowledge_store,
                charging_depots=list(charging_depots.values()),
            )
    except Exception:
        knowledge_store.close()
        raise
    config = {
        "city": tasks[0].city if tasks else args.city,
        "day": args.day if day_filter is not None else "all",
        "task_limit": args.task_limit,
        "drone_count": args.drone_count,
        "coordinator_class": coordinator.__class__.__name__,
        "planner": planner.__class__.__name__,
        "depot_mode": args.depot_mode,
        "region_count": len(charging_depots),
        "active_region_count": len(region_depots),
        "llm_enabled": args.use_llm,
        "vllm_api_base": args.vllm_api_base,
        "vllm_model": args.vllm_model,
        "llm_max_tokens": args.llm_max_tokens,
        "llm_log_path": args.llm_log_path,
        "max_steps": args.max_steps,
        "frame_stride": args.frame_stride,
        "wall_interval": args.wall_interval,
        "backend": args.backend,
        "ray_address": args.ray_address,
        "ray_num_cpus": args.ray_num_cpus,
        "kg_write_mode": write_mode,
        "kg_run_id": getattr(knowledge_store, "run_id", None),
        "state_source": state_source if args.backend == "ray" else "memory",
        "state_execution_id": getattr(env, "execution_id", None),
    }
    write_run_start(args.llm_log_path, config)
    return env, config, list(charging_depots.values())


def make_llm_log_path(args):
    timestamp = datetime.now().strftime("%m%d_%H%M%S")
    parts = [
        "llm",
        timestamp,
        safe_slug(args.city),
        safe_slug(args.day),
        f"{args.task_limit}tasks",
        f"{args.drone_count}agents",
        f"{args.max_steps}steps",
        f"{args.frame_stride}stride",
        f"{args.llm_max_tokens}tok",
        safe_slug(args.vllm_model),
    ]
    return LLM_LOG_ROOT / ("_".join(parts) + ".jsonl")


def safe_slug(value):
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value)).strip("-") or "unknown"


def write_run_start(path, config):
    if not path:
        return
    log_path = Path(path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "event": "run_start",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "config": config,
    }
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


class planner_environment:
    def __init__(self, args):
        self.args = args
        self.keys = [
            "DRONE_COORDINATOR",
            "DRONE_PLANNER",
            "VLLM_API_BASE",
            "VLLM_MODEL",
            "VLLM_API_KEY",
            "LLM_API_BASE",
            "LLM_MODEL",
            "LLM_API_KEY",
            "LLM_MAX_TOKENS",
            "LLM_LOG_PATH",
        ]
        self.previous = {}

    def __enter__(self):
        self.previous = {key: os.environ.get(key) for key in self.keys}
        if self.args.use_llm:
            os.environ["DRONE_COORDINATOR"] = "llm-region"
            os.environ["VLLM_API_BASE"] = self.args.vllm_api_base
            os.environ["VLLM_MODEL"] = self.args.vllm_model
            os.environ["LLM_MAX_TOKENS"] = str(self.args.llm_max_tokens)
            if self.args.llm_log_path:
                os.environ["LLM_LOG_PATH"] = self.args.llm_log_path
        else:
            os.environ["DRONE_COORDINATOR"] = "auction"
            os.environ["DRONE_PLANNER"] = "rule"
        return self

    def __exit__(self, exc_type, exc, tb):
        for key, value in self.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class DynamicHandler(SimpleHTTPRequestHandler):
    runtime_args = None
    active_env = None
    active_env_lock = threading.Lock()
    query_service = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(VIEWER_ROOT), **kwargs)

    @classmethod
    def replace_active_env(cls, env):
        with cls.active_env_lock:
            previous = cls.active_env
            cls.active_env = env
        close_env(previous)

    @classmethod
    def clear_active_env(cls, env):
        with cls.active_env_lock:
            if cls.active_env is env:
                cls.active_env = None
        close_env(env)

    @classmethod
    def is_active_env(cls, env):
        with cls.active_env_lock:
            return cls.active_env is env

    def notify_superseded(self):
        try:
            event = format_sse("superseded", {"reason": "new_stream_started"})
            self.wfile.write(event.encode("utf-8"))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def do_GET(self):
        if urlparse(self.path).path == "/api/graph-query/config":
            service = self.query_service
            if service is None:
                return self.send_query_json(503, {"ok": False, "error": "查询服务未启动。"})
            return self.send_query_json(200, service.config())
        if self.path in ("/", "/dynamic"):
            self.path = "/dynamic.html"
            return super().do_GET()
        if self.path.startswith("/events"):
            return self.handle_events()
        return super().do_GET()

    def send_query_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if urlparse(self.path).path != "/api/graph-query":
            return self.send_query_json(404, {"ok": False, "error": "接口不存在。"})
        try:
            # No CORS: a foreign site must not submit queries through the viewer.
            origin = self.headers.get("Origin")
            if origin:
                parsed = urlparse(origin)
                if parsed.scheme not in ("http", "https") or parsed.netloc != self.headers.get("Host"):
                    raise GraphQueryError("不允许跨站提交查询。", 403, "origin_rejected")
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                raise GraphQueryError("不允许跨站提交查询。", 403, "origin_rejected")
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                raise GraphQueryError("请使用 application/json 提交查询。", 415)
            if self.headers.get("Transfer-Encoding"):
                raise GraphQueryError("不支持分块请求体。")
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise GraphQueryError("Content-Length 不合法。") from None
            if not 0 < length <= MAX_REQUEST_BYTES:
                raise GraphQueryError("请求体为空或超过 64 KiB。", 413)
            previous_timeout = self.connection.gettimeout()
            try:
                self.connection.settimeout(10)
                body = self.rfile.read(length)
            except TimeoutError:
                raise GraphQueryError("读取查询请求超时。", 408, "request_timeout") from None
            finally:
                self.connection.settimeout(previous_timeout)
            try:
                payload = json.loads(body.decode("utf-8"))
            except (ValueError, UnicodeError, RecursionError):
                raise GraphQueryError("请求体不是合法的 UTF-8 JSON。") from None
            if self.query_service is None:
                raise GraphQueryError("查询服务未启动。", 503)
            return self.send_query_json(200, self.query_service.execute(payload))
        except GraphQueryError as error:
            return self.send_query_json(error.status, {"ok": False, "error": str(error), "code": error.code})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            # Do not return tracebacks, credentials or connection URIs to the browser.
            logging.getLogger(__name__).exception("Manual graph query failed")
            return self.send_query_json(500, {"ok": False, "error": "查询服务内部错误，请检查服务端日志。"})

    def handle_events(self):
        runtime_args = args_from_request(self.runtime_args, self.path)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        env = None
        try:
            self.replace_active_env(None)
            initialize_ray_if_needed(runtime_args)
            env, config, depots = build_runtime(runtime_args)
            self.replace_active_env(env)
            for event in stream_simulation(env, config, depots, runtime_args.max_steps, runtime_args.frame_stride):
                if not self.is_active_env(env):
                    self.notify_superseded()
                    return
                self.wfile.write(event.encode("utf-8"))
                self.wfile.flush()
                time.sleep(runtime_args.wall_interval)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            if env is not None and not self.is_active_env(env):
                self.notify_superseded()
                return
            raise
        finally:
            self.clear_active_env(env)


def close_env(env):
    close = getattr(env, "close", None)
    try:
        if close:
            close()
    finally:
        store_close = getattr(getattr(env, "knowledge_store", None), "close", None)
        if store_close:
            store_close()


def args_from_request(base_args, path):
    query = parse_qs(urlparse(path).query)
    values = vars(base_args).copy()
    values.setdefault("kg_write_mode", os.environ.get("KG_WRITE_MODE", "main"))
    values.setdefault("state_source", os.environ.get("KG_STATE_SOURCE", "ray"))

    for key in ("city", "day", "depot_mode", "vllm_api_base", "vllm_model", "backend", "ray_address", "kg_write_mode", "state_source"):
        if key in query:
            values[key] = query[key][0]
    for key in (
        "task_limit",
        "drone_count",
        "step_seconds",
        "max_steps",
        "frame_stride",
        "seed",
        "llm_max_tokens",
        "ray_num_cpus",
    ):
        if key in query:
            values[key] = parse_int(query[key][0], values[key], minimum=1)
    if "wall_interval" in query:
        values["wall_interval"] = parse_float(query["wall_interval"][0], values["wall_interval"], minimum=0)
    if "use_llm" in query:
        values["use_llm"] = query["use_llm"][0].lower() in ("1", "true", "yes", "on")

    if values["city"] not in CITY_FILES:
        values["city"] = base_args.city
    if values["depot_mode"] not in ("region", "center"):
        values["depot_mode"] = base_args.depot_mode
    if values.get("backend") not in ("local", "ray"):
        values["backend"] = getattr(base_args, "backend", "local")
    if values["kg_write_mode"] not in WRITE_MODES:
        values["kg_write_mode"] = getattr(base_args, "kg_write_mode", "main")
    if values["state_source"] not in STATE_SOURCES:
        values["state_source"] = getattr(base_args, "state_source", "ray")
    values["task_limit"] = min(values["task_limit"], 10000)
    values["drone_count"] = min(values["drone_count"], 1000)
    values["max_steps"] = min(values["max_steps"], 100000)
    values["llm_max_tokens"] = min(values["llm_max_tokens"], 4096)
    if values["backend"] == "ray":
        values["ray_num_cpus"] = max(values.get("ray_num_cpus") or 0, values["drone_count"])
    return argparse.Namespace(**values)


def parse_int(value, default, minimum=0):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, parsed)


def parse_float(value, default, minimum=0):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, parsed)


def initialize_ray_if_needed(args):
    if args.backend != "ray":
        return
    import ray

    requested_cpus = args.ray_num_cpus or getattr(args, "drone_count", None)
    init_kwargs = {}
    if args.ray_address:
        init_kwargs["address"] = args.ray_address
    if requested_cpus is not None:
        init_kwargs["num_cpus"] = requested_cpus

    if ray.is_initialized():
        current_cpus = ray.cluster_resources().get("CPU", 0)
        minimum_cpus = requested_cpus or current_cpus
        if current_cpus >= minimum_cpus:
            return
        ray.shutdown()
    ray.init(**init_kwargs)


def parse_args():
    parser = argparse.ArgumentParser(description="Run live drone delivery simulation viewer.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--city", default="Jilin", choices=sorted(CITY_FILES))
    parser.add_argument("--day", default="0925")
    parser.add_argument("--task-limit", type=int, default=20)
    parser.add_argument("--drone-count", type=int, default=3)
    parser.add_argument("--step-seconds", type=int, default=20)
    parser.add_argument("--max-steps", type=int, default=2000)
    parser.add_argument("--frame-stride", type=int, default=1)
    parser.add_argument("--wall-interval", type=float, default=0.25)
    parser.add_argument("--depot-mode", choices=["region", "center"], default="region")
    parser.add_argument("--seed", type=int, default=13)
    parser.add_argument("--use-llm", action="store_true")
    parser.add_argument("--vllm-api-base", default=os.environ.get("VLLM_API_BASE", "http://127.0.0.1:6666/v1"))
    parser.add_argument("--vllm-model", default=os.environ.get("VLLM_MODEL", "qwen2.5-7b"))
    parser.add_argument("--llm-max-tokens", type=int, default=int(os.environ.get("LLM_MAX_TOKENS", "512")))
    parser.add_argument("--backend", choices=["local", "ray"], default="ray")
    parser.add_argument("--kg-write-mode", choices=WRITE_MODES, default=os.environ.get("KG_WRITE_MODE", "main"))
    parser.add_argument("--state-source", choices=STATE_SOURCES, default=os.environ.get("KG_STATE_SOURCE", "ray"))
    parser.add_argument("--ray-address", default=os.environ.get("RAY_ADDRESS", ""))
    parser.add_argument("--ray-num-cpus", type=int, default=None)
    return parser.parse_args()


def main():
    args = parse_args()
    DynamicHandler.runtime_args = args
    initialize_ray_if_needed(args)
    DynamicHandler.query_service = GraphQueryService()
    server = ThreadingHTTPServer((args.host, args.port), DynamicHandler)
    print(f"Dynamic viewer: http://{args.host}:{args.port}/dynamic.html")
    try:
        server.serve_forever()
    finally:
        server.server_close()
        DynamicHandler.query_service.close()


if __name__ == "__main__":
    main()
