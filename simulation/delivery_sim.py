import csv
import json
import math
import random
from dataclasses import dataclass, field, fields, replace
from functools import lru_cache
from pathlib import Path

from scripts.build_summary import city_name, normalize_coordinate, parse_minutes
#多agent配送的仿真核心部分    
#定义了agent 


VIEWER_ROOT = Path(__file__).resolve().parents[1]
FIXED_REGION_DEPOTS_PATH = VIEWER_ROOT / "data" / "fixed_region_depots.json"


def select_nearest_charge_depot(position, depots, home_depot=None):
    candidates = list(depots)
    if home_depot is not None and not any(
        depot.get("region_id") == home_depot.get("region_id")
        and depot.get("x") == home_depot.get("x")
        and depot.get("y") == home_depot.get("y")
        for depot in candidates
    ):
        candidates.append(home_depot)
    if not candidates:
        return None
    home_region_id = home_depot.get("region_id") if home_depot else None
    return min(
        candidates,
        key=lambda depot: (
            euclidean(position, (depot["x"], depot["y"])),
            depot.get("region_id") != home_region_id,
            str(depot.get("region_id")),
        ),
    )


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
    pickup_time: int | None = None
    onboard_drone_id: str | None = None
    assistance_reason: str | None = None
    assistance_requested_at: float | None = None
    assistance_source_drone_id: str | None = None
    handover_from_drone_id: str | None = None
    handover_to_drone_id: str | None = None
    handover_x: float | None = None
    handover_y: float | None = None
    handover_completed_at: float | None = None
    takeover_count: int = 0
    previous_assignees: list[str] = field(default_factory=list)

    @property
    def pickup_position(self):
        if not self.picked_up and self.handover_x is not None and self.handover_y is not None:
            return self.handover_x, self.handover_y
        return self.pickup_x, self.pickup_y

    @property
    def dropoff_position(self):
        return self.x, self.y


@dataclass
class DroneAgent:
    drone_id: str
    x: float
    y: float
    capacity: int = 6
    speed_m_per_minute: float = 1200
    battery_range_m: float = 80_000
    queue_capacity: int | None = None
    cargo_capacity: int = 3
    battery_capacity_m: float = 60_000
    battery_remaining_m: float = 60_000
    low_battery_threshold_m: float = 12_000
    safety_buffer_m: float = 5_000
    queue: list[DeliveryTask] = field(default_factory=list)
    onboard_tasks: list[DeliveryTask] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)
    total_distance_m: float = 0
    status: str = "idle"
    region_id: str | None = None
    planner: object | None = None
    home_depot: dict | None = None
    depots: list[dict] = field(default_factory=list)
    target_depot: dict | None = None
    failure_mode: str | None = None
    takeover_mode: str | None = None
    step_events: list[dict] = field(default_factory=list)

    def __post_init__(self):
        if self.queue_capacity is None:
            self.queue_capacity = self.capacity
        self.capacity = self.queue_capacity
        if self.planner is None:
            from simulation.planner import RuleBasedPlanner

            self.planner = RuleBasedPlanner()

    def can_accept(self):
        return not self.failure_mode and len(self.queue) < self.queue_capacity and self.status not in {
            "failed",
            "charging",
            "returning",
        }

#加上了剩余电量判断部分
    # def can_accept_task(self, task):
    #     return self.can_accept() and self.battery_remaining_m >= self.required_battery_for(task)
