import {
  colors,
  taskColors,
  prepareCanvas,
  drawGrid,
  getBounds,
  makeProjector,
  clampZoom,
  bindCanvasViewport,
  resolveTaskStatus,
} from "./canvas_utils.mjs?v=20261021-status-colors";
import { escapeHtml, droneStatusBadge, showPageError } from "./ui.mjs";
import {
  loadRegionBoundaries,
  buildCityRegionMap,
  regionBoundaryCaption,
  drawCityRegions,
} from "./city_regions.mjs?v=20261104-fixed-regions";
import {
  loadDepotCatalog,
  buildCityDepots,
  drawCityDepots,
} from "./city_depots.mjs?v=20261104-inactive-depots";

let canvasRenderRequest = 0;

const state = {
  trace: null,
  traces: [],
  traceFile: null,
  frameIndex: 0,
  timer: null,
  intervalMs: 90,
  zoom: 1,
  panX: 0,
  panY: 0,
  loading: true,
  loadSequence: 0,
  loadController: null,
  staticBounds: null,
  taskBounds: null,
  mapFocus: "regions",
  city: null,
  boundaries: null,
  boundaryStatus: "loading",
  regions: [],
  depotCatalog: null,
  mapDepots: [],
  regionBoundary: {
    enabled: true,
    width: 0.8,
    opacity: 0.35,
    color: "#26323b",
  },
};

const els = {
  canvas: document.querySelector("#simCanvas"),
  traceSelect: document.querySelector("#traceSelect"),
  playPause: document.querySelector("#playPause"),
  reset: document.querySelector("#reset"),
  speed: document.querySelector("#speed"),
  completedTasks: document.querySelector("#completedTasks"),
  droneCount: document.querySelector("#droneCount"),
  lateTasks: document.querySelector("#lateTasks"),
  simTime: document.querySelector("#simTime"),
  frameCaption: document.querySelector("#frameCaption"),
  zoomIn: document.querySelector("#zoomIn"),
  zoomOut: document.querySelector("#zoomOut"),
  zoomReset: document.querySelector("#zoomReset"),
  zoomTasks: document.querySelector("#zoomTasks"),
  zoomLabel: document.querySelector("#zoomLabel"),
  agentCaption: document.querySelector("#agentCaption"),
  progress: document.querySelector("#replayProgress"),
  progressCaption: document.querySelector("#replayCaption"),
  scenarioTitle: document.querySelector("#scenarioTitle"),
  scenarioCaption: document.querySelector("#scenarioCaption"),
  fileInfo: document.querySelector("#traceFileInfo"),
  agentTable: document.querySelector("#agentTable"),
  regionBoundaryEnabled: document.querySelector("#regionBoundaryEnabled"),
  regionBoundaryWidth: document.querySelector("#regionBoundaryWidth"),
  regionBoundaryOpacity: document.querySelector("#regionBoundaryOpacity"),
  regionBoundaryColor: document.querySelector("#regionBoundaryColor"),
  regionBoundaryStatus: document.querySelector("#regionBoundaryStatus"),
  depotCatalogStatus: document.querySelector("#depotCatalogStatus"),
};

async function init() {
  void loadDepotCatalog()
    .then((catalog) => {
      state.depotCatalog = catalog;
    })
    .catch(() => {
      els.depotCatalogStatus.hidden = false;
      els.depotCatalogStatus.textContent =
        "完整配送站数据加载失败，仅显示轨迹记录的站点；刷新页面可重试。";
    })
    .finally(() => {
      updateCityDepots();
      scheduleCanvasRender();
    });
  void loadRegionBoundaries()
    .then((boundaries) => {
      state.boundaries = boundaries;
      state.boundaryStatus = "ready";
    })
    .catch(() => {
      state.boundaryStatus = "error";
    })
    .finally(() => {
      updateCityRegions();
      scheduleCanvasRender();
    });
  await loadTraceIndex();
  bindEvents();
  await loadSelectedTrace();
  render();
}

