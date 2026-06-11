const colors = ["#1b8a6b", "#2866b6", "#b7791f", "#8a3ffc", "#c43d4b", "#4f6f52"];
const minZoom = 0.25;
const maxZoom = 50;

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
  drag: null,
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
  zoomLabel: document.querySelector("#zoomLabel"),
  agentCaption: document.querySelector("#agentCaption"),
  agentTable: document.querySelector("#agentTable"),
};

async function init() {
  await loadTraceIndex();
  bindEvents();
  await loadSelectedTrace();
  render();
}

async function loadTraceIndex() {
  const response = await fetch("./data/traces.json");
  if (!response.ok) throw new Error(`Unable to load traces.json: ${response.status}`);
  const payload = await response.json();
  state.traces = payload.traces ?? [];
  const requested = new URLSearchParams(window.location.search).get("trace");
  state.traceFile = requested || state.traces[0]?.file || "delivery_sim_current.json";
  els.traceSelect.innerHTML = state.traces
    .map((trace) => `<option value="${trace.file}">${trace.label}</option>`)
    .join("");
  els.traceSelect.value = state.traceFile;
}

async function loadSelectedTrace() {
  stop();
  const response = await fetch(`./data/${encodeURIComponent(state.traceFile)}`);
  if (!response.ok) throw new Error(`Unable to load ${state.traceFile}: ${response.status}`);
  state.trace = await response.json();
  state.frameIndex = 0;
  resetView();
}