#只判断队列容量和状态是否满足
    def can_accept_task(self, task):
      return self.can_accept()


    def required_battery_for(self, task):
        return_depot = self.nearest_depot_from(task.dropoff_position, fallback_task=task)
        return (
            euclidean(self.position, task.pickup_position)
            + euclidean(task.pickup_position, task.dropoff_position)
            + euclidean(task.dropoff_position, return_depot)
            + self.safety_buffer_m
        )

    def estimate_cost(self, task, now_minute):
        if not self.can_accept_task(task):
            if self.is_low_battery() and not self.onboard_tasks:
                self.start_returning()
            return 1_000_000_000
        return self.planner.estimate_cost(self, task, now_minute)

    @property
    def position(self):
        return self.x, self.y

    @property
    def battery_consumed_m(self):
        return max(0, self.battery_capacity_m - self.battery_remaining_m)

    def assign(self, task):
        if not self.can_accept_task(task):
            return False
        task.status = "assigned"
        task.assigned_to = self.drone_id
        self.queue.append(task)
        self.status = "queued"
        return True

    def step(self, minutes, now_minute):
        self.step_events = []
        if self.failure_mode and self.status != "failed":
            self.mark_failed(self.failure_mode)
        if self.status == "failed":
            return []

        completed = []
        remaining_distance = min(
            self.speed_m_per_minute * minutes,
            self.battery_remaining_m,
        )
        if not self.queue and not self.onboard_tasks and self.is_low_battery():
            self.start_returning()

        while remaining_distance >= 0:
            if self.status == "returning":
                target = self.depot_target_position()
                if target is None:
                    self.status = "idle"
                    break
                if self.move_toward(target, remaining_distance) >= remaining_distance:
                    break
                break

            self.pickup_available_tasks(now_minute)
            target_task = self.next_target_task()
            if target_task is None:
                if self.is_low_battery():
                    self.start_returning()
                    continue
                self.status = "idle"
                break
            self.status = "delivering"
            target = target_task.dropoff_position if target_task.picked_up else target_task.pickup_position
            distance = euclidean(self.position, target)
            if distance <= remaining_distance:
                self.x, self.y = target
                remaining_distance -= distance
                self.consume_distance(distance)
                if not target_task.picked_up:
                    self.pickup_task(target_task, now_minute)
                    continue
                self.complete_task(target_task, now_minute)
                completed.append(target_task)
            else:
                ratio = remaining_distance / distance
                self.x += (target[0] - self.x) * ratio
                self.y += (target[1] - self.y) * ratio
                self.consume_distance(remaining_distance)
                remaining_distance = 0
                break
        if not self.queue and not self.onboard_tasks and self.status not in {"returning", "charging", "failed"}:
            self.status = "idle"
        return completed

    def move_toward(self, target, max_distance):
        distance = euclidean(self.position, target)
        if distance <= max_distance:
            self.x, self.y = target
            self.consume_distance(distance)
            self.charge()
            return distance
        ratio = max_distance / distance if distance else 0
        self.x += (target[0] - self.x) * ratio
        self.y += (target[1] - self.y) * ratio
        self.consume_distance(max_distance)
        return max_distance

    def consume_distance(self, distance):
        self.total_distance_m += distance
        self.battery_remaining_m = max(0, self.battery_remaining_m - distance)

    def is_low_battery(self):
        return self.battery_remaining_m <= self.return_battery_threshold()

    def return_battery_threshold(self, position=None):
        position = position or self.position
        depot = self.select_charge_depot(position)
        if depot is None:
            return self.low_battery_threshold_m
        return max(
            self.low_battery_threshold_m,
            euclidean(position, (depot["x"], depot["y"])) + self.safety_buffer_m,
        )

    def start_returning(self):
        if self.status != "returning":
            self.step_events.append({"type": "drone_low_battery", "drone": self})
            self.step_events.append({"type": "drone_returning_to_charge", "drone": self})
        self.status = "returning"
        self.target_depot = self.resolve_charge_depot()

    def charge(self):
        self.status = "charging"
        self.battery_remaining_m = self.battery_capacity_m
        self.status = "idle"
        self.target_depot = None
        self.step_events.append({"type": "drone_charged", "drone": self})

    def resolve_charge_depot(self):
        nearest = self.select_charge_depot(self.position)
        if nearest is not None:
            return nearest
        return {"region_id": self.region_id, "x": self.x, "y": self.y}

    def depot_target_position(self):
        depot = self.target_depot or self.resolve_charge_depot()
        return (depot["x"], depot["y"]) if depot else None

    def nearest_depot_from(self, position, fallback_task=None):
        depot = self.select_charge_depot(position)
        if depot is not None:
            return depot["x"], depot["y"]
        if fallback_task is not None:
            return fallback_task.pickup_position
        return self.position

    def select_charge_depot(self, position):
        return select_nearest_charge_depot(
            position,
            self.depots,
            home_depot=self.home_depot,
        )

    def pickup_available_tasks(self, now_minute):
        made_progress = True
        while made_progress and len(self.onboard_tasks) < self.cargo_capacity:
            made_progress = False
            for task in self.queue:
                if task.picked_up or task.status == "completed":
                    continue
                if euclidean(self.position, task.pickup_position) == 0:
                    self.pickup_task(task, now_minute)
                    made_progress = True
                    break

    def pickup_task(self, task, now_minute):
        if task.picked_up or len(self.onboard_tasks) >= self.cargo_capacity:
            return False
        task.picked_up = True
        task.status = "picked_up"
        if task.pickup_time is None:
            task.pickup_time = now_minute
        task.onboard_drone_id = self.drone_id
        self.onboard_tasks.append(task)
        self.step_events.append({"type": "task_picked_up", "task": task, "drone": self})
        if task.handover_from_drone_id is not None:
            task.handover_completed_at = now_minute
            task.handover_to_drone_id = self.drone_id
            self.step_events.append(
                {
                    "type": "task_handover_completed",
                    "task": task,
                    "drone": self,
                    "source_drone_id": task.handover_from_drone_id,
                    "reason": task.assistance_reason,
                }
            )
            self.step_events.append(
                {
                    "type": "task_taken_over",
                    "task": task,
                    "drone": self,
                    "source_drone_id": task.handover_from_drone_id,
                    "reason": task.assistance_reason,
                }
            )
        return True

    def complete_task(self, task, now_minute):
        task.status = "completed"
        task.completed_at = now_minute
        self.completed.append(task.task_id)
        if task in self.onboard_tasks:
            self.onboard_tasks.remove(task)
        if task in self.queue:
            self.queue.remove(task)
        if self.takeover_mode == "assisting" and not any(
            queued.handover_to_drone_id == self.drone_id
            and queued.status != "completed"
            for queued in self.queue
        ):
            self.takeover_mode = None

    def detach_task_for_assistance(self, task):
        was_onboard = task in self.onboard_tasks
        if was_onboard:
            self.onboard_tasks.remove(task)
        if task in self.queue:
            self.queue.remove(task)
        self.takeover_mode = "assisted"
        if not self.queue and not self.onboard_tasks:
            if self.status != "failed" and self.is_low_battery():
                self.start_returning()
            elif self.status != "failed":
                self.status = "idle"
        return was_onboard

    def restore_assistance_task(self, task, was_onboard):
        if task not in self.queue:
            self.queue.append(task)
        if was_onboard and task not in self.onboard_tasks:
            self.onboard_tasks.append(task)
        if self.status != "failed":
            self.status = "delivering" if was_onboard else "queued"
        self.takeover_mode = "requesting_help"

    def mark_failed(self, failure_mode="unknown"):
        self.failure_mode = failure_mode
        self.status = "failed"
        self.takeover_mode = "requesting_help" if self.queue else None

    def next_target_task(self):
        if self.onboard_tasks:
            return sorted(self.onboard_tasks, key=lambda task: (task.deadline_minute, task.task_id))[0]
        for task in self.queue:
            if not task.picked_up:
                return task
        return None

