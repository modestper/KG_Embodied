import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace


VIEWER_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = VIEWER_ROOT.parent
sys.path.insert(0, str(VIEWER_ROOT))

from scripts.build_delivery_sim import CITY_FILES
from simulation.checkpoint import (
    CheckpointError,
    TASK_STATE_DEFAULTS,
    TASK_STATE_FIELDS,
    apply_task_states,
    build_checkpoint,
    task_state_payload,
    write_checkpoint,
)
from simulation.delivery_sim import (
    build_region_depots,
    load_fixed_region_depots,
    load_delivery_tasks,
    make_region_drones,
)
from simulation.planner import build_planner_from_env
from simulation.ray_delivery_sim import drone_to_payload


DEFAULT_URI = "bolt://127.0.0.1:17687"
DEFAULT_USER = "neo4j"
DEFAULT_PASSWORD = "neo4j123456"


def load_static_scenario(args):
    day_filter = None if args.day in ("all", "ALL", "*", "") else args.day
    tasks = load_delivery_tasks(
        DATA_ROOT / "delivery" / CITY_FILES[args.city],
        city=args.city,
        day=day_filter,
        limit=args.task_limit,
        sample_seed=args.seed,
    )
    region_depots = build_region_depots(tasks)
    charging_depots = load_fixed_region_depots(args.city)
    drones = make_region_drones(
        args.drone_count, region_depots, capacity=6, seed=args.seed
    )
    planner = build_planner_from_env()
    depots = list(charging_depots.values())
    by_region = {depot["region_id"]: depot for depot in depots}
    for drone in drones:
        drone.planner = planner
        drone.depots = depots
        drone.home_depot = by_region.get(drone.region_id)
    return tasks, drones


def read_graph_state(session, run_id, step_index):
    latest = session.run(
        """
        MATCH (s:Step {run_id: $run_id})
        RETURN max(s.step_index) AS step_index
        """,
        run_id=run_id,
    ).single()
    if not latest or latest["step_index"] is None:
        raise CheckpointError(f"run has no Step nodes: {run_id}")
    if int(latest["step_index"]) != step_index:
        raise CheckpointError(
            f"requested step is not latest: requested={step_index} "
            f"latest={latest['step_index']}"
        )

    step = session.run(
        """
        MATCH (s:Step {run_id: $run_id, step_index: $step_index})
        RETURN s.time_minute AS time_minute
        """,
        run_id=run_id,
        step_index=step_index,
    ).single()
    if not step:
        raise CheckpointError(f"Step not found: run={run_id} step={step_index}")

    agent_states = [
        dict(row["state"])
        for row in session.run(
            """
            MATCH (a:AgentState {run_id: $run_id, step_index: $step_index})
            RETURN properties(a) AS state
            ORDER BY a.drone_id
            """,
            run_id=run_id,
            step_index=step_index,
        )
    ]
    task_properties = [
        dict(row["task"])
        for row in session.run(
            """
            MATCH (t:Task {run_id: $run_id})
            RETURN properties(t) AS task
            """,
            run_id=run_id,
        )
    ]
    assignments = [
        dict(row)
        for row in session.run(
            """
            MATCH (d:Drone {run_id: $run_id})-[rel:ASSIGNED]->
                  (t:Task {run_id: $run_id})
            WHERE coalesce(rel.active, true) = true
            RETURN d.id AS drone_id, t.id AS task_id,
                   rel.time_minute AS time_minute
            """,
            run_id=run_id,
        )
    ]
    completions = [
        dict(row)
        for row in session.run(
            """
            MATCH (d:Drone {run_id: $run_id})-[rel:COMPLETED]->
                  (t:Task {run_id: $run_id})
            RETURN d.id AS drone_id, t.id AS task_id,
                   rel.time_minute AS time_minute
            ORDER BY rel.time_minute, t.id
            """,
            run_id=run_id,
        )
    ]
    return {
        "time_minute": step["time_minute"],
        "agent_states": agent_states,
        "task_properties": task_properties,
        "assignments": assignments,
        "completions": completions,
    }


