import argparse
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path


VIEWER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_ROOT))

from scripts.build_delivery_sim import CITY_FILES
from scripts.run_dynamic_sim import (
    build_runtime,
    close_env,
    initialize_ray_if_needed,
    metrics,
)
from simulation.checkpoint import (
    CheckpointError,
    build_checkpoint,
    read_checkpoint,
    validate_checkpoint,
    write_checkpoint,
)
from simulation.knowledge_writer import WRITE_MODES
from simulation.knowledge_state import STATE_SOURCES


class StepCounter:
    """Provide the frame API needed by the environments without retaining payloads."""

    def __init__(self, count=0):
        self.count = count

    def append(self, _frame):
        self.count += 1

    def __len__(self):
        return self.count


def env_bool(name, default):
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_value(name, default):
    value = os.environ.get(name)
    return value if value is not None and value.strip() else default


def env_int(name, default):
    return int(env_value(name, str(default)))


def env_float(name, default):
    return float(env_value(name, str(default)))


def configure_knowledge_store(args):
    os.environ["KG_BACKEND"] = args.kg_backend
    os.environ["KG_RUN_ID"] = args.run_id
    os.environ["KG_RECORD_AGENT_STATE"] = "1" if args.record_agent_state else "0"


def disable_frame_retention(env, step_index=0):
    env.frames = StepCounter(step_index)
    env.snapshot = lambda _completed: None


def default_checkpoint_path(run_id):
    safe_run_id = re.sub(r"[^A-Za-z0-9._-]+", "_", run_id).strip("._")
    if not safe_run_id:
        raise CheckpointError("run_id cannot produce an empty checkpoint filename")
    return VIEWER_ROOT / "data" / "checkpoints" / f"{safe_run_id}.json"


def resolve_checkpoint_path(args):
    return Path(args.checkpoint_path) if args.checkpoint_path else default_checkpoint_path(args.run_id)


def capture_checkpoint(env, args, step_index, completed_this_step=None):
    if not hasattr(env, "checkpoint_state"):
        raise CheckpointError(f"backend {args.backend!r} does not support checkpoints")
    state = env.checkpoint_state()
    state["completed_this_step_ids"] = [
        task.task_id for task in (completed_this_step or [])
    ]
    return build_checkpoint(
        args,
        step_index=step_index,
        now_minute=env.now_minute,
        state=state,
    )


def verify_resume_position(env, checkpoint):
    latest_step = getattr(env.knowledge_store, "latest_step", lambda: None)()
    if latest_step is None:
        if checkpoint["step_index"] != 0:
            raise CheckpointError(
                "Neo4j has no Step for a non-zero checkpoint; refusing to resume"
            )
        return "aligned"
    if latest_step["step_index"] != checkpoint["step_index"]:
        if latest_step["step_index"] == checkpoint["step_index"] - 1:
            expected_time = latest_step["time_minute"] + args_step_minutes(checkpoint)
            if abs(expected_time - checkpoint["now_minute"]) <= 1e-6:
                return "checkpoint_ahead"
        raise CheckpointError(
            "Neo4j/checkpoint step mismatch: "
            f"neo4j={latest_step['step_index']} checkpoint={checkpoint['step_index']}"
        )
    if abs(latest_step["time_minute"] - checkpoint["now_minute"]) > 1e-6:
        raise CheckpointError(
            "Neo4j/checkpoint time mismatch: "
            f"neo4j={latest_step['time_minute']} checkpoint={checkpoint['now_minute']}"
        )
    return "aligned"


def args_step_minutes(checkpoint):
    return checkpoint["config"]["step_seconds"] / 60


def commit_checkpoint_ahead_step(env, checkpoint):
    task_by_id = {task.task_id: task for task in env.tasks}
    completed = []
    for task_id in checkpoint["state"].get("completed_this_step_ids", []):
        task = task_by_id.get(task_id)
        if task is None:
            raise CheckpointError(
                f"checkpoint completed task is absent from scenario: {task_id}"
            )
        completed.append(task)
    env.knowledge_store.record_step(
        checkpoint["step_index"],
        checkpoint["now_minute"],
        env.tasks,
        env.drones,
        completed,
    )