#调度器，负责把任务分给 agent
class AuctionCoordinator:
    def assign(self, tasks, drones, now_minute):
        assignments = {}
        for task in tasks:
            candidates = self.candidates_for(task, drones)
            if not candidates:
                continue
            winner = min(candidates, key=lambda drone: drone.estimate_cost(task, now_minute))
            if winner.assign(task):
                assignments[task.task_id] = winner.drone_id
        return assignments

    def candidates_for(self, task, drones, excluded_drone_ids=None):
        excluded_drone_ids = excluded_drone_ids or set()

        def can_take(drone):
            if drone.drone_id in excluded_drone_ids:
                return False
            if hasattr(drone, "can_accept_task"):
                return drone.can_accept_task(task)
            return drone.can_accept()

        region_candidates = [
            drone for drone in drones if can_take(drone) and drone.region_id == task.region_id
        ]
        return region_candidates or [drone for drone in drones if can_take(drone)]


def projected_distance_until_task(drone, target_task):
    position = drone.position
    distance = 0.0
    visited = set()
    onboard = sorted(
        getattr(drone, "onboard_tasks", []),
        key=lambda task: (task.deadline_minute, task.task_id),
    )
    for task in onboard:
        distance += euclidean(position, task.dropoff_position)
        position = task.dropoff_position
        visited.add(task.task_id)
        if task.task_id == target_task.task_id:
            return distance

    for task in getattr(drone, "queue", []):
        if task.task_id in visited or task.status == "completed":
            continue
        if task.picked_up:
            distance += euclidean(position, task.dropoff_position)
        else:
            distance += euclidean(position, task.pickup_position)
            distance += euclidean(task.pickup_position, task.dropoff_position)
        position = task.dropoff_position
        visited.add(task.task_id)
        if task.task_id == target_task.task_id:
            return distance

    distance += euclidean(position, target_task.pickup_position)
    distance += euclidean(target_task.pickup_position, target_task.dropoff_position)
    return distance


