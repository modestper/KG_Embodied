"""Cooperative pause/resume at simulation step boundaries."""

import threading
import uuid


class SimulationControlError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class SimulationControl:
    TERMINAL = {"finished", "stopped", "superseded"}

    def __init__(self):
        self.run_id = uuid.uuid4().hex
        self._condition = threading.Condition()
        self._state = "running"
        self._in_step = False
        self._step_index = 0
        self._revision = 0
        self._published_revision = -1

    def _snapshot(self):
        return {"run_id": self.run_id, "state": self._state,
                "step_index": self._step_index, "revision": self._revision}

    def snapshot(self):
        with self._condition:
            return self._snapshot()

    def _set_state(self, state):
        if state != self._state:
            self._state = state
            self._revision += 1
        self._condition.notify_all()

    def request(self, action):
        if action not in ("pause", "resume"):
            raise SimulationControlError("操作必须是 pause 或 resume。")
        with self._condition:
            if self._state in self.TERMINAL:
                raise SimulationControlError("本次仿真已结束或已被替换。", 409)
            self._set_state(("pausing" if self._in_step else "paused") if action == "pause" else "running")
            return self._snapshot()

    def next_step(self):
        """Return (permission, update). None permission means send an SSE update and retry."""
        with self._condition:
            if self._state in self.TERMINAL:
                return False, None
            if self._published_revision != self._revision:
                self._published_revision = self._revision
                return None, self._snapshot()
            if self._state == "running":
                self._in_step = True
                return True, None
            # Periodic updates also detect a disconnected client while paused.
            self._condition.wait(timeout=1)
            self._published_revision = self._revision
            return None, self._snapshot()

    def step_finished(self, step_index):
        with self._condition:
            self._in_step = False
            self._step_index = step_index
            if self._state == "pausing":
                self._set_state("paused")

    def wait_interval(self, seconds):
        with self._condition:
            self._condition.wait_for(lambda: self._state != "running", timeout=seconds)

    def finish(self, state="finished"):
        with self._condition:
            if self._state not in self.TERMINAL:
                self._set_state(state)
