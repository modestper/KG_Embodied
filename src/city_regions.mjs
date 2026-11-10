import { getCityRegions } from "./data.mjs?v=20261104-fixed-regions";
import { outlinePoints } from "./dynamic_regions.mjs";
import { getBounds } from "./canvas_utils.mjs?v=20261021-status-colors";

let boundaryRequest;

export function loadRegionBoundaries() {
  if (!boundaryRequest) {
    boundaryRequest = fetch(
      new URL("../data/region_boundaries.json", import.meta.url),
      { cache: "no-cache" },
    )
      .then(async (response) => {
        if (!response.ok)
          throw new Error(`区域边界加载失败：HTTP ${response.status}`);
        const payload = await response.json();
        if (!payload?.cities) throw new Error("区域边界缺少城市数据");
        return payload;
      })
      .catch((error) => {
        boundaryRequest = null;
        throw error;
      });
  }
  return boundaryRequest;
}

export function buildCityRegionMap(boundaries, city) {
  if (!city) return { regions: [], bounds: null };
  const regions = getCityRegions(boundaries, city)
    .map((region) => ({ ...region, outline: region.polygon }))
    .filter((region) => outlinePoints(region).length >= 3);
  const points = regions.flatMap(outlinePoints);
  return { regions, bounds: points.length ? getBounds(points) : null };
}

export function regionBoundaryCaption(regions, status) {
  if (status === "loading") return "正在加载全城区域边界…";
  if (status === "error") return "全城区域边界加载失败，刷新页面可重试。";
  if (!regions.length) return "当前城市暂无固定区域边界。";
  return `全城 ${regions.length} 个区域 · 固定分区，不随任务选择变化 · 放大查看区域编号`;
}

export function drawCityRegions(ctx, project, regions, style, zoom) {
  if (!style.enabled || !regions.length) return;
  ctx.save();
  ctx.lineWidth = style.width;
  ctx.globalAlpha = style.opacity;
  ctx.strokeStyle = style.color;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  for (const region of regions) {
    const points = outlinePoints(region);
    if (points.length < 3) continue;
    ctx.beginPath();
    points.forEach((point, index) => {
      const pixel = project(point);
      if (index === 0) ctx.moveTo(pixel.x, pixel.y);
      else ctx.lineTo(pixel.x, pixel.y);
    });
    ctx.closePath();
    ctx.stroke();
  }
  ctx.restore();

  if (zoom < 2) return;
  ctx.save();
  ctx.font = "600 11px sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillStyle = style.color;
  ctx.strokeStyle = "rgba(255,255,255,0.9)";
  ctx.lineWidth = 3;
  for (const region of regions) {
    if (
      !Number.isFinite(region.centroid?.x) ||
      !Number.isFinite(region.centroid?.y)
    )
      continue;
    const point = project(region.centroid);
    const label = `R${region.region_id}`;
    ctx.strokeText(label, point.x, point.y - 12);
    ctx.fillText(label, point.x, point.y - 12);
  }
  ctx.restore();
}