def projected_completion_minute(drone, task, now_minute):
    if drone.speed_m_per_minute <= 0:
        return float("inf")
    return now_minute + projected_distance_until_task(drone, task) / drone.speed_m_per_minute


def required_battery_for_projected_task(drone, task):
    route_distance = projected_distance_until_task(drone, task)
    return_depot = drone.nearest_depot_from(task.dropoff_position, fallback_task=task)
    return route_distance + euclidean(task.dropoff_position, return_depot) + drone.safety_buffer_m


def needs_battery_assistance(drone):
    onboard = sorted(
        getattr(drone, "onboard_tasks", []),
        key=lambda task: (task.deadline_minute, task.task_id),
    )
    if not onboard:
        return bool(drone.queue) and drone.is_low_battery()

    position = drone.position
    distance = 0.0
    for task in onboard:
        distance += euclidean(position, task.dropoff_position)
        position = task.dropoff_position
    return_depot = drone.nearest_depot_from(position, fallback_task=onboard[-1])
    required = distance + euclidean(position, return_depot) + drone.safety_buffer_m
    return drone.battery_remaining_m < required


def assistance_reason_for(drone, task, now_minute):
    if drone.status == "failed" or drone.failure_mode:
        return "failure"
    if needs_battery_assistance(drone):
        return "low_battery"
    if (
        task.takeover_count == 0
        and now_minute < task.deadline_minute
        and projected_completion_minute(drone, task, now_minute) > task.deadline_minute
    ):
        return "deadline_risk"
    return None


def configure_assistance_task(task, source_drone, helper_drone_id, reason, now_minute):
    was_onboard = task.picked_up
    pending_handover = (
        not was_onboard
        and task.handover_x is not None
        and task.handover_y is not None
        and task.handover_completed_at is None
    )
    handover_from_drone_id = task.handover_from_drone_id
    handover_x = task.handover_x
    handover_y = task.handover_y
    if was_onboard:
        handover_from_drone_id = source_drone.drone_id
        handover_x = source_drone.x
        handover_y = source_drone.y
    source_drone_id = source_drone.drone_id
    task.status = "waiting"
    task.assigned_to = None
    task.picked_up = False
    task.onboard_drone_id = None
    task.completed_at = None
    task.assistance_reason = reason
    task.assistance_requested_at = now_minute
    task.assistance_source_drone_id = source_drone_id
    task.handover_from_drone_id = (
        handover_from_drone_id if was_onboard or pending_handover else None
    )
    task.handover_to_drone_id = (
        helper_drone_id if was_onboard or pending_handover else None
    )
    task.handover_x = handover_x if was_onboard or pending_handover else None
    task.handover_y = handover_y if was_onboard or pending_handover else None
    task.handover_completed_at = None
    task.takeover_count += 1
    if source_drone_id not in task.previous_assignees:
        task.previous_assignees.append(source_drone_id)
    return task


