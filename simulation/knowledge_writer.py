"""Write routing for the main process, Ray actors, and a standalone subprocess.

Only plain state payloads cross process boundaries. The coordinator's read
connection never executes schema or data writes in either offloaded mode.
"""

import copy
import json
import os
import queue
import subprocess
import sys
import threading
import time
from concurrent.futures import Future
from dataclasses import asdict, dataclass, field
from pathlib import Path
from uuid import uuid4

from simulation.knowledge import Neo4jKnowledgeStore, TemporalKnowledgeStore
from simulation.state_codec import (
    drone_from_payload, drone_to_payload, task_from_payload, task_to_payload,
)


WRITE_MODES = ("main", "ray", "process")


@dataclass(frozen=True)
class KnowledgeWriterConfig:
    backend: str = "neo4j"
    uri: str = "bolt://127.0.0.1:7687"
    user: str = "neo4j"
    password: str = field(default="", repr=False)
    run_id: str = ""
    record_agent_state: bool = False
    auto_create_indexes: bool = True
    index_wait_timeout_seconds: float = 600

    def __post_init__(self):
        if not self.run_id:
            object.__setattr__(self, "run_id", f"run-{uuid4()}")

    @classmethod
    def from_env(cls):
        return cls(
            backend=os.environ.get("KG_BACKEND", "memory").lower(),
            uri=os.environ.get("NEO4J_URI", "bolt://127.0.0.1:7687"),
            user=os.environ.get("NEO4J_USER", "neo4j"),
            password=os.environ.get("NEO4J_PASSWORD", ""),
            run_id=os.environ.get("KG_RUN_ID") or f"run-{uuid4()}",
            record_agent_state=os.environ.get("KG_RECORD_AGENT_STATE") == "1",
            auto_create_indexes=os.environ.get("KG_AUTO_CREATE_INDEXES", "1") == "1",
            index_wait_timeout_seconds=float(os.environ.get("KG_INDEX_WAIT_TIMEOUT_SECONDS", "600")),
        )

    def build_store(self, bootstrap=False):
        if self.backend == "memory":
            store = TemporalKnowledgeStore()
            store.run_id = self.run_id
            return store
        if self.backend not in {"neo4j", "graph"}:
            raise ValueError(f"Unsupported knowledge backend: {self.backend}")
        store = Neo4jKnowledgeStore(
            uri=self.uri, user=self.user, password=self.password, run_id=self.run_id,
            record_agent_state=self.record_agent_state,
            auto_create_indexes=bootstrap and self.auto_create_indexes and self.record_agent_state,
            index_wait_timeout_seconds=self.index_wait_timeout_seconds,
            max_connection_pool_size=2,
        )
        if bootstrap:
            try:
                store.ensure_writer_constraints()
            except Exception:
                store.close()
                raise
        return store


def encode_record(method, *args, **kwargs):
    if method in {"record_assignment", "record_pickup", "record_completion"}:
        now, task, drone = args
        payload = [now, task_to_payload(task), drone_to_payload(drone)]
    elif method == "record_drone_event":
        now, event_type, drone = args
        payload = [now, event_type, drone_to_payload(drone)]
    elif method == "record_assistance_event":
        now, event_type, task, source = args
        helper = kwargs.get("helper_drone")
        payload = [now, event_type, task_to_payload(task), drone_to_payload(source)]
        kwargs = {
            "helper_drone": drone_to_payload(helper) if helper is not None else None,
            "reason": kwargs.get("reason"),
        }
    else:
        raise ValueError(f"Unsupported knowledge record: {method}")
    return copy.deepcopy({"method": method, "args": payload, "kwargs": kwargs})


class KnowledgeWriteExecutor:
    """Created in the write-owning process, never sent as a Ray payload."""

    def __init__(self, config):
        self.store = config.build_store(bootstrap=True)

    def apply(self, command):
        method = command["method"]
        args = list(command.get("args", []))
        kwargs = dict(command.get("kwargs", {}))
        if method in {"record_assignment", "record_pickup", "record_completion"}:
            args[1] = task_from_payload(args[1])
            args[2] = drone_from_payload(args[2])
        elif method == "record_drone_event":
            args[2] = drone_from_payload(args[2])
        elif method == "record_assistance_event":
            args[2] = task_from_payload(args[2])
            args[3] = drone_from_payload(args[3])
            if kwargs.get("helper_drone") is not None:
                kwargs["helper_drone"] = drone_from_payload(kwargs["helper_drone"])
        elif method not in {"record_step_data", "ensure_runtime_state_schema", "publish_runtime_states"}:
            raise ValueError(f"Unsupported knowledge write: {method}")
        getattr(self.store, method)(*args, **kwargs)

    def status(self):
        return {
            "pid": os.getpid(), "run_id": self.store.run_id,
            "events": len(self.store.events), "steps": len(self.store.steps),
        }

    def close(self):
        self.store.close()


