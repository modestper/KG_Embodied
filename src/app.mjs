import { prepareCanvas, roundRect } from "./canvas_utils.mjs?v=20261021-status-colors";
import { showPageError } from "./ui.mjs";
import {
  ALL_CITIES,
  buildCityOptions,
  buildFilteredSummary,
  formatCount,
} from "./data.mjs";

const state = {
  dataset: null,
  city: ALL_CITIES,
  taskType: "delivery",
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
};

async function init() {
  const response = await fetch("./data/summary.json");
  if (!response.ok) {
    throw new Error(`Unable to load summary.json: ${response.status}`);
  }
  state.dataset = await response.json();
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
    render();
  });
  window.addEventListener("resize", () => renderMap(currentSummary()));
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
  renderMap(summary);
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

  const roads = state.dataset.roads.filter(
    (road) => state.city === ALL_CITIES || road.city === state.city,
  );
  const roadPoints = roads.flatMap((road) =>
    road.points.map(([lng, lat]) => ({ lng, lat })),
  );
  const bounds = getBounds([...roadPoints, ...summary.visibleSamples]);
  if (!bounds) {
    drawEmpty(ctx, width, height);
    return;
  }
  const project = makeProjector(bounds, width, height);

  drawMapBackground(ctx, width, height);

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

  const sampleColor = state.taskType === "pickup" ? "#2866b6" : "#1b8a6b";
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
  drawCityLabels(ctx, summary.visibleSamples, project);
  drawLegend(ctx, sampleColor, width, height);
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
    const label = `${city} ${group.count}`;
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

function drawLegend(ctx, sampleColor, width, height) {
  ctx.save();
  const x = 18;
  const y = height - 58;
  ctx.fillStyle = "rgba(255,255,255,0.92)";
  ctx.strokeStyle = "#d7dde3";
  roundRect(ctx, x, y, 138, 40, 6);
  ctx.fill();
  ctx.stroke();
  ctx.strokeStyle = "#8f9ba8";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(x + 14, y + 15);
  ctx.lineTo(x + 48, y + 15);
  ctx.stroke();
  ctx.fillStyle = sampleColor;
  ctx.beginPath();
  ctx.arc(x + 31, y + 28, 4, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "#3e4650";
  ctx.font = "12px sans-serif";
  ctx.fillText("road", x + 58, y + 18);
  ctx.fillText(state.taskType, x + 58, y + 32);
  ctx.restore();
}

function getBounds(points) {
  const valid = points.filter(
    (point) => Number.isFinite(point.lng) && Number.isFinite(point.lat),
  );
  if (!valid.length) return null;
  return valid.reduce(
    (bounds, point) => ({
      minLng: Math.min(bounds.minLng, point.lng),
      maxLng: Math.max(bounds.maxLng, point.lng),
      minLat: Math.min(bounds.minLat, point.lat),
      maxLat: Math.max(bounds.maxLat, point.lat),
    }),
    {
      minLng: valid[0].lng,
      maxLng: valid[0].lng,
      minLat: valid[0].lat,
      maxLat: valid[0].lat,
    },
  );
}

function makeProjector(bounds, width, height) {
  const pad = 42;
  const zoomOut = 0.82;
  const xRange = bounds.maxLng - bounds.minLng || 1;
  const yRange = bounds.maxLat - bounds.minLat || 1;
  const scale =
    Math.min((width - pad * 2) / xRange, (height - pad * 2) / yRange) * zoomOut;
  const xOffset = (width - xRange * scale) / 2;
  const yOffset = (height - yRange * scale) / 2;
  return (point) => ({
    x: xOffset + (point.lng - bounds.minLng) * scale,
    y: height - yOffset - (point.lat - bounds.minLat) * scale,
  });
}

function drawEmpty(ctx, width, height) {
  ctx.fillStyle = "#68707c";
  ctx.font = "14px sans-serif";
  ctx.textAlign = "center";
  ctx.fillText("当前筛选没有可绘制坐标", width / 2, height / 2);
}

init().catch(showPageError);
