import tempfile
import unittest
from pathlib import Path

from simulation.delivery_sim import (
    AuctionCoordinator,
    DroneAgent,
    SimulationEnv,
    build_region_depots,
    load_delivery_tasks,
    make_region_drones,
)


class DeliverySimulationTest(unittest.TestCase):
    def test_load_delivery_tasks_filters_jilin_and_limits_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "delivery.csv"
            path.write_text(
                "order_id,region_id,city,courier_id,lng,lat,aoi_id,aoi_type,"
                "accept_time,accept_gps_time,accept_gps_lng,accept_gps_lat,"
                "delivery_time,delivery_gps_time,delivery_gps_lng,delivery_gps_lat,ds\n"
                "1,31,Jilin,4849,126.56526,43.84112,94,14,"
                "09-25 08:08:00,09-25 08:08:00,126.56614,43.87092,"
                "09-25 11:32:00,09-25 11:32:00,126.56919,43.84248,0925\n"
                "2,86,Yantai,3135,121.40538,37.35796,175,14,"
                "09-25 08:30:00,09-25 08:30:00,121.38013,37.41137,"
                "09-25 10:30:00,09-25 10:30:00,121.35896,37.37456,0925\n",
                encoding="utf-8",
            )

            tasks = load_delivery_tasks(path, city="Jilin", day="0925", limit=10)

        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].city, "吉林市")
        self.assertEqual(tasks[0].region_id, "31")
        self.assertEqual(tasks[0].release_minute, 488)
        self.assertGreater(tasks[0].x, 10_000_000)

    def test_region_depots_are_attached_to_tasks(self):
        tasks = load_delivery_tasks_fixture()
        depots = build_region_depots(tasks)

        self.assertEqual(set(depots), {"31"})
        self.assertEqual(tasks[0].pickup_x, depots["31"]["x"])
        self.assertEqual(tasks[0].pickup_y, depots["31"]["y"])

    def test_auction_coordinator_assigns_to_lowest_cost_drone(self):
        task = load_delivery_tasks_fixture()[0]
        near = DroneAgent("drone-a", task.pickup_x - 100, task.pickup_y, capacity=2, region_id=task.region_id)
        far = DroneAgent("drone-b", task.x - 10_000, task.y, capacity=2)
        coordinator = AuctionCoordinator()

        assignments = coordinator.assign([task], [far, near], now_minute=task.release_minute)

        self.assertEqual(assignments[task.task_id], "drone-a")
        self.assertEqual(near.queue[0].task_id, task.task_id)
        self.assertEqual(far.queue, [])

    def test_drone_picks_up_before_delivery(self):
        task = load_delivery_tasks_fixture()[0]
        task.pickup_x = task.x - 100
        task.pickup_y = task.y
        drone = DroneAgent("drone-a", task.pickup_x - 60, task.pickup_y, speed_m_per_minute=60)
        drone.assign(task)

        completed = drone.step(1, task.release_minute + 1)
        self.assertEqual(completed, [])
        self.assertTrue(task.picked_up)
        self.assertEqual(drone.x, task.pickup_x)

        completed = drone.step(2, task.release_minute + 3)
        self.assertEqual(completed[0].task_id, task.task_id)
        self.assertEqual(task.status, "completed")

    def test_make_region_drones_assigns_regions(self):
        tasks = load_delivery_tasks_fixture()
        depots = build_region_depots(tasks)

        drones = make_region_drones(3, depots, seed=7)

        self.assertEqual(len(drones), 3)
        self.assertTrue(all(drone.region_id == "31" for drone in drones))
        self.assertEqual(drones[0].x, depots["31"]["x"])

    def test_simulation_produces_trace_and_metrics(self):
        tasks = load_delivery_tasks_fixture()
        depot = (tasks[0].x - 500, tasks[0].y - 500)
        drones = [
            DroneAgent("drone-1", depot[0], depot[1], capacity=2, speed_m_per_minute=1800),
            DroneAgent("drone-2", depot[0] + 200, depot[1], capacity=2, speed_m_per_minute=1800),
        ]
        env = SimulationEnv(tasks, drones, AuctionCoordinator(), step_minutes=5)

        trace = env.run(max_steps=80)

        self.assertEqual(trace["metrics"]["completed_tasks"], len(tasks))
        self.assertGreater(trace["metrics"]["total_distance_m"], 0)
        self.assertGreater(len(trace["frames"]), 1)
        self.assertIn("drones", trace["frames"][0])
        self.assertIn("task_statuses", trace["frames"][0])
        self.assertEqual(len(trace["tasks"]), len(tasks))

    def test_simulation_supports_sub_minute_steps(self):
        tasks = load_delivery_tasks_fixture()
        depot = (tasks[0].x - 500, tasks[0].y - 500)
        drones = [DroneAgent("drone-1", depot[0], depot[1], capacity=2, speed_m_per_minute=600)]
        env = SimulationEnv(tasks[:1], drones, AuctionCoordinator(), step_minutes=1 / 3)

        trace = env.run(max_steps=120)

        self.assertEqual(trace["step_seconds"], 20)
        self.assertIn(":", trace["frames"][0]["time_label"])
        self.assertGreater(len(trace["frames"]), 1)

    def test_snapshot_marks_completed_tasks(self):
        tasks = load_delivery_tasks_fixture()[:1]
        task = tasks[0]
        task.pickup_x = task.x
        task.pickup_y = task.y
        drone = DroneAgent("drone-1", task.x, task.y, region_id=task.region_id, speed_m_per_minute=600)
        env = SimulationEnv(tasks, [drone], AuctionCoordinator(), step_minutes=1)

        trace = env.run(max_steps=2)

        self.assertEqual(trace["frames"][-1]["task_statuses"][task.task_id], "completed")
        self.assertEqual(trace["frames"][-1]["task_states"][task.task_id]["status"], "completed")
        self.assertEqual(trace["frames"][-1]["task_states"][task.task_id]["assigned_to"], "drone-1")
        self.assertIsNotNone(trace["frames"][-1]["task_states"][task.task_id]["completed_at"])

    def test_snapshot_task_states_only_include_released_tasks(self):
        tasks = load_delivery_tasks_fixture()
        env = SimulationEnv(tasks, [], AuctionCoordinator(), step_minutes=1)

        frame = env.snapshot([])

        self.assertIn(tasks[0].task_id, frame["task_states"])
        self.assertNotIn(tasks[1].task_id, frame["task_states"])


def load_delivery_tasks_fixture():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "delivery.csv"
        path.write_text(
            "order_id,region_id,city,courier_id,lng,lat,aoi_id,aoi_type,"
            "accept_time,accept_gps_time,accept_gps_lng,accept_gps_lat,"
            "delivery_time,delivery_gps_time,delivery_gps_lng,delivery_gps_lat,ds\n"
            "1,31,Jilin,4849,126.56526,43.84112,94,14,"
            "09-25 08:08:00,09-25 08:08:00,126.56614,43.87092,"
            "09-25 11:32:00,09-25 11:32:00,126.56919,43.84248,0925\n"
            "2,31,Jilin,4849,126.56600,43.84200,94,14,"
            "09-25 08:09:00,09-25 08:09:00,126.56620,43.87090,"
            "09-25 11:40:00,09-25 11:40:00,126.57000,43.84300,0925\n",
            encoding="utf-8",
        )
        return load_delivery_tasks(path, city="Jilin", day="0925", limit=10)


if __name__ == "__main__":
    unittest.main()
