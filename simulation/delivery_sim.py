import csv
import math
import random
from dataclasses import dataclass, field
from pathlib import Path

from scripts.build_summary import city_name, normalize_coordinate, parse_minutes
#多agent配送的仿真核心部分    
#定义了agent 


@dataclass
class DeliveryTask:
    task_id: str
    city: str
    x: float
    y: float
    release_minute: int
    deadline_minute: int
    aoi_id: str
    region_id: str
    pickup_x: float | None = None
    pickup_y: float | None = None
    picked_up: bool = False
    status: str = "waiting"
    assigned_to: str | None = None
    completed_at: int | None = None


@dataclass
class DroneAgent:
    drone_id: str
    x: float
    y: float
    capacity: int = 5
    speed_m_per_minute: float = 1200
    battery_range_m: float = 80_000
    queue: list[DeliveryTask] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)
    total_distance_m: float = 0
    status: str = "idle"
    region_id: str | None = None
    planner: object | None = None

    def __post_init__(self):
        if self.planner is None:
            from simulation.planner import RuleBasedPlanner

            self.planner = RuleBasedPlanner()

    def can_accept(self):
        return len(self.queue) < self.capacity

    def estimate_cost(self, task, now_minute):
        return self.planner.estimate_cost(self, task, now_minute)

    @property
    def position(self):
        return self.x, self.y

    def assign(self, task):
        task.status = "assigned"
        task.assigned_to = self.drone_id
        self.queue.append(task)
        self.status = "queued"

    def step(self, minutes, now_minute):
        if not self.queue:
            self.status = "idle"
            return []

        completed = []
        remaining_distance = self.speed_m_per_minute * minutes
        self.status = "delivering"
        while self.queue and remaining_distance > 0:
            task = self.queue[0]
            target = task.dropoff_position if task.picked_up else task.pickup_position
            distance = euclidean(self.position, target)
            if distance <= remaining_distance:
                self.x, self.y = target
                self.total_distance_m += distance
                remaining_distance -= distance
                if not task.picked_up:
                    task.picked_up = True
                    continue
                task.status = "completed"
                task.completed_at = now_minute
                self.completed.append(task.task_id)
                completed.append(task)
                self.queue.pop(0)
            else:
                ratio = remaining_distance / distance
                self.x += (target[0] - self.x) * ratio
                self.y += (target[1] - self.y) * ratio
                self.total_distance_m += remaining_distance
                remaining_distance = 0
        if not self.queue:
            self.status = "idle"
        return completed

#调度器，负责把任务分给 agent
class AuctionCoordinator:
    def assign(self, tasks, drones, now_minute):
        assignments = {}
        for task in tasks:
            region_candidates = [
                drone for drone in drones if drone.can_accept() and drone.region_id == task.region_id
            ]
            candidates = region_candidates or [drone for drone in drones if drone.can_accept()]
            if not candidates:
                break
            winner = min(candidates, key=lambda drone: drone.estimate_cost(task, now_minute))
            winner.assign(task)
            assignments[task.task_id] = winner.drone_id
        return assignments

#仿真循环，释放当前时间可派送的任务、 用AuctionCoordinator分配任务，
#诶个
class SimulationEnv:
    def __init__(self, tasks, drones, coordinator, step_minutes=5, knowledge_store=None):
        self.tasks = sorted(tasks, key=lambda task: (task.release_minute, task.task_id))
        self.drones = drones
        self.coordinator = coordinator
        self.step_minutes = step_minutes
        self.now_minute = min((task.release_minute for task in tasks), default=0)
        self.frames = []
        if knowledge_store is None:
            from simulation.knowledge import TemporalKnowledgeStore

            knowledge_store = TemporalKnowledgeStore()
        self.knowledge_store = knowledge_store
        if hasattr(self.coordinator, "knowledge_store"):
            self.coordinator.knowledge_store = knowledge_store

    def run(self, max_steps=240):
        for _ in range(max_steps):
            self.release_and_assign()
            completed_this_step = []
            for drone in self.drones:
                completed_tasks = drone.step(self.step_minutes, self.now_minute + self.step_minutes)
                for task in completed_tasks:
                    self.knowledge_store.record_completion(self.now_minute + self.step_minutes, task, drone)
                completed_this_step.extend(completed_tasks)
            self.now_minute += self.step_minutes
            self.frames.append(self.snapshot(completed_this_step))
            if self.is_complete():
                break
        return self.to_trace()

    #派单阶段用的
    def release_and_assign(self):
        available = [
            task
            for task in self.tasks
            if task.status == "waiting" and task.release_minute <= self.now_minute
        ]
        assignments = self.coordinator.assign(available, self.drones, self.now_minute)
        drone_by_id = {drone.drone_id: drone for drone in self.drones}
        task_by_id = {task.task_id: task for task in available}
        for task_id, drone_id in assignments.items():
            task = task_by_id.get(task_id)
            drone = drone_by_id.get(drone_id)
            if task and drone:
                self.knowledge_store.record_assignment(self.now_minute, task, drone)

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
                    "queue": [task.task_id for task in drone.queue],
                    "target": current_target(drone),
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