def copy_task_state(source, target):
    for item in fields(DeliveryTask):
        value = getattr(source, item.name)
        if isinstance(value, list):
            value = list(value)
        setattr(target, item.name, value)
    return target


def select_assistance_drone(task, source_drone, drones, now_minute, reason):
    excluded = set(task.previous_assignees)
    excluded.add(source_drone.drone_id)
    candidates = []
    for drone in drones:
        if drone.drone_id in excluded or not drone.can_accept():
            continue
        if drone.battery_remaining_m < required_battery_for_projected_task(drone, task):
            continue
        completion = projected_completion_minute(drone, task, now_minute)
        if reason == "deadline_risk" and completion > task.deadline_minute:
            continue
        candidates.append((drone, completion))
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda item: (
            item[1] > task.deadline_minute,
            max(0, item[1] - task.deadline_minute),
            item[0].region_id != task.region_id,
            item[1],
            len(item[0].queue),
            item[0].drone_id,
        ),
    )[0]

#仿真循环，释放当前时间可派送的任务、 用AuctionCoordinator分配任务，
#诶个
class SimulationEnv:
    def __init__(
        self,
        tasks,
        drones,
        coordinator,
        step_minutes=5,
        knowledge_store=None,
        charging_depots=None,
    ):
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
        self.depots = (
            list(charging_depots)
            if charging_depots is not None
            else self._build_depot_list()
        )
        self._attach_depots_to_drones()

    def _build_depot_list(self):
        depots = {}
        for task in self.tasks:
            if task.pickup_x is None or task.pickup_y is None:
                continue
            depots.setdefault(
                task.region_id,
                {"region_id": task.region_id, "x": task.pickup_x, "y": task.pickup_y},
            )
        return list(depots.values())

    def _attach_depots_to_drones(self):
        by_region = {depot["region_id"]: depot for depot in self.depots}
        for drone in self.drones:
            drone.depots = self.depots
            if drone.region_id in by_region:
                drone.home_depot = by_region[drone.region_id]

    def run(self, max_steps=240):
        for _ in range(max_steps):
            self.step_once()
            if self.is_complete():
                break
        return self.to_trace()

    def step_once(self):
        self.release_and_assign()
        completed_this_step = []
        next_time = self.now_minute + self.step_minutes
        for drone in self.drones:
            completed_tasks = drone.step(self.step_minutes, next_time)
            self.record_drone_step_events(drone, next_time)
            for task in completed_tasks:
                self.knowledge_store.record_completion(next_time, task, drone)
            completed_this_step.extend(completed_tasks)
        self.now_minute = next_time
        frame = self.snapshot(completed_this_step)
        self.frames.append(frame)
        self.knowledge_store.record_step(
            len(self.frames),
            self.now_minute,
            self.tasks,
            self.drones,
            completed_this_step,
        )
        return frame

    def record_drone_step_events(self, drone, time_minute):
        for event in getattr(drone, "step_events", []):
            event_type = event["type"]
            if event_type == "task_picked_up":
                self.knowledge_store.record_pickup(time_minute, event["task"], drone)
            elif event_type in {"task_handover_completed", "task_taken_over"}:
                source = self.drone_by_id(event["source_drone_id"])
                self.knowledge_store.record_assistance_event(
                    time_minute,
                    event_type,
                    event["task"],
                    source,
                    helper_drone=drone,
                    reason=event.get("reason"),
                )
            else:
                self.knowledge_store.record_drone_event(time_minute, event_type, drone)

    #派单阶段用的
    def release_and_assign(self):
        self.coordinate_assistance()
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

    def coordinate_assistance(self):
        for source in list(self.drones):
            if source.failure_mode and source.status != "failed":
                source.mark_failed(source.failure_mode)
            for task in list(source.queue):
                reason = assistance_reason_for(source, task, self.now_minute)
                if reason is None:
                    continue
                self._transfer_task_for_assistance(source, task, reason)

    def _transfer_task_for_assistance(self, source, task, reason):
        is_new_request = (
            task.assistance_source_drone_id != source.drone_id
            or task.assistance_reason != reason
        )
        task.assistance_reason = reason
        task.assistance_requested_at = self.now_minute
        task.assistance_source_drone_id = source.drone_id
        source.takeover_mode = "requesting_help"
        if is_new_request:
            self.knowledge_store.record_assistance_event(
                self.now_minute,
                "task_assistance_requested",
                task,
                source,
                reason=reason,
            )

        transfer_candidate = replace(task, previous_assignees=list(task.previous_assignees))
        configure_assistance_task(
            transfer_candidate,
            source,
            helper_drone_id=None,
            reason=reason,
            now_minute=self.now_minute,
        )
        helper = select_assistance_drone(
            transfer_candidate,
            source,
            self.drones,
            self.now_minute,
            reason,
        )
        if helper is None:
            return False

        original = replace(task, previous_assignees=list(task.previous_assignees))
        previous_status = source.status
        was_onboard = source.detach_task_for_assistance(task)
        configure_assistance_task(
            task,
            source,
            helper_drone_id=helper.drone_id,
            reason=reason,
            now_minute=self.now_minute,
        )
        if not helper.assign(task):
            copy_task_state(original, task)
            source.restore_assistance_task(task, was_onboard)
            return False

        helper.takeover_mode = "assisting"
        self.knowledge_store.record_assignment(self.now_minute, task, helper)
        self.knowledge_store.record_assistance_event(
            self.now_minute,
            "assist_dispatched",
            task,
            source,
            helper_drone=helper,
            reason=reason,
        )
        if not was_onboard:
            self.knowledge_store.record_assistance_event(
                self.now_minute,
                "task_taken_over",
                task,
                source,
                helper_drone=helper,
                reason=reason,
            )
        if previous_status != "returning" and source.status == "returning":
            self.knowledge_store.record_drone_event(
                self.now_minute, "drone_low_battery", source
            )
            self.knowledge_store.record_drone_event(
                self.now_minute, "drone_returning_to_charge", source
            )
        return True

    def drone_by_id(self, drone_id):
        for drone in self.drones:
            if drone.drone_id == drone_id:
                return drone
        raise KeyError(drone_id)

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
                    "queue_capacity": drone.queue_capacity,
                    "cargo_capacity": drone.cargo_capacity,
                    "onboard": [task.task_id for task in drone.onboard_tasks],
                    "onboard_count": len(drone.onboard_tasks),
                    "battery_capacity_m": round(drone.battery_capacity_m, 1),
                    "battery_remaining_m": round(drone.battery_remaining_m, 1),
                    "battery_consumed_m": round(drone.battery_consumed_m, 1),
                    "low_battery_threshold_m": round(drone.low_battery_threshold_m, 1),
                    "safety_buffer_m": round(drone.safety_buffer_m, 1),
                    "failure_mode": drone.failure_mode,
                    "takeover_mode": drone.takeover_mode,
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
                    "pickup_time": task.pickup_time,
                    "onboard_drone_id": task.onboard_drone_id,
                    "completed_at": task.completed_at,
                    "assistance_reason": task.assistance_reason,
                    "assistance_source_drone_id": task.assistance_source_drone_id,
                    "handover_from_drone_id": task.handover_from_drone_id,
                    "handover_to_drone_id": task.handover_to_drone_id,
                    "handover_completed_at": task.handover_completed_at,
                    "takeover_count": task.takeover_count,
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
                    "pickup_time": format_minute(task.pickup_time) if task.pickup_time is not None else None,
                    "onboard_drone_id": task.onboard_drone_id,
                    "completed_at": format_minute(task.completed_at) if task.completed_at is not None else None,
                    "status": task.status,
                    "assistance_reason": task.assistance_reason,
                    "assistance_source_drone_id": task.assistance_source_drone_id,
                    "handover_from_drone_id": task.handover_from_drone_id,
                    "handover_to_drone_id": task.handover_to_drone_id,
                    "handover_completed_at": format_minute(task.handover_completed_at)
                    if task.handover_completed_at is not None
                    else None,
                    "takeover_count": task.takeover_count,
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


