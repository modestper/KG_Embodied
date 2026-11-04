import {
  prepareCanvas,
  roundRect,
  getBounds,
  makeProjector,
  clampZoom,
  bindCanvasViewport,
} from "./canvas_utils.mjs?v=20261021-status-colors";
import { showPageError } from "./ui.mjs";
import {
  ALL_CITIES,
  buildCityOptions,
  buildFilteredSummary,
  formatCount,
  getCityRegions,
} from "./data.mjs?v=20261028-overview-map";

let mapRenderRequest = 0;

const state = {
  dataset: null,
  city: ALL_CITIES,
  taskType: "delivery",
  boundaries: null,
  boundaryLoadFailed: false,
  zoom: 1,
  panX: 0,
  panY: 0,
};

const els = {
  taskType: document.querySelector("#taskType"),
  city: document.querySelector("#city"),
  totalTasks: document.querySelector("#totalTasks"),
  totalCouriers: document.querySelector("#totalCouriers"),
  totalAoi: document.querySelector("#totalAoi"),
  sampleCount: document.querySelector("#sampleCount"),
  sourceText: document.querySelector("#sourceText"),
  mapCaption: document.querySelector("#mapCaption"),
  tableCaption: document.querySelector("#tableCaption"),
  hourChart: document.querySelector("#hourChart"),
  cityTable: document.querySelector("#cityTable"),
  mapCanvas: document.querySelector("#mapCanvas"),
  zoomIn: document.querySelector("#zoomIn"),
  zoomOut: document.querySelector("#zoomOut"),
  zoomReset: document.querySelector("#zoomReset"),
  zoomLabel: document.querySelector("#zoomLabel"),
  regionBoundaryEnabled: document.querySelector("#regionBoundaryEnabled"),
  mapHelp: document.querySelector("#mapHelp"),
  sampleLegendDot: document.querySelector("#sampleLegendDot"),
};

async function init() {
  const [response, boundaryResponse] = await Promise.all([
    fetch("./data/summary.json"),
    fetch("./data/region_boundaries.json").catch(() => null),
  ]);
  if (!response.ok) {
    throw new Error(`Unable to load summary.json: ${response.status}`);
  }
  state.dataset = await response.json();
  try {
    if (!boundaryResponse?.ok) throw new Error("区域边界不可用");
    state.boundaries = await boundaryResponse.json();
  } catch {
    state.boundaryLoadFailed = true;
  }
  populateCityOptions();
  bindEvents();
  render();
}

function populateCityOptions() {
  els.city.innerHTML = buildCityOptions(state.dataset)
    .map((city) => `<option value="${city}">${city}</option>`)
    .join("");
}

function bindEvents() {
  els.taskType.addEventListener("change", () => {
    state.taskType = els.taskType.value;
    render();
  });
  els.city.addEventListener("change", () => {
    state.city = els.city.value;
    resetView();
    render();
  });
  els.zoomIn.addEventListener("click", () => setZoom(state.zoom * 1.2));
  els.zoomOut.addEventListener("click", () => setZoom(state.zoom / 1.2));
  els.zoomReset.addEventListener("click", resetView);
  els.regionBoundaryEnabled.addEventListener("change", scheduleMapRender);
  els.mapCanvas.addEventListener("dblclick", resetView);
  els.mapCanvas.addEventListener("keydown", (event) => {
    const pan = {
      ArrowLeft: [40, 0],
      ArrowRight: [-40, 0],
      ArrowUp: [0, 40],
      ArrowDown: [0, -40],
    }[event.key];
    if (pan) {
      state.panX += pan[0];
      state.panY += pan[1];
      scheduleMapRender();
    } else if (event.key === "+" || event.key === "=") {
      setZoom(state.zoom * 1.2);
    } else if (event.key === "-") {
      setZoom(state.zoom / 1.2);
    } else if (event.key === "Home") {
      resetView();
    } else {
      return;
    }
    event.preventDefault();
  });
  bindCanvasViewport(els.mapCanvas, state, scheduleMapRender);
  window.addEventListener("resize", scheduleMapRender);
}

function setZoom(value) {
  state.zoom = clampZoom(value);
  scheduleMapRender();
}

function resetView() {
  state.zoom = 1;
  state.panX = 0;
  state.panY = 0;
  scheduleMapRender();
}

