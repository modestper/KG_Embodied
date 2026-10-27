"""Plain-data state exchanged by Ray actors and the knowledge writer process."""

from dataclasses import asdict

from simulation.delivery_sim import DeliveryTask, DroneAgent


def task_to_payload(task):
    return asdict(task)


def task_from_payload(payload):
    return DeliveryTask(**payload)


def drone_to_payload(drone):
    return {
        "drone_id": drone.drone_id,
        "x": drone.x,
        "y": drone.y,
        "capacity": drone.capacity,
        "queue_capacity": drone.queue_capacity,
        "cargo_capacity": drone.cargo_capacity,
        "speed_m_per_minute": drone.speed_m_per_minute,
        "battery_range_m": drone.battery_range_m,
        "battery_capacity_m": drone.battery_capacity_m,
        "battery_remaining_m": drone.battery_remaining_m,
        "low_battery_threshold_m": drone.low_battery_threshold_m,
        "safety_buffer_m": drone.safety_buffer_m,
        "queue": [task_to_payload(task) for task in drone.queue],
        "onboard_tasks": [task_to_payload(task) for task in drone.onboard_tasks],
        "completed": list(drone.completed),
        "total_distance_m": drone.total_distance_m,
        "status": drone.status,
        "region_id": drone.region_id,
        "home_depot": drone.home_depot,
        "depots": list(drone.depots),
        "target_depot": drone.target_depot,
        "failure_mode": drone.failure_mode,
        "takeover_mode": drone.takeover_mode,
    }


def drone_from_payload(payload):
    drone = DroneAgent(**{
        key: value for key, value in payload.items()
        if key not in ("completed", "queue", "onboard_tasks")
    })
    drone.completed = list(payload.get("completed", []))
    drone.queue = [task_from_payload(task) for task in payload.get("queue", [])]
    by_id = {task.task_id: task for task in drone.queue}
    drone.onboard_tasks = [
        by_id.get(task["task_id"]) or task_from_payload(task)
        for task in payload.get("onboard_tasks", [])
    ]
    return drone
