import hashlib
import json
import os
from pathlib import Path


CHECKPOINT_VERSION = 1
TASK_STATE_FIELDS = (
    "picked_up",
    "status",
    "assigned_to",
    "completed_at",
    "pickup_time",
    "onboard_drone_id",
    "assistance_reason",
    "assistance_requested_at",
    "assistance_source_drone_id",
    "handover_from_drone_id",
    "handover_to_drone_id",
    "handover_x",
    "handover_y",
    "handover_completed_at",
    "takeover_count",
    "previous_assignees",
)
TASK_STATE_DEFAULTS = {
    "picked_up": False,
    "status": "waiting",
    "assigned_to": None,
    "completed_at": None,
    "pickup_time": None,
    "onboard_drone_id": None,
    "assistance_reason": None,
    "assistance_requested_at": None,
    "assistance_source_drone_id": None,
    "handover_from_drone_id": None,
    "handover_to_drone_id": None,
    "handover_x": None,
    "handover_y": None,
    "handover_completed_at": None,
    "takeover_count": 0,
    "previous_assignees": [],
}
CONFIG_FIELDS = (
    "run_id",
    "backend",
    "city",
    "day",
    "task_limit",
    "drone_count",
    "step_seconds",
    "depot_mode",
    "seed",
    "use_llm",
)


class CheckpointError(RuntimeError):
    pass


def task_state_payload(task):
    return {
        "task_id": task.task_id,
        **{field: getattr(task, field) for field in TASK_STATE_FIELDS},
    }


def apply_task_states(tasks, task_states):
    tasks_by_id = {task.task_id: task for task in tasks}
    unknown = sorted(
        state["task_id"] for state in task_states if state["task_id"] not in tasks_by_id
    )
    if unknown:
        raise CheckpointError(f"checkpoint contains unknown task ids: {unknown[:5]}")

    for task in tasks:
        for field in TASK_STATE_FIELDS:
            value = TASK_STATE_DEFAULTS[field]
            setattr(task, field, list(value) if isinstance(value, list) else value)
    for state in task_states:
        task = tasks_by_id[state["task_id"]]
        for field in TASK_STATE_FIELDS:
            value = state.get(field, TASK_STATE_DEFAULTS[field])
            setattr(task, field, list(value) if isinstance(value, list) else value)
    return tasks_by_id


def checkpoint_config(args):
    return {field: getattr(args, field) for field in CONFIG_FIELDS}


def build_checkpoint(args, step_index, now_minute, state):
    return {
        "version": CHECKPOINT_VERSION,
        "run_id": args.run_id,
        "step_index": int(step_index),
        "now_minute": now_minute,
        "config": checkpoint_config(args),
        "state": state,
    }


def _canonical_payload(payload):
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def write_checkpoint(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload_bytes = _canonical_payload(payload)
    document = {
        "sha256": hashlib.sha256(payload_bytes).hexdigest(),
        "payload": payload,
    }
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(
                document,
                handle,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def read_checkpoint(path):
    path = Path(path)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CheckpointError(f"cannot read checkpoint {path}: {exc}") from exc
    payload = document.get("payload")
    expected = document.get("sha256")
    if not isinstance(payload, dict) or not expected:
        raise CheckpointError(f"invalid checkpoint envelope: {path}")
    actual = hashlib.sha256(_canonical_payload(payload)).hexdigest()
    if actual != expected:
        raise CheckpointError(
            f"checkpoint checksum mismatch: expected={expected} actual={actual}"
        )
    if payload.get("version") != CHECKPOINT_VERSION:
        raise CheckpointError(
            f"unsupported checkpoint version: {payload.get('version')}"
        )
    return payload


def validate_checkpoint(payload, args):
    if payload.get("run_id") != args.run_id:
        raise CheckpointError(
            f"run_id mismatch: checkpoint={payload.get('run_id')} current={args.run_id}"
        )
    actual = payload.get("config") or {}
    expected = checkpoint_config(args)
    mismatches = {
        field: {"checkpoint": actual.get(field), "current": expected[field]}
        for field in CONFIG_FIELDS
        if actual.get(field) != expected[field]
    }
    if mismatches:
        raise CheckpointError(f"checkpoint config mismatch: {mismatches}")
    if payload.get("step_index", -1) < 0:
        raise CheckpointError("checkpoint step_index must be non-negative")
    if not isinstance(payload.get("state"), dict):
        raise CheckpointError("checkpoint state is missing")
    return payload