function scheduleMapRender() {
  els.zoomLabel.textContent = `${Math.round(state.zoom * 100)}%`;
  if (mapRenderRequest) return;
  mapRenderRequest = requestAnimationFrame(() => {
    mapRenderRequest = 0;
    renderMap(currentSummary());
  });
}

function currentSummary() {
  return buildFilteredSummary(state.dataset, {
    city: state.city,
    taskType: state.taskType,
  });
}

function render() {
  const summary = currentSummary();
  els.sourceText.textContent = Object.values(state.dataset.generated_from).join(
    " / ",
  );
  els.totalTasks.textContent = formatCount(summary.totalTasks);
  els.totalCouriers.textContent = formatCount(summary.totalCouriers);
  els.totalAoi.textContent = formatCount(summary.totalAoi);
  els.sampleCount.textContent = formatCount(summary.visibleSamples.length);
  els.mapCaption.textContent = `${state.city} · ${state.taskType}`;
  els.tableCaption.textContent = `${summary.cityRows.length} cities`;
  renderHourly(summary);
  renderTable(summary);
  scheduleMapRender();
}

function renderHourly(summary) {
  const max = Math.max(...summary.hourlySeries.map((row) => row.count), 1);
  const peak = summary.hourlySeries.reduce(
    (best, row) => (row.count > best.count ? row : best),
    { hour: 0, count: 0 },
  );
  document.querySelector("#hourSummary").textContent = peak.count
    ? `高峰 ${peak.hour}:00 · ${formatCount(peak.count)} 个任务`
    : "暂无小时统计";
  els.hourChart.innerHTML = summary.hourlySeries
    .map((row) => {
      const height = Math.max(2, (row.count / max) * 100);
      const label = row.hour % 3 === 0 ? `<span>${row.hour}</span>` : "";
      return `<div class="bar ${state.taskType}" style="height:${height}%" tabindex="0" role="img" aria-label="${row.hour} 时，${formatCount(row.count)} 个任务" title="${row.hour}:00 ${formatCount(row.count)}">${label}</div>`;
    })
    .join("");
}

function renderTable(summary) {
  els.cityTable.innerHTML = summary.cityRows
    .map(
      (row) => `
        <tr>
          <td>${row.name}</td>
          <td>${formatCount(row.delivery)}</td>
          <td>${formatCount(row.pickup)}</td>
          <td>${formatCount(row.couriers)}</td>
          <td>${formatCount(row.aoi)}</td>
          <td>${formatMinutes(row.avg_delivery_minutes)}</td>
          <td>${formatMinutes(row.avg_pickup_minutes)}</td>
        </tr>
      `,
    )
    .join("");
}

function formatMinutes(value) {
  return value == null ? "-" : `${Number(value).toFixed(1)}`;
}

function renderMap(summary) {
  const { ctx, width, height } = prepareCanvas(els.mapCanvas);
  const regions = getCityRegions(state.boundaries, state.city);
  const showBoundaries = els.regionBoundaryEnabled.checked;
  els.mapHelp.textContent = state.boundaryLoadFailed
    ? "区域边界加载失败，重新加载页面可重试；路网、抽样点和缩放仍可使用。"
    : `${regions.length} 个配送区域${showBoundaries ? "" : "（边界已隐藏）"} · 边界为配送服务分区示意，派送与揽收共用。聚焦地图后可用方向键平移、+/− 缩放、Home 复位。`;
  const roads = state.dataset.roads.filter(
    (road) => state.city === ALL_CITIES || road.city === state.city,
  );
  const roadPoints = roads.flatMap((road) =>
    road.points.map(([lng, lat]) => ({ lng, lat })),
  );
  const contentPoints = [...roadPoints, ...summary.visibleSamples].map(
    (point) => ({ x: point.lng, y: point.lat }),
  );
  // Keep distant boundary vertices from shrinking the sampled data and roads.
  const boundsPoints = contentPoints.length
    ? contentPoints
    : regions.flatMap((region) =>
        (region.polygon ?? []).map(([x, y]) => ({ x, y })),
      );
  if (!boundsPoints.length) {
    drawEmpty(ctx, width, height);
    return;
  }
  const projectPoint = makeProjector(
    getBounds(boundsPoints),
    width,
    height,
    state.zoom,
    state.panX,
    state.panY,
  );
  const project = (point) => projectPoint({ x: point.lng, y: point.lat });

  drawMapBackground(ctx, width, height);
  if (showBoundaries) drawRegionBoundaries(ctx, regions, projectPoint);

  ctx.save();
  ctx.lineWidth = 1.35;
  ctx.strokeStyle = "#8f9ba8";
  ctx.globalAlpha = 0.62;
  for (const road of roads) {
    ctx.beginPath();
    road.points.forEach(([lng, lat], index) => {
      const point = project({ lng, lat });
      if (index === 0) ctx.moveTo(point.x, point.y);
      else ctx.lineTo(point.x, point.y);
    });
    ctx.stroke();
  }
  ctx.restore();

  const sampleColor = state.taskType === "pickup" ? "#2866b6" : "#1b8a6b";
  els.sampleLegendDot.style.backgroundColor = sampleColor;
  ctx.globalAlpha = 1;
  for (const sample of summary.visibleSamples) {
    const point = project(sample);
    ctx.beginPath();
    ctx.fillStyle = "#ffffff";
    ctx.arc(point.x, point.y, 4.8, 0, Math.PI * 2);
    ctx.fill();
    ctx.beginPath();
    ctx.fillStyle = sampleColor;
    ctx.arc(point.x, point.y, 3.2, 0, Math.PI * 2);
    ctx.fill();
  }
  if (showBoundaries)
    drawRegionLabels(ctx, regions, projectPoint, width, height);
  drawCityLabels(ctx, summary.visibleSamples, project);
}