async function loadTraceIndex() {
  const response = await fetch("./data/traces.json", { cache: "no-store" });
  if (!response.ok)
    throw new Error(`Unable to load traces.json: ${response.status}`);
  const payload = await response.json();
  state.traces = payload.traces ?? [];
  const requested = new URLSearchParams(window.location.search).get("trace");
  state.traceFile =
    requested || state.traces[0]?.file || "delivery_sim_current.json";
  els.traceSelect.innerHTML = state.traces
    .map(
      (trace) =>
        `<option value="${escapeHtml(trace.file)}">${escapeHtml(trace.label)}</option>`,
    )
    .join("");
  if (!state.traces.some((trace) => trace.file === state.traceFile)) {
    const option = document.createElement("option");
    option.value = state.traceFile;
    option.textContent = state.traceFile;
    els.traceSelect.append(option);
  }
  els.traceSelect.value = state.traceFile;
}

async function loadSelectedTrace() {
  const sequence = ++state.loadSequence;
  state.loadController?.abort();
  state.loadController = new AbortController();
  stop();
  state.loading = true;
  state.trace = null;
  state.city = null;
  state.regions = [];
  state.mapDepots = [];
  state.staticBounds = null;
  state.taskBounds = null;
  els.regionBoundaryStatus.textContent = "正在加载轨迹对应城市的全部区域…";
  els.playPause.disabled = true;
  els.reset.disabled = true;
  els.progress.disabled = true;
  for (const metric of [
    els.completedTasks,
    els.droneCount,
    els.lateTasks,
    els.simTime,
  ]) {
    metric.textContent = "—";
  }
  els.progress.max = "0";
  els.progress.value = "0";
  els.progressCaption.textContent = "正在加载轨迹";
  els.agentTable.innerHTML =
    '<tr><td colspan="6" class="empty-state">正在加载无人机状态</td></tr>';
  document.querySelector(".page-notice")?.remove();
  const entry = state.traces.find((trace) => trace.file === state.traceFile);
  const size = entry?.size_bytes
    ? `${(entry.size_bytes / 1024 / 1024).toFixed(1)} MiB · `
    : "";
  els.fileInfo.textContent = `${size}正在加载轨迹…`;
  els.scenarioCaption.textContent = "正在读取轨迹文件，请稍候。";
  scheduleCanvasRender();
  try {
    const response = await fetch(
      `./data/${state.traceFile.split("/").map(encodeURIComponent).join("/")}`,
      { signal: state.loadController.signal },
    );
    if (!response.ok)
      throw new Error(`无法读取 ${state.traceFile}（HTTP ${response.status}）`);
    const trace = await response.json();
    if (sequence !== state.loadSequence) return;
    if (!Array.isArray(trace.frames) || !trace.frames.length)
      throw new Error("轨迹文件没有可回放的帧。");
    state.trace = trace;
    state.frameIndex = 0;
    state.taskBounds = getBounds([
      ...(trace.tasks ?? []),
      ...(trace.tasks ?? []).map((task) => ({
        x: task.pickup_x,
        y: task.pickup_y,
      })),
      ...(trace.depots ?? []),
      trace.config?.depot,
    ]);
    const config = trace.config ?? {};
    const city = config.city || entry?.city || "配送";
    state.city = city;
    updateCityDepots();
    updateCityRegions();
    els.scenarioTitle.textContent = `${city} · 轨迹回放`;
    els.scenarioCaption.textContent = `${config.day || entry?.day || "全部日期"} · ${trace.metrics.total_tasks} 个任务 · ${trace.metrics.drones} 架无人机 · ${trace.frames.length.toLocaleString()} 帧`;
    els.fileInfo.textContent = `${size}已加载 ${trace.frames.length.toLocaleString()} 帧`;
    document.title = `${city}轨迹回放 · LaDe 配送模拟`;
    resetView();
  } catch (error) {
    if (sequence !== state.loadSequence || error.name === "AbortError") return;
    els.fileInfo.textContent = "轨迹加载失败";
    els.scenarioCaption.textContent = "读取失败，可重试或选择其他轨迹文件。";
    els.progressCaption.textContent = "轨迹未加载";
    els.agentTable.innerHTML =
      '<tr><td colspan="6" class="empty-state">无法加载无人机状态</td></tr>';
    throw error;
  } finally {
    if (sequence === state.loadSequence) {
      state.loading = false;
      els.playPause.disabled = !state.trace;
      els.reset.disabled = !state.trace;
      els.progress.disabled = !state.trace;
      scheduleCanvasRender();
    }
  }
}

