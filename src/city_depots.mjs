const CITY_NAMES = {
  Shanghai: "上海市",
  Hangzhou: "杭州市",
  Chongqing: "重庆市",
  Jilin: "吉林市",
  Yantai: "烟台市",
};

let catalogRequest;

export function loadDepotCatalog() {
  if (!catalogRequest) {
    catalogRequest = fetch(
      new URL("../data/fixed_region_depots.json", import.meta.url),
      { cache: "no-cache" },
    )
      .then(async (response) => {
        if (!response.ok)
          throw new Error(`配送站加载失败：HTTP ${response.status}`);
        const catalog = await response.json();
        if (!catalog?.cities) throw new Error("配送站目录缺少城市数据");
        return catalog;
      })
      .catch((error) => {
        catalogRequest = null;
        throw error;
      });
  }
  return catalogRequest;
}

export function buildCityDepots(catalog, city, runtimeDepots = [], tasks = []) {
  const configured = catalog?.cities?.[CITY_NAMES[city] ?? city]?.regions ?? {};
  const depots = new Map();
  // Keep recorded coordinates for existing runs; supplement missing city depots.
  for (const depot of [...Object.values(configured), ...runtimeDepots]) {
    if (!Number.isFinite(depot?.x) || !Number.isFinite(depot?.y)) continue;
    const key = depot.region_id == null ? "central" : String(depot.region_id);
    depots.set(key, depot);
  }
  const activeRegions = new Set(
    tasks
      .filter((task) => task.region_id != null)
      .map((task) => String(task.region_id)),
  );
  return [...depots.values()].map((depot) => ({
    ...depot,
    hasTasks:
      depot.region_id == null
        ? tasks.length > 0
        : activeRegions.has(String(depot.region_id)),
  }));
}

export function drawCityDepots(ctx, project, depots, zoom) {
  ctx.save();
  ctx.font = "600 12px sans-serif";
  ctx.strokeStyle = "#ffffff";
  ctx.lineWidth = 2;
  for (const depot of depots) {
    const point = project(depot);
    ctx.fillStyle = depot.hasTasks ? "#20242a" : "#9aa4b2";
    ctx.beginPath();
    ctx.rect(point.x - 5, point.y - 5, 10, 10);
    ctx.fill();
    ctx.stroke();
    if (zoom >= 2.5) {
      ctx.fillText(
        depot.region_id == null ? "中心站" : `R${depot.region_id}`,
        point.x + 8,
        point.y - 8,
      );
    }
  }
  ctx.restore();
}
