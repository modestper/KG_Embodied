import json
import tempfile
import unittest
from pathlib import Path

from simulation.delivery_sim import (
    AuctionCoordinator,
    DeliveryTask,
    DroneAgent,
    SimulationEnv,
    build_region_depots,
    load_delivery_tasks,
    make_region_drones,
)


class DeliverySimulationTest(unittest.TestCase):
    def make_task(self, task_id="task-1", pickup=(0, 0), dropoff=(100, 0), deadline=520):
        return DeliveryTask(
            task_id=task_id,
            city="吉林市",
            x=dropoff[0],
            y=dropoff[1],
            pickup_x=pickup[0],
            pickup_y=pickup[1],
            release_minute=480,
            deadline_minute=deadline,
            aoi_id="aoi-1",
            region_id="31",
        )

    def test_drone_defaults_include_battery_queue_and_cargo_capacity(self):
        drone = DroneAgent("drone-a", 0, 0)

        self.assertEqual(drone.queue_capacity, 6)
        self.assertEqual(drone.capacity, 6)
        self.assertEqual(drone.cargo_capacity, 3)
        self.assertEqual(drone.battery_capacity_m, 60_000)
        self.assertEqual(drone.battery_remaining_m, 60_000)
        self.assertEqual(drone.low_battery_threshold_m, 12_000)
        self.assertEqual(drone.safety_buffer_m, 5_000)
        self.assertEqual(drone.onboard_tasks, [])
        self.assertEqual(drone.battery_consumed_m, 0)

    def test_capacity_argument_is_queue_capacity_compatibility_alias(self):
        drone = DroneAgent("drone-a", 0, 0, capacity=2)

        self.assertEqual(drone.queue_capacity, 2)
        self.assertEqual(drone.capacity, 2)

    def test_battery_insufficient_for_task_and_return_prevents_assignment(self):
        task = self.make_task(dropoff=(10_000, 0))
        drone = DroneAgent(
            "drone-a",
            0,
            0,
            region_id="31",
            battery_remaining_m=14_000,
        )

        assignments = AuctionCoordinator().assign([task], [drone], now_minute=480)

        self.assertEqual(assignments, {})
        self.assertEqual(task.status, "waiting")
        self.assertEqual(drone.queue, [])

    def test_auction_retries_other_region_when_same_region_drone_lacks_battery(self):
        task = self.make_task(dropoff=(4_000, 0))
        low_battery_same_region = DroneAgent(
            "drone-low",
            0,
            0,
            region_id="31",
            battery_remaining_m=10_000,
        )
        available_other_region = DroneAgent(
            "drone-ok",
            0,
            0,
            region_id="99",
            battery_remaining_m=50_000,
        )

        assignments = AuctionCoordinator().assign(
            [task],
            [low_battery_same_region, available_other_region],
            now_minute=480,
        )

        self.assertEqual(assignments, {task.task_id: "drone-ok"})
        self.assertEqual(task.assigned_to, "drone-ok")
        self.assertEqual(low_battery_same_region.queue, [])
        self.assertEqual(available_other_region.queue[0].task_id, task.task_id)

    def test_low_battery_drone_returns_to_depot_before_accepting_more_work(self):
        drone = DroneAgent(
            "drone-a",
            1000,
            0,
            region_id="31",
            speed_m_per_minute=500,
            battery_remaining_m=11_000,
        )
        drone.home_depot = {"region_id": "31", "x": 0, "y": 0}

        completed = drone.step(1, 481)

        self.assertEqual(completed, [])
        self.assertEqual(drone.status, "returning")
        self.assertEqual(drone.target_depot["x"], 0)
        self.assertLess(drone.x, 1000)

    def test_dynamic_low_battery_threshold_includes_nearest_depot_distance(self):
        drone = DroneAgent(
            "drone-a",
            0,
            0,
            battery_remaining_m=15_000,
            depots=[{"region_id": "near", "x": 10_000, "y": 0}],
        )

        self.assertEqual(drone.return_battery_threshold(), 15_000)
        self.assertTrue(drone.is_low_battery())
        drone.battery_remaining_m = 15_001
        self.assertFalse(drone.is_low_battery())

    def test_assignment_and_return_use_the_same_nearest_depot(self):
        task = self.make_task(dropoff=(10_000, 0))
        home = {"region_id": "home", "x": 0, "y": 0}
        nearest = {"region_id": "near", "x": 10_000, "y": 0}
        drone = DroneAgent(
            "drone-a",
            0,
            0,
            battery_remaining_m=15_000,
            home_depot=home,
            depots=[home, nearest],
        )

        self.assertEqual(drone.required_battery_for(task), 15_000)
        self.assertTrue(drone.can_accept_task(task))
        drone.x = task.x
        drone.y = task.y
        drone.start_returning()
        self.assertEqual(drone.target_depot, nearest)

    def test_returning_prefers_nearest_depot_over_home_depot(self):
        home = {"region_id": "home", "x": 0, "y": 0}
        nearest = {"region_id": "near", "x": 800, "y": 0}
        drone = DroneAgent(
            "drone-a",
            1_000,
            0,
            speed_m_per_minute=100,
            battery_remaining_m=11_000,
            home_depot=home,
            depots=[home, nearest],
        )

        drone.step(1, 481)

        self.assertEqual(drone.status, "returning")
        self.assertEqual(drone.target_depot, nearest)
        self.assertEqual(drone.position, (900.0, 0.0))

    def test_arriving_at_depot_charges_battery_immediately(self):
        drone = DroneAgent(
            "drone-a",
            100,
            0,
            region_id="31",
            speed_m_per_minute=500,
            battery_remaining_m=11_000,
        )
        drone.home_depot = {"region_id": "31", "x": 0, "y": 0}

        drone.step(1, 481)

        self.assertEqual(drone.position, (0, 0))
        self.assertEqual(drone.status, "idle")
        self.assertEqual(drone.battery_remaining_m, 60_000)

    def test_drone_cannot_move_farther_than_remaining_battery(self):
        drone = DroneAgent(
            "drone-a",
            0,
            0,
            speed_m_per_minute=1_000,
            battery_remaining_m=100,
            depots=[{"region_id": "near", "x": 1_000, "y": 0}],
        )

        drone.step(1, 481)

        self.assertEqual(drone.position, (100.0, 0.0))
        self.assertEqual(drone.battery_remaining_m, 0)
        self.assertEqual(drone.status, "returning")

    def test_local_env_attaches_all_charging_depots_to_drones(self):
        home = {"region_id": "31", "x": 0, "y": 0}
        nearest = {"region_id": "99", "x": 500, "y": 0}
        drone = DroneAgent("drone-a", 0, 0, region_id="31", home_depot=home)

        env = SimulationEnv(
            [],
            [drone],
            AuctionCoordinator(),
            charging_depots=[home, nearest],
        )

        self.assertEqual(env.depots, [home, nearest])
        self.assertEqual(drone.depots, [home, nearest])

    def test_cargo_capacity_limits_simultaneous_onboard_tasks_to_three(self):
        tasks = [
            self.make_task(f"task-{index}", pickup=(0, 0), dropoff=(10_000 + index, 0), deadline=520 + index)
            for index in range(4)
        ]
        drone = DroneAgent("drone-a", 0, 0, speed_m_per_minute=1, cargo_capacity=3, queue_capacity=6)
        for task in tasks:
            drone.assign(task)

        drone.step(1, 481)

        self.assertEqual(len(drone.onboard_tasks), 3)
        self.assertEqual([task.status for task in tasks[:3]], ["picked_up", "picked_up", "picked_up"])
        self.assertEqual(tasks[3].status, "assigned")

    def test_failed_drone_unpicked_task_is_reassigned_to_helper(self):
        task = self.make_task(dropoff=(2_000, 0), deadline=520)
        source = DroneAgent("source", 0, 0, region_id="31")
        helper = DroneAgent("helper", 0, 100, region_id="31")
        source.assign(task)
        source.mark_failed("motor")
        env = SimulationEnv([task], [source, helper], AuctionCoordinator(), step_minutes=1)

        env.release_and_assign()

        self.assertEqual(source.queue, [])
        self.assertEqual([item.task_id for item in helper.queue], [task.task_id])
        self.assertEqual(task.assigned_to, helper.drone_id)
        self.assertEqual(task.assistance_reason, "failure")
        self.assertEqual(task.takeover_count, 1)
        self.assertIsNone(task.handover_from_drone_id)
        event_types = [event["type"] for event in env.knowledge_store.events]
        self.assertIn("task_assistance_requested", event_types)
        self.assertIn("assist_dispatched", event_types)
        self.assertIn("task_taken_over", event_types)

    def test_failed_drone_onboard_task_uses_failure_position_as_handover(self):
        task = self.make_task(dropoff=(1_000, 0), deadline=520)
        source = DroneAgent("source", 0, 0, region_id="31")
        helper = DroneAgent(
            "helper", 0, 0, region_id="31", speed_m_per_minute=500
        )
        source.assign(task)
        source.pickup_task(task, 480)
        source.x = 250
        source.mark_failed("navigation")
        env = SimulationEnv([task], [source, helper], AuctionCoordinator(), step_minutes=1)

        env.release_and_assign()

        self.assertFalse(task.picked_up)
        self.assertEqual(task.pickup_position, (250, 0))
        self.assertEqual(task.handover_from_drone_id, source.drone_id)
        self.assertEqual(task.handover_to_drone_id, helper.drone_id)
        self.assertEqual(source.onboard_tasks, [])
        helper.step(1, 481)
        self.assertTrue(task.picked_up)
        self.assertEqual(task.onboard_drone_id, helper.drone_id)
        self.assertEqual(task.handover_completed_at, 481)
        env.record_drone_step_events(helper, 481)
        event_types = [event["type"] for event in env.knowledge_store.events]
        self.assertIn("task_handover_completed", event_types)
        self.assertIn("task_taken_over", event_types)
        helper.step(2, 483)
        self.assertEqual(task.status, "completed")

    def test_low_battery_with_onboard_task_requests_help_then_returns(self):
        task = self.make_task(dropoff=(10_000, 0), deadline=600)
        source = DroneAgent(
            "source",
            0,
            0,
            region_id="31",
        )
        helper = DroneAgent("helper", 0, 100, region_id="31")
        source.assign(task)
        source.pickup_task(task, 480)
        source.battery_remaining_m = 6_000
        env = SimulationEnv([task], [source, helper], AuctionCoordinator(), step_minutes=1)

        env.release_and_assign()

        self.assertEqual(task.assistance_reason, "low_battery")
        self.assertEqual(source.status, "returning")
        self.assertEqual(source.queue, [])
        self.assertEqual(task.assigned_to, helper.drone_id)

    def test_deadline_risk_moves_task_only_to_helper_that_can_meet_deadline(self):
        task = self.make_task(dropoff=(2_000, 0), deadline=490)
        source = DroneAgent(
            "source", 0, 0, region_id="31", speed_m_per_minute=100
        )
        helper = DroneAgent(
            "helper", 0, 100, region_id="31", speed_m_per_minute=1_000
        )
        source.assign(task)
        env = SimulationEnv([task], [source, helper], AuctionCoordinator(), step_minutes=1)

        env.release_and_assign()

        self.assertEqual(task.assistance_reason, "deadline_risk")
        self.assertEqual(task.assigned_to, helper.drone_id)
        self.assertEqual(source.queue, [])

    def test_assistance_without_eligible_helper_keeps_original_ownership(self):
        task = self.make_task(dropoff=(2_000, 0), deadline=520)
        source = DroneAgent("source", 0, 0, region_id="31")
        source.assign(task)
        source.pickup_task(task, 480)
        source.mark_failed("motor")
        env = SimulationEnv([task], [source], AuctionCoordinator(), step_minutes=1)

        env.release_and_assign()

        self.assertEqual(task.assigned_to, source.drone_id)
        self.assertTrue(task.picked_up)
        self.assertEqual(source.queue, [task])
        self.assertEqual(source.onboard_tasks, [task])

    def test_second_helper_keeps_existing_handover_location(self):
        task = self.make_task(dropoff=(2_000, 0), deadline=520)
        source = DroneAgent("source", 300, 0, region_id="31")
        first_helper = DroneAgent("helper-1", 0, 0, region_id="31")
        second_helper = DroneAgent("helper-2", 5_000, 0, region_id="31")
        source.assign(task)
        source.pickup_task(task, 480)
        source.mark_failed("motor")
        env = SimulationEnv(
            [task],
            [source, first_helper, second_helper],
            AuctionCoordinator(),
            step_minutes=1,
        )
        env.release_and_assign()
        self.assertEqual(task.assigned_to, first_helper.drone_id)
        self.assertEqual(task.pickup_position, (300, 0))

        first_helper.mark_failed("navigation")
        env.release_and_assign()

        self.assertEqual(task.assigned_to, second_helper.drone_id)
        self.assertEqual(task.pickup_position, (300, 0))
        self.assertEqual(task.handover_from_drone_id, source.drone_id)
        self.assertEqual(task.handover_to_drone_id, second_helper.drone_id)
        self.assertEqual(task.previous_assignees, ["source", "helper-1"])

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

    def test_region_depot_is_fixed_instead_of_recomputed_from_task_subset(self):
        first = self.make_task(task_id="task-1", dropoff=(100, 200))
        second = self.make_task(task_id="task-2", dropoff=(900, 800))
        catalog = {
            "schema_version": 1,
            "cities": {
                "吉林市": {
                    "regions": {
                        "31": {
                            "region_id": "31",
                            "x": 123.5,
                            "y": 456.5,
                            "task_count": 999,
                        }
                    }
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "depots.json"
            path.write_text(json.dumps(catalog), encoding="utf-8")

            one_task_depot = build_region_depots([first], catalog_path=path)
            two_task_depot = build_region_depots([first, second], catalog_path=path)

        self.assertEqual(one_task_depot, two_task_depot)
        self.assertEqual(first.pickup_position, (123.5, 456.5))
        self.assertEqual(second.pickup_position, (123.5, 456.5))
        self.assertEqual(one_task_depot["31"]["task_count"], 999)

    def test_region_depot_fails_when_fixed_region_is_missing(self):
        task = self.make_task()
        catalog = {"schema_version": 1, "cities": {"吉林市": {"regions": {}}}}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "depots.json"
            path.write_text(json.dumps(catalog), encoding="utf-8")

            with self.assertRaisesRegex(KeyError, "Missing fixed region depots"):
                build_region_depots([task], catalog_path=path)

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
        self.assertEqual(task.status, "picked_up")
        self.assertEqual(task.onboard_drone_id, drone.drone_id)
        self.assertIsNotNone(task.pickup_time)
        self.assertEqual(drone.x, task.pickup_x)

        completed = drone.step(2, task.release_minute + 3)
        self.assertEqual(completed[0].task_id, task.task_id)
        self.assertEqual(task.status, "completed")
        self.assertEqual(drone.onboard_tasks, [])

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