function bindEvents() {
  els.traceSelect.addEventListener("change", async () => {
    state.traceFile = els.traceSelect.value;
    const url = new URL(window.location.href);
    url.searchParams.set("trace", state.traceFile);
    window.history.replaceState({}, "", url);
    try {
      await loadSelectedTrace();
      render();
    } catch (error) {
      showPageError(error);
    }
  });
  els.playPause.addEventListener("click", togglePlayback);
  els.reset.addEventListener("click", reset);
  els.speed.addEventListener("change", () => {
    state.intervalMs = Number(els.speed.value);
    if (state.timer) {
      stop();
      start();
    }
  });
  els.zoomIn.addEventListener("click", () => setZoom(state.zoom * 1.2));
  els.zoomOut.addEventListener("click", () => setZoom(state.zoom / 1.2));
  els.zoomReset.addEventListener("click", resetView);
  els.zoomTasks.addEventListener("click", () => resetView("tasks"));
  for (const control of [
    els.regionBoundaryEnabled,
    els.regionBoundaryWidth,
    els.regionBoundaryOpacity,
    els.regionBoundaryColor,
  ]) {
    control.addEventListener(
      control === els.regionBoundaryEnabled ? "change" : "input",
      updateRegionBoundaryStyle,
    );
  }
  bindCanvasViewport(els.canvas, state, scheduleCanvasRender);
  els.progress.addEventListener("input", () => {
    if (!state.trace) return;
    stop();
    state.frameIndex = Number(els.progress.value);
    render();
  });
  window.addEventListener("resize", scheduleCanvasRender);
}

function togglePlayback() {
  if (state.timer) stop();
  else start();
}

function start() {
  if (!state.trace || state.loading) return;
  if (state.frameIndex >= state.trace.frames.length - 1) state.frameIndex = 0;
  els.playPause.textContent = "暂停";
  state.timer = window.setInterval(() => {
    state.frameIndex = Math.min(
      state.frameIndex + 1,
      state.trace.frames.length - 1,
    );
    render();
    if (state.frameIndex >= state.trace.frames.length - 1) stop();
  }, state.intervalMs);
}

function stop() {
  window.clearInterval(state.timer);
  state.timer = null;
  els.playPause.textContent = "播放";
}

function reset() {
  stop();
  state.frameIndex = 0;
  render();
}

function setZoom(value) {
  state.zoom = clampZoom(value);
  scheduleCanvasRender();
}

function resetView(focus = "regions") {
  state.mapFocus = focus === "tasks" ? "tasks" : "regions";
  state.zoom = 1;
  state.panX = 0;
  state.panY = 0;
  scheduleCanvasRender();
}

function updateCityRegions() {
  const map = buildCityRegionMap(state.boundaries, state.city);
  state.regions = map.regions;
  state.staticBounds = map.bounds;
  els.regionBoundaryStatus.textContent = regionBoundaryCaption(
    state.regions,
    state.boundaryStatus,
  );
}

function updateCityDepots() {
  state.mapDepots = buildCityDepots(
    state.depotCatalog,
    state.city,
    state.trace?.depots ?? [],
    state.trace?.tasks ?? [],
  );
}

function updateRegionBoundaryStyle() {
  state.regionBoundary = {
    enabled: els.regionBoundaryEnabled.checked,
    width: Number(els.regionBoundaryWidth.value),
    opacity: Number(els.regionBoundaryOpacity.value),
    color: els.regionBoundaryColor.value,
  };
  scheduleCanvasRender();
}

function render() {
  if (!state.trace) {
    renderCanvas();
    return;
  }
  const frame = currentFrame();
  const metrics = state.trace.metrics;
  els.completedTasks.textContent = `${frame.tasks.completed}/${metrics.total_tasks}`;
  els.droneCount.textContent = String(metrics.drones);
  els.lateTasks.textContent = String(metrics.late_tasks);
  els.simTime.textContent = frame.time_label;
  els.frameCaption.textContent = `第 ${state.frameIndex + 1}/${state.trace.frames.length} 帧 · ${state.trace.step_seconds} 秒/步`;
  els.progress.max = String(state.trace.frames.length - 1);
  els.progress.value = String(state.frameIndex);
  els.progressCaption.textContent = `${frame.time_label} · ${state.frameIndex + 1} / ${state.trace.frames.length} 帧`;
  els.zoomLabel.textContent = `${Math.round(state.zoom * 100)}%`;
  els.agentCaption.textContent = `${frame.drones.length} 架无人机`;
  renderAgentTable(frame);
  renderCanvas(frame);
}

