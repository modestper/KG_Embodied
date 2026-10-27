import os
import time
from uuid import uuid4

from simulation.delivery_sim import euclidean, format_minute


AGENT_STATE_INDEXES = (
    (
        "lade_agent_state_run_drone_step",
        ("run_id", "drone_id", "step_index"),
    ),
    (
        "lade_agent_state_run_region_step",
        ("run_id", "region_id", "step_index"),
    ),
    (
        "lade_agent_state_run_step",
        ("run_id", "step_index"),
    ),
)


def drone_is_low_battery(drone):
    checker = getattr(drone, "is_low_battery", None)
    if callable(checker):
        return bool(checker())
    return getattr(drone, "battery_remaining_m", 0) <= getattr(
        drone,
        "low_battery_threshold_m",
        -1,
    )


class TemporalKnowledgeStore:
    def __init__(self):
        self.events = []
        self.steps = []

    def record_assignment(self, time_minute, task, drone):
        self.events.append(
            {
                "time_minute": time_minute,
                "time": format_minute(time_minute),
                "type": "task_assigned",
                "region_id": task.region_id,
                "task_id": task.task_id,
                "drone_id": drone.drone_id,
                "queue_len": len(drone.queue),
                "onboard_count": len(getattr(drone, "onboard_tasks", [])),
                "battery_remaining_m": getattr(drone, "battery_remaining_m", None),
            }
        )

    def record_pickup(self, time_minute, task, drone):
        self.events.append(
            {
                "time_minute": time_minute,
                "time": format_minute(time_minute),
                "type": "task_picked_up",
                "region_id": task.region_id,
                "task_id": task.task_id,
                "drone_id": drone.drone_id,
                "onboard_count": len(getattr(drone, "onboard_tasks", [])),
                "battery_remaining_m": getattr(drone, "battery_remaining_m", None),
            }
        )

    def record_completion(self, time_minute, task, drone):
        self.events.append(
            {
                "time_minute": time_minute,
                "time": format_minute(time_minute),
                "type": "task_completed",
                "region_id": task.region_id,
                "task_id": task.task_id,
                "drone_id": drone.drone_id,
                "late": time_minute > task.deadline_minute,
                "battery_remaining_m": getattr(drone, "battery_remaining_m", None),
            }
        )

    def record_drone_event(self, time_minute, event_type, drone):
        self.events.append(
            {
                "time_minute": time_minute,
                "time": format_minute(time_minute),
                "type": event_type,
                "region_id": drone.region_id,
                "task_id": None,
                "drone_id": drone.drone_id,
                "status": drone.status,
                "battery_remaining_m": getattr(drone, "battery_remaining_m", None),
                "battery_consumed_m": getattr(drone, "battery_consumed_m", None),
            }
        )

    def record_assistance_event(
        self,
        time_minute,
        event_type,
        task,
        source_drone,
        helper_drone=None,
        reason=None,
    ):
        actor = helper_drone or source_drone
        self.events.append(
            {
                "time_minute": time_minute,
                "time": format_minute(time_minute),
                "type": event_type,
                "region_id": task.region_id,
                "task_id": task.task_id,
                "drone_id": actor.drone_id,
                "source_drone_id": source_drone.drone_id,
                "helper_drone_id": helper_drone.drone_id if helper_drone else None,
                "reason": reason,
                "handover_x": getattr(task, "handover_x", None),
                "handover_y": getattr(task, "handover_y", None),
                "takeover_count": getattr(task, "takeover_count", 0),
            }
        )

    def record_step(self, step_index, time_minute, tasks, drones, completed_this_step):
        step = self.step_summary(step_index, time_minute, tasks, drones, completed_this_step)
        return self.record_step_data(step)

    def record_step_data(self, step, agent_states=None, external_agent_states=False):
        self.steps.append(step)
        return step

    def record_drone_state(self, step_index, time_minute, drone):
        # The memory backend keeps events/step summaries, not a persistent state graph.
        pass

    def step_summary(self, step_index, time_minute, tasks, drones, completed_this_step):
        return {
            "step_index": step_index,
            "time_minute": time_minute,
            "time": format_minute(time_minute),
            "completed_tasks": len(completed_this_step),
            "unfinished_tasks": len([task for task in tasks if task.status != "completed"]),
            "idle_drones": len([drone for drone in drones if drone.status == "idle"]),
            "delivering_drones": len([drone for drone in drones if drone.status == "delivering"]),
            "returning_drones": len([drone for drone in drones if drone.status == "returning"]),
            "charging_drones": len([drone for drone in drones if drone.status == "charging"]),
            "assisting_drones": len(
                [
                    drone
                    for drone in drones
                    if getattr(drone, "takeover_mode", None)
                    in {"assisting", "requesting_help"}
                ]
            ),
            "low_battery_drones": len(
                [drone for drone in drones if drone_is_low_battery(drone)]
            ),
            "total_distance_m": round(sum(drone.total_distance_m for drone in drones), 1),
        }

    def query_region_context(
        self,
        region_id,
        tasks,
        drones,
        now_minute,
        max_tasks=20,
        max_drones=10,
        window_minutes=30,
    ):
        region_tasks = [task for task in tasks if task.region_id == region_id]
        region_drones = [drone for drone in drones if drone.region_id == region_id and drone.can_accept()]
        urgent_tasks = sorted(region_tasks, key=lambda task: (task.deadline_minute, task.release_minute))[
            :max_tasks
        ]
        candidate_drones = sorted(
            region_drones,
            key=lambda drone: (
                len(drone.queue),
                euclidean(drone.position, (urgent_tasks[0].pickup_x, urgent_tasks[0].pickup_y))
                if urgent_tasks
                else 0,
            ),
        )[:max_drones]
        return {
            "region_id": region_id,
            "time": format_minute(now_minute),
            "urgent_tasks": [self.task_summary(task) for task in urgent_tasks],
            "candidate_drones": [self.drone_summary(drone, urgent_tasks) for drone in candidate_drones],
            "recent_agent_performance": self.recent_agent_performance(region_id, now_minute, window_minutes),
            "recent_events": self.recent_events(region_id, now_minute, window_minutes, limit=20),
        }

    def task_summary(self, task):
        return {
            "task_id": task.task_id,
            "deadline": format_minute(task.deadline_minute),
            "pickup": {"x": task.pickup_x, "y": task.pickup_y},
            "dropoff": {"x": task.x, "y": task.y},
            "distance_from_depot_m": round(euclidean(task.pickup_position, task.dropoff_position), 1),
        }

    def drone_summary(self, drone, tasks):
        depot = (tasks[0].pickup_x, tasks[0].pickup_y) if tasks else drone.position
        return {
            "drone_id": drone.drone_id,
            "position": {"x": round(drone.x, 3), "y": round(drone.y, 3)},
            "queue_len": len(drone.queue),
            "distance_to_depot_m": round(euclidean(drone.position, depot), 1),
            "completed": len(drone.completed),
        }

    def recent_agent_performance(self, region_id, now_minute, window_minutes):
        performance = {}
        start = now_minute - window_minutes
        for event in self.events:
            if event["region_id"] != region_id or event["time_minute"] < start:
                continue
            drone = performance.setdefault(event["drone_id"], {"assigned": 0, "completed": 0, "late": 0})
            if event["type"] == "task_assigned":
                drone["assigned"] += 1
            elif event["type"] == "task_completed":
                drone["completed"] += 1
                if event.get("late"):
                    drone["late"] += 1
        return performance

    def recent_events(self, region_id, now_minute, window_minutes, limit=20):
        start = now_minute - window_minutes
        events = [
            event
            for event in self.events
            if event["region_id"] == region_id and start <= event["time_minute"] <= now_minute
        ]
        return events[-limit:]

    def close(self):
        pass


