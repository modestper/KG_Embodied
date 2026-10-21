import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from simulation.checkpoint import (
    CheckpointError,
    apply_task_states,
    build_checkpoint,
    read_checkpoint,
    task_state_payload,
    validate_checkpoint,
    write_checkpoint,
)
from simulation.delivery_sim import DeliveryTask


def make_args(**overrides):
    values = {
        "run_id": "checkpoint-test",
        "backend": "ray",
        "city": "Hangzhou",
        "day": "0621",
        "task_limit": 10,
        "drone_count": 2,
        "step_seconds": 6,
        "depot_mode": "region",
        "seed": 13,
        "use_llm": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def make_task(task_id="task-1"):
    return DeliveryTask(
        task_id=task_id,
        city="杭州市",
        x=100,
        y=200,
        pickup_x=0,
        pickup_y=0,
        release_minute=480,
        deadline_minute=520,
        aoi_id="aoi-1",
        region_id="31",
    )


class CheckpointTest(unittest.TestCase):
    def test_atomic_checkpoint_round_trip(self):
        payload = build_checkpoint(
            make_args(),
            step_index=12,
            now_minute=481.2,
            state={"drones": [{"drone_id": "drone-1"}]},
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.json"
            write_checkpoint(path, payload)

            loaded = read_checkpoint(path)

        self.assertEqual(loaded, payload)

    def test_checkpoint_detects_tampering(self):
        payload = build_checkpoint(make_args(), 1, 480.1, {"drones": []})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.json"
            write_checkpoint(path, payload)
            document = json.loads(path.read_text(encoding="utf-8"))
            document["payload"]["step_index"] = 2
            path.write_text(json.dumps(document), encoding="utf-8")

            with self.assertRaisesRegex(CheckpointError, "checksum mismatch"):
                read_checkpoint(path)

    def test_validate_rejects_configuration_mismatch(self):
        payload = build_checkpoint(make_args(), 1, 480.1, {"drones": []})

        with self.assertRaisesRegex(CheckpointError, "config mismatch"):
            validate_checkpoint(payload, make_args(seed=99))

    def test_apply_task_states_restores_mutable_fields(self):
        task = make_task()
        task.status = "picked_up"
        task.picked_up = True
        task.assigned_to = "drone-1"
        task.pickup_time = 490
        task.onboard_drone_id = "drone-1"
        task.assistance_reason = "failure"
        task.assistance_source_drone_id = "drone-0"
        task.handover_from_drone_id = "drone-0"
        task.handover_to_drone_id = "drone-1"
        task.handover_x = 20
        task.handover_y = 30
        task.takeover_count = 1
        task.previous_assignees = ["drone-0"]
        state = task_state_payload(task)
        restored = make_task()

        apply_task_states([restored], [state])

        self.assertEqual(task_state_payload(restored), state)


if __name__ == "__main__":
    unittest.main()