function currentFrame() {
  return state.trace?.frames[state.frameIndex];
}

function renderAgentTable(frame) {
  els.agentTable.innerHTML = frame.drones
    .map(
      (drone) => `<tr>
    <td>${escapeHtml(drone.id)}</td><td>${droneStatusBadge(drone.status)}</td><td>${escapeHtml(drone.region_id)}</td>
    <td>${drone.queue?.length ?? 0}</td><td>${drone.completed}</td><td>${Math.round(drone.distance_m).toLocaleString()} m</td>
  </tr>`,
    )
    .join("");
}

function renderCanvas(frame = currentFrame()) {
  const { ctx, width, height } = prepareCanvas(els.canvas);
  drawGrid(ctx, width, height);
  if (!frame) {
    ctx.fillStyle = "#a0acc2";
    ctx.font = "13px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(
      state.loading ? "正在加载历史轨迹" : "请选择可用的轨迹文件",
      width / 2,
      height / 2,
    );
    ctx.textAlign = "start";
    return;
  }
  const bounds =
    state.mapFocus === "tasks"
      ? state.taskBounds
      : (state.staticBounds ?? state.taskBounds);
  const project = makeProjector(
    bounds ?? getBounds([]),
    width,
    height,
    state.zoom,
    state.panX,
    state.panY,
  );
  drawCityRegions(
    ctx,
    project,
    state.regions,
    state.regionBoundary,
    state.zoom,
  );
  drawCityDepots(ctx, project, state.mapDepots, state.zoom);
  drawTasks(ctx, project, frame);
  drawRoutes(ctx, project, frame);
  drawDrones(ctx, project, frame);
}

function drawTasks(ctx, project, frame) {
  for (const task of state.trace.tasks) {
    const point = project(task);
    ctx.beginPath();
    ctx.fillStyle =
      taskColors[resolveTaskStatus(task, frame)] ?? taskColors.waiting;
    ctx.arc(point.x, point.y, 3.5, 0, Math.PI * 2);
    ctx.fill();
  }
}

function drawRoutes(ctx, project, frame) {
  for (const [index, drone] of frame.drones.entries()) {
    if (!drone.target) continue;
    const from = project(drone);
    const to = project(drone.target);
    ctx.save();
    ctx.strokeStyle = colors[index % colors.length];
    ctx.globalAlpha = drone.target.phase === "pickup" ? 0.36 : 0.62;
    ctx.setLineDash(drone.target.phase === "pickup" ? [2, 5] : [7, 5]);
    ctx.beginPath();
    ctx.moveTo(from.x, from.y);
    ctx.lineTo(to.x, to.y);
    ctx.stroke();
    ctx.restore();
  }
}

function drawDrones(ctx, project, frame) {
  for (const [index, drone] of frame.drones.entries()) {
    const point = project(drone);
    ctx.save();
    ctx.fillStyle = colors[index % colors.length];
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(point.x, point.y - 9);
    ctx.lineTo(point.x + 9, point.y + 8);
    ctx.lineTo(point.x, point.y + 4);
    ctx.lineTo(point.x - 9, point.y + 8);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
    ctx.font = "600 11px sans-serif";
    ctx.fillStyle = "#20242a";
    ctx.fillText(drone.id.replace("drone-", "D"), point.x + 11, point.y + 4);
    ctx.restore();
  }
}

function scheduleCanvasRender() {
  els.zoomLabel.textContent = `${Math.round(state.zoom * 100)}%`;
  if (canvasRenderRequest) return;
  canvasRenderRequest = requestAnimationFrame(() => {
    canvasRenderRequest = 0;
    renderCanvas();
  });
}

init().catch(showPageError);
