import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import Mock

from scripts.run_dynamic_sim import DynamicHandler, stream_simulation
from scripts.simulation_control import SimulationControl, SimulationControlError
from tests.trace_storage_test import make_env


class SimulationControlTest(unittest.TestCase):
    def test_pause_waits_for_inflight_step_and_resume_wakes_boundary(self):
        control = SimulationControl()
        self.assertIsNone(control.next_step()[0])
        self.assertTrue(control.next_step()[0])
        self.assertEqual(control.request("pause")["state"], "pausing")
        control.step_finished(1)
        self.assertEqual(control.next_step()[1]["state"], "paused")
        ready = threading.Event()

        def wait_for_step():
            while True:
                allowed, _ = control.next_step()
                if allowed is not None:
                    if allowed:
                        ready.set()
                    return

        thread = threading.Thread(target=wait_for_step, daemon=True)
        thread.start()
        try:
            self.assertFalse(ready.wait(0.05), "A paused simulation must not start another step")
            self.assertEqual(control.snapshot()["step_index"], 1)
            control.request("resume")
            self.assertTrue(ready.wait(1))
        finally:
            control.finish("stopped")
            thread.join(timeout=2)

    def test_stream_stops_advancing_then_finishes_without_losing_steps(self):
        env = make_env()
        original_step = env.step_once
        entered = threading.Event()
        release = threading.Event()
        paused = threading.Event()
        events = []
        errors = []
        control = SimulationControl()

        def step():
            if not env.frames:
                entered.set()
                if not release.wait(2):
                    raise RuntimeError("test did not release step")
            return original_step()

        env.step_once = step

        def consume():
            try:
                for event in stream_simulation(env, {"city": "吉林市"}, [], 4, 1, control=control):
                    events.append(event)
                    if event.startswith("event: simulation_state\n") and '"state":"paused"' in event:
                        paused.set()
            except Exception as error:
                errors.append(error)

        thread = threading.Thread(target=consume, daemon=True)
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertEqual(control.request("pause")["state"], "pausing")
            release.set()
            self.assertTrue(paused.wait(1))
            self.assertEqual(len(env.frames), 1)
            self.assertEqual(len(env.knowledge_store.steps), 1)
            self.assertTrue(thread.is_alive())
            control.request("resume")
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            self.assertFalse(errors)
            self.assertEqual(len(env.frames), 4)
            self.assertEqual([step["step_index"] for step in env.knowledge_store.steps], [1, 2, 3, 4])
            done = json.loads(events[-1].split("data: ", 1)[1])
            self.assertEqual(done["steps_run"], 4)
            self.assertEqual(control.snapshot()["state"], "finished")
            with self.assertRaises(SimulationControlError):
                control.request("resume")
        finally:
            release.set()
            control.finish("stopped")
            thread.join(timeout=2)

    def test_replacing_paused_run_wakes_old_stream_and_rejects_stale_controls(self):
        class Handler(DynamicHandler):
            active_env = None
            active_control = None

        old = SimulationControl()
        current = SimulationControl()
        old.request("pause")
        Handler.replace_active_env(Mock(), old)
        handler = object.__new__(Handler)
        Handler.replace_active_env(Mock(), current)
        try:
            self.assertFalse(old.next_step()[0])
            with self.assertRaises(SimulationControlError) as error:
                handler.control_simulation({"run_id": old.run_id, "action": "pause"})
            self.assertEqual(error.exception.status, 409)
            self.assertEqual(current.snapshot()["state"], "running")
        finally:
            Handler.clear_active_env(Handler.active_env)

    def test_control_http_validates_run_action_and_origin(self):
        control = SimulationControl()

        class Handler(DynamicHandler):
            active_control = control
            active_env = object()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()

        def request(payload, origin=None):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            headers = {"Content-Type": "application/json"}
            if origin:
                headers["Origin"] = origin
            try:
                connection.request("POST", "/api/simulation/control", json.dumps(payload), headers)
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()

        payload = {"run_id": control.run_id, "action": "pause"}
        try:
            self.assertEqual(request(payload, "https://foreign.example")[0], 403)
            self.assertEqual(control.snapshot()["state"], "running")
            self.assertEqual(request({**payload, "run_id": "previous-run"})[0], 409)
            self.assertEqual(request({**payload, "action": "reset"})[0], 400)
            self.assertEqual(request([])[0], 400)
            status, result = request(payload)
            self.assertEqual(status, 200)
            self.assertEqual(result["simulation"]["state"], "paused")
            self.assertEqual(request({**payload, "action": "resume"})[1]["simulation"]["state"], "running")
        finally:
            control.finish()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