def is_recoverable_ray_error(exc):
    if exc.__class__.__name__ in {
        "ActorDiedError",
        "ActorUnavailableError",
        "OutOfMemoryError",
        "RayActorError",
    }:
        return True
    return exc.__cause__ is not None and is_recoverable_ray_error(exc.__cause__)


def available_memory_gb(meminfo_path="/proc/meminfo"):
    try:
        values = {}
        with open(meminfo_path, encoding="ascii") as handle:
            for line in handle:
                name, value = line.split(":", 1)
                values[name] = int(value.strip().split()[0])
        return values["MemAvailable"] / (1024 * 1024)
    except (OSError, KeyError, ValueError):
        return None


def wait_for_ray_recovery_memory(args):
    minimum = args.ray_min_available_memory_gb
    if minimum <= 0:
        return
    while True:
        available = available_memory_gb()
        if available is None or available >= minimum:
            return
        print(
            "[headless] waiting for memory before Ray actor restore "
            f"available_gb={available:.1f} required_gb={minimum:.1f} "
            f"poll_seconds={args.ray_memory_poll_interval}",
            flush=True,
        )
        time.sleep(args.ray_memory_poll_interval)


def close_ray_actors_for_recovery(env):
    close = getattr(env, "close", None)
    if close is None:
        return
    try:
        close()
    except Exception as exc:  # noqa: BLE001 - cleanup is best effort after actor death.
        print(
            "[headless] Ray actor cleanup before restore failed "
            f"error={type(exc).__name__}: {exc}",
            flush=True,
        )


def step_with_ray_recovery(env, args, checkpoint):
    attempts = args.ray_recovery_attempts if args.backend == "ray" else 0
    try:
        return env.step_once()
    except Exception as exc:  # noqa: BLE001 - Ray wraps failures differently by version.
        if attempts <= 0 or not is_recoverable_ray_error(exc):
            raise
        failure = exc

    for attempt in range(1, attempts + 1):
        delay = args.ray_recovery_delay * (2 ** (attempt - 1))
        print(
            "[headless] ray actor failure; restoring checkpoint "
            f"step={checkpoint['step_index']} attempt={attempt}/{attempts} "
            f"delay_seconds={delay} error={type(failure).__name__}: {failure}",
            flush=True,
        )
        close_ray_actors_for_recovery(env)
        if delay > 0:
            time.sleep(delay)
        wait_for_ray_recovery_memory(args)
        try:
            env.restore_checkpoint_state(checkpoint["state"])
        except Exception as exc:  # noqa: BLE001 - actor creation can also be OOM-killed.
            if not is_recoverable_ray_error(exc) or attempt >= attempts:
                raise
            print(
                "[headless] Ray actor restore failed; will retry "
                f"step={checkpoint['step_index']} attempt={attempt}/{attempts} "
                f"error={type(exc).__name__}: {exc}",
                flush=True,
            )
            failure = exc
            continue

        try:
            return env.step_once()
        except Exception as exc:  # noqa: BLE001 - retry the whole checkpoint on Ray failure.
            if not is_recoverable_ray_error(exc) or attempt >= attempts:
                raise
            failure = exc

    raise failure


def shutdown_ray(args):
    if args.backend != "ray":
        return
    import ray

    if ray.is_initialized():
        ray.shutdown()


def progress_payload(env, step_index, started):
    current_metrics = metrics(env)
    return {
        "step": step_index,
        "elapsed_seconds": round(time.perf_counter() - started, 1),
        "time_minute": round(env.now_minute, 3),
        **current_metrics,
    }


def print_progress(env, step_index, started):
    values = progress_payload(env, step_index, started)
    fields = " ".join(f"{key}={value}" for key, value in values.items())
    print(f"[headless] {fields}", flush=True)


def sleep_after_step(args):
    wall_interval = getattr(args, "wall_interval", 0.0)
    if wall_interval > 0:
        time.sleep(wall_interval)