@lru_cache(maxsize=None)
def _load_region_depot_catalog(catalog_path):
    path = Path(catalog_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Fixed region depot catalog not found: {path}. "
            "Run scripts/build_fixed_region_depots.py first."
        )
    catalog = json.loads(path.read_text(encoding="utf-8"))
    if catalog.get("schema_version") != 1 or not isinstance(catalog.get("cities"), dict):
        raise ValueError(f"Invalid fixed region depot catalog: {path}")
    return catalog


def load_fixed_region_depots(city, region_ids=None, catalog_path=FIXED_REGION_DEPOTS_PATH):
    catalog = _load_region_depot_catalog(str(Path(catalog_path).resolve()))
    canonical_city = city_name(city)
    city_payload = catalog["cities"].get(canonical_city)
    if city_payload is None:
        raise KeyError(f"No fixed region depots configured for city {canonical_city!r}")

    configured = city_payload.get("regions", {})
    selected_ids = set(configured) if region_ids is None else {str(value) for value in region_ids}
    missing = sorted(selected_ids - set(configured))
    if missing:
        raise KeyError(
            f"Missing fixed region depots for city {canonical_city!r}: {', '.join(missing)}"
        )
    return {
        region_id: {
            "region_id": region_id,
            "x": float(configured[region_id]["x"]),
            "y": float(configured[region_id]["y"]),
            "task_count": int(configured[region_id]["task_count"]),
        }
        for region_id in sorted(selected_ids)
    }


