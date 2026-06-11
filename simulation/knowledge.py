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
