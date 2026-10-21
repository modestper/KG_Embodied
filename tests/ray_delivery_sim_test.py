import unittest
from unittest.mock import patch

from simulation.delivery_sim import AuctionCoordinator, DeliveryTask, DroneAgent
from simulation.ray_delivery_sim import DroneStateProxy, RaySimulationEnv, drone_to_payload


class FakeRemoteMethod:
    def __init__(self, func):
        self.func = func

    def remote(self, *args, **kwargs):
        return self.func(*args, **kwargs)


class FakeActorHandle:
    def __init__(self, instance):
        self.instance = instance

    def __getattr__(self, name):
        return FakeRemoteMethod(getattr(self.instance, name))


class FakeActorClass:
    def __init__(self, actor_cls, fake_ray):
        self.actor_cls = actor_cls
        self.fake_ray = fake_ray

    def remote(self, *args, **kwargs):
        self.fake_ray.actor_count += 1
        return FakeActorHandle(self.actor_cls(*args, **kwargs))


class FakeRay:
    def __init__(self):
        self.remote_options = []
        self.init_kwargs = []
        self.initialized = False
        self.actor_count = 0
        self.killed = []

    def is_initialized(self):
        return self.initialized

    def init(self, **kwargs):
        self.initialized = True
        self.init_kwargs.append(kwargs)

    def remote(self, **options):
        self.remote_options.append(options)

        def decorate(actor_cls):
            return FakeActorClass(actor_cls, self)

        return decorate

    def get(self, refs):
        return refs

    def kill(self, actor, no_restart=True):
        self.killed.append((actor, no_restart))


class StaleAssignmentCoordinator:
    def assign(self, tasks, drones, now_minute):
        if not tasks:
            return {}
        task = tasks[0]
        task.status = "assigned"
        task.assigned_to = "drone-low"
        return {task.task_id: "drone-low"}


