import os
from uuid import uuid4

from simulation.delivery_sim import euclidean, format_minute


class TemporalKnowledgeStore:
    def __init__(self):
        self.events = []

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
            }
        )

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
    def __init__(self, driver=None, uri=None, user=None, password=None, driver_factory=None, run_id=None):
        super().__init__()
        self.run_id = run_id or f"run-{uuid4()}"
        if driver is not None:
            self.driver = driver
            return
        if driver_factory is None:
            try:
                from neo4j import GraphDatabase
            except ImportError as exc:
                raise RuntimeError(
                    "Neo4j KG backend requires the neo4j Python package. "
                    "Install it with `pip install neo4j` or set KG_BACKEND=memory."
                ) from exc
            driver_factory = GraphDatabase.driver
        self.driver = driver_factory(uri, auth=(user, password))

    def record_assignment(self, time_minute, task, drone):
        super().record_assignment(time_minute, task, drone)
        self._write_event(
            event_type="task_assigned",
            time_minute=time_minute,
            task=task,
            drone=drone,
            properties={"queue_len": len(drone.queue)},
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

    def _write_event(self, event_type, time_minute, task, drone, properties=None):
        properties = properties or {}
        event = {
            "id": f"{event_type}-{uuid4()}",
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
            "assigned_to": task.assigned_to,
            "completed_at": task.completed_at,
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
            "speed_m_per_minute": drone.speed_m_per_minute,
            "battery_range_m": drone.battery_range_m,
            "status": drone.status,
            "x": drone.x,
            "y": drone.y,
            "queue_len": len(drone.queue),
            "completed_count": len(drone.completed),
            "total_distance_m": drone.total_distance_m,
            "updated_at": time_minute,
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
        with self.driver.session() as session:
            session.run(
                """
                MERGE (run:Run {id: $run_id})
                SET run += $run
                MERGE (r:Region {id: $region_id, run_id: $run_id})
                SET r += $region
                MERGE (d:Drone {id: $drone_id, run_id: $run_id})
                SET d += $drone
                MERGE (t:Task {id: $task_id, run_id: $run_id})
                SET t += $task
                MERGE (d)-[:HOME_REGION]->(r)
                MERGE (d)-[:CURRENTLY_IN]->(r)
                MERGE (t)-[:BELONGS_TO]->(r)
                MERGE (e:Event {id: $event_id})
                SET e = $event
                CREATE (e)-[:ABOUT_DRONE]->(d)
                CREATE (e)-[:ABOUT_TASK]->(t)
                CREATE (e)-[:IN_REGION]->(r)
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
                        rel.time = $time
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
    return Neo4jKnowledgeStore(
        uri=os.environ.get("NEO4J_URI", "bolt://127.0.0.1:7687"),
        user=os.environ.get("NEO4J_USER", "neo4j"),
        password=os.environ.get("NEO4J_PASSWORD", ""),
        driver_factory=driver_factory,
        run_id=os.environ.get("KG_RUN_ID"),
    )
