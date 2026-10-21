import unittest

from scripts.bootstrap_headless_checkpoint import restore_graph_state
from simulation.delivery_sim import DeliveryTask, DroneAgent


def task(task_id, status="waiting", assigned_to=None):
    item = DeliveryTask(
        task_id=task_id,
        city="杭州市",
        x=100,
        y=0,
        pickup_x=0,
        pickup_y=0,
        release_minute=480,
        deadline_minute=520,
        aoi_id="aoi-1",
        region_id="31",
    )
    item.status = status
    item.assigned_to = assigned_to
    return item


class BootstrapHeadlessCheckpointTest(unittest.TestCase):
    def test_restores_queue_onboard_and_completed_state(self):
        tasks = [task("waiting"), task("assigned"), task("onboard"), task("done")]
        drones = [DroneAgent("drone-1", 0, 0, region_id="31")]
        graph_state = {
            "time_minute": 500.0,
            "task_properties": [
                {"id": "assigned", "status": "assigned", "assigned_to": "drone-1"},
                {
                    "id": "onboard",
                    "status": "picked_up",
                    "assigned_to": "drone-1",
                    "picked_up": True,
                    "pickup_time": 499.0,
                    "onboard_drone_id": "drone-1",
                },
                {
                    "id": "done",
                    "status": "completed",
                    "assigned_to": "drone-1",
                    "picked_up": True,
                    "pickup_time": 490.0,
                    "completed_at": 495.0,
                },
            ],
            "agent_states": [
                {
                    "drone_id": "drone-1",
                    "x": 20.0,
                    "y": 30.0,
                    "status": "delivering",
                    "region_id": "31",
                    "battery_remaining_m": 40_000.0,
                    "total_distance_m": 10_000.0,
                    "queue_len": 2,
                    "onboard_count": 1,
                }
            ],
            "assignments": [
                {"drone_id": "drone-1", "task_id": "assigned", "time_minute": 480.0},
                {"drone_id": "drone-1", "task_id": "onboard", "time_minute": 481.0},
            ],
            "completions": [
                {"drone_id": "drone-1", "task_id": "done", "time_minute": 495.0}
            ],
        }

        state = restore_graph_state(tasks, drones, graph_state)

        drone = state["drones"][0]
        self.assertEqual([item["task_id"] for item in drone["queue"]], ["assigned", "onboard"])
        self.assertEqual([item["task_id"] for item in drone["onboard_tasks"]], ["onboard"])
        self.assertEqual(drone["completed"], ["done"])
        self.assertEqual(drone["battery_remaining_m"], 40_000.0)
        self.assertEqual(state["now_minute"], 500.0)


if __name__ == "__main__":
    unittest.main()