def restore_graph_state(tasks, drones, graph_state):
    task_states = []
    generated_task_ids = {task.task_id for task in tasks}
    for properties in graph_state["task_properties"]:
        task_id = properties["id"]
        if task_id not in generated_task_ids:
            raise CheckpointError(f"Neo4j task is absent from source dataset: {task_id}")
        task_states.append(
            {
                "task_id": task_id,
                **{
                    field: properties.get(field, TASK_STATE_DEFAULTS[field])
                    for field in TASK_STATE_FIELDS
                },
            }
        )
    tasks_by_id = apply_task_states(tasks, task_states)
    drones_by_id = {drone.drone_id: drone for drone in drones}
    states_by_drone = {
        state["drone_id"]: state for state in graph_state["agent_states"]
    }
    if set(states_by_drone) != set(drones_by_id):
        raise CheckpointError(
            "AgentState drone ids do not match generated drones: "
            f"states={len(states_by_drone)} generated={len(drones_by_id)}"
        )

    assignment_time = {
        row["task_id"]: row.get("time_minute")
        for row in graph_state["assignments"]
    }
    completed_by_drone = defaultdict(list)
    for row in graph_state["completions"]:
        completed_by_drone[row["drone_id"]].append(row["task_id"])

    active_by_drone = defaultdict(list)
    for task in tasks:
        if task.status in {"assigned", "picked_up"}:
            if task.assigned_to not in drones_by_id:
                raise CheckpointError(
                    f"active task has invalid assigned_to: {task.task_id} -> {task.assigned_to}"
                )
            active_by_drone[task.assigned_to].append(task)

    for drone_id, drone in drones_by_id.items():
        state = states_by_drone[drone_id]
        drone.x = state["x"]
        drone.y = state["y"]
        drone.status = state["status"]
        drone.region_id = state.get("region_id")
        drone.battery_remaining_m = state["battery_remaining_m"]
        drone.total_distance_m = state["total_distance_m"]
        drone.queue = sorted(
            active_by_drone[drone_id],
            key=lambda task: (
                assignment_time.get(task.task_id) is None,
                assignment_time.get(task.task_id) or 0,
                task.deadline_minute,
                task.task_id,
            ),
        )
        drone.onboard_tasks = [
            task for task in drone.queue if task.status == "picked_up"
        ]
        drone.completed = list(completed_by_drone[drone_id])
        if drone.status == "returning":
            drone.target_depot = drone.resolve_charge_depot()

        if len(drone.queue) != state["queue_len"]:
            raise CheckpointError(
                f"queue mismatch for {drone_id}: graph={state['queue_len']} "
                f"restored={len(drone.queue)}"
            )
        if len(drone.onboard_tasks) != state["onboard_count"]:
            raise CheckpointError(
                f"onboard mismatch for {drone_id}: graph={state['onboard_count']} "
                f"restored={len(drone.onboard_tasks)}"
            )

    completed_tasks = {task.task_id for task in tasks if task.status == "completed"}
    completed_relationships = {
        task_id for values in completed_by_drone.values() for task_id in values
    }
    if completed_tasks != completed_relationships:
        raise CheckpointError(
            "completed Task/relationship mismatch: "
            f"tasks={len(completed_tasks)} relationships={len(completed_relationships)}"
        )
    return {
        "now_minute": graph_state["time_minute"],
        "task_states": [task_state_payload(task) for task in tasks],
        "drones": [drone_to_payload(drone) for drone in drones],
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Build a headless checkpoint from the latest committed Neo4j Step."
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--step-index", type=int, required=True)
    parser.add_argument("--city", default="Hangzhou", choices=sorted(CITY_FILES))
    parser.add_argument("--day", default="0621")
    parser.add_argument("--task-limit", type=int, default=10424)
    parser.add_argument("--drone-count", type=int, default=100)
    parser.add_argument("--step-seconds", type=int, default=6)
    parser.add_argument("--depot-mode", choices=["region"], default="region")
    parser.add_argument("--seed", type=int, default=13)
    parser.add_argument("--backend", choices=["ray"], default="ray")
    parser.add_argument("--use-llm", action="store_true")
    parser.add_argument("--uri", default=os.environ.get("NEO4J_URI", DEFAULT_URI))
    parser.add_argument("--user", default=os.environ.get("NEO4J_USER", DEFAULT_USER))
    parser.add_argument("--password", default=os.environ.get("NEO4J_PASSWORD", DEFAULT_PASSWORD))
    parser.add_argument("--database", default=os.environ.get("NEO4J_DATABASE"))
    parser.add_argument("--checkpoint-path", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args(argv)


def run(args):
    checkpoint_path = args.checkpoint_path or (
        VIEWER_ROOT / "data" / "checkpoints" / f"{args.run_id}.json"
    )
    if checkpoint_path.exists() and not args.overwrite:
        raise CheckpointError(f"checkpoint already exists: {checkpoint_path}")
    tasks, drones = load_static_scenario(args)

    from neo4j import GraphDatabase

    with GraphDatabase.driver(args.uri, auth=(args.user, args.password)) as driver:
        session_args = {"database": args.database} if args.database else {}
        with driver.session(**session_args) as session:
            graph_state = read_graph_state(session, args.run_id, args.step_index)
    state = restore_graph_state(tasks, drones, graph_state)
    checkpoint_args = SimpleNamespace(
        run_id=args.run_id,
        backend=args.backend,
        city=args.city,
        day=args.day,
        task_limit=args.task_limit,
        drone_count=args.drone_count,
        step_seconds=args.step_seconds,
        depot_mode=args.depot_mode,
        seed=args.seed,
        use_llm=args.use_llm,
    )
    checkpoint = build_checkpoint(
        checkpoint_args,
        args.step_index,
        graph_state["time_minute"],
        state,
    )
    write_checkpoint(checkpoint_path, checkpoint)
    active = sum(task.status in {"assigned", "picked_up"} for task in tasks)
    completed = sum(task.status == "completed" for task in tasks)
    print(
        "checkpoint created "
        f"path={checkpoint_path} step={args.step_index} "
        f"time_minute={graph_state['time_minute']} tasks={len(tasks)} "
        f"active={active} completed={completed} drones={len(drones)}",
        flush=True,
    )
    return checkpoint


def main():
    try:
        run(parse_args())
    except CheckpointError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
