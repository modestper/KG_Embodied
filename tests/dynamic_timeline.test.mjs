import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { FrameTimeline } from "../src/dynamic_timeline.mjs";

describe("dynamic frame timeline", () => {
  it("follows new frames while live", () => {
    const timeline = new FrameTimeline();

    timeline.append({ frame_index: 0 });
    timeline.append({ frame_index: 1 });

    assert.equal(timeline.current.frame_index, 1);
    assert.equal(timeline.cursor, 1);
    assert.equal(timeline.followingLive, true);
    assert.equal(timeline.pending, 0);
  });

  it("keeps receiving frames while reviewing history", () => {
    const timeline = new FrameTimeline();
    timeline.append({ frame_index: 0 });
    timeline.append({ frame_index: 1 });
    timeline.seek(0);

    const followed = timeline.append({ frame_index: 2 });

    assert.equal(followed, null);
    assert.equal(timeline.current.frame_index, 0);
    assert.equal(timeline.length, 3);
    assert.equal(timeline.pending, 2);
    assert.equal(timeline.followingLive, false);
  });

  it("stops live updates as soon as dragging starts", () => {
    const timeline = new FrameTimeline();
    timeline.append({ frame_index: 0 });
    timeline.append({ frame_index: 1 });
    timeline.hold();

    const followed = timeline.append({ frame_index: 2 });

    assert.equal(followed, null);
    assert.equal(timeline.current.frame_index, 1);
    assert.equal(timeline.pending, 1);
    assert.equal(timeline.followingLive, false);
  });

  it("returns to live mode at the right edge", () => {
    const timeline = new FrameTimeline();
    timeline.append({ frame_index: 0 });
    timeline.append({ frame_index: 5 });
    timeline.seek(0);

    assert.equal(timeline.advance().frame_index, 5);
    assert.equal(timeline.followingLive, true);
    timeline.hold();
    assert.equal(timeline.goLive().frame_index, 5);
    assert.equal(timeline.followingLive, true);
  });

  it("clamps invalid seek positions to the retained range", () => {
    const timeline = new FrameTimeline();
    timeline.append({ frame_index: 10 });
    timeline.append({ frame_index: 20 });

    assert.equal(timeline.seek(-10).frame_index, 10);
    assert.equal(timeline.seek(99).frame_index, 20);
  });
});