def build_region_depots(tasks, catalog_path=FIXED_REGION_DEPOTS_PATH):
    if not tasks:
        return {}
    cities = {city_name(task.city) for task in tasks}
    if len(cities) != 1:
        raise ValueError(f"Tasks must belong to one city, got: {sorted(cities)}")
    depots = load_fixed_region_depots(
        cities.pop(),
        region_ids={task.region_id for task in tasks},
        catalog_path=catalog_path,
    )
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


def make_drones(count, depot, capacity=6):
    return [
        DroneAgent(
            drone_id=f"drone-{index + 1}",
            x=depot[0] + (index % 3) * 60,
            y=depot[1] + (index // 3) * 60,
            capacity=capacity,
            home_depot={"region_id": None, "x": depot[0], "y": depot[1]},
        )
        for index in range(count)
    ]


def make_region_drones(count, depots, capacity=6, seed=13):
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
                home_depot=depot,
                depots=list(depots.values()),
            )
        )
    return drones


def current_target(drone):
    if drone.status == "returning" and drone.target_depot:
        return {
            "task_id": None,
            "phase": "charge",
            "x": round(drone.target_depot["x"], 3),
            "y": round(drone.target_depot["y"], 3),
        }
    task = drone.next_target_task() if hasattr(drone, "next_target_task") else None
    if task is None:
        return None
    target = task.dropoff_position if task.picked_up else task.pickup_position
    is_handover = not task.picked_up and task.handover_from_drone_id is not None
    return {
        "task_id": task.task_id,
        "phase": "delivery" if task.picked_up else ("handover" if is_handover else "pickup"),
        "x": round(target[0], 3),
        "y": round(target[1], 3),
    }


def task_counts(tasks):
    counts = {"waiting": 0, "assigned": 0, "picked_up": 0, "completed": 0}
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
