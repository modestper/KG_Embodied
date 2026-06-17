import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { buildDynamicRegions } from "../src/dynamic_regions.mjs";

describe("dynamic region helpers", () => {
  it("builds drawable fallback regions from dynamic tasks", () => {
    const regions = buildDynamicRegions([
      { id: "1", region_id: "31", x: 0, y: 0 },
      { id: "2", region_id: "31", x: 100, y: 0 },
      { id: "3", region_id: "31", x: 50, y: 80 },
      { id: "4", region_id: "88", x: 500, y: 500 },
    ]);

    assert.equal(regions.length, 2);
    assert.equal(regions[0].region_id, "31");
    assert.equal(regions[0].task_count, 3);
    assert.ok(regions[0].outline.length >= 3);
    assert.equal(regions[1].region_id, "88");
    assert.equal(regions[1].outline.length, 12);
  });
});
