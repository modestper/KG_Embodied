import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  getBounds,
  makeProjector,
  prepareCanvas,
  resolveTaskStatus,
  taskColors,
} from "../src/canvas_utils.mjs";

describe("shared canvas behavior", () => {
  it("shows the historical status rather than the final status stored in a trace", () => {
    const task = {
      id: "task-1",
      release: "08:00:00",
      completed_at: "09:00:00",
      status: "completed",
    };
    assert.equal(
      resolveTaskStatus(task, { time_label: "07:59:59" }),
      "unreleased",
    );
    assert.equal(
      resolveTaskStatus(task, { time_label: "08:10:00" }),
      "waiting",
    );
    assert.equal(
      resolveTaskStatus(task, {
        time_label: "08:20:00",
        task_statuses: { "task-1": "picked_up" },
      }),
      "picked_up",
    );
    assert.equal(
      resolveTaskStatus(task, { time_label: "09:00:00" }),
      "completed",
    );
    assert.notEqual(taskColors.waiting, taskColors.picked_up);
  });

  it("distinguishes unreleased tasks in live frames", () => {
    assert.equal(
      resolveTaskStatus({ id: "t", release: 500 }, { time_minute: 499 }),
      "unreleased",
    );
    assert.equal(
      resolveTaskStatus(
        { id: "t", release: 500 },
        { time_minute: 500, task_states: { t: { status: "assigned" } } },
      ),
      "assigned",
    );
  });

  it("fits valid points on narrow canvases while tolerating missing legacy depots", () => {
    const bounds = getBounds([
      undefined,
      { x: NaN, y: 5 },
      { x: 0, y: 0 },
      { x: 20, y: 10 },
    ]);
    const project = makeProjector(bounds, 280, 320);
    assert.deepEqual(project({ x: 10, y: 5 }), { x: 140, y: 160 });
    for (const point of [
      { x: 0, y: 0 },
      { x: 20, y: 10 },
    ]) {
      const pixel = project(point);
      assert.ok(pixel.x > 0 && pixel.x < 280 && pixel.y > 0 && pixel.y < 320);
    }
  });

  it("matches CSS coordinates at different pixel densities without reallocating every frame", () => {
    const previousWindow = globalThis.window;
    globalThis.window = { devicePixelRatio: 2 };
    try {
      let allocations = 0;
      let backingWidth = 0;
      let backingHeight = 0;
      const ctx = { setTransform() {}, setLineDash() {}, clearRect() {} };
      const canvas = {
        getBoundingClientRect: () => ({ width: 280, height: 320 }),
        getContext: () => ctx,
        get width() {
          return backingWidth;
        },
        set width(value) {
          backingWidth = value;
          allocations++;
        },
        get height() {
          return backingHeight;
        },
        set height(value) {
          backingHeight = value;
          allocations++;
        },
      };
      assert.equal(prepareCanvas(canvas).width, 280);
      assert.equal(canvas.width, 560);
      assert.equal(canvas.height, 640);
      prepareCanvas(canvas);
      assert.equal(allocations, 2);
    } finally {
      if (previousWindow === undefined) delete globalThis.window;
      else globalThis.window = previousWindow;
    }
  });
});