def load_delivery_tasks(path, city="Jilin", day=None, limit=200, sample_seed=None):
    city_cn = city_name(city)
    tasks = []
    rng = random.Random(sample_seed) if sample_seed is not None else None
    seen = 0
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if city_name(row["city"]) != city_cn:
                continue
            if day and row["ds"] != day:
                continue
            x, y = normalize_coordinate(row.get("lng"), row.get("lat"))
            release = parse_minutes(row.get("accept_time"))
            deadline = parse_minutes(row.get("delivery_time"))
            if x is None or y is None or release is None or deadline is None:
                continue
            task = DeliveryTask(
                task_id=str(row["order_id"]),
                city=city_cn,
                x=x,
                y=y,
                release_minute=release,
                deadline_minute=deadline,
                aoi_id=str(row["aoi_id"]),
                region_id=str(row["region_id"]),
            )
            if rng is None:
                tasks.append(task)
                if len(tasks) >= limit:
                    break
            else:
                seen += 1
                if len(tasks) < limit:
                    tasks.append(task)
                else:
                    index = rng.randrange(seen)
                    if index < limit:
                        tasks[index] = task
    build_region_depots(tasks)
    return tasks


@property
def _unused_property_placeholder():
    return None


DeliveryTask.pickup_position = property(lambda task: (task.pickup_x, task.pickup_y))
DeliveryTask.dropoff_position = property(lambda task: (task.x, task.y))


def build_region_depots(tasks):
    grouped = {}
    for task in tasks:
        item = grouped.setdefault(task.region_id, {"x": 0, "y": 0, "count": 0})
        item["x"] += task.x
        item["y"] += task.y
        item["count"] += 1
    depots = {
        region_id: {
            "region_id": region_id,
            "x": round(item["x"] / item["count"], 3),
            "y": round(item["y"] / item["count"], 3),
            "task_count": item["count"],
        }
        for region_id, item in grouped.items()
    }
    for task in tasks:
        depot = depots[task.region_id]
        task.pickup_x = depot["x"]
        task.pickup_y = depot["y"]
    return depots


def build_depot(tasks):
    if not tasks:
        return 0, 0
    return (
        sum(task.x for task in tasks) / len(tasks),
        sum(task.y for task in tasks) / len(tasks),
    )


def make_drones(count, depot, capacity=5):
    return [
        DroneAgent(
            drone_id=f"drone-{index + 1}",
            x=depot[0] + (index % 3) * 60,
            y=depot[1] + (index // 3) * 60,
            capacity=capacity,
        )
        for index in range(count)
    ]


def make_region_drones(count, depots, capacity=5, seed=13):
    if not depots:
        return make_drones(count, (0, 0), capacity=capacity)
    rng = random.Random(seed)
    region_ids = sorted(depots)
    rng.shuffle(region_ids)
    drones = []
    for index in range(count):
        region_id = region_ids[index % len(region_ids)]
        depot = depots[region_id]
        drones.append(
            DroneAgent(
                drone_id=f"drone-{index + 1}",
                x=depot["x"],
                y=depot["y"],
                capacity=capacity,
                region_id=region_id,
            )
        )
    return drones


def current_target(drone):
    if not drone.queue:
        return None
    task = drone.queue[0]
    target = task.dropoff_position if task.picked_up else task.pickup_position
    return {
        "task_id": task.task_id,
        "phase": "delivery" if task.picked_up else "pickup",
        "x": round(target[0], 3),
        "y": round(target[1], 3),
    }


def task_counts(tasks):
    counts = {"waiting": 0, "assigned": 0, "completed": 0}
    for task in tasks:
        counts[task.status] = counts.get(task.status, 0) + 1
    return counts


def format_minute(value):
    if value is None:
        return None
    total_seconds = int(round(value * 60)) % (24 * 60 * 60)
    hour = total_seconds // 3600
    minute = (total_seconds % 3600) // 60
    second = total_seconds % 60
    return f"{hour:02d}:{minute:02d}:{second:02d}"


def euclidean(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])
