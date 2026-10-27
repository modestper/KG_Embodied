from dataclasses import dataclass, field

from simulation.delivery_sim import (
    DeliveryTask,
    DroneAgent,
    assistance_reason_for,
    configure_assistance_task,
    copy_task_state,
    euclidean,
    format_minute,
    select_assistance_drone,
    select_nearest_charge_depot,
    task_counts,
)
from simulation.knowledge import TemporalKnowledgeStore
from simulation.checkpoint import apply_task_states, task_state_payload
from simulation.state_codec import (
    drone_from_payload, drone_to_payload, task_from_payload, task_to_payload,
)


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


@dataclass
class DroneStateProxy:
    drone_id: str
    x: float
    y: float
    capacity: int = 5
    queue_capacity: int = 6
    cargo_capacity: int = 3
    speed_m_per_minute: float = 1200
    battery_range_m: float = 80_000
    battery_capacity_m: float = 60_000
    battery_remaining_m: float = 60_000
    low_battery_threshold_m: float = 12_000
    safety_buffer_m: float = 5_000
    queue: list = field(default_factory=list)
    onboard_tasks: list = field(default_factory=list)
    completed: list = field(default_factory=list)
    total_distance_m: float = 0
    status: str = "idle"
    region_id: str | None = None
    target: dict | None = None
    home_depot: dict | None = None
    depots: list = field(default_factory=list)
    target_depot: dict | None = None
    failure_mode: str | None = None
    takeover_mode: str | None = None
    planner: object | None = None

    @property
    def position(self):
        return self.x, self.y

    def can_accept(self):
        return not self.failure_mode and len(self.queue) < self.queue_capacity and self.status not in {
            "failed",
            "charging",
            "returning",
        }
#修改了接单前无人机电量判断
    # def can_accept_task(self, task):
    #     return self.can_accept() and self.battery_remaining_m >= self.required_battery_for(task)
#
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

    def estimate_cost(self, task, now_minute):
        return self.planner.estimate_cost(self, task, now_minute)

    def assign(self, task):
        if not self.can_accept_task(task):
            return False
        task.status = "assigned"
        task.assigned_to = self.drone_id
        self.queue.append(task)
        self.status = "queued"
        return True


class _DroneActor:
    def __init__(self, drone_payload, knowledge_config=None):
        self.drone = drone_from_payload(drone_payload)
        self.knowledge_store = None
        if knowledge_config is not None:
            from simulation.knowledge_writer import KnowledgeWriterConfig

            self.knowledge_store = KnowledgeWriterConfig(**knowledge_config).build_store()
            self.knowledge_store.actor_owned = True

    def snapshot(self):
        return drone_snapshot(self.drone)

    def knowledge_status(self):
        import os

        return {
            "pid": os.getpid(),
            "writes_enabled": self.knowledge_store is not None,
            "events": len(self.knowledge_store.events) if self.knowledge_store is not None else 0,
        }

    def assign(self, task_payload, now_minute=None):
        task = task_from_payload(task_payload)
        accepted = self.drone.assign(task)
        if accepted and self.knowledge_store is not None:
            self.knowledge_store.record_assignment(now_minute, task, self.drone)
        return {
            "accepted": accepted,
            "drone": self.snapshot(),
            "task": task_to_payload(task),
        }

    def assign_assistance(self, task_payload, now_minute=None):
        previous_mode = self.drone.takeover_mode
        self.drone.takeover_mode = "assisting"
        result = self.assign(task_payload, now_minute)
        if not result["accepted"]:
            self.drone.takeover_mode = previous_mode
            result["drone"] = self.snapshot()
        return result

    def release_task_for_assistance(self, task_id):
        task = next((item for item in self.drone.queue if item.task_id == task_id), None)
        if task is None:
            return {"released": False, "drone": self.snapshot()}
        if self.drone.failure_mode and self.drone.status != "failed":
            self.drone.mark_failed(self.drone.failure_mode)
        payload = task_to_payload(task)
        previous_status = self.drone.status
        was_onboard = self.drone.detach_task_for_assistance(task)
        return {
            "released": True,
            "task": payload,
            "was_onboard": was_onboard,
            "previous_status": previous_status,
            "drone": self.snapshot(),
        }

    def restore_task_after_assistance(self, task_payload, was_onboard):
        task = task_from_payload(task_payload)
        self.drone.restore_assistance_task(task, was_onboard)
        return self.snapshot()

    def step(self, minutes, now_minute, step_index=None):
        completed = self.drone.step(minutes, now_minute)
        if self.knowledge_store is not None:
            for event in self.drone.step_events:
                if event["type"] == "task_picked_up":
                    self.knowledge_store.record_pickup(now_minute, event["task"], self.drone)
                elif event["type"] not in {"task_handover_completed", "task_taken_over"}:
                    self.knowledge_store.record_drone_event(now_minute, event["type"], self.drone)
            for task in completed:
                self.knowledge_store.record_completion(now_minute, task, self.drone)
            if step_index is not None:
                self.knowledge_store.record_drone_state(step_index, now_minute, self.drone)
        return {
            "drone": self.snapshot(),
            "completed_tasks": [task_to_payload(task) for task in completed],
            "events": [event_to_payload(event) for event in self.drone.step_events],
        }

    def close(self):
        if self.knowledge_store is not None:
            self.knowledge_store.close()
            self.knowledge_store = None