class KnowledgeWriterError(RuntimeError):
    pass


class ProcessKnowledgeWriter:
    """Bounded FIFO to a real subprocess; errors and shutdown are acknowledged."""

    def __init__(self, config, queue_size=128, timeout=120):
        if queue_size < 1 or timeout <= 0:
            raise ValueError("knowledge writer queue size and timeout must be positive")
        self.timeout = timeout
        self._queue = queue.Queue(maxsize=queue_size)
        self._lock = threading.Lock()
        self._pending = {}
        self._next_id = 0
        self._failure = None
        self._closed = False
        self._closing = False
        self.process = subprocess.Popen(
            [sys.executable, "-u", "-m", "simulation.knowledge_worker"],
            cwd=Path(__file__).resolve().parents[1],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, encoding="utf-8", bufsize=1,
        )
        self._reader = threading.Thread(target=self._read, daemon=True, name="kg-worker-results")
        self._sender = threading.Thread(target=self._send, daemon=True, name="kg-worker-requests")
        self._reader.start()
        self._sender.start()
        try:
            startup_timeout = max(timeout, config.index_wait_timeout_seconds + 30)
            self.info = self._await(self._request("init", asdict(config)), startup_timeout)
        except Exception:
            self._closed = True
            self._abort()
            raise

    def _check(self):
        if self._failure is not None:
            raise KnowledgeWriterError(str(self._failure)) from self._failure
        if self._closed:
            raise KnowledgeWriterError("knowledge writer is closed")

    def _fail(self, error):
        with self._lock:
            if self._failure is None:
                self._failure = error
            pending = list(self._pending.values())
            self._pending.clear()
        for future in pending:
            if not future.done():
                future.set_exception(error)

    def _request(self, operation, payload=None):
        self._check()
        future = Future()
        with self._lock:
            self._check()
            if self._closing and operation not in {"flush", "close"}:
                raise KnowledgeWriterError("knowledge writer is closing")
            self._next_id += 1
            request_id = self._next_id
            self._pending[request_id] = future
        request = {"id": request_id, "operation": operation, "payload": payload}
        deadline = time.monotonic() + self.timeout
        while True:
            self._check()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                error = KnowledgeWriterError("knowledge writer queue is full")
                self._fail(error)
                raise error
            try:
                self._queue.put(request, timeout=min(0.1, remaining))
                break
            except queue.Full:
                continue
        return future

    def _await(self, future, timeout=None):
        try:
            return future.result(self.timeout if timeout is None else timeout)
        except TimeoutError:
            error = KnowledgeWriterError("knowledge worker acknowledgement timed out")
            self._fail(error)
            raise error from None

    def _send(self):
        try:
            while True:
                request = self._queue.get()
                if request is None:
                    break
                self.process.stdin.write(json.dumps(request, ensure_ascii=False, allow_nan=False) + "\n")
                self.process.stdin.flush()
        except Exception as error:
            self._fail(KnowledgeWriterError(f"knowledge worker input failed: {error}"))

    def _read(self):
        try:
            for line in self.process.stdout:
                response = json.loads(line)
                error = KnowledgeWriterError(response["error"]) if not response["ok"] else None
                with self._lock:
                    future = self._pending.pop(response["id"], None)
                    if error is not None and self._failure is None:
                        self._failure = error
                if error is not None:
                    if future is not None:
                        future.set_exception(error)
                    self._fail(error)
                    return
                if future is not None:
                    future.set_result(response.get("result"))
            with self._lock:
                unacknowledged = bool(self._pending)
            if not self._closed and (not self._closing or unacknowledged):
                self._fail(KnowledgeWriterError("knowledge worker exited before acknowledging requests"))
        except Exception as error:
            self._fail(KnowledgeWriterError(f"knowledge worker response failed: {error}"))

    def submit(self, command):
        self._request("write", command)

    def flush(self):
        self._check()
        with self._lock:
            idle = not self._pending
        if idle:
            return
        self._await(self._request("flush"))
        self._check()

    def status(self):
        return self._await(self._request("status"))

    def _abort(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._queue.put_nowait(None)
        self._sender.join(timeout=5)
        self._reader.join(timeout=5)
        self.process.stdin.close()
        self.process.stdout.close()

    def close(self):
        if self._closed:
            return
        try:
            with self._lock:
                self._closing = True
            self.flush()
            self.final_status = self._await(self._request("close"))
            self._closed = True
            self.process.wait(timeout=self.timeout)
        finally:
            self._closed = True
            self._abort()


class _KnowledgeWriterActor:
    def __init__(self, config):
        self.executor = KnowledgeWriteExecutor(KnowledgeWriterConfig(**config))

    def apply(self, command):
        self.executor.apply(command)

    def status(self):
        return self.executor.status()

    def close(self):
        self.executor.close()


class RayKnowledgeWriter:
    def __init__(self, config, ray_module, max_pending=128):
        if max_pending < 1:
            raise ValueError("max_pending must be positive")
        self.ray = ray_module
        self.actor = self.ray.remote(num_cpus=0)(_KnowledgeWriterActor).remote(asdict(config))
        self.pending = []
        self.max_pending = max_pending
        self._closed = False
        try:
            self.info = self.ray.get(self.actor.status.remote())
        except Exception:
            self.ray.kill(self.actor, no_restart=True)
            raise

    def submit(self, command):
        if self._closed:
            raise KnowledgeWriterError("knowledge writer is closed")
        self.pending.append(self.actor.apply.remote(command))
        if len(self.pending) >= self.max_pending:
            self.flush()

    def flush(self):
        if self.pending:
            self.ray.get(self.pending)
            self.pending.clear()

    def status(self):
        self.flush()
        return self.ray.get(self.actor.status.remote())

    def close(self):
        if self._closed:
            return
        try:
            self.flush()
            self.ray.get(self.actor.close.remote())
        finally:
            self._closed = True
            self.ray.kill(self.actor, no_restart=True)


class DelegatingKnowledgeStore(TemporalKnowledgeStore):
    """Local display history + read connection; every DB write is delegated."""

    def __init__(self, config, mode, writer):
        super().__init__()
        self.config = config
        self.run_id = config.run_id
        self.mode = mode
        self.writer = writer
        self.reader = config.build_store()
        self.actor_config = asdict(config) if mode == "ray" else None
        self._closed = False

    def _record(self, method, *args, **kwargs):
        getattr(TemporalKnowledgeStore, method)(self, *args, **kwargs)
        # In ray mode these records were already persisted by the drone actor.
        if self.mode != "ray":
            self.writer.submit(encode_record(method, *args, **kwargs))

    def record_assignment(self, *args):
        self._record("record_assignment", *args)

    def record_pickup(self, *args):
        self._record("record_pickup", *args)

    def record_completion(self, *args):
        self._record("record_completion", *args)

    def record_drone_event(self, *args):
        self._record("record_drone_event", *args)

    def record_coordinator_drone_event(self, *args):
        if self.mode != "ray":
            return self.record_drone_event(*args)
        TemporalKnowledgeStore.record_drone_event(self, *args)
        self.writer.submit(encode_record("record_drone_event", *args))
        self.flush()

    def record_assistance_event(self, *args, **kwargs):
        TemporalKnowledgeStore.record_assistance_event(self, *args, **kwargs)
        self.writer.submit(encode_record("record_assistance_event", *args, **kwargs))
        if self.mode == "ray":
            # Complete cross-agent changes before the next actor mutation.
            self.flush()

    def record_step(self, step_index, time_minute, tasks, drones, completed_this_step):
        step = TemporalKnowledgeStore.record_step(self, step_index, time_minute, tasks, drones, completed_this_step)
        states = []
        if self.mode != "ray" and self.config.record_agent_state:
            states = [Neo4jKnowledgeStore.agent_state_summary(self, step_index, time_minute, drone) for drone in drones]
        self.writer.submit({
            "method": "record_step_data", "args": [dict(step), states],
            "kwargs": {"external_agent_states": self.mode == "ray" and self.config.record_agent_state},
        })
        return step

    def flush(self):
        self.writer.flush()

    def ensure_runtime_state_schema(self):
        self.writer.submit({"method": "ensure_runtime_state_schema"})
        self.flush()

    def publish_runtime_states(self, context, states):
        self.writer.submit({"method": "publish_runtime_states", "args": [context, states]})

    def query_runtime_states(self, execution_id, expected_versions):
        self.flush()
        return self.reader.query_runtime_states(execution_id, expected_versions)

    def query_region_context(self, *args, **kwargs):
        self.flush()
        return super().query_region_context(*args, **kwargs)

    def recent_agent_performance(self, *args, **kwargs):
        if self.config.backend == "memory":
            return super().recent_agent_performance(*args, **kwargs)
        self.flush()
        return self.reader.recent_agent_performance(*args, **kwargs)

    def recent_events(self, *args, **kwargs):
        if self.config.backend == "memory":
            return super().recent_events(*args, **kwargs)
        self.flush()
        return self.reader.recent_events(*args, **kwargs)

    def latest_step(self):
        self.flush()
        if self.config.backend == "memory":
            return self.steps[-1] if self.steps else None
        return self.reader.latest_step()

    def close(self):
        if self._closed:
            return
        try:
            self.writer.close()
        finally:
            self._closed = True
            self.reader.close()


def build_delegating_store(mode, config=None, ray_module=None):
    config = config or KnowledgeWriterConfig.from_env()
    if mode == "process":
        writer = ProcessKnowledgeWriter(config)
    elif mode == "ray":
        writer = RayKnowledgeWriter(config, ray_module)
    else:
        raise ValueError(f"Unsupported delegated write mode: {mode}")
    try:
        return DelegatingKnowledgeStore(config, mode, writer)
    except Exception:
        writer.close()
        raise