function bindEvents() {
  els.traceSelect.addEventListener("change", async () => {
    state.traceFile = els.traceSelect.value;
    const url = new URL(window.location.href);
    url.searchParams.set("trace", state.traceFile);
    window.history.replaceState({}, "", url);
    await loadSelectedTrace();
    render();
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
  els.canvas.addEventListener("pointerdown", startPan);
  els.canvas.addEventListener("pointermove", movePan);
  els.canvas.addEventListener("pointerup", endPan);
  els.canvas.addEventListener("pointerleave", endPan);
  els.canvas.addEventListener("wheel", zoomAtPointer, { passive: false });
  window.addEventListener("resize", render);
}

function togglePlayback() {
  if (state.timer) stop();
  else start();
}

function start() {
  els.playPause.textContent = "暂停";
  state.timer = window.setInterval(() => {
    state.frameIndex = Math.min(state.frameIndex + 1, state.trace.frames.length - 1);
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
  render();
}

function resetView() {
  state.zoom = 1;
  state.panX = 0;
  state.panY = 0;
  render();
}

function startPan(event) {
  state.drag = {
    x: event.clientX,
    y: event.clientY,
    panX: state.panX,
    panY: state.panY,
  };
  els.canvas.setPointerCapture(event.pointerId);
}

function movePan(event) {
  if (!state.drag) return;
  state.panX = state.drag.panX + event.clientX - state.drag.x;
  state.panY = state.drag.panY + event.clientY - state.drag.y;
  render();
}

function endPan(event) {
  if (!state.drag) return;
  state.drag = null;
  if (els.canvas.hasPointerCapture(event.pointerId)) {
    els.canvas.releasePointerCapture(event.pointerId);
  }
}

function zoomAtPointer(event) {
  event.preventDefault();
  const previousZoom = state.zoom;
  const nextZoom = clampZoom(previousZoom * (event.deltaY < 0 ? 1.12 : 1 / 1.12));
  const rect = els.canvas.getBoundingClientRect();
  const x = event.clientX - rect.left - rect.width / 2;
  const y = event.clientY - rect.top - rect.height / 2;
  const ratio = nextZoom / previousZoom;
  state.panX = x - (x - state.panX) * ratio;
  state.panY = y - (y - state.panY) * ratio;
  state.zoom = nextZoom;
  render();
}

function clampZoom(value) {
  return Math.min(maxZoom, Math.max(minZoom, value));
}

function render() {
  const frame = currentFrame();
  const metrics = state.trace.metrics;
  els.completedTasks.textContent = `${frame.tasks.completed}/${metrics.total_tasks}`;
  els.droneCount.textContent = String(metrics.drones);
  els.lateTasks.textContent = String(metrics.late_tasks);
  els.simTime.textContent = frame.time_label;
  els.frameCaption.textContent = `frame ${state.frameIndex + 1}/${state.trace.frames.length} · ${state.trace.step_seconds}s/step`;
  els.zoomLabel.textContent = `${Math.round(state.zoom * 100)}%`;
  els.agentCaption.textContent = state.trace.config.coordinator;
  renderAgentTable(frame);
  renderCanvas(frame);
}

function currentFrame() {
  return state.trace.frames[state.frameIndex];
}

function renderAgentTable(frame) {
  els.agentTable.innerHTML = frame.drones
    .map(
      (drone) => `
        <tr>
          <td>${drone.id}</td>
          <td>${drone.status}</td>
          <td>${drone.region_id ?? "-"}</td>
          <td>${drone.queue.length}</td>
          <td>${drone.completed}</td>
          <td>${Math.round(drone.distance_m).toLocaleString()} m</td>
        </tr>
      `,
    )
    .join("");
}

function renderCanvas(frame) {
  const canvas = els.canvas;
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(760, Math.floor(rect.width * ratio));
  canvas.height = Math.max(460, Math.floor(rect.height * ratio));

  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  const width = canvas.width / ratio;
  const height = canvas.height / ratio;
  ctx.clearRect(0, 0, width, height);

  const points = [
    ...state.trace.tasks.map((task) => ({ x: task.x, y: task.y })),
    ...(state.trace.depots ?? []).map((depot) => ({ x: depot.x, y: depot.y })),
    ...frame.drones.map((drone) => ({ x: drone.x, y: drone.y })),
    state.trace.config.depot,
  ];
  const project = makeProjector(getBounds(points), width, height, state.zoom, state.panX, state.panY);
  drawGrid(ctx, width, height);
  drawDepots(ctx, project);
  drawTasks(ctx, project, frame);
  drawRoutes(ctx, project, frame);
  drawDrones(ctx, project, frame);
  drawLegend(ctx, width, height);
}

function drawGrid(ctx, width, height) {
  ctx.save();
  ctx.strokeStyle = "#e7ebef";
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

function drawTasks(ctx, project, frame) {
  for (const task of state.trace.tasks) {
    const point = project(task);
    const status = resolveTaskStatus(task, frame);
    ctx.beginPath();
    if (status === "completed") ctx.fillStyle = "#c9d1d9";
    else if (status === "assigned") ctx.fillStyle = "#f1b44c";
    else ctx.fillStyle = "#1b8a6b";
    ctx.arc(point.x, point.y, 3.5, 0, Math.PI * 2);
    ctx.fill();
  }
}

function resolveTaskStatus(task, frame) {
  if (task.completed_at && task.completed_at <= frame.time_label) return "completed";
  return frame.task_statuses?.[task.id] ?? "waiting";
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

function drawDepots(ctx, project) {
  ctx.save();
  ctx.font = "600 12px sans-serif";
  for (const depot of state.trace.depots ?? []) {
    const point = project(depot);
    ctx.fillStyle = "#20242a";
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.rect(point.x - 5, point.y - 5, 10, 10);
    ctx.fill();
    ctx.stroke();
    if (state.zoom >= 2.5) {
      ctx.fillStyle = "#20242a";
      ctx.fillText(`R${depot.region_id}`, point.x + 8, point.y - 8);
    }
  }
  ctx.restore();
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

function drawLegend(ctx, width, height) {
  ctx.save();
  ctx.fillStyle = "rgba(255,255,255,0.94)";
  ctx.strokeStyle = "#d7dde3";
  roundRect(ctx, 18, height - 74, 250, 54, 6);
  ctx.fill();
  ctx.stroke();
  const items = [
    ["#1b8a6b", "waiting"],
    ["#f1b44c", "assigned"],
    ["#c9d1d9", "completed"],
    ["#20242a", "depot"],
  ];
  ctx.font = "12px sans-serif";
  for (const [index, [color, label]] of items.entries()) {
    const x = 32 + index * 58;
    ctx.fillStyle = color;
    ctx.beginPath();
    if (label === "depot") {
      ctx.rect(x - 4, height - 54, 8, 8);
      ctx.fill();
    } else {
      ctx.arc(x, height - 50, 4, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.fillStyle = "#3e4650";
    ctx.fillText(label, x - 12, height - 31);
  }
  ctx.restore();
}

function getBounds(points) {
  return points.reduce(
    (bounds, point) => ({
      minX: Math.min(bounds.minX, point.x),
      maxX: Math.max(bounds.maxX, point.x),
      minY: Math.min(bounds.minY, point.y),
      maxY: Math.max(bounds.maxY, point.y),
    }),
    {
      minX: points[0].x,
      maxX: points[0].x,
      minY: points[0].y,
      maxY: points[0].y,
    },
  );
}

function makeProjector(bounds, width, height, zoom = 1, panX = 0, panY = 0) {
  const pad = 42;
  const zoomOut = 0.86;
  const xRange = bounds.maxX - bounds.minX || 1;
  const yRange = bounds.maxY - bounds.minY || 1;
  const scale = Math.min((width - pad * 2) / xRange, (height - pad * 2) / yRange) * zoomOut * zoom;
  const xOffset = (width - xRange * scale) / 2;
  const yOffset = (height - yRange * scale) / 2;
  return (point) => ({
    x: xOffset + (point.x - bounds.minX) * scale + panX,
    y: height - yOffset - (point.y - bounds.minY) * scale + panY,
  });
}

function roundRect(ctx, x, y, width, height, radius) {
  ctx.beginPath();
  ctx.moveTo(x + radius, y);
  ctx.arcTo(x + width, y, x + width, y + height, radius);
  ctx.arcTo(x + width, y + height, x, y + height, radius);
  ctx.arcTo(x, y + height, x, y, radius);
  ctx.arcTo(x, y, x + width, y, radius);
  ctx.closePath();
}

init().catch((error) => {
  document.body.innerHTML = `<pre style="padding:24px;color:#a12b2b">${error.stack}</pre>`;
});
