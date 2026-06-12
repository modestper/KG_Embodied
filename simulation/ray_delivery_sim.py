from dataclasses import dataclass, field

from simulation.delivery_sim import (
    DeliveryTask,
    DroneAgent,
    format_minute,
    task_counts,
)
from simulation.knowledge import TemporalKnowledgeStore


def require_ray(ray_module=None):
    if ray_module is not None:
        return ray_module
    try:
        import ray
    except ImportError as exc:
        raise RuntimeError(
            "Ray backend requires Ray to be installed. Install it with `pip install ray` "
            "or run with `--backend local`."
        ) from exc
    return ray


def task_to_payload(task):
    return {
        "task_id": task.task_id,
        "city": task.city,
        "x": task.x,
        "y": task.y,
        "release_minute": task.release_minute,
        "deadline_minute": task.deadline_minute,
        "aoi_id": task.aoi_id,
        "region_id": task.region_id,
        "pickup_x": task.pickup_x,
        "pickup_y": task.pickup_y,
        "picked_up": task.picked_up,
        "status": task.status,
        "assigned_to": task.assigned_to,
        "completed_at": task.completed_at,
    }


def task_from_payload(payload):
    return DeliveryTask(**payload)


def drone_to_payload(drone):
    return {
        "drone_id": drone.drone_id,
        "x": drone.x,
        "y": drone.y,
        "capacity": drone.capacity,
        "speed_m_per_minute": drone.speed_m_per_minute,
        "battery_range_m": drone.battery_range_m,
        "completed": list(drone.completed),
        "total_distance_m": drone.total_distance_m,
        "status": drone.status,
        "region_id": drone.region_id,
    }


@dataclass
class DroneStateProxy:
    drone_id: str
    x: float
    y: float
    capacity: int = 5
    speed_m_per_minute: float = 1200
    battery_range_m: float = 80_000
    queue: list = field(default_factory=list)
    completed: list = field(default_factory=list)
    total_distance_m: float = 0
    status: str = "idle"
    region_id: str | None = None
    target: dict | None = None
    planner: object | None = None

    @property
    def position(self):
        return self.x, self.y

    def can_accept(self):
        return len(self.queue) < self.capacity

    def estimate_cost(self, task, now_minute):
        return self.planner.estimate_cost(self, task, now_minute)

    def assign(self, task):
        task.status = "assigned"
        task.assigned_to = self.drone_id
        self.queue.append(task)
        self.status = "queued"


class _DroneActor:
    def __init__(self, drone_payload):
        self.drone = DroneAgent(**{key: value for key, value in drone_payload.items() if key != "completed"})
        self.drone.completed = list(drone_payload.get("completed", []))

    def snapshot(self):
        return drone_snapshot(self.drone)

    def assign(self, task_payload):
        task = task_from_payload(task_payload)
        self.drone.assign(task)
        return self.snapshot()

    def step(self, minutes, now_minute):
        completed = self.drone.step(minutes, now_minute)
        return {
            "drone": self.snapshot(),
            "completed_tasks": [task_to_payload(task) for task in completed],
        }


def drone_snapshot(drone):
    target = None
    if drone.queue:
        task = drone.queue[0]
        pickup = (task.pickup_x, task.pickup_y)
        dropoff = (task.x, task.y)
        target_position = dropoff if task.picked_up else pickup
        target = {
            "task_id": task.task_id,
            "phase": "delivery" if task.picked_up else "pickup",
            "x": round(target_position[0], 3),
            "y": round(target_position[1], 3),
        }
    return {
        "id": drone.drone_id,
        "drone_id": drone.drone_id,
        "x": round(drone.x, 3),
        "y": round(drone.y, 3),
        "status": drone.status,
        "region_id": drone.region_id,
        "queue": [task.task_id for task in drone.queue],
        "target": target,
        "completed": len(drone.completed),
        "completed_ids": list(drone.completed),
        "distance_m": round(drone.total_distance_m, 1),
        "capacity": drone.capacity,
        "speed_m_per_minute": drone.speed_m_per_minute,
        "battery_range_m": drone.battery_range_m,
    }


def proxy_from_snapshot(snapshot, planner):
    return DroneStateProxy(
        drone_id=snapshot["drone_id"],
        x=snapshot["x"],
        y=snapshot["y"],
        capacity=snapshot["capacity"],
        speed_m_per_minute=snapshot["speed_m_per_minute"],
        battery_range_m=snapshot["battery_range_m"],
        queue=list(snapshot["queue"]),
        completed=list(snapshot["completed_ids"]),
        total_distance_m=snapshot["distance_m"],
        status=snapshot["status"],
        region_id=snapshot["region_id"],
        target=snapshot["target"],
        planner=planner,
    )


