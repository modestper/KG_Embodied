import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  buildCityOptions,
  buildFilteredSummary,
  formatCount,
  getHourlySeries,
} from "../src/data.mjs";

const fixture = {
  totals: {
    delivery: 12,
    pickup: 8,
    cities: 2,
    couriers: 6,
  },
  cities: [
    {
      name: "上海市",
      delivery: 7,
      pickup: 2,
      couriers: 3,
      aoi: 4,
      avg_delivery_minutes: 42,
      avg_pickup_minutes: 18,
    },
    {
      name: "杭州市",
      delivery: 5,
      pickup: 6,
      couriers: 4,
      aoi: 6,
      avg_delivery_minutes: 35,
      avg_pickup_minutes: 21,
    },
  ],
  hourly: {
    delivery: [
      { hour: 8, count: 3 },
      { hour: 9, count: 4 },
    ],
    pickup: [
      { hour: 8, count: 2 },
      { hour: 9, count: 6 },
    ],
  },
  samples: {
    delivery: [
      { city: "上海市", lng: 1, lat: 2 },
      { city: "杭州市", lng: 3, lat: 4 },
    ],
    pickup: [{ city: "杭州市", lng: 5, lat: 6 }],
  },
};

describe("viewer data helpers", () => {
  it("builds stable city options with an all-cities entry", () => {
    assert.deepEqual(buildCityOptions(fixture), ["全部城市", "上海市", "杭州市"]);
  });

  it("summarizes all data for the selected task type", () => {
    const summary = buildFilteredSummary(fixture, {
      city: "全部城市",
      taskType: "delivery",
    });

    assert.equal(summary.totalTasks, 12);
    assert.equal(summary.totalCouriers, 6);
    assert.equal(summary.visibleSamples.length, 2);
    assert.equal(summary.cityRows[0].name, "上海市");
  });

  it("filters cities and samples together", () => {
    const summary = buildFilteredSummary(fixture, {
      city: "杭州市",
      taskType: "pickup",
    });

    assert.equal(summary.totalTasks, 6);
    assert.equal(summary.totalCouriers, 4);
    assert.deepEqual(summary.visibleSamples, [{ city: "杭州市", lng: 5, lat: 6 }]);
  });

  it("fills missing hourly buckets with zeroes", () => {
    const series = getHourlySeries(fixture.hourly.delivery);
    assert.equal(series.length, 24);
    assert.deepEqual(series.slice(8, 10), [
      { hour: 8, count: 3 },
      { hour: 9, count: 4 },
    ]);
    assert.equal(series[7].count, 0);
  });

  it("formats large counts for dashboard labels", () => {
    assert.equal(formatCount(999), "999");
    assert.equal(formatCount(1200), "1.2k");
    assert.equal(formatCount(1_450_000), "1.5M");
  });
});