def run_headless(args):
    configure_knowledge_store(args)
    initialize_ray_if_needed(args)
    env = None
    started = time.perf_counter()
    try:
        env, config, _depots = build_runtime(args)
        checkpoint_path = resolve_checkpoint_path(args)
        if args.resume:
            checkpoint = validate_checkpoint(read_checkpoint(checkpoint_path), args)
            resume_position = verify_resume_position(env, checkpoint)
            env.restore_checkpoint_state(checkpoint["state"])
            disable_frame_retention(env, checkpoint["step_index"])
            if resume_position == "checkpoint_ahead":
                commit_checkpoint_ahead_step(env, checkpoint)
                print(
                    "[headless] committed checkpoint-ahead step "
                    f"step={checkpoint['step_index']}",
                    flush=True,
                )
            print(
                "[headless] resumed "
                f"checkpoint={checkpoint_path} step={checkpoint['step_index']} "
                f"time_minute={checkpoint['now_minute']}",
                flush=True,
            )
        else:
            disable_frame_retention(env)
            checkpoint = capture_checkpoint(env, args, 0)
            if args.checkpoint_enabled:
                if checkpoint_path.exists() and not args.overwrite_checkpoint:
                    raise CheckpointError(
                        f"checkpoint already exists: {checkpoint_path}; "
                        "use --resume or explicitly pass --overwrite-checkpoint"
                    )
                write_checkpoint(checkpoint_path, checkpoint)

        checkpoint_holder = {"value": checkpoint}

        def save_before_step_commit(current_env, step_index, completed_this_step):
            next_checkpoint = capture_checkpoint(
                current_env,
                args,
                step_index,
                completed_this_step,
            )
            if args.checkpoint_enabled:
                write_checkpoint(checkpoint_path, next_checkpoint)
            checkpoint_holder["value"] = next_checkpoint

        if hasattr(env, "before_step_commit"):
            env.before_step_commit = save_before_step_commit
        print(
            "[headless] started "
            f"run_id={args.run_id} backend={config['backend']} city={config['city']} "
            f"day={config['day']} tasks={len(env.tasks)} drones={len(env.drones)} "
            f"max_steps={args.max_steps} kg_backend={args.kg_backend} "
            f"kg_write_mode={config.get('kg_write_mode', 'main')} "
            f"state_source={config.get('state_source', 'ray')} "
            f"record_agent_state={int(args.record_agent_state)} "
            f"checkpoint={checkpoint_path}",
            flush=True,
        )

        reason = "max_steps_reached"
        start_step = len(env.frames)
        for _ in range(start_step, args.max_steps):
            previous_step = checkpoint_holder["value"]["step_index"]
            step_with_ray_recovery(env, args, checkpoint_holder["value"])
            sleep_after_step(args)
            step_index = len(env.frames)
            if checkpoint_holder["value"]["step_index"] == previous_step:
                checkpoint_holder["value"] = capture_checkpoint(env, args, step_index)
                if args.checkpoint_enabled:
                    write_checkpoint(checkpoint_path, checkpoint_holder["value"])
            if args.progress_every and step_index % args.progress_every == 0:
                print_progress(env, step_index, started)
            if env.is_complete():
                reason = "all_tasks_completed"
                break

        step_index = len(env.frames)
        if not args.progress_every or step_index % args.progress_every != 0:
            print_progress(env, step_index, started)
        flush = getattr(env.knowledge_store, "flush", None)
        if flush is not None:
            flush()
        result = {
            "run_id": args.run_id,
            "reason": reason,
            "steps_run": step_index,
            "expected_agent_states": step_index * len(env.drones) if args.record_agent_state else 0,
            "metrics": metrics(env),
        }
        print(
            "[headless] finished "
            f"run_id={result['run_id']} reason={reason} steps_run={step_index} "
            f"expected_agent_states={result['expected_agent_states']}",
            flush=True,
        )
        return result
    finally:
        try:
            if env is not None:
                close_env(env)
        finally:
            shutdown_ray(args)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run drone delivery simulation without an HTTP server.")
    parser.add_argument("--city", default=env_value("CITY", "Hangzhou"), choices=sorted(CITY_FILES))
    parser.add_argument("--day", default=env_value("DAY", "0621"))
    parser.add_argument("--task-limit", type=int, default=env_int("TASK_LIMIT", 10424))
    parser.add_argument("--drone-count", type=int, default=env_int("DRONE_COUNT", 100))
    parser.add_argument("--step-seconds", type=int, default=env_int("STEP_SECONDS", 6))
    parser.add_argument("--max-steps", type=int, default=env_int("MAX_STEPS", 10000))
    parser.add_argument("--depot-mode", choices=["region", "center"], default=env_value("DEPOT_MODE", "region"))
    parser.add_argument("--seed", type=int, default=env_int("SEED", 13))
    parser.add_argument("--backend", choices=["local", "ray"], default=env_value("SIM_BACKEND", "ray"))
    parser.add_argument("--ray-address", default=os.environ.get("RAY_ADDRESS", ""))
    parser.add_argument("--ray-num-cpus", type=int, default=int(os.environ["RAY_NUM_CPUS"]) if os.environ.get("RAY_NUM_CPUS") else None)
    parser.add_argument("--progress-every", type=int, default=env_int("PROGRESS_EVERY", 100))
    parser.add_argument("--wall-interval", type=float, default=env_float("WALL_INTERVAL", 0.25))
    parser.add_argument("--use-llm", action="store_true", default=env_bool("USE_LLM", False))
    parser.add_argument("--vllm-api-base", default=os.environ.get("VLLM_API_BASE", "http://127.0.0.1:6666/v1"))
    parser.add_argument("--vllm-model", default=os.environ.get("VLLM_MODEL", "qwen2.5-7b"))
    parser.add_argument("--llm-max-tokens", type=int, default=env_int("LLM_MAX_TOKENS", 512))
    parser.add_argument("--kg-backend", choices=["memory", "neo4j"], default=env_value("KG_BACKEND", "neo4j"))
    parser.add_argument("--kg-write-mode", choices=WRITE_MODES, default=env_value("KG_WRITE_MODE", "main"))
    parser.add_argument("--state-source", choices=STATE_SOURCES, default=env_value("KG_STATE_SOURCE", "ray"))
    parser.add_argument("--run-id", default=os.environ.get("KG_RUN_ID") or f"million-headless-{datetime.now():%Y%m%d-%H%M%S}")
    parser.add_argument(
        "--record-agent-state",
        action=argparse.BooleanOptionalAction,
        default=env_bool("KG_RECORD_AGENT_STATE", True),
    )
    parser.add_argument("--checkpoint-path", default=os.environ.get("HEADLESS_CHECKPOINT_PATH"))
    parser.add_argument(
        "--checkpoint-enabled",
        action=argparse.BooleanOptionalAction,
        default=env_bool("HEADLESS_CHECKPOINT_ENABLED", True),
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        default=env_bool("HEADLESS_RESUME", False),
    )
    parser.add_argument("--overwrite-checkpoint", action="store_true")
    parser.add_argument(
        "--ray-recovery-attempts",
        type=int,
        default=env_int("RAY_RECOVERY_ATTEMPTS", 5),
    )
    parser.add_argument(
        "--ray-recovery-delay",
        type=float,
        default=env_float("RAY_RECOVERY_DELAY", 30.0),
    )
    parser.add_argument(
        "--ray-min-available-memory-gb",
        type=float,
        default=env_float("RAY_MIN_AVAILABLE_MEMORY_GB", 32.0),
    )
    parser.add_argument(
        "--ray-memory-poll-interval",
        type=float,
        default=env_float("RAY_MEMORY_POLL_INTERVAL", 30.0),
    )
    args = parser.parse_args(argv)
    # build_runtime includes these values in its shared config, but headless mode does not use them.
    args.frame_stride = 0
    return args


def main():
    run_headless(parse_args())


if __name__ == "__main__":
    main()