def drone_snapshot(drone):
    from simulation.delivery_sim import current_target

    target = current_target(drone)
    return {
        "id": drone.drone_id,
        "drone_id": drone.drone_id,
        "x": round(drone.x, 3),
        "y": round(drone.y, 3),
        "status": drone.status,
        "region_id": drone.region_id,
        "queue": [task.task_id for task in drone.queue],
        "queue_payload": [task_to_payload(task) for task in drone.queue],
        "queue_capacity": drone.queue_capacity,
        "cargo_capacity": drone.cargo_capacity,
        "onboard": [task.task_id for task in drone.onboard_tasks],
        "onboard_tasks": [task_to_payload(task) for task in drone.onboard_tasks],
        "onboard_count": len(drone.onboard_tasks),
        "target": target,
        "completed": len(drone.completed),
        "completed_ids": list(drone.completed),
        "distance_m": round(drone.total_distance_m, 1),
        "capacity": drone.capacity,
        "speed_m_per_minute": drone.speed_m_per_minute,
        "battery_range_m": drone.battery_range_m,
        "battery_capacity_m": drone.battery_capacity_m,
        "battery_remaining_m": drone.battery_remaining_m,
        "battery_consumed_m": drone.battery_consumed_m,
        "low_battery_threshold_m": drone.low_battery_threshold_m,
        "safety_buffer_m": drone.safety_buffer_m,
        "home_depot": drone.home_depot,
        "depots": list(drone.depots),
        "target_depot": drone.target_depot,
        "failure_mode": drone.failure_mode,
        "takeover_mode": drone.takeover_mode,
    }


def event_to_payload(event):
    payload = {
        key: value
        for key, value in event.items()
        if key not in {"task", "drone"}
    }
    if event.get("task") is not None:
        payload["task"] = task_to_payload(event["task"])
    return payload


