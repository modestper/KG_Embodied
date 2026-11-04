import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

from scripts import run_dynamic_sim
from scripts.build_trace_index import build_index
from scripts.run_dynamic_sim import DynamicHandler, stream_simulation
from scripts.trace_storage import TraceStorage, TraceStorageError
from simulation.delivery_sim import AuctionCoordinator, DeliveryTask, DroneAgent, SimulationEnv
from simulation.knowledge import TemporalKnowledgeStore


def make_env():
    task = DeliveryTask(task_id="task-1", city="吉林市", x=100, y=0,
                        pickup_x=0, pickup_y=0, release_minute=480,
                        deadline_minute=520, aoi_id="aoi-1", region_id="31")
    drone = DroneAgent("drone-1", 0, 0, region_id="31", speed_m_per_minute=1)
    return SimulationEnv([task], [drone], AuctionCoordinator(), step_minutes=1,
                         knowledge_store=TemporalKnowledgeStore())


class TraceStorageTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data = Path(self.temporary.name)
        self.storage = TraceStorage(self.data)

    def finish(self):
        env = make_env()
        events = list(stream_simulation(
            env, {"city": "吉林市", "day": "0925", "frame_stride": 3}, [], 8, 3,
            on_complete=lambda summary, regions: self.storage.retain(
                env, {"city": "吉林市", "day": "0925", "frame_stride": 3}, [], summary, regions),
        ))
        done = json.loads(events[-1].split("data: ", 1)[1])
        return env, events, done["trace"]

    def test_manual_save_keeps_all_steps_despite_sampled_stream_and_rebuilds_index(self):
        env, events, descriptor = self.finish()
        self.assertFalse(list(self.data.iterdir()), "Completing a run must not write a file")
        self.assertEqual(sum(event.startswith("event: frame\n") for event in events), 3)
        self.assertEqual(descriptor["frame_count"], 8)
        old = {"file": "existing.json", "label": "已有轨迹"}
        (self.data / "traces.json").write_text(json.dumps({"traces": [old]}))
        result = self.storage.save({"run_id": descriptor["run_id"], "directory": "重庆实验/第一组",
                                    "filename": "本次运行.json"})
        saved = json.loads((self.data / result["file"]).read_text())
        self.assertEqual(saved["frames"][-1]["time_minute"], env.frames[-1]["time_minute"])
        self.assertEqual([frame["step_index"] for frame in saved["frames"]], list(range(1, 9)))
        self.assertEqual(saved["frame_stride"], 1)
        self.assertEqual(saved["config"]["frame_stride"], 3)
        self.assertEqual(saved["steps_run"], 8)
        self.assertEqual(saved["reason"], "max_steps_reached")
        self.assertEqual(saved["metrics"]["unfinished_tasks"], 1)
        self.assertEqual(json.loads((self.data / "traces.json").read_text())["traces"][1], old)
        self.assertEqual(build_index(self.data)["traces"][0]["file"], result["file"])
        self.assertIn("trace=", result["replay_url"])
        self.assertEqual(self.storage.directories()["directories"][0]["name"], "重庆实验")
        self.assertEqual(self.storage.directories("重庆实验/第一组")["parent"], "重庆实验")

    def test_does_not_overwrite_and_rejects_expired_run(self):
        _, _, descriptor = self.finish()
        request = {"run_id": descriptor["run_id"], "filename": "trace.json"}
        self.storage.save(request)
        original = (self.data / "traces/trace.json").read_bytes()
        with self.assertRaises(TraceStorageError) as error:
            self.storage.save(request)
        self.assertEqual(error.exception.status, 409)
        self.assertEqual((self.data / "traces/trace.json").read_bytes(), original)
        self.finish()
        with self.assertRaises(TraceStorageError) as error:
            self.storage.save({**request, "filename": "expired.json"})
        self.assertEqual(error.exception.status, 410)
        self.assertFalse((self.data / "traces/expired.json").exists())

    def test_rejects_traversal_absolute_paths_invalid_names_and_symlinks(self):
        _, _, descriptor = self.finish()
        request = {"run_id": descriptor["run_id"], "filename": "trace.json"}
        for directory in ("../outside", "/tmp", "a/../b", "a\\b", "a//b"):
            with self.subTest(directory=directory), self.assertRaises(TraceStorageError):
                self.storage.save({**request, "directory": directory})
        for filename in ("../trace.json", "/tmp/trace.json", "trace.txt", ".json", "a\n.json"):
            with self.subTest(filename=filename), self.assertRaises(TraceStorageError):
                self.storage.save({**request, "filename": filename})
        self.storage.root.mkdir()
        with tempfile.TemporaryDirectory() as outside:
            (self.storage.root / "link").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(TraceStorageError):
                self.storage.save({**request, "directory": "link"})
            self.assertFalse(list(Path(outside).iterdir()))

    def test_failed_index_update_rolls_back_new_file_and_leaves_existing_index(self):
        _, _, descriptor = self.finish()
        index = '{"traces":[{"file":"old.json"}]}'
        (self.data / "traces.json").write_text(index)
        with patch("scripts.trace_storage.write_index", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.storage.save({"run_id": descriptor["run_id"], "filename": "failed.json"})
        self.assertEqual((self.data / "traces.json").read_text(), index)
        self.assertFalse(list(self.storage.root.iterdir()))

    def test_rebuild_keeps_nested_file_named_traces_json(self):
        _, _, descriptor = self.finish()
        result = self.storage.save({"run_id": descriptor["run_id"], "directory": "experiment",
                                    "filename": "traces.json"})
        self.assertEqual(build_index(self.data)["traces"][0]["file"], result["file"])

    def test_concurrent_saves_of_same_name_create_one_trace_and_index_entry(self):
        _, _, descriptor = self.finish()
        outcomes = []

        def save():
            try:
                self.storage.save({"run_id": descriptor["run_id"], "filename": "same.json"})
                outcomes.append(200)
            except TraceStorageError as error:
                outcomes.append(error.status)

        threads = [threading.Thread(target=save) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual(sorted(outcomes), [200, 409])
        self.assertEqual(len(json.loads((self.data / "traces.json").read_text())["traces"]), 1)

    def test_http_completed_run_can_save_after_environment_cleanup(self):
        storage = self.storage

        class Handler(DynamicHandler):
            trace_storage = storage
            active_env = None

            def log_message(self, *args):
                pass

        with patch("sys.argv", ["run_dynamic_sim.py", "--backend", "local", "--max-steps", "8",
                                "--frame-stride", "3", "--wall-interval", "0"]):
            Handler.runtime_args = run_dynamic_sim.parse_args()
        env = make_env()
        env.knowledge_store.close = Mock()
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()

        def request(method, path, payload=None, headers=None):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                connection.request(method, path, json.dumps(payload) if payload is not None else None,
                                   headers or {"Content-Type": "application/json"})
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()

        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            with patch("scripts.run_dynamic_sim.build_runtime", return_value=(env, {"city": "吉林市"}, [])):
                connection.request("GET", "/events")
                response = connection.getresponse()
                while True:
                    line = response.readline().decode()
                    if line == "event: done\n":
                        done = json.loads(response.readline().decode().removeprefix("data: "))
                        break
                    self.assertTrue(line, "SSE ended without done")
                response.close()
                connection.close()
            # Acquiring the lock ensures any final cleanup has progressed; save uses retained data.
            status, _ = request("GET", "/api/traces/directories")
            self.assertEqual(status, 200)
            payload = {"run_id": done["trace"]["run_id"], "directory": "http-test", "filename": "trace.json"}
            self.assertEqual(request("POST", "/api/traces/save", payload,
                                     {"Content-Type": "application/json", "Origin": "https://foreign.example"})[0], 403)
            status, result = request("POST", "/api/traces/save", payload)
            self.assertEqual(status, 200)
            self.assertEqual(result["frame_count"], 8)
            self.assertIsNone(Handler.active_env)
            env.knowledge_store.close.assert_called_once()
            self.assertEqual(request("GET", "/api/traces/directories?path=../outside")[0], 400)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
