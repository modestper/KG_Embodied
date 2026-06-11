import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUT = DATA_DIR / "traces.json"


def is_trace_payload(payload):
    return isinstance(payload, dict) and "frames" in payload and "tasks" in payload and "metrics" in payload


def trace_entry(path, payload):
    config = payload.get("config", {})
    metrics = payload.get("metrics", {})
    drone_count = metrics.get("drones", 0)
    return {
        "file": path.name,
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


def build_index():
    entries = []
    for path in sorted(DATA_DIR.glob("*.json")):
        if path.name == OUT.name:
            continue
        try:
            with path.open(encoding="utf-8") as handle:
                payload = json.load(handle)
        except (json.JSONDecodeError, OSError):
            continue
        if is_trace_payload(payload):
            entries.append(trace_entry(path, payload))
    return {"traces": entries}


def main():
    OUT.write_text(json.dumps(build_index(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