class Neo4jKnowledgeStore(TemporalKnowledgeStore):
    def __init__(
        self,
        driver=None,
        uri=None,
        user=None,
        password=None,
        driver_factory=None,
        run_id=None,
        record_agent_state=False,
        auto_create_indexes=False,
        index_wait_timeout_seconds=600,
        index_poll_interval_seconds=1,
        max_connection_pool_size=None,
    ):
        super().__init__()
        self.run_id = run_id or f"run-{uuid4()}"
        self.record_agent_state = record_agent_state
        self.auto_create_indexes = auto_create_indexes
        self.index_wait_timeout_seconds = index_wait_timeout_seconds
        self.index_poll_interval_seconds = index_poll_interval_seconds
        owns_driver = driver is None
        if driver is not None:
            self.driver = driver
        else:
            if driver_factory is None:
                try:
                    from neo4j import GraphDatabase
                except ImportError as exc:
                    raise RuntimeError(
                        "Neo4j KG backend requires the neo4j Python package. "
                        "Install it with `pip install neo4j` or set KG_BACKEND=memory."
                    ) from exc
                driver_factory = GraphDatabase.driver
            driver_options = {"auth": (user, password)}
            if max_connection_pool_size is not None:
                driver_options["max_connection_pool_size"] = max_connection_pool_size
            self.driver = driver_factory(uri, **driver_options)
        if self.auto_create_indexes:
            try:
                self.ensure_agent_state_indexes()
            except Exception:
                if owns_driver:
                    self.driver.close()
                raise

    def _event_id(self, event_type, time_minute, drone_id, task_id=None):
        normalized_time = format(float(time_minute), ".12f").rstrip("0").rstrip(".")
        task_part = task_id if task_id is not None else "-"
        return f"{self.run_id}:{event_type}:{normalized_time}:{drone_id}:{task_part}"

    def ensure_writer_constraints(self):
        """Run once in the write owner before starting concurrent actor writers."""
        definitions = (
            ("lade_run_id_unique", "Run", "n.id"),
            ("lade_drone_run_id_unique", "Drone", "(n.run_id, n.id)"),
            ("lade_task_run_id_unique", "Task", "(n.run_id, n.id)"),
            ("lade_region_run_id_unique", "Region", "(n.run_id, n.id)"),
            ("lade_event_run_id_unique", "Event", "(n.run_id, n.id)"),
            ("lade_step_run_id_unique", "Step", "(n.run_id, n.id)"),
            ("lade_agent_state_run_id_unique", "AgentState", "(n.run_id, n.id)"),
        )
        with self.driver.session() as session:
            for name, label, properties in definitions:
                session.run(
                    f"CREATE CONSTRAINT {name} IF NOT EXISTS "
                    f"FOR (n:{label}) REQUIRE {properties} IS UNIQUE"
                ).consume()

    def ensure_agent_state_indexes(self):
        deadline = time.monotonic() + self.index_wait_timeout_seconds
        with self.driver.session() as session:
            indexes = self._show_indexes(session)
            expected_names = []
            missing_indexes = []
            for name, properties in AGENT_STATE_INDEXES:
                equivalent = next(
                    (
                        index
                        for index in indexes
                        if index.get("type") == "RANGE"
                        and index.get("entityType") == "NODE"
                        and index.get("labelsOrTypes") == ["AgentState"]
                        and tuple(index.get("properties") or ()) == properties
                    ),
                    None,
                )
                if equivalent is not None:
                    if equivalent.get("state") == "FAILED":
                        failure_message = (
                            equivalent.get("failureMessage") or "unknown failure"
                        )
                        raise RuntimeError(
                            f"Neo4j index {equivalent['name']!r} failed: "
                            f"{failure_message}"
                        )
                    expected_names.append(equivalent["name"])
                    continue

                collision = next(
                    (index for index in indexes if index.get("name") == name),
                    None,
                )
                if collision is not None:
                    raise RuntimeError(
                        f"Neo4j index {name!r} exists with a different definition"
                    )
                missing_indexes.append((name, properties))
                expected_names.append(name)

            for name, properties in missing_indexes:
                columns = ", ".join(f"a.{property_name}" for property_name in properties)
                session.run(
                    f"CREATE INDEX {name} IF NOT EXISTS "
                    f"FOR (a:AgentState) ON ({columns})"
                ).data()
                print(f"Neo4j index created; waiting for ONLINE: {name}")

            while True:
                indexes = self._show_indexes(session)
                by_name = {index["name"]: index for index in indexes}
                pending = []
                for name in expected_names:
                    index = by_name.get(name)
                    if index is None:
                        pending.append(f"{name}=MISSING")
                        continue
                    state = index.get("state")
                    if state == "FAILED":
                        failure_message = index.get("failureMessage") or "unknown failure"
                        raise RuntimeError(
                            f"Neo4j index {name!r} failed: {failure_message}"
                        )
                    if state != "ONLINE":
                        pending.append(f"{name}={state}")

                if not pending:
                    self.agent_state_index_names = tuple(expected_names)
                    return
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        "Timed out waiting for Neo4j indexes: " + ", ".join(pending)
                    )
                time.sleep(self.index_poll_interval_seconds)

    @staticmethod
    def _show_indexes(session):
        return session.run(
            """
            SHOW INDEXES
            YIELD name, state, type, entityType, labelsOrTypes, properties, failureMessage
            RETURN name, state, type, entityType, labelsOrTypes, properties, failureMessage
            """
        ).data()

    def record_assignment(self, time_minute, task, drone):
        super().record_assignment(time_minute, task, drone)
        self._write_event(
            event_type="task_assigned",
            time_minute=time_minute,
            task=task,
            drone=drone,
            properties={"queue_len": len(drone.queue)},
        )

    def record_pickup(self, time_minute, task, drone):
        super().record_pickup(time_minute, task, drone)
        self._write_event(
            event_type="task_picked_up",
            time_minute=time_minute,
            task=task,
            drone=drone,
            properties={"onboard_count": len(getattr(drone, "onboard_tasks", []))},
        )

    def record_completion(self, time_minute, task, drone):
        super().record_completion(time_minute, task, drone)
        self._write_event(
            event_type="task_completed",
            time_minute=time_minute,
            task=task,
            drone=drone,
            properties={"late": time_minute > task.deadline_minute},
        )

    def record_drone_event(self, time_minute, event_type, drone):
        super().record_drone_event(time_minute, event_type, drone)
        self._write_drone_event(event_type, time_minute, drone)

    def record_assistance_event(
        self,
        time_minute,
        event_type,
        task,
        source_drone,
        helper_drone=None,
        reason=None,
    ):
        super().record_assistance_event(
            time_minute,
            event_type,
            task,
            source_drone,
            helper_drone=helper_drone,
            reason=reason,
        )
        actor = helper_drone or source_drone
        event_properties = {
            "source_drone_id": source_drone.drone_id,
            "helper_drone_id": helper_drone.drone_id if helper_drone else None,
            "reason": reason,
            "handover_x": getattr(task, "handover_x", None),
            "handover_y": getattr(task, "handover_y", None),
            "takeover_count": getattr(task, "takeover_count", 0),
        }
        self._write_event(
            event_type=event_type,
            time_minute=time_minute,
            task=task,
            drone=actor,
            properties=event_properties,
        )
        event_id = self._event_id(
            event_type,
            time_minute,
            actor.drone_id,
            task.task_id,
        )
        with self.driver.session() as session:
            session.run(
                """
                MATCH (e:Event {id: $event_id, run_id: $run_id}), (run:Run {id: $run_id})
                MERGE (source:Drone {id: $source_drone_id, run_id: $run_id})
                MERGE (source)-[:IN_RUN]->(run)
                MERGE (e)-[:ABOUT_SOURCE_DRONE]->(source)
                """,
                event_id=event_id,
                source_drone_id=source_drone.drone_id,
                run_id=self.run_id,
            ).consume()
            if helper_drone is not None:
                session.run(
                    """
                    MATCH (e:Event {id: $event_id, run_id: $run_id}),
                          (run:Run {id: $run_id}),
                          (source:Drone {id: $source_drone_id, run_id: $run_id}),
                          (task:Task {id: $task_id, run_id: $run_id})
                    MERGE (helper:Drone {id: $helper_drone_id, run_id: $run_id})
                    MERGE (helper)-[:IN_RUN]->(run)
                    MERGE (e)-[:ABOUT_HELPER_DRONE]->(helper)
                    MERGE (helper)-[assist:ASSISTS {task_id: $task_id}]->(source)
                    SET assist.active = $active,
                        assist.reason = $reason,
                        assist.updated_at = $time_minute
                    MERGE (source)-[handover:HANDOVER_TO {task_id: $task_id}]->(helper)
                    SET handover.active = $active,
                        handover.reason = $reason,
                        handover.updated_at = $time_minute
                    MERGE (helper)-[takeover:TAKES_OVER]->(task)
                    SET takeover.active = true,
                        takeover.reason = $reason,
                        takeover.time_minute = coalesce(takeover.time_minute, $time_minute)
                    WITH source, helper, task
                    OPTIONAL MATCH (source)-[carrying:CARRYING]->(task)
                    SET carrying.active = false,
                        carrying.handover_time_minute = $time_minute
                    WITH source, helper, task
                    OPTIONAL MATCH (source)-[assigned:ASSIGNED]->(task)
                    SET assigned.active = false,
                        assigned.handover_time_minute = $time_minute
                    WITH source, task
                    OPTIONAL MATCH (source)-[previous_takeover:TAKES_OVER]->(task)
                    SET previous_takeover.active = false,
                        previous_takeover.handover_time_minute = $time_minute
                    """,
                    event_id=event_id,
                    source_drone_id=source_drone.drone_id,
                    helper_drone_id=helper_drone.drone_id,
                    task_id=task.task_id,
                    run_id=self.run_id,
                    reason=reason,
                    time_minute=time_minute,
                    active=event_type == "assist_dispatched",
                ).consume()

    def record_step(self, step_index, time_minute, tasks, drones, completed_this_step):
        step = self.step_summary(step_index, time_minute, tasks, drones, completed_this_step)
        agent_states = []
        if self.record_agent_state:
            agent_states = [
                self.agent_state_summary(step_index, time_minute, drone)
                for drone in drones
            ]
        return self.record_step_data(step, agent_states)

    def record_step_data(self, step, agent_states=None, external_agent_states=False):
        super().record_step_data(step)
        step_index = step["step_index"]
        step = {"id": f"{self.run_id}-{step_index}", "run_id": self.run_id, **step}
        with self.driver.session() as session:
            if agent_states:
                session.run(
                    """
                    MERGE (run:Run {id: $run_id})
                    MERGE (s:Step {id: $step_id, run_id: $run_id})
                    SET s = $step
                    MERGE (s)-[:IN_RUN]->(run)
                    WITH run, s
                    UNWIND $agent_states AS state
                    MERGE (d:Drone {id: state.drone_id, run_id: $run_id})
                    SET d.region_id = state.region_id,
                        d.x = state.x,
                        d.y = state.y,
                        d.status = state.status,
                        d.battery_remaining_m = state.battery_remaining_m,
                        d.queue_len = state.queue_len,
                        d.onboard_count = state.onboard_count,
                        d.total_distance_m = state.total_distance_m
                    MERGE (a:AgentState {
                        run_id: $run_id,
                        drone_id: state.drone_id,
                        step_index: state.step_index
                    })
                    SET a = state
                    MERGE (a)-[:IN_RUN]->(run)
                    MERGE (a)-[:IN_STEP]->(s)
                    MERGE (a)-[:OF_DRONE]->(d)
                    """,
                    run_id=self.run_id,
                    step_id=step["id"],
                    step=step,
                    agent_states=agent_states,
                ).consume()
            elif external_agent_states:
                session.run(
                    """
                    MERGE (run:Run {id: $run_id})
                    MERGE (s:Step {id: $step_id, run_id: $run_id})
                    SET s = $step
                    MERGE (s)-[:IN_RUN]->(run)
                    WITH s
                    MATCH (a:AgentState {run_id: $run_id, step_index: $step_index})
                    MERGE (a)-[:IN_STEP]->(s)
                    """,
                    run_id=self.run_id, step_id=step["id"], step=step,
                    step_index=step_index,
                ).consume()
            else:
                session.run(
                    """
                    MERGE (run:Run {id: $run_id})
                    MERGE (s:Step {id: $step_id, run_id: $run_id})
                    SET s = $step
                    MERGE (s)-[:IN_RUN]->(run)
                    """,
                    run_id=self.run_id,
                    step_id=step["id"],
                    step=step,
                ).consume()
        return step

    def record_drone_state(self, step_index, time_minute, drone):
        """An actor publishes its state; only the coordinator publishes Step."""
        state = self.agent_state_summary(step_index, time_minute, drone)
        properties = {
            "id": drone.drone_id, "run_id": self.run_id,
            "region_id": drone.region_id, "x": drone.x, "y": drone.y,
            "status": drone.status, "battery_remaining_m": drone.battery_remaining_m,
            "queue_len": len(drone.queue), "onboard_count": len(drone.onboard_tasks),
            "total_distance_m": drone.total_distance_m,
            "capacity": drone.capacity, "queue_capacity": drone.queue_capacity,
            "cargo_capacity": drone.cargo_capacity,
            "speed_m_per_minute": drone.speed_m_per_minute,
            "battery_capacity_m": drone.battery_capacity_m,
            "battery_range_m": drone.battery_range_m,
            "battery_consumed_m": drone.battery_consumed_m,
            "low_battery_threshold_m": drone.low_battery_threshold_m,
            "safety_buffer_m": drone.safety_buffer_m,
            "completed_count": len(drone.completed),
            "failure_mode": drone.failure_mode, "takeover_mode": drone.takeover_mode,
            "updated_at": time_minute,
        }
        with self.driver.session() as session:
            session.run(
                """
                MERGE (run:Run {id: $run_id})
                MERGE (d:Drone {id: $drone_id, run_id: $run_id})
                SET d += $properties
                MERGE (d)-[:IN_RUN]->(run)
                """,
                run_id=self.run_id, drone_id=drone.drone_id, properties=properties,
            ).consume()
            if self.record_agent_state:
                session.run(
                    """
                    MATCH (run:Run {id: $run_id})
                    MATCH (d:Drone {id: $drone_id, run_id: $run_id})
                    MERGE (a:AgentState {id: $state_id, run_id: $run_id})
                    SET a = $state
                    MERGE (a)-[:IN_RUN]->(run)
                    MERGE (a)-[:OF_DRONE]->(d)
                    """,
                    run_id=self.run_id, drone_id=drone.drone_id,
                    state_id=state["id"], state=state,
                ).consume()

    def latest_step(self):
        with self.driver.session() as session:
            row = session.run(
                """
                MATCH (s:Step {run_id: $run_id})
                RETURN max(s.step_index) AS step_index,
                       max(s.time_minute) AS time_minute
                """,
                run_id=self.run_id,
            ).single()
        if not row or row["step_index"] is None:
            return None
        return {
            "step_index": int(row["step_index"]),
            "time_minute": row["time_minute"],
        }

    def agent_state_summary(self, step_index, time_minute, drone):
        return {
            "id": f"{self.run_id}-{step_index}-{drone.drone_id}",
            "run_id": self.run_id,
            "step_index": step_index,
            "time_minute": time_minute,
            "drone_id": drone.drone_id,
            "region_id": drone.region_id,
            "x": drone.x,
            "y": drone.y,
            "status": drone.status,
            "battery_remaining_m": getattr(drone, "battery_remaining_m", None),
            "queue_len": len(drone.queue),
            "onboard_count": len(getattr(drone, "onboard_tasks", [])),
            "total_distance_m": drone.total_distance_m,
            "failure_mode": getattr(drone, "failure_mode", None),
            "takeover_mode": getattr(drone, "takeover_mode", None),
        }

    def _write_event(self, event_type, time_minute, task, drone, properties=None):
        properties = properties or {}
        event = {
            "id": self._event_id(event_type, time_minute, drone.drone_id, task.task_id),
            "run_id": self.run_id,
            "type": event_type,
            "time_minute": time_minute,
            "time": format_minute(time_minute),
            "region_id": task.region_id,
            "task_id": task.task_id,
            "drone_id": drone.drone_id,
            **properties,
        }
        task_props = {
            "id": task.task_id,
            "run_id": self.run_id,
            "city": task.city,
            "region_id": task.region_id,
            "release_minute": task.release_minute,
            "deadline_minute": task.deadline_minute,
            "status": task.status,
            "picked_up": task.picked_up,
            "pickup_time": task.pickup_time,
            "onboard_drone_id": task.onboard_drone_id,
            "assigned_to": task.assigned_to,
            "completed_at": task.completed_at,
            "assistance_reason": getattr(task, "assistance_reason", None),
            "assistance_requested_at": getattr(task, "assistance_requested_at", None),
            "assistance_source_drone_id": getattr(
                task, "assistance_source_drone_id", None
            ),
            "handover_from_drone_id": getattr(task, "handover_from_drone_id", None),
            "handover_to_drone_id": getattr(task, "handover_to_drone_id", None),
            "handover_x": getattr(task, "handover_x", None),
            "handover_y": getattr(task, "handover_y", None),
            "handover_completed_at": getattr(task, "handover_completed_at", None),
            "takeover_count": getattr(task, "takeover_count", 0),
            "previous_assignees": list(getattr(task, "previous_assignees", [])),
            "late": task.completed_at is not None and task.completed_at > task.deadline_minute,
            "pickup_x": task.pickup_x,
            "pickup_y": task.pickup_y,
            "dropoff_x": task.x,
            "dropoff_y": task.y,
            "aoi_id": task.aoi_id,
        }
        drone_props = {
            "id": drone.drone_id,
            "run_id": self.run_id,
            "region_id": drone.region_id,
            "capacity": drone.capacity,
            "queue_capacity": getattr(drone, "queue_capacity", drone.capacity),
            "cargo_capacity": getattr(drone, "cargo_capacity", None),
            "onboard_count": len(getattr(drone, "onboard_tasks", [])),
            "speed_m_per_minute": drone.speed_m_per_minute,
            "battery_range_m": drone.battery_range_m,
            "battery_capacity_m": getattr(drone, "battery_capacity_m", None),
            "battery_remaining_m": getattr(drone, "battery_remaining_m", None),
            "battery_consumed_m": getattr(drone, "battery_consumed_m", None),
            "low_battery_threshold_m": getattr(drone, "low_battery_threshold_m", None),
            "safety_buffer_m": getattr(drone, "safety_buffer_m", None),
            "status": drone.status,
            "x": drone.x,
            "y": drone.y,
            "queue_len": len(drone.queue),
            "completed_count": len(drone.completed),
            "total_distance_m": drone.total_distance_m,
            "updated_at": time_minute,
            "failure_mode": getattr(drone, "failure_mode", None),
            "takeover_mode": getattr(drone, "takeover_mode", None),
        }
        region_props = {
            "id": task.region_id,
            "run_id": self.run_id,
            "city": task.city,
            "depot_x": task.pickup_x,
            "depot_y": task.pickup_y,
        }
        run_props = {
            "id": self.run_id,
        }
        shared_update = "ON CREATE SET" if getattr(self, "actor_owned", False) else "SET"
        with self.driver.session() as session:
            session.run(
                "MERGE (run:Run {id: $run_id})\n"
                f"{shared_update} run += $run\n"
                "MERGE (r:Region {id: $region_id, run_id: $run_id})\n"
                f"{shared_update} r += $region\n"
                """
                MERGE (d:Drone {id: $drone_id, run_id: $run_id})
                SET d += $drone
                MERGE (t:Task {id: $task_id, run_id: $run_id})
                SET t += $task
                MERGE (d)-[:HOME_REGION]->(r)
                MERGE (d)-[:CURRENTLY_IN]->(r)
                MERGE (t)-[:BELONGS_TO]->(r)
                MERGE (e:Event {id: $event_id, run_id: $run_id})
                SET e = $event
                MERGE (e)-[:ABOUT_DRONE]->(d)
                MERGE (e)-[:ABOUT_TASK]->(t)
                MERGE (e)-[:IN_REGION]->(r)
                MERGE (r)-[:IN_RUN]->(run)
                MERGE (d)-[:IN_RUN]->(run)
                MERGE (t)-[:IN_RUN]->(run)
                MERGE (e)-[:IN_RUN]->(run)
                """,
                run_id=self.run_id,
                region_id=task.region_id,
                drone_id=drone.drone_id,
                task_id=task.task_id,
                event_id=event["id"],
                run=run_props,
                region=region_props,
                drone=drone_props,
                task=task_props,
                event=event,
            )
            if event_type == "task_assigned":
                session.run(
                    """
                    MATCH (d:Drone {id: $drone_id, run_id: $run_id}), (t:Task {id: $task_id, run_id: $run_id})
                    MERGE (d)-[rel:ASSIGNED]->(t)
                    SET rel.time_minute = $time_minute,
                        rel.time = $time,
                        rel.active = true
                    """,
                    run_id=self.run_id,
                    drone_id=drone.drone_id,
                    task_id=task.task_id,
                    time_minute=time_minute,
                    time=format_minute(time_minute),
                )
            elif event_type == "task_picked_up":
                session.run(
                    """
                    MATCH (d:Drone {id: $drone_id, run_id: $run_id}), (t:Task {id: $task_id, run_id: $run_id})
                    MERGE (d)-[rel:CARRYING]->(t)
                    SET rel.active = true,
                        rel.pickup_time_minute = $time_minute,
                        rel.pickup_time = $time
                    """,
                    run_id=self.run_id,
                    drone_id=drone.drone_id,
                    task_id=task.task_id,
                    time_minute=time_minute,
                    time=format_minute(time_minute),
                )
            elif event_type == "task_completed":
                session.run(
                    """
                    MATCH (d:Drone {id: $drone_id, run_id: $run_id}), (t:Task {id: $task_id, run_id: $run_id})
                    MERGE (d)-[rel:COMPLETED]->(t)
                    SET rel.time_minute = $time_minute,
                        rel.time = $time,
                        rel.late = $late
                    """,
                    run_id=self.run_id,
                    drone_id=drone.drone_id,
                    task_id=task.task_id,
                    time_minute=time_minute,
                    time=format_minute(time_minute),
                    late=time_minute > task.deadline_minute,
                )
                session.run(
                    """
                    MATCH (d:Drone {id: $drone_id, run_id: $run_id})-[rel:CARRYING]->(t:Task {id: $task_id, run_id: $run_id})
                    SET rel.active = false,
                        rel.completed_time_minute = $time_minute,
                        rel.completed_time = $time
                    """,
                    run_id=self.run_id,
                    drone_id=drone.drone_id,
                    task_id=task.task_id,
                    time_minute=time_minute,
                    time=format_minute(time_minute),
                )
                session.run(
                    """
                    MATCH (d:Drone {id: $drone_id, run_id: $run_id})-[rel:TAKES_OVER|ASSIGNED]->(t:Task {id: $task_id, run_id: $run_id})
                    SET rel.active = false,
                        rel.completed_time_minute = $time_minute,
                        rel.completed_time = $time
                    """,
                    run_id=self.run_id,
                    drone_id=drone.drone_id,
                    task_id=task.task_id,
                    time_minute=time_minute,
                    time=format_minute(time_minute),
                )

    def _write_drone_event(self, event_type, time_minute, drone):
        event = {
            "id": self._event_id(event_type, time_minute, drone.drone_id),
            "run_id": self.run_id,
            "type": event_type,
            "time_minute": time_minute,
            "time": format_minute(time_minute),
            "region_id": drone.region_id,
            "task_id": None,
            "drone_id": drone.drone_id,
            "status": drone.status,
            "battery_remaining_m": getattr(drone, "battery_remaining_m", None),
            "battery_consumed_m": getattr(drone, "battery_consumed_m", None),
        }
        drone_props = {
            "id": drone.drone_id,
            "run_id": self.run_id,
            "region_id": drone.region_id,
            "status": drone.status,
            "x": drone.x,
            "y": drone.y,
            "queue_len": len(drone.queue),
            "queue_capacity": getattr(drone, "queue_capacity", drone.capacity),
            "cargo_capacity": getattr(drone, "cargo_capacity", None),
            "onboard_count": len(getattr(drone, "onboard_tasks", [])),
            "battery_capacity_m": getattr(drone, "battery_capacity_m", None),
            "battery_remaining_m": getattr(drone, "battery_remaining_m", None),
            "battery_consumed_m": getattr(drone, "battery_consumed_m", None),
            "low_battery_threshold_m": getattr(drone, "low_battery_threshold_m", None),
            "safety_buffer_m": getattr(drone, "safety_buffer_m", None),
            "total_distance_m": drone.total_distance_m,
            "updated_at": time_minute,
            "failure_mode": getattr(drone, "failure_mode", None),
            "takeover_mode": getattr(drone, "takeover_mode", None),
        }
        with self.driver.session() as session:
            session.run(
                """
                MERGE (run:Run {id: $run_id})
                MERGE (d:Drone {id: $drone_id, run_id: $run_id})
                SET d += $drone
                MERGE (e:Event {id: $event_id, run_id: $run_id})
                SET e = $event
                MERGE (e)-[:ABOUT_DRONE]->(d)
                MERGE (d)-[:IN_RUN]->(run)
                MERGE (e)-[:IN_RUN]->(run)
                """,
                run_id=self.run_id,
                drone_id=drone.drone_id,
                event_id=event["id"],
                drone=drone_props,
                event=event,
            )

    def recent_agent_performance(self, region_id, now_minute, window_minutes):
        start = now_minute - window_minutes
        query = """
        MATCH (e:Event)-[:IN_RUN]->(:Run {id: $run_id})
        MATCH (e)-[:IN_REGION]->(:Region {id: $region_id, run_id: $run_id})
        WHERE e.time_minute >= $start AND e.time_minute <= $now
        RETURN e.drone_id AS drone_id,
               sum(CASE WHEN e.type = 'task_assigned' THEN 1 ELSE 0 END) AS assigned,
               sum(CASE WHEN e.type = 'task_completed' THEN 1 ELSE 0 END) AS completed,
               sum(CASE WHEN e.type = 'task_completed' AND coalesce(e.late, false) THEN 1 ELSE 0 END) AS late
        """
        with self.driver.session() as session:
            rows = session.run(
                query,
                run_id=self.run_id,
                region_id=region_id,
                start=start,
                now=now_minute,
            ).data()
        return {
            row["drone_id"]: {
                "assigned": int(row.get("assigned") or 0),
                "completed": int(row.get("completed") or 0),
                "late": int(row.get("late") or 0),
            }
            for row in rows
            if row.get("drone_id")
        }

    def recent_events(self, region_id, now_minute, window_minutes, limit=20):
        start = now_minute - window_minutes
        query = """
        MATCH (e:Event)-[:IN_RUN]->(:Run {id: $run_id})
        MATCH (e)-[:IN_REGION]->(:Region {id: $region_id, run_id: $run_id})
        WHERE e.time_minute >= $start AND e.time_minute <= $now
        RETURN e
        ORDER BY e.time_minute DESC
        LIMIT $limit
        """
        with self.driver.session() as session:
            rows = session.run(
                query,
                run_id=self.run_id,
                region_id=region_id,
                start=start,
                now=now_minute,
                limit=limit,
            ).data()
        return list(reversed([dict(row["e"]) for row in rows if row.get("e") is not None]))

    def close(self):
        self.driver.close()


def build_knowledge_store_from_env(driver_factory=None):
    backend = os.environ.get("KG_BACKEND", "memory").lower()
    if backend not in ("neo4j", "graph"):
        return TemporalKnowledgeStore()
    record_agent_state = os.environ.get("KG_RECORD_AGENT_STATE") == "1"
    return Neo4jKnowledgeStore(
        uri=os.environ.get("NEO4J_URI", "bolt://127.0.0.1:7687"),
        user=os.environ.get("NEO4J_USER", "neo4j"),
        password=os.environ.get("NEO4J_PASSWORD", ""),
        driver_factory=driver_factory,
        run_id=os.environ.get("KG_RUN_ID"),
        record_agent_state=record_agent_state,
        auto_create_indexes=(
            record_agent_state
            and os.environ.get("KG_AUTO_CREATE_INDEXES", "1") == "1"
        ),
        index_wait_timeout_seconds=float(
            os.environ.get("KG_INDEX_WAIT_TIMEOUT_SECONDS", "600")
        ),
    )
