import unittest

from simulation.delivery_sim import DeliveryTask, DroneAgent
from simulation.knowledge import TemporalKnowledgeStore


class TemporalKnowledgeStoreTest(unittest.TestCase):
    def make_task(self, task_id="task-1", deadline=520):
        return DeliveryTask(
            task_id=task_id,
            city="重庆市",
            x=1000,
            y=0,
            pickup_x=0,
            pickup_y=0,
            release_minute=480,
            deadline_minute=deadline,
            aoi_id="aoi-1",
            region_id="10",
        )

    def test_records_assignment_and_completion_events(self):
        kg = TemporalKnowledgeStore()
        task = self.make_task()
        drone = DroneAgent("drone-1", 0, 0, region_id="10")

        kg.record_assignment(481, task, drone)
        kg.record_completion(500, task, drone)

        self.assertEqual(len(kg.events), 2)
        self.assertEqual(kg.events[0]["type"], "task_assigned")
        self.assertEqual(kg.events[1]["type"], "task_completed")

    def test_query_region_context_returns_urgent_tasks_and_candidates(self):
        kg = TemporalKnowledgeStore()
        urgent = self.make_task("urgent", deadline=490)
        relaxed = self.make_task("relaxed", deadline=700)
        drones = [
            DroneAgent("free", 0, 0, region_id="10"),
            DroneAgent("busy", 0, 0, region_id="10"),
        ]
        drones[1].queue.append(relaxed)

        context = kg.query_region_context(
            region_id="10",
            tasks=[relaxed, urgent],
            drones=drones,
            now_minute=480,
            max_tasks=1,
            max_drones=1,
        )

        self.assertEqual(context["urgent_tasks"][0]["task_id"], "urgent")
        self.assertEqual(context["candidate_drones"][0]["drone_id"], "free")

    def test_recent_performance_counts_completed_tasks(self):
        kg = TemporalKnowledgeStore()
        task = self.make_task()
        drone = DroneAgent("drone-1", 0, 0, region_id="10")
        kg.record_completion(500, task, drone)

        context = kg.query_region_context("10", [task], [drone], now_minute=510)

        self.assertEqual(context["recent_agent_performance"]["drone-1"]["completed"], 1)


if __name__ == "__main__":
    unittest.main()
