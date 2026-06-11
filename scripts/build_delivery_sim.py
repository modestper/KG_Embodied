#生成多dorne 仿真 trace

import argparse
import json
import sys
from pathlib import Path

VIEWER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_ROOT))

from simulation.delivery_sim import (
    AuctionCoordinator,
    SimulationEnv,
    build_depot,
    build_region_depots,
    load_delivery_tasks,
    make_drones,
    make_region_drones,
)
from simulation.planner import build_coordinator_from_env, build_planner_from_env


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "viewer" / "data" / "delivery_sim_current.json"

CITY_FILES = {
    "Jilin": "delivery_jl.csv",
    "吉林市": "delivery_jl.csv",
    "Shanghai": "delivery_sh.csv",
    "上海市": "delivery_sh.csv",
    "Hangzhou": "delivery_hz.csv",
    "杭州市": "delivery_hz.csv",
    "Chongqing": "delivery_cq.csv",
    "重庆市": "delivery_cq.csv",
    "Yantai": "delivery_yt.csv",
    "烟台市": "delivery_yt.csv",
}

CITY_SLUGS = {
    "Jilin": "jilin",
    "吉林市": "jilin",
    "Shanghai": "shanghai",
    "上海市": "shanghai",
    "Hangzhou": "hangzhou",
    "杭州市": "hangzhou",
    "Chongqing": "chongqing",
    "重庆市": "chongqing",
    "Yantai": "yantai",
    "烟台市": "yantai",
}


def default_output_path(city, day, task_limit, drone_count):
    city_slug = CITY_SLUGS.get(city, str(city).lower())
    day_slug = "all" if day in ("all", "ALL", "*", "") else str(day)
    return ROOT / "viewer" / "data" / f"delivery_sim_{city_slug}_{day_slug}_{task_limit}tasks_{drone_count}agents.json"


def build_trace(
    task_limit=120,
    drone_count=5,
    day="0925",
    max_steps=4500,
    step_seconds=20,
    city="Jilin",
    depot_mode="region",
    seed=13,
    frame_stride=1,
):
    day_filter = None if day in ("all", "ALL", "*", "") else day
    delivery_file = CITY_FILES.get(city)
    if delivery_file is None:
        valid = ", ".join(sorted(CITY_FILES))
        raise ValueError(f"Unsupported city {city!r}. Use one of: {valid}")
    tasks = load_delivery_tasks(
        ROOT / "delivery" / delivery_file,
        city=city,
        day=day_filter,
        limit=task_limit,
        sample_seed=seed,
    )
    region_depots = build_region_depots(tasks)
    depot = build_depot(tasks)
    if depot_mode == "region":
        drones = make_region_drones(drone_count, region_depots, capacity=6, seed=seed)
    else:
        drones = make_drones(drone_count, depot, capacity=6)
    planner = build_planner_from_env()
    for drone in drones:
        drone.planner = planner
    coordinator = build_coordinator_from_env()
    env = SimulationEnv(tasks, drones, coordinator, step_minutes=step_seconds / 60)
    trace = env.run(max_steps=max_steps)
    if frame_stride > 1:
        trace["frames"] = trace["frames"][::frame_stride]
    trace["config"] = {
        "city": tasks[0].city if tasks else city,
        "day": day if day_filter is not None else "all",
        "task_limit": task_limit,
        "drone_count": drone_count,
        "depot": {"x": round(depot[0], 3), "y": round(depot[1], 3)},
        "coordinator": "auction",
        "coordinator_class": coordinator.__class__.__name__,
        "planner": planner.__class__.__name__,
        "step_seconds": step_seconds,
        "depot_mode": depot_mode,
        "seed": seed,
        "region_count": len(region_depots),
        "frame_stride": frame_stride,
    }
    trace["depots"] = list(region_depots.values())
    return trace


def main():
    parser = argparse.ArgumentParser(description="Build a multi-drone delivery simulation trace.")
    parser.add_argument("--task-limit", type=int, default=120)
    parser.add_argument("--drone-count", type=int, default=5)
    parser.add_argument("--city", default="Jilin")
    parser.add_argument("--day", default="0925")
    parser.add_argument("--depot-mode", choices=["region", "center"], default="region")
    parser.add_argument("--seed", type=int, default=13)
    parser.add_argument("--frame-stride", "--frame_stride", type=int, default=1)
    parser.add_argument("--max-steps", type=int, default=4500)
    parser.add_argument("--step-seconds", "--step_seconds", type=int, default=20)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.out is None:
        args.out = default_output_path(args.city, args.day, args.task_limit, args.drone_count)

    trace = build_trace(
        task_limit=args.task_limit,
        drone_count=args.drone_count,
        day=args.day,
        max_steps=args.max_steps,
        step_seconds=args.step_seconds,
        city=args.city,
        depot_mode=args.depot_mode,
        seed=args.seed,
        frame_stride=args.frame_stride,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(trace, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    try:
        output_path = args.out.relative_to(ROOT)
    except ValueError:
        output_path = args.out
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
