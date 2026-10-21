import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  assistanceRoleForDrone,
  assistanceVisuals,
  frameHasActiveAssistance,
} from "../src/dynamic_assistance.mjs";

describe("dynamic assistance visuals", () => {
  it("marks explicit help requests and failed drones as requesters", () => {
    assert.equal(
      assistanceRoleForDrone({ id: "drone-1", takeover_mode: "requesting_help" }),
      "requester",
    );
    assert.equal(
      assistanceRoleForDrone({ id: "drone-1", failure_mode: "motor" }),
      "requester",
    );
    assert.equal(assistanceVisuals.requester.borderColor, "#cf222e");
  });

  it("keeps the source red while its transferred task is unfinished", () => {
    const role = assistanceRoleForDrone(
      { id: "drone-source", takeover_mode: "assisted" },
      {
        "task-1": {
          status: "picked_up",
          assistance_source_drone_id: "drone-source",
          assigned_to: "drone-helper",
        },
      },
    );

    assert.equal(role, "requester");
  });

  it("marks the dispatched drone green until the assistance task completes", () => {
    const drone = { id: "drone-helper" };
    const activeTask = {
      "task-1": {
        status: "assigned",
        assistance_source_drone_id: "drone-source",
        assigned_to: "drone-helper",
      },
    };
    const completedTask = {
      "task-1": { ...activeTask["task-1"], status: "completed" },
    };

    assert.equal(assistanceRoleForDrone(drone, activeTask), "helper");
    assert.equal(assistanceRoleForDrone(drone, completedTask), null);
    assert.equal(assistanceVisuals.helper.borderColor, "#1a7f37");
  });

  it("gives requester state priority if a helper also needs help", () => {
    assert.equal(
      assistanceRoleForDrone({
        id: "drone-2",
        takeover_mode: "assisting",
        failure_mode: "battery",
      }),
      "requester",
    );
  });

  it("detects whether a frame contains active assistance", () => {
    const frame = {
      drones: [{ id: "drone-source" }, { id: "drone-helper" }],
      task_states: {
        "task-1": {
          status: "assigned",
          assistance_source_drone_id: "drone-source",
          assigned_to: "drone-helper",
        },
      },
    };

    assert.equal(frameHasActiveAssistance(frame), true);
    frame.task_states["task-1"].status = "completed";
    assert.equal(frameHasActiveAssistance(frame), false);
  });
});
