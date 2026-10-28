"""Versioned, complete scheduling snapshots stored in Neo4j.

Historical AgentState nodes deliberately remain separate from this read view.
Each environment (including checkpoint restores) gets a fresh execution_id.
"""

import json

from simulation.state_codec import task_from_payload


STATE_SOURCES = ("ray", "neo4j")
SCHEMA_VERSION = 1


class KnowledgeStateError(RuntimeError):
    pass


def runtime_state(snapshot, extra_tasks=()):
    tasks = {
        task["task_id"]: task
        for task in [*snapshot["queue_payload"], *snapshot["onboard_tasks"], *extra_tasks]
    }
    return {"snapshot": snapshot, "task_updates": list(tasks.values())}


def encode_runtime_states(states):
    return [{
        "drone_id": state["snapshot"]["drone_id"],
        "payload_json": json.dumps(state, ensure_ascii=False, allow_nan=False),
        **{key: state["snapshot"][key] for key in (
            "x", "y", "status", "region_id", "battery_remaining_m",
        )},
    } for state in states]


def decode_runtime_states(rows, execution_id, expected_versions):
    """Reject missing, foreign, duplicate, corrupt or stale snapshots."""
    states = {}
    for row in rows:
        drone_id = row["drone_id"]
        if drone_id not in expected_versions or drone_id in states:
            raise KnowledgeStateError(f"Unexpected/duplicate KG drone state: {drone_id}")
        if (row["execution_id"] != execution_id
                or row["version"] != expected_versions[drone_id]
                or row["schema_version"] != SCHEMA_VERSION):
            raise KnowledgeStateError(
                f"KG state version mismatch for {drone_id}: "
                f"expected {execution_id}/{expected_versions[drone_id]}, "
                f"got {row['execution_id']}/{row['version']}"
            )
        try:
            state = json.loads(row["payload_json"])
            snapshot = state["snapshot"]
            required = (
                "drone_id", "x", "y", "status", "region_id", "capacity",
                "queue_capacity", "cargo_capacity", "speed_m_per_minute",
                "battery_range_m", "battery_capacity_m", "battery_remaining_m",
                "low_battery_threshold_m", "safety_buffer_m", "queue", "queue_payload",
                "onboard", "onboard_tasks", "completed_ids", "distance_m", "target",
                "home_depot", "depots", "target_depot", "failure_mode", "takeover_mode",
            )
            if any(key not in snapshot for key in required):
                raise ValueError("incomplete drone snapshot")
            if snapshot["drone_id"] != drone_id:
                raise ValueError("drone identity mismatch")
            if not isinstance(state["task_updates"], list):
                raise ValueError("invalid task updates")
            for field, ids in (("queue_payload", "queue"), ("onboard_tasks", "onboard")):
                if [task["task_id"] for task in snapshot[field]] != snapshot[ids]:
                    raise ValueError(f"inconsistent {field}")
            for task in [*snapshot["queue_payload"], *snapshot["onboard_tasks"], *state["task_updates"]]:
                task_from_payload(task)
        except (ValueError, KeyError, TypeError) as exc:
            raise KnowledgeStateError(f"Invalid KG state for {drone_id}: {exc}") from exc
        states[drone_id] = state
    missing = set(expected_versions) - states.keys()
    if missing:
        raise KnowledgeStateError(f"Missing KG drone states: {sorted(missing)}")
    return states
