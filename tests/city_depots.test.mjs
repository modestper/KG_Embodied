import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";
import { buildCityDepots } from "../src/city_depots.mjs";

const catalog = JSON.parse(
  readFileSync(new URL("../data/fixed_region_depots.json", import.meta.url)),
);

describe("city depots for the selected run", () => {
  it("shows every city depot as inactive for a run with no tasks, regardless of historical counts", () => {
    for (const [city, count] of [
      ["Shanghai", 52],
      ["Jilin", 4],
      ["Hangzhou", 51],
      ["Yantai", 17],
      ["Chongqing", 48],
    ]) {
      const depots = buildCityDepots(catalog, city);
      assert.equal(depots.length, count);
      assert.ok(depots.every((depot) => depot.hasTasks === false));
      assert.ok(depots.some((depot) => depot.task_count > 0));
    }
  });

  it("uses all run tasks, normalizes region ids, and preserves recorded depot coordinates", () => {
    const source = catalog.cities["吉林市"];
    const regionId = Object.keys(source.regions)[0];
    const recorded = { region_id: Number(regionId), x: 123, y: 456 };
    const depots = buildCityDepots(
      catalog,
      "吉林市",
      [recorded],
      [{ region_id: regionId, status: "completed" }],
    );
    assert.equal(depots.length, 4);
    assert.deepEqual(
      depots.find((depot) => depot.hasTasks),
      { ...recorded, hasTasks: true },
    );
    assert.equal(depots.filter((depot) => !depot.hasTasks).length, 3);
    assert.notEqual(source.regions[regionId].x, 123);
  });

  it("supplements missing depots in a real historical trace without moving recorded depots", () => {
    const trace = JSON.parse(
      readFileSync(
        new URL(
          "../data/delivery_sim_jilin_0925_5tasks_2agents.json",
          import.meta.url,
        ),
      ),
    );
    const activeIds = new Set(
      trace.tasks.map((task) => String(task.region_id)),
    );
    const depots = buildCityDepots(
      catalog,
      trace.config.city,
      trace.depots,
      trace.tasks,
    );
    assert.equal(depots.length, 4);
    assert.equal(
      depots.filter((depot) => depot.hasTasks).length,
      activeIds.size,
    );
    for (const recorded of trace.depots) {
      const depot = depots.find(
        (entry) => String(entry.region_id) === String(recorded.region_id),
      );
      assert.equal(depot.x, recorded.x);
      assert.equal(depot.y, recorded.y);
    }
  });

  it("keeps runtime depots when the catalog is unavailable and handles region zero and central depots", () => {
    const depots = [
      { region_id: 0, x: 1, y: 2 },
      { region_id: "2", x: 3, y: 4 },
      { region_id: null, x: 5, y: 6 },
    ];
    assert.deepEqual(
      buildCityDepots(null, "Unknown", depots, [{ region_id: "0" }]).map(
        (depot) => depot.hasTasks,
      ),
      [true, false, true],
    );
    assert.ok(
      buildCityDepots(null, "Unknown", depots).every(
        (depot) => !depot.hasTasks,
      ),
    );
  });
});