class RaySimulationEnv:
    def __init__(
        self,
        tasks,
        drones,
        coordinator,
        step_minutes=5,
        knowledge_store=None,
        ray_module=None,
        ray_address=None,
        ray_num_cpus=None,
    ):
        self.ray = require_ray(ray_module)
        if not self.ray.is_initialized():
            init_kwargs = {}
            if ray_address:
                init_kwargs["address"] = ray_address
            if ray_num_cpus is not None:
                init_kwargs["num_cpus"] = ray_num_cpus
            self.ray.init(**init_kwargs)

        self.tasks = sorted(tasks, key=lambda task: (task.release_minute, task.task_id))
        self.coordinator = coordinator
        self.step_minutes = step_minutes
        self.now_minute = min((task.release_minute for task in tasks), default=0)
        self.frames = []
        self.knowledge_store = knowledge_store or TemporalKnowledgeStore()
        if hasattr(self.coordinator, "knowledge_store"):
            self.coordinator.knowledge_store = self.knowledge_store

        actor_class = self.ray.remote(num_cpus=1)(_DroneActor)
        self.actors = {
            drone.drone_id: actor_class.remote(drone_to_payload(drone))
            for drone in drones
        }
        self.planner = drones[0].planner if drones else None
        self.drones = self._sync_drones()

    def close(self):
        for actor in list(self.actors.values()):
            self.ray.kill(actor, no_restart=True)
        self.actors = {}

    def _sync_drones(self):
        snapshots = self.ray.get([actor.snapshot.remote() for actor in self.actors.values()])
        self.drones = [proxy_from_snapshot(snapshot, self.planner) for snapshot in snapshots]
        return self.drones

    def run(self, max_steps=240):
        for _ in range(max_steps):
            self.step_once()
            if self.is_complete():
                break
        return self.to_trace()

    def step_once(self):
        self.release_and_assign()
        next_time = self.now_minute + self.step_minutes
        results = self.ray.get(
            [actor.step.remote(self.step_minutes, next_time) for actor in self.actors.values()]
        )
        completed_this_step = []
        drone_snapshots = []
        for result in results:
            drone_snapshots.append(result["drone"])
            for payload in result["completed_tasks"]:
                task = self._apply_completed_task(payload)
                completed_this_step.append(task)
                drone = proxy_from_snapshot(result["drone"], self.planner)
                self.knowledge_store.record_completion(next_time, task, drone)
        self.now_minute = next_time
        self.drones = [proxy_from_snapshot(snapshot, self.planner) for snapshot in drone_snapshots]
        frame = self.snapshot(completed_this_step)
        self.frames.append(frame)
        return frame

    def release_and_assign(self):
        available = [
            task
            for task in self.tasks
            if task.status == "waiting" and task.release_minute <= self.now_minute
        ]
        self._sync_drones()
        assignments = self.coordinator.assign(available, self.drones, self.now_minute)
        drone_by_id = {drone.drone_id: drone for drone in self.drones}
        task_by_id = {task.task_id: task for task in available}
        assign_refs = []
        for task_id, drone_id in assignments.items():
            task = task_by_id.get(task_id)
            drone = drone_by_id.get(drone_id)
            actor = self.actors.get(drone_id)
            if not task or not drone or not actor:
                continue
            self.knowledge_store.record_assignment(self.now_minute, task, drone)
            assign_refs.append(actor.assign.remote(task_to_payload(task)))
        if assign_refs:
            self.ray.get(assign_refs)
            self._sync_drones()

    def _apply_completed_task(self, payload):
        task = self._task_by_id(payload["task_id"])
        task.status = "completed"
        task.picked_up = payload["picked_up"]
        task.completed_at = payload["completed_at"]
        task.assigned_to = payload["assigned_to"]
        return task

    def _task_by_id(self, task_id):
        for task in self.tasks:
            if task.task_id == task_id:
                return task
        raise KeyError(task_id)

    def is_complete(self):
        return all(task.status == "completed" for task in self.tasks)

    def snapshot(self, completed):
        return {
            "time_minute": self.now_minute,
            "time_label": format_minute(self.now_minute),
            "completed_this_step": [task.task_id for task in completed],
            "drones": [
                {
                    "id": drone.drone_id,
                    "x": round(drone.x, 3),
                    "y": round(drone.y, 3),
                    "status": drone.status,
                    "region_id": drone.region_id,
                    "queue": list(drone.queue),
                    "target": drone.target,
                    "completed": len(drone.completed),
                    "distance_m": round(drone.total_distance_m, 1),
                }
                for drone in self.drones
            ],
            "tasks": task_counts(self.tasks),
            "task_statuses": {
                task.task_id: task.status
                for task in self.tasks
                if task.status != "waiting"
            },
            "task_states": {
                task.task_id: {
                    "status": task.status,
                    "assigned_to": task.assigned_to,
                    "picked_up": task.picked_up,
                    "completed_at": task.completed_at,
                    "late": task.completed_at is not None and task.completed_at > task.deadline_minute,
                }
                for task in self.tasks
                if task.release_minute <= self.now_minute
            },
        }

    def to_trace(self):
        completed = [task for task in self.tasks if task.status == "completed"]
        late = [task for task in completed if task.completed_at and task.completed_at > task.deadline_minute]
        return {
            "scenario": "Jilin delivery multi-drone demo",
            "step_minutes": self.step_minutes,
            "step_seconds": round(self.step_minutes * 60),
            "metrics": {
                "total_tasks": len(self.tasks),
                "completed_tasks": len(completed),
                "unfinished_tasks": len(self.tasks) - len(completed),
                "late_tasks": len(late),
                "total_distance_m": round(sum(drone.total_distance_m for drone in self.drones), 1),
                "drones": len(self.drones),
                "finished_at": format_minute(self.now_minute),
            },
            "tasks": [
                {
                    "id": task.task_id,
                    "city": task.city,
                    "region_id": task.region_id,
                    "pickup_x": task.pickup_x,
                    "pickup_y": task.pickup_y,
                    "x": task.x,
                    "y": task.y,
                    "aoi": task.aoi_id,
                    "release": format_minute(task.release_minute),
                    "deadline": format_minute(task.deadline_minute),
                    "assigned_to": task.assigned_to,
                    "completed_at": format_minute(task.completed_at) if task.completed_at is not None else None,
                    "status": task.status,
                }
                for task in self.tasks
            ],
            "frames": self.frames,
            "kg_events": self.knowledge_store.events[-200:],
        }