def proxy_from_snapshot(snapshot, planner):
    return DroneStateProxy(
        drone_id=snapshot["drone_id"],
        x=snapshot["x"],
        y=snapshot["y"],
        capacity=snapshot["capacity"],
        queue_capacity=snapshot["queue_capacity"],
        cargo_capacity=snapshot["cargo_capacity"],
        speed_m_per_minute=snapshot["speed_m_per_minute"],
        battery_range_m=snapshot["battery_range_m"],
        battery_capacity_m=snapshot["battery_capacity_m"],
        battery_remaining_m=snapshot["battery_remaining_m"],
        low_battery_threshold_m=snapshot["low_battery_threshold_m"],
        safety_buffer_m=snapshot["safety_buffer_m"],
        queue=[task_from_payload(payload) for payload in snapshot.get("queue_payload", [])],
        onboard_tasks=[task_from_payload(payload) for payload in snapshot.get("onboard_tasks", [])],
        completed=list(snapshot["completed_ids"]),
        total_distance_m=snapshot["distance_m"],
        status=snapshot["status"],
        region_id=snapshot["region_id"],
        target=snapshot["target"],
        home_depot=snapshot.get("home_depot"),
        depots=list(snapshot.get("depots", [])),
        target_depot=snapshot.get("target_depot"),
        failure_mode=snapshot.get("failure_mode"),
        takeover_mode=snapshot.get("takeover_mode"),
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
        charging_depots=None,
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
        self.actor_knowledge_config = getattr(self.knowledge_store, "actor_config", None)
        if hasattr(self.coordinator, "knowledge_store"):
            self.coordinator.knowledge_store = self.knowledge_store
        self.depots = (
            list(charging_depots)
            if charging_depots is not None
            else self._build_depot_list()
        )
        self._attach_depots_to_drones(drones)

        self.actor_class = self.ray.remote(num_cpus=1)(_DroneActor)
        self.actors = self._create_actors(
            [drone_to_payload(drone) for drone in drones]
        )
        self.planner = drones[0].planner if drones else None
        self.before_step_commit = None
        try:
            self.drones = self._sync_drones()
        except Exception:
            self.close()
            raise

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

    def _attach_depots_to_drones(self, drones):
        by_region = {depot["region_id"]: depot for depot in self.depots}
        for drone in drones:
            drone.depots = self.depots
            if drone.region_id in by_region:
                drone.home_depot = by_region[drone.region_id]

    def close(self):
        actors = list(self.actors.values())
        close_refs = []
        for actor in actors:
            try:
                close_refs.append(actor.close.remote())
            except Exception:
                pass
        if close_refs:
            try:
                self.ray.get(close_refs, timeout=5)
            except Exception:
                pass
        for actor in actors:
            try:
                self.ray.kill(actor, no_restart=True)
            except Exception:  # A dead actor is already closed.
                pass
        self.actors = {}

    def _create_actors(self, drone_payloads):
        return {
            payload["drone_id"]: self.actor_class.remote(payload, self.actor_knowledge_config)
            for payload in drone_payloads
        }

    def checkpoint_state(self):
        return {
            "now_minute": self.now_minute,
            "task_states": [task_state_payload(task) for task in self.tasks],
            "drones": [drone_to_payload(drone) for drone in self.drones],
        }

    def restore_checkpoint_state(self, state):
        task_states = state.get("task_states") or []
        apply_task_states(self.tasks, task_states)
        drone_payloads = state.get("drones") or []
        expected_ids = {drone.drone_id for drone in self.drones}
        actual_ids = {payload["drone_id"] for payload in drone_payloads}
        if actual_ids != expected_ids:
            missing = sorted(expected_ids - actual_ids)
            extra = sorted(actual_ids - expected_ids)
            raise ValueError(
                f"checkpoint drone ids mismatch: missing={missing} extra={extra}"
            )

        self.close()
        self.actors = self._create_actors(drone_payloads)
        self.now_minute = state["now_minute"]
        self._sync_drones()

        restored_task_states = {
            task.task_id: task_state_payload(task) for task in self.tasks
        }
        for drone in self.drones:
            for task in [*drone.queue, *drone.onboard_tasks]:
                expected = restored_task_states.get(task.task_id)
                if expected is None:
                    raise ValueError(
                        f"checkpoint drone references unknown task: {task.task_id}"
                    )
                for field, value in expected.items():
                    if field != "task_id":
                        setattr(task, field, value)
        return self.drones

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
            [actor.step.remote(self.step_minutes, next_time, len(self.frames) + 1)
             for actor in self.actors.values()]
        )
        completed_this_step = []
        drone_snapshots = []
        for result in results:
            drone_snapshots.append(result["drone"])
            drone = proxy_from_snapshot(result["drone"], self.planner)
            self.record_drone_step_events(result.get("events", []), drone, next_time)
            for payload in result["completed_tasks"]:
                task = self._apply_completed_task(payload)
                completed_this_step.append(task)
                self.knowledge_store.record_completion(next_time, task, drone)
        self.now_minute = next_time
        self.drones = [proxy_from_snapshot(snapshot, self.planner) for snapshot in drone_snapshots]
        frame = self.snapshot(completed_this_step)
        self.frames.append(frame)
        if self.before_step_commit is not None:
            flush = getattr(self.knowledge_store, "flush", None)
            if flush is not None:
                # Keep the durable checkpoint at most one Step ahead of Neo4j.
                flush()
            self.before_step_commit(self, len(self.frames), completed_this_step)
        self.knowledge_store.record_step(
            len(self.frames),
            self.now_minute,
            self.tasks,
            self.drones,
            completed_this_step,
        )
        return frame

    def record_drone_step_events(self, events, drone, time_minute):
        for event in events:
            event_type = event["type"]
            if event_type == "task_picked_up":
                task = self._apply_picked_task(event["task"])
                self.knowledge_store.record_pickup(time_minute, task, drone)
            elif event_type in {"task_handover_completed", "task_taken_over"}:
                task = self._apply_picked_task(event["task"])
                source = self.drone_by_id(event["source_drone_id"])
                self.knowledge_store.record_assistance_event(
                    time_minute,
                    event_type,
                    task,
                    source,
                    helper_drone=drone,
                    reason=event.get("reason"),
                )
            else:
                self.knowledge_store.record_drone_event(time_minute, event_type, drone)

    def release_and_assign(self):
        if self.actor_knowledge_config is not None:
            # Coordinator writes must not trail a later actor mutation of Task.
            self.knowledge_store.flush()
        self._sync_drones()
        self.coordinate_assistance()
        available = [
            task
            for task in self.tasks
            if task.status == "waiting" and task.release_minute <= self.now_minute
        ]
        assignments = self.coordinator.assign(available, self.drones, self.now_minute)
        task_by_id = {task.task_id: task for task in available}
        self._sync_drones()
        for task_id, drone_id in assignments.items():
            task = task_by_id.get(task_id)
            if not task:
                continue
            self._reset_task_assignment(task)
            self._assign_task_with_retry(task, preferred_drone_id=drone_id)
        self._sync_drones()

    def coordinate_assistance(self):
        for source_id in [drone.drone_id for drone in self.drones]:
            source = self.drone_by_id(source_id)
            for task_id in [task.task_id for task in source.queue]:
                source = self.drone_by_id(source_id)
                actor_task = next(
                    (task for task in source.queue if task.task_id == task_id),
                    None,
                )
                if actor_task is None:
                    continue
                reason = assistance_reason_for(source, actor_task, self.now_minute)
                if reason is None:
                    continue
                task = self._task_by_id(task_id)
                self._transfer_task_for_assistance(source, task, reason)

    def _transfer_task_for_assistance(self, source, task, reason):
        is_new_request = (
            task.assistance_source_drone_id != source.drone_id
            or task.assistance_reason != reason
        )
        task.assistance_reason = reason
        task.assistance_requested_at = self.now_minute
        task.assistance_source_drone_id = source.drone_id
        if is_new_request:
            self.knowledge_store.record_assistance_event(
                self.now_minute,
                "task_assistance_requested",
                task,
                source,
                reason=reason,
            )

        transfer_candidate = task_from_payload(task_to_payload(task))
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

        source_actor = self.actors.get(source.drone_id)
        helper_actor = self.actors.get(helper.drone_id)
        if source_actor is None or helper_actor is None:
            return False
        release = self.ray.get(
            source_actor.release_task_for_assistance.remote(task.task_id)
        )
        if not release.get("released"):
            self._replace_drone_from_snapshot(release["drone"])
            return False
        source_after = self._replace_drone_from_snapshot(release["drone"])

        original = task_from_payload(task_to_payload(task))
        configure_assistance_task(
            task,
            source,
            helper_drone_id=helper.drone_id,
            reason=reason,
            now_minute=self.now_minute,
        )
        result = self.ray.get(
            helper_actor.assign_assistance.remote(task_to_payload(task), self.now_minute)
        )
        helper_after = self._replace_drone_from_snapshot(result["drone"])
        if not result.get("accepted"):
            copy_task_state(original, task)
            restored = self.ray.get(
                source_actor.restore_task_after_assistance.remote(
                    release["task"], release["was_onboard"]
                )
            )
            self._replace_drone_from_snapshot(restored)
            return False

        self._apply_assigned_task(result["task"])
        self.knowledge_store.record_assignment(self.now_minute, task, helper_after)
        self.knowledge_store.record_assistance_event(
            self.now_minute,
            "assist_dispatched",
            task,
            source_after,
            helper_drone=helper_after,
            reason=reason,
        )
        if not release["was_onboard"]:
            self.knowledge_store.record_assistance_event(
                self.now_minute,
                "task_taken_over",
                task,
                source_after,
                helper_drone=helper_after,
                reason=reason,
            )
        if release["previous_status"] != "returning" and source_after.status == "returning":
            record_event = getattr(
                self.knowledge_store, "record_coordinator_drone_event",
                self.knowledge_store.record_drone_event,
            )
            record_event(
                self.now_minute, "drone_low_battery", source_after
            )
            record_event(
                self.now_minute, "drone_returning_to_charge", source_after
            )
        return True

    def _assign_task_with_retry(self, task, preferred_drone_id=None):
        rejected = set()
        while task.status == "waiting":
            candidates = self._candidate_drones(task, preferred_drone_id, rejected)
            if not candidates:
                return False
            drone = min(candidates, key=lambda item: item.estimate_cost(task, self.now_minute))
            actor = self.actors.get(drone.drone_id)
            if actor is None:
                rejected.add(drone.drone_id)
                continue

            result = self.ray.get(actor.assign.remote(task_to_payload(task), self.now_minute))
            self._replace_drone_from_snapshot(result["drone"])
            if result.get("accepted"):
                self._apply_assigned_task(result["task"])
                assigned_drone = proxy_from_snapshot(result["drone"], self.planner)
                self.knowledge_store.record_assignment(self.now_minute, task, assigned_drone)
                return True

            self._reset_task_assignment(task)
            rejected.add(drone.drone_id)
            preferred_drone_id = None
        return False

    def _candidate_drones(self, task, preferred_drone_id, rejected):
        drone_by_id = {drone.drone_id: drone for drone in self.drones}
        if preferred_drone_id and preferred_drone_id not in rejected:
            preferred = drone_by_id.get(preferred_drone_id)
            if preferred and preferred.can_accept_task(task):
                return [preferred]
            rejected.add(preferred_drone_id)

        if hasattr(self.coordinator, "candidates_for"):
            return self.coordinator.candidates_for(task, self.drones, excluded_drone_ids=rejected)
        return [
            drone for drone in self.drones
            if drone.drone_id not in rejected and drone.can_accept_task(task)
        ]

    def _replace_drone_from_snapshot(self, snapshot):
        proxy = proxy_from_snapshot(snapshot, self.planner)
        for index, drone in enumerate(self.drones):
            if drone.drone_id == proxy.drone_id:
                self.drones[index] = proxy
                return proxy
        self.drones.append(proxy)
        return proxy

    def _reset_task_assignment(self, task):
        if task.status != "completed":
            task.status = "waiting"
            task.assigned_to = None
            task.picked_up = False
            task.pickup_time = None
            task.onboard_drone_id = None

    def _apply_assigned_task(self, payload):
        task = self._task_by_id(payload["task_id"])
        return copy_task_state(task_from_payload(payload), task)

    def _apply_completed_task(self, payload):
        task = self._task_by_id(payload["task_id"])
        return copy_task_state(task_from_payload(payload), task)

    def _apply_picked_task(self, payload):
        task = self._task_by_id(payload["task_id"])
        return copy_task_state(task_from_payload(payload), task)

    def _task_by_id(self, task_id):
        for task in self.tasks:
            if task.task_id == task_id:
                return task
        raise KeyError(task_id)

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
                    "onboard": [task.task_id for task in getattr(drone, "onboard_tasks", [])],
                    "onboard_count": len(getattr(drone, "onboard_tasks", [])),
                    "battery_capacity_m": round(drone.battery_capacity_m, 1),
                    "battery_remaining_m": round(drone.battery_remaining_m, 1),
                    "battery_consumed_m": round(
                        max(0, drone.battery_capacity_m - drone.battery_remaining_m),
                        1,
                    ),
                    "low_battery_threshold_m": round(drone.low_battery_threshold_m, 1),
                    "safety_buffer_m": round(drone.safety_buffer_m, 1),
                    "failure_mode": drone.failure_mode,
                    "takeover_mode": drone.takeover_mode,
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