class RayDeliverySimulationTest(unittest.TestCase):
    def test_ray_proxy_uses_nearest_depot_for_battery_checks(self):
        proxy = DroneStateProxy(
            drone_id="drone-1",
            x=0,
            y=0,
            battery_remaining_m=15_000,
            home_depot={"region_id": "home", "x": 20_000, "y": 0},
            depots=[
                {"region_id": "home", "x": 20_000, "y": 0},
                {"region_id": "near", "x": 10_000, "y": 0},
            ],
        )
        task = DeliveryTask(
            task_id="task-1",
            city="重庆市",
            x=10_000,
            y=0,
            pickup_x=0,
            pickup_y=0,
            release_minute=480,
            deadline_minute=520,
            aoi_id="aoi-1",
            region_id="10",
        )

        self.assertEqual(proxy.return_battery_threshold(), 15_000)
        self.assertTrue(proxy.is_low_battery())
        self.assertEqual(proxy.required_battery_for(task), 15_000)
        proxy.battery_remaining_m = 15_001
        self.assertFalse(proxy.is_low_battery())

    def test_ray_env_creates_one_cpu_actor_per_drone_and_completes_delivery(self):
        fake_ray = FakeRay()
        task = DeliveryTask(
            task_id="task-1",
            city="吉林市",
            x=100,
            y=0,
            pickup_x=0,
            pickup_y=0,
            release_minute=480,
            deadline_minute=520,
            aoi_id="aoi-1",
            region_id="31",
        )
        drones = [
            DroneAgent("drone-1", 0, 0, region_id="31", speed_m_per_minute=100),
            DroneAgent("drone-2", 0, 20, region_id="31", speed_m_per_minute=100),
        ]
        env = RaySimulationEnv(
            [task],
            drones,
            AuctionCoordinator(),
            step_minutes=1,
            ray_module=fake_ray,
        )

        trace = env.run(max_steps=3)

        self.assertEqual(fake_ray.remote_options, [{"num_cpus": 1}])
        self.assertEqual(fake_ray.actor_count, 2)
        self.assertEqual(trace["metrics"]["completed_tasks"], 1)
        self.assertEqual(trace["metrics"]["unfinished_tasks"], 0)
        self.assertEqual(trace["tasks"][0]["assigned_to"], "drone-1")
        self.assertEqual(trace["frames"][-1]["task_states"]["task-1"]["status"], "completed")
        self.assertEqual(len(trace["frames"][-1]["drones"]), 2)
        self.assertIn("battery_remaining_m", trace["frames"][-1]["drones"][0])
        self.assertIn("cargo_capacity", trace["frames"][-1]["drones"][0])
        self.assertIn("onboard_count", trace["frames"][-1]["drones"][0])

    def test_ray_reassigns_task_when_preferred_actor_rejects_assignment(self):
        fake_ray = FakeRay()
        task = DeliveryTask(
            task_id="task-1",
            city="杭州市",
            x=4_000,
            y=0,
            pickup_x=0,
            pickup_y=0,
            release_minute=480,
            deadline_minute=520,
            aoi_id="aoi-1",
            region_id="31",
        )
        drones = [
            DroneAgent(
                "drone-low",
                0,
                0,
                region_id="31",
                battery_remaining_m=10_000,
            ),
            DroneAgent(
                "drone-ok",
                0,
                0,
                region_id="99",
                battery_remaining_m=50_000,
            ),
        ]
        env = RaySimulationEnv(
            [task],
            drones,
            StaleAssignmentCoordinator(),
            step_minutes=1,
            ray_module=fake_ray,
        )

        env.release_and_assign()

        self.assertEqual(task.status, "assigned")
        self.assertEqual(task.assigned_to, "drone-ok")
        actor_snapshots = fake_ray.get([actor.snapshot.remote() for actor in env.actors.values()])
        low_snapshot = next(item for item in actor_snapshots if item["drone_id"] == "drone-low")
        ok_snapshot = next(item for item in actor_snapshots if item["drone_id"] == "drone-ok")
        self.assertEqual(low_snapshot["queue"], [])
        self.assertEqual(ok_snapshot["queue"], ["task-1"])

    def test_ray_payload_includes_battery_queue_and_cargo_fields(self):
        task = DeliveryTask(
            task_id="task-1",
            city="吉林市",
            x=100,
            y=0,
            pickup_x=0,
            pickup_y=0,
            release_minute=480,
            deadline_minute=520,
            aoi_id="aoi-1",
            region_id="31",
        )
        drone = DroneAgent(
            "drone-1",
            0,
            0,
            region_id="31",
            capacity=4,
            cargo_capacity=3,
            speed_m_per_minute=1,
            battery_remaining_m=40_000,
        )
        drone.assign(task)
        drone.step(1, 481)

        payload = drone_to_payload(drone)

        self.assertEqual(payload["queue_capacity"], 4)
        self.assertEqual(payload["cargo_capacity"], 3)
        self.assertEqual(payload["battery_capacity_m"], 60_000)
        self.assertLess(payload["battery_remaining_m"], 50_000)
        self.assertEqual(payload["queue"][0]["task_id"], "task-1")
        self.assertEqual(payload["onboard_tasks"][0]["task_id"], "task-1")

    def test_ray_hands_over_onboard_task_from_failed_actor(self):
        fake_ray = FakeRay()
        task = DeliveryTask(
            task_id="task-help",
            city="杭州市",
            x=1_000,
            y=0,
            pickup_x=0,
            pickup_y=0,
            release_minute=480,
            deadline_minute=520,
            aoi_id="aoi-1",
            region_id="31",
        )
        source = DroneAgent("source", 0, 0, region_id="31")
        helper = DroneAgent(
            "helper", 0, 100, region_id="31", speed_m_per_minute=500
        )
        source.assign(task)
        source.pickup_task(task, 480)
        source.x = 250
        source.mark_failed("motor")
        env = RaySimulationEnv(
            [task],
            [source, helper],
            AuctionCoordinator(),
            step_minutes=1,
            ray_module=fake_ray,
        )

        env.release_and_assign()

        self.assertEqual(task.assigned_to, helper.drone_id)
        self.assertFalse(task.picked_up)
        self.assertEqual(task.pickup_position, (250, 0))
        snapshots = fake_ray.get(
            [actor.snapshot.remote() for actor in env.actors.values()]
        )
        source_state = next(item for item in snapshots if item["drone_id"] == "source")
        helper_state = next(item for item in snapshots if item["drone_id"] == "helper")
        self.assertEqual(source_state["queue"], [])
        self.assertEqual(helper_state["queue"], [task.task_id])
        self.assertEqual(helper_state["takeover_mode"], "assisting")
        event_types = [event["type"] for event in env.knowledge_store.events]
        self.assertIn("task_assistance_requested", event_types)
        self.assertIn("assist_dispatched", event_types)
        env.step_once()
        self.assertTrue(task.picked_up)
        self.assertEqual(task.onboard_drone_id, helper.drone_id)
        self.assertIsNotNone(task.handover_completed_at)

    def test_ray_env_restores_complete_checkpoint_state(self):
        fake_ray = FakeRay()
        task = DeliveryTask(
            task_id="task-1",
            city="杭州市",
            x=1_000,
            y=0,
            pickup_x=0,
            pickup_y=0,
            release_minute=480,
            deadline_minute=520,
            aoi_id="aoi-1",
            region_id="31",
        )
        drone = DroneAgent(
            "drone-1", 0, 0, region_id="31", speed_m_per_minute=100
        )
        env = RaySimulationEnv(
            [task], [drone], AuctionCoordinator(), step_minutes=1, ray_module=fake_ray
        )
        env.step_once()
        checkpoint = env.checkpoint_state()
        expected_snapshot = fake_ray.get(
            [actor.snapshot.remote() for actor in env.actors.values()]
        )[0]

        env.step_once()
        env.restore_checkpoint_state(checkpoint)

        restored_snapshot = fake_ray.get(
            [actor.snapshot.remote() for actor in env.actors.values()]
        )[0]
        self.assertEqual(env.now_minute, checkpoint["now_minute"])
        self.assertEqual(restored_snapshot, expected_snapshot)
        self.assertEqual(env.tasks[0].status, checkpoint["task_states"][0]["status"])
        self.assertEqual(fake_ray.actor_count, 2)
        self.assertEqual(len(fake_ray.killed), 1)

    def test_checkpoint_hook_runs_before_knowledge_step_commit(self):
        fake_ray = FakeRay()
        drone = DroneAgent("drone-1", 0, 0, region_id="31")
        env = RaySimulationEnv(
            [], [drone], AuctionCoordinator(), step_minutes=1, ray_module=fake_ray
        )
        order = []
        env.before_step_commit = lambda *_args: order.append("checkpoint")
        original_record_step = env.knowledge_store.record_step

        def record_step(*args, **kwargs):
            order.append("neo4j")
            return original_record_step(*args, **kwargs)

        env.knowledge_store.record_step = record_step

        env.step_once()

        self.assertEqual(order, ["checkpoint", "neo4j"])

    def test_ray_env_reports_clear_error_when_ray_is_missing(self):
        task = DeliveryTask(
            task_id="task-1",
            city="吉林市",
            x=0,
            y=0,
            pickup_x=0,
            pickup_y=0,
            release_minute=480,
            deadline_minute=520,
            aoi_id="aoi-1",
            region_id="31",
        )

        with patch.dict("sys.modules", {"ray": None}):
            with self.assertRaisesRegex(RuntimeError, "pip install ray"):
                RaySimulationEnv([task], [], AuctionCoordinator(), ray_module=None)

    def test_ray_env_close_releases_actor_resources(self):
        fake_ray = FakeRay()
        drones = [
            DroneAgent("drone-1", 0, 0, region_id="31"),
            DroneAgent("drone-2", 0, 20, region_id="31"),
        ]
        env = RaySimulationEnv([], drones, AuctionCoordinator(), ray_module=fake_ray)

        env.close()

        self.assertEqual(len(fake_ray.killed), 2)
        self.assertTrue(all(no_restart for _, no_restart in fake_ray.killed))
        self.assertEqual(env.actors, {})

    def test_ray_env_uses_one_cpu_per_drone_actor(self):
        fake_ray = FakeRay()
        drones = [
            DroneAgent(f"drone-{index}", 0, index, region_id="31")
            for index in range(10)
        ]

        RaySimulationEnv([], drones, AuctionCoordinator(), ray_module=fake_ray, ray_num_cpus=10)

        self.assertEqual(fake_ray.remote_options, [{"num_cpus": 1}])
        self.assertEqual(fake_ray.actor_count, 10)

    def test_ray_env_attaches_all_charging_depots_to_actors(self):
        fake_ray = FakeRay()
        home = {"region_id": "31", "x": 0, "y": 0}
        nearest = {"region_id": "99", "x": 500, "y": 0}
        drone = DroneAgent("drone-1", 0, 0, region_id="31", home_depot=home)

        env = RaySimulationEnv(
            [],
            [drone],
            AuctionCoordinator(),
            ray_module=fake_ray,
            charging_depots=[home, nearest],
        )

        snapshot = fake_ray.get([next(iter(env.actors.values())).snapshot.remote()])[0]
        self.assertEqual(snapshot["depots"], [home, nearest])


if __name__ == "__main__":
    unittest.main()