function drawRegionBoundaries(ctx, regions, project) {
  ctx.save();
  ctx.strokeStyle = "#8666b3";
  ctx.globalAlpha = 0.65;
  ctx.lineWidth = 1.2;
  ctx.lineJoin = "round";
  for (const region of regions) {
    const points = (region.polygon ?? [])
      .map(([x, y]) => ({ x, y }))
      .filter((point) => Number.isFinite(point.x) && Number.isFinite(point.y));
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
}

function drawRegionLabels(ctx, regions, project, width, height) {
  if (state.zoom < 2) return;
  ctx.save();
  ctx.font = "600 11px sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.lineWidth = 3;
  ctx.strokeStyle = "rgba(255,255,255,0.9)";
  ctx.fillStyle = "#78589f";
  for (const region of regions) {
    if (!region.centroid) continue;
    const point = project(region.centroid);
    if (
      point.x < 20 ||
      point.x > width - 20 ||
      point.y < 10 ||
      point.y > height - 10
    )
      continue;
    const label = `R${region.region_id}`;
    ctx.strokeText(label, point.x, point.y - 12);
    ctx.fillText(label, point.x, point.y - 12);
  }
  ctx.restore();
}

function drawMapBackground(ctx, width, height) {
  ctx.save();
  ctx.strokeStyle = "#e7ebef";
  ctx.lineWidth = 1;
  for (let x = 40; x < width; x += 80) {
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, height);
    ctx.stroke();
  }
  for (let y = 40; y < height; y += 80) {
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(width, y);
    ctx.stroke();
  }
  ctx.restore();
}

function drawCityLabels(ctx, samples, project) {
  const groups = new Map();
  for (const sample of samples) {
    const group = groups.get(sample.city) ?? { count: 0, lng: 0, lat: 0 };
    group.count += 1;
    group.lng += sample.lng;
    group.lat += sample.lat;
    groups.set(sample.city, group);
  }
  ctx.save();
  ctx.font = "600 12px sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  for (const [city, group] of groups) {
    const point = project({
      lng: group.lng / group.count,
      lat: group.lat / group.count,
    });
    const label = `${city} · 抽样 ${group.count} 点`;
    const width = ctx.measureText(label).width + 16;
    ctx.fillStyle = "rgba(255,255,255,0.92)";
    ctx.strokeStyle = "#d7dde3";
    ctx.lineWidth = 1;
    roundRect(ctx, point.x - width / 2, point.y - 28, width, 22, 5);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = "#20242a";
    ctx.fillText(label, point.x, point.y - 17);
  }
  ctx.restore();
}

function drawEmpty(ctx, width, height) {
  ctx.fillStyle = "#68707c";
  ctx.font = "14px sans-serif";
  ctx.textAlign = "center";
  ctx.fillText("当前筛选没有可绘制坐标", width / 2, height / 2);
}

init().catch(showPageError);
