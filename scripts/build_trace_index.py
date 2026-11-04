import json
import fcntl
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUT = DATA_DIR / "traces.json"


def is_trace_payload(payload):
    return isinstance(payload, dict) and "frames" in payload and "tasks" in payload and "metrics" in payload


def trace_entry(path, payload, data_dir=DATA_DIR):
    config = payload.get("config", {})
    metrics = payload.get("metrics", {})
    drone_count = metrics.get("drones", 0)
    return {
        "file": path.relative_to(data_dir).as_posix(),
        "label": (
            f"{config.get('city', 'unknown')} {config.get('day', '')} · "
            f"{metrics.get('total_tasks', 0)} tasks · {drone_count} agents"
        ),
        "city": config.get("city"),
        "day": config.get("day"),
        "tasks": metrics.get("total_tasks", 0),
        "drones": metrics.get("drones", 0),
        "step_seconds": payload.get("step_seconds"),
        "size_bytes": path.stat().st_size,
    }


@contextmanager
def index_lock(data_dir=DATA_DIR):
    with (data_dir / ".traces.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def write_index(payload, data_dir=DATA_DIR):
    """Replace the small index atomically; callers hold index_lock."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=data_dir,
                                         suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(data_dir / "traces.json")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def build_index(data_dir=DATA_DIR):
    entries = []
    trace_root = data_dir / "traces"
    paths = list(data_dir.glob("*.json"))
    if trace_root.exists() and not trace_root.is_symlink():
        paths.extend(path for path in trace_root.rglob("*.json")
                     if not path.is_symlink() and path.resolve().is_relative_to(trace_root.resolve()))
    for path in sorted(paths):
        if path == data_dir / "traces.json":
            continue
        try:
            with path.open(encoding="utf-8") as handle:
                payload = json.load(handle)
        except (json.JSONDecodeError, OSError):
            continue
        if is_trace_payload(payload):
            entries.append(trace_entry(path, payload, data_dir))
    return {"traces": entries}


def main():
    with index_lock():
        write_index(build_index())
    print(f"Wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
