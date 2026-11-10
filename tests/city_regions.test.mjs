import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";
import {
  buildCityRegionMap,
  loadRegionBoundaries,
} from "../src/city_regions.mjs";

const boundaries = JSON.parse(
  readFileSync(new URL("../data/region_boundaries.json", import.meta.url)),
);

describe("fixed city region maps", () => {
  it("uses every stored city polygon and the same bounds for English and Chinese city names", () => {
    for (const [city, count] of [
      ["Chongqing", 48],
      ["Hangzhou", 51],
      ["Jilin", 4],
      ["Shanghai", 52],
      ["Yantai", 17],
    ]) {
      const source = boundaries.cities[city];
      const map = buildCityRegionMap(boundaries, city);
      assert.equal(map.regions.length, count);
      assert.deepEqual(map, buildCityRegionMap(boundaries, source.city));
      assert.deepEqual(
        map.regions.map((region) => region.outline),
        source.regions.map((region) => region.polygon),
      );
      for (const region of map.regions) {
        for (const [x, y] of region.outline) {
          assert.ok(x >= map.bounds.minX && x <= map.bounds.maxX);
          assert.ok(y >= map.bounds.minY && y <= map.bounds.maxY);
        }
      }
    }
  });

  it("keeps city-qualified region ids separate and deduplicates bilingual city entries", () => {
    const map = buildCityRegionMap(boundaries, "全部城市");
    assert.equal(map.regions.length, 172);
    assert.equal(
      new Set(map.regions.map((r) => `${r.city}/${r.region_id}`)).size,
      172,
    );
  });

  it("does not invent task-based polygons when a city has no fixed boundary", () => {
    for (const city of [null, "Unknown"]) {
      assert.deepEqual(buildCityRegionMap(boundaries, city), {
        regions: [],
        bounds: null,
      });
    }
    assert.deepEqual(buildCityRegionMap(null, "Jilin"), {
      regions: [],
      bounds: null,
    });
  });

  it("retries a failed boundary fetch and shares a successful request", async () => {
    const originalFetch = globalThis.fetch;
    let calls = 0;
    globalThis.fetch = async () => {
      calls += 1;
      if (calls === 1) return { ok: false, status: 503 };
      return { ok: true, json: async () => boundaries };
    };
    try {
      await assert.rejects(loadRegionBoundaries(), /503/);
      const first = loadRegionBoundaries();
      const second = loadRegionBoundaries();
      assert.equal(first, second);
      assert.equal(await first, boundaries);
      assert.equal(await loadRegionBoundaries(), boundaries);
      assert.equal(calls, 2);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
