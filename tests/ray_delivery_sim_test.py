import unittest
from unittest.mock import patch

from simulation.delivery_sim import AuctionCoordinator, DeliveryTask, DroneAgent
from simulation.ray_delivery_sim import RaySimulationEnv


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


class RayDeliverySimulationTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
