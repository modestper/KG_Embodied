import { buildDynamicRegions, outlinePoints } from "./dynamic_regions.mjs";

const colors = ["#1b8a6b", "#2866b6", "#b7791f", "#8a3ffc", "#c43d4b", "#4f6f52"];
const minZoom = 0.25;
const maxZoom = 50;

const state = {
  source: null,
  config: null,
  depots: [],
  regions: [],
  tasks: [],
  frame: null,
  metrics: null,
  kgEvents: [],
  llmEvents: [],
  frameBuffer: [],
  frameIndex: 0,
  stepSeconds: null,
  playing: true,
  playbackTimer: null,
  streamDone: false,
  donePayload: null,
  wallStartedAt: null,
  wallElapsedMs: 0,
  wallTimer: null,
  zoom: 1,
  panX: 0,
  panY: 0,
  drag: null,
};

const els = {
  canvas: document.querySelector("#liveCanvas"),
  playPause: document.querySelector("#playPause"),
  resetRun: document.querySelector("#resetRun"),
  playSpeed: document.querySelector("#playSpeed"),
  reconnect: document.querySelector("#reconnect"),
  resetView: document.querySelector("#resetView"),
  zoomPreset: document.querySelector("#zoomPreset"),
  city: document.querySelector("#city"),
  day: document.querySelector("#day"),
  droneCountInput: document.querySelector("#droneCount"),
  taskLimit: document.querySelector("#taskLimit"),
  maxSteps: document.querySelector("#maxSteps"),
  frameStride: document.querySelector("#frameStride"),
  wallIntervalMs: document.querySelector("#wallIntervalMs"),
  backend: document.querySelector("#backend"),
  rayAddress: document.querySelector("#rayAddress"),
  rayNumCpus: document.querySelector("#rayNumCpus"),
  useLlm: document.querySelector("#useLlm"),
  vllmApiBase: document.querySelector("#vllmApiBase"),
  vllmModel: document.querySelector("#vllmModel"),
  llmMaxTokens: document.querySelector("#llmMaxTokens"),
  connectionStatus: document.querySelector("#connectionStatus"),
  runCaption: document.querySelector("#runCaption"),
  scenarioCaption: document.querySelector("#scenarioCaption"),
  runStatus: document.querySelector("#runStatus"),
  runStatusTitle: document.querySelector("#runStatusTitle"),
  runStatusDetail: document.querySelector("#runStatusDetail"),
  completedTasks: document.querySelector("#completedTasks"),
  releasedUnfinishedTasks: document.querySelector("#releasedUnfinishedTasks"),
  droneCount: document.querySelector("#droneCountMetric"),
  idleDroneCount: document.querySelector("#idleDroneCount"),
  lateTasks: document.querySelector("#lateTasks"),
  simTime: document.querySelector("#simTime"),
  wallElapsed: document.querySelector("#wallElapsed"),
  frameCaption: document.querySelector("#frameCaption"),
  zoomIn: document.querySelector("#zoomIn"),
  zoomOut: document.querySelector("#zoomOut"),
  zoomReset: document.querySelector("#zoomReset"),
  zoomLabel: document.querySelector("#zoomLabel"),
  agentCaption: document.querySelector("#agentCaption"),
  agentTable: document.querySelector("#agentTable"),
  taskCaption: document.querySelector("#taskCaption"),
  taskTable: document.querySelector("#taskTable"),
  eventCaption: document.querySelector("#eventCaption"),
  eventList: document.querySelector("#eventList"),
  llmCaption: document.querySelector("#llmCaption"),
  llmInput: document.querySelector("#llmInput"),
  llmOutput: document.querySelector("#llmOutput"),
};

function init() {
  bindEvents();
  connect();
  renderEmpty();
}

function bindEvents() {
  els.playPause.addEventListener("click", togglePlayback);
  els.resetRun.addEventListener("click", connect);
  els.reconnect.addEventListener("click", connect);
  els.playSpeed.addEventListener("change", restartPlaybackTimer);
  els.resetView.addEventListener("click", resetView);
  els.zoomPreset.addEventListener("change", () => {
    if (els.zoomPreset.value !== "custom") setZoom(Number(els.zoomPreset.value));
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

function connect() {
  if (state.source) state.source.close();
  resetRuntime();
  startWallTimer();
  state.playing = true;
  restartPlaybackTimer();
  setConnection("connecting");
  const source = new EventSource(`./events?${buildQuery()}`);
  state.source = source;

  source.addEventListener("open", () => setConnection("connected"));
  source.addEventListener("init", (event) => {
    const payload = JSON.parse(event.data);
    state.config = payload.config;
    state.depots = payload.depots ?? [];
    state.regions = payload.regions ?? [];
    state.tasks = payload.tasks ?? [];
    state.metrics = payload.metrics ?? null;
    state.stepSeconds = payload.step_seconds ?? null;
    resetView(false);
    render();
  });
  source.addEventListener("frame", (event) => {
    const payload = JSON.parse(event.data);
    state.frameBuffer.push(payload);
    if (state.playing && state.frameBuffer.length === 1) consumeFrame();
  });
  source.addEventListener("done", (event) => {
    const payload = JSON.parse(event.data);
    state.metrics = payload.metrics ?? state.metrics;
    state.kgEvents = payload.kg_events ?? state.kgEvents;
    state.llmEvents = payload.llm_events ?? state.llmEvents;
    state.streamDone = true;
    state.donePayload = payload;
    stopWallTimer();
    setConnection("done");
    source.close();
    render();
  });
  source.addEventListener("error", () => {
    if (els.connectionStatus.textContent !== "done") setConnection("reconnecting");
  });
}

function buildQuery() {
  const params = new URLSearchParams({
    city: els.city.value,
    day: els.day.value.trim() || "all",
    drone_count: normalizedInt(els.droneCountInput.value, 3),
    task_limit: normalizedInt(els.taskLimit.value, 20),
    max_steps: normalizedInt(els.maxSteps.value, 1000),
    frame_stride: normalizedInt(els.frameStride.value, 1),
    wall_interval: String(normalizedInt(els.wallIntervalMs.value, 250) / 1000),
    use_llm: els.useLlm.checked ? "1" : "0",
    vllm_api_base: els.vllmApiBase.value.trim() || "http://127.0.0.1:6666/v1",
    vllm_model: els.vllmModel.value.trim() || "qwen2.5-7b",
    llm_max_tokens: normalizedInt(els.llmMaxTokens.value, 512),
  });
  if (els.backend.value) params.set("backend", els.backend.value);
  if (els.rayAddress.value.trim()) params.set("ray_address", els.rayAddress.value.trim());
  if (els.rayNumCpus.value.trim()) {
    params.set("ray_num_cpus", normalizedInt(els.rayNumCpus.value, 1));
  }
  return params.toString();
}

function normalizedInt(value, fallback) {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function togglePlayback() {
  state.playing = !state.playing;
  if (state.playing) restartPlaybackTimer();
  else stopPlaybackTimer();
  renderPlaybackButton();
}

function restartPlaybackTimer() {
  stopPlaybackTimer();
  if (!state.playing) return;
  state.playbackTimer = window.setInterval(consumeFrame, Number(els.playSpeed.value));
  renderPlaybackButton();
}

function stopPlaybackTimer() {
  window.clearInterval(state.playbackTimer);
  state.playbackTimer = null;
}

function startWallTimer() {
  stopWallTimer();
  state.wallStartedAt = Date.now();
  state.wallElapsedMs = 0;
  state.wallTimer = window.setInterval(renderWallElapsed, 1000);
  renderWallElapsed();
}

function stopWallTimer() {
  if (state.wallStartedAt != null) {
    state.wallElapsedMs = Date.now() - state.wallStartedAt;
  }
  window.clearInterval(state.wallTimer);
  state.wallTimer = null;
  renderWallElapsed();
}

function renderWallElapsed() {
  const elapsed =
    state.wallStartedAt == null || state.wallTimer == null
      ? state.wallElapsedMs
      : Date.now() - state.wallStartedAt;
  els.wallElapsed.textContent = formatDuration(elapsed);
}

function formatDuration(milliseconds) {
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return [hours, minutes, seconds].map((part) => String(part).padStart(2, "0")).join(":");
}

function consumeFrame() {
  const payload = state.frameBuffer.shift();
  if (!payload) {
    if (state.streamDone) stopPlaybackTimer();
    return;
  }
  state.frameIndex = payload.frame_index ?? state.frameIndex + 1;
  state.frame = payload.frame;
  state.metrics = payload.metrics ?? state.metrics;
  state.kgEvents = payload.kg_events ?? state.kgEvents;
  state.llmEvents = payload.llm_events ?? state.llmEvents;
  render();
}

function renderPlaybackButton() {
  els.playPause.textContent = state.playing ? "暂停" : "播放";
}

function resetRuntime() {
  state.config = null;
  state.depots = [];
  state.regions = [];
  state.tasks = [];
  state.frame = null;
  state.metrics = null;
  state.kgEvents = [];
  state.llmEvents = [];
  state.frameBuffer = [];
  state.frameIndex = 0;
  state.stepSeconds = null;
  state.streamDone = false;
  state.donePayload = null;
  state.wallStartedAt = null;
  state.wallElapsedMs = 0;
  renderEmpty();
}

function setConnection(status) {
  els.connectionStatus.textContent = status;
  els.connectionStatus.dataset.status = status;
}

function setZoom(value) {
  state.zoom = clampZoom(value);
  els.zoomPreset.value = presetValue(state.zoom);
  render();
}

function resetView(shouldRender = true) {
  state.zoom = 1;
  state.panX = 0;
  state.panY = 0;
  els.zoomPreset.value = "1";
  if (shouldRender) render();
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
  els.zoomPreset.value = presetValue(state.zoom);
  render();
}

function clampZoom(value) {
  return Math.min(maxZoom, Math.max(minZoom, value));
}

function presetValue(value) {
  const rounded = Math.round(value);
  return ["1", "5", "20", "50"].includes(String(rounded)) ? String(rounded) : "custom";
}

function renderEmpty() {
  els.completedTasks.textContent = "-";
  els.releasedUnfinishedTasks.textContent = "已下发未完成 -";
  els.droneCount.textContent = "-";
  els.idleDroneCount.textContent = "当前空闲 -";
  els.lateTasks.textContent = "-";
  els.simTime.textContent = "-";
  renderWallElapsed();
  els.frameCaption.textContent = "frame 0";
  els.zoomLabel.textContent = `${Math.round(state.zoom * 100)}%`;
  renderPlaybackButton();
  els.agentCaption.textContent = "-";
  els.agentTable.innerHTML = "";
  els.taskCaption.textContent = "-";
  els.taskTable.innerHTML = "";
  els.eventCaption.textContent = "-";
  els.eventList.innerHTML = "";
  els.llmCaption.textContent = "未调用";
  els.llmInput.textContent = "-";
  els.llmOutput.textContent = "-";
  renderRunStatus();
  drawEmptyCanvas();
}

function render() {
  if (!state.config) {
    renderEmpty();
    return;
  }

  const metrics = state.metrics ?? {};
  const completed = state.frame?.tasks?.completed ?? metrics.completed_tasks ?? 0;
  const releasedTaskStates = Object.values(state.frame?.task_states ?? {});
  const releasedUnfinished = releasedTaskStates.filter((task) => task.status !== "completed").length;
  const drones = state.frame?.drones ?? [];
  const idleDrones = drones.filter((drone) => drone.status === "idle").length;
  els.completedTasks.textContent = `${completed}/${metrics.total_tasks ?? state.tasks.length}`;
  els.releasedUnfinishedTasks.textContent = `已下发未完成 ${releasedUnfinished}`;
  els.droneCount.textContent = String(metrics.drones ?? "-");
  els.idleDroneCount.textContent = `当前空闲 ${idleDrones}`;
  els.lateTasks.textContent = String(metrics.late_tasks ?? 0);
  els.simTime.textContent = state.frame?.time_label ?? "-";
  els.frameCaption.textContent = `frame ${state.frameIndex} · ${state.stepSeconds ?? "-"}s/step · buffer ${state.frameBuffer.length}`;
  els.zoomLabel.textContent = `${Math.round(state.zoom * 100)}%`;
  els.runCaption.textContent = `${state.config.city} ${state.config.day} · ${state.config.task_limit} tasks · ${state.config.drone_count} agents`;
  els.scenarioCaption.textContent = [
    `backend ${state.config.backend ?? "local"}`,
    rayConfigLabel(state.config),
    `${state.config.coordinator_class} + ${state.config.planner}`,
    `${state.config.region_count} regions`,
    `LLM ${state.config.llm_enabled ? "on" : "off"}`,
  ]
    .filter(Boolean)
    .join(" · ");
  els.agentCaption.textContent = state.config.depot_mode === "region" ? "region depot" : "center depot";

  renderAgentTable();
  renderTaskTable();
  renderEvents();
  renderLlmEvents();
  renderRunStatus();
  renderCanvas();
}

function renderRunStatus() {
  const completed = state.metrics?.completed_tasks ?? 0;
  const total = state.metrics?.total_tasks ?? state.tasks.length;
  const unfinished = state.metrics?.unfinished_tasks ?? Math.max(0, total - completed);

  if (!state.donePayload) {
    els.runStatus.dataset.status = "running";
    els.runStatusTitle.textContent = state.config ? "仿真运行中" : "等待仿真初始化";
    els.runStatusDetail.textContent = state.config
      ? `已完成 ${completed}/${total}，其中迟到完成 ${state.metrics?.late_tasks ?? 0}`
      : "等待实时帧数据";
    return;
  }

  if (state.frameBuffer.length > 0) {
    els.runStatus.dataset.status = "buffering";
    els.runStatusTitle.textContent = "服务端仿真已结束，正在播放缓冲帧";
    els.runStatusDetail.textContent = `缓冲区剩余 ${state.frameBuffer.length} 帧，最终完成 ${completed}/${total}`;
    return;
  }

  if (state.donePayload.reason === "all_tasks_completed") {
    els.runStatus.dataset.status = "completed";
    els.runStatusTitle.textContent = "全部任务已完成";
    els.runStatusDetail.textContent = `完成 ${completed}/${total}，迟到完成 ${state.metrics?.late_tasks ?? 0}，结束时间 ${state.donePayload.final_time ?? "-"}`;
    return;
  }

  els.runStatus.dataset.status = "incomplete";
  els.runStatusTitle.textContent = "仿真已停止，但任务未全部完成";
  els.runStatusDetail.textContent = `达到最大帧数 ${state.donePayload.steps_run ?? state.config?.max_steps ?? "-"}，完成 ${completed}/${total}，仍有 ${unfinished} 个任务未完成`;
}

function rayConfigLabel(config) {
  if ((config.backend ?? "local") !== "ray") return null;
  const parts = [];
  if (config.ray_address) parts.push(`address ${config.ray_address}`);
  if (config.ray_num_cpus) parts.push(`${config.ray_num_cpus} CPUs`);
  return parts.length ? `Ray ${parts.join(" · ")}` : "Ray local runtime";
}

function renderAgentTable() {
  const drones = state.frame?.drones ?? [];
  els.agentTable.innerHTML = drones
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

function renderTaskTable() {
  const currentMinute = state.frame?.time_minute;
  const taskStates = state.frame?.task_states ?? {};
  const releasedTasks = state.tasks
    .map((task, index) => ({ task, index }))
    .filter(({ task }) => Number.isFinite(currentMinute) && task.release <= currentMinute);
  const completed = releasedTasks.filter(({ task }) => taskStates[task.id]?.status === "completed").length;
  els.taskCaption.textContent = `${releasedTasks.length} 已下发 · ${completed} 已完成`;
  els.taskTable.innerHTML = releasedTasks
    .map(({ task, index }) => {
      const taskState = taskStates[task.id] ?? { status: "waiting" };
      return `
        <tr>
          <td>${index + 1}</td>
          <td>${task.id}</td>
          <td>${task.region_id ?? "-"}</td>
          <td>${formatMinuteValue(task.release)}</td>
          <td>${formatMinuteValue(task.deadline)}</td>
          <td>${taskStatusLabel(taskState)}</td>
          <td>${taskState.assigned_to ?? "-"}</td>
          <td>${formatMinuteValue(taskState.completed_at)}</td>
          <td>${taskTimelinessLabel(taskState)}</td>
        </tr>
      `;
    })
    .join("");
}

function taskStatusLabel(taskState) {
  if (taskState.status === "completed") return "已完成";
  if (taskState.status === "assigned" && taskState.picked_up) return "配送中";
  if (taskState.status === "assigned") return "前往取货";
  return "等待派单";
}

function taskTimelinessLabel(taskState) {
  if (taskState.status !== "completed") return "-";
  return taskState.late ? "迟到" : "准时";
}

function formatMinuteValue(value) {
  if (!Number.isFinite(value)) return "-";
  const totalSeconds = Math.round(value * 60) % (24 * 60 * 60);
  const hour = Math.floor(totalSeconds / 3600);
  const minute = Math.floor((totalSeconds % 3600) / 60);
  const second = totalSeconds % 60;
  return [hour, minute, second].map((part) => String(part).padStart(2, "0")).join(":");
}

function renderEvents() {
  const events = state.kgEvents.slice(-8).reverse();
  els.eventCaption.textContent = `${state.kgEvents.length} events`;
  els.eventList.innerHTML = events
    .map(
      (event) => `
        <li>
          <strong>${event.type}</strong>
          <span>${formatEvent(event)}</span>
        </li>
      `,
    )
    .join("");
}

function renderLlmEvents() {
  const latest = state.llmEvents.at(-1);
  if (!latest) {
    const logName = state.config?.llm_log_path ? ` · ${basename(state.config.llm_log_path)}` : "";
    els.llmCaption.textContent = state.config?.llm_enabled ? `等待 LLM 调用${logName}` : "LLM off";
    els.llmInput.textContent = "-";
    els.llmOutput.textContent = "-";
    return;
  }

  const status = latest.used_fallback ? "fallback" : "ok";
  const elapsed = latest.elapsed_ms == null ? "-" : `${latest.elapsed_ms} ms`;
  const logName = state.config?.llm_log_path ? ` · ${basename(state.config.llm_log_path)}` : "";
  els.llmCaption.textContent = `${latest.time} · R${latest.region_id} · ${status} · ${elapsed} · max ${latest.max_tokens}${logName}`;
  els.llmInput.textContent = formatJsonForPanel(latest.request);
  els.llmOutput.textContent = formatJsonForPanel({
    response: latest.response,
    decisions: latest.decisions,
    error: latest.error,
    used_fallback: latest.used_fallback,
  });
}

function formatJsonForPanel(value) {
  if (value == null) return "-";
  return JSON.stringify(value, null, 2);
}

function basename(path) {
  return String(path).split("/").pop();
}

function formatEvent(event) {
  const parts = [
    event.time_label ?? event.time_minute,
    event.region_id ? `R${event.region_id}` : null,
    event.task_id ? `task ${event.task_id}` : null,
    event.drone_id ?? null,
  ].filter(Boolean);
  return parts.join(" · ");
}

function renderCanvas() {
  const frame = state.frame;
  if (!frame) {
    drawEmptyCanvas();
    return;
  }
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

  const regions = currentRegions();
  const points = [
    ...state.tasks.map((task) => ({ x: task.x, y: task.y })),
    ...state.tasks.map((task) => ({ x: task.pickup_x, y: task.pickup_y })),
    ...state.depots.map((depot) => ({ x: depot.x, y: depot.y })),
    ...regions.flatMap((region) => outlinePoints(region)),
    ...frame.drones.map((drone) => ({ x: drone.x, y: drone.y })),
  ].filter((point) => point && Number.isFinite(point.x) && Number.isFinite(point.y));

  const project = makeProjector(getBounds(points), width, height, state.zoom, state.panX, state.panY);
  drawGrid(ctx, width, height);
  drawRegionOutlines(ctx, project, regions);
  drawDepots(ctx, project);
  drawPickupLinks(ctx, project, frame);
  drawTasks(ctx, project, frame);
  drawRoutes(ctx, project, frame);
  drawDrones(ctx, project, frame);
  drawLegend(ctx, width, height);
}

function drawEmptyCanvas() {
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
  drawGrid(ctx, width, height);
  ctx.fillStyle = "#68707c";
  ctx.font = "600 14px sans-serif";
  ctx.fillText("等待动态仿真数据", 28, 34);
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

function drawRegionOutlines(ctx, project, regions) {
  if (!regions.length) return;
  ctx.save();
  ctx.lineWidth = 0.55;
  ctx.globalAlpha = 0.28;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  for (const [index, region] of regions.entries()) {
    const points = outlinePoints(region);
    if (points.length < 2) continue;
    ctx.strokeStyle = colors[index % colors.length];
    drawClosedPath(ctx, points.map(project));
  }
  ctx.restore();

  if (state.zoom < 2) return;
  ctx.save();
  ctx.font = "600 11px sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.globalAlpha = 0.5;
  for (const [index, region] of regions.entries()) {
    if (!region.centroid) continue;
    const point = project(region.centroid);
    ctx.fillStyle = colors[index % colors.length];
    ctx.fillText(`R${region.region_id}`, point.x, point.y - 12);
  }
  ctx.restore();
}

function drawClosedPath(ctx, points) {
  ctx.beginPath();
  for (const [index, point] of points.entries()) {
    if (index === 0) ctx.moveTo(point.x, point.y);
    else ctx.lineTo(point.x, point.y);
  }
  if (points.length >= 3) ctx.closePath();
  ctx.stroke();
}

function currentRegions() {
  return state.regions.length ? state.regions : buildDynamicRegions(state.tasks);
}

function drawDepots(ctx, project) {
  ctx.save();
  ctx.font = "600 12px sans-serif";
  for (const depot of state.depots) {
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

function drawPickupLinks(ctx, project, frame) {
  if (state.zoom < 2) return;
  ctx.save();
  ctx.strokeStyle = "#d0d7de";
  ctx.globalAlpha = 0.35;
  ctx.setLineDash([2, 5]);
  for (const task of state.tasks) {
    const status = resolveTaskStatus(task, frame);
    if (status === "completed") continue;
    const from = project({ x: task.pickup_x, y: task.pickup_y });
    const to = project(task);
    ctx.beginPath();
    ctx.moveTo(from.x, from.y);
    ctx.lineTo(to.x, to.y);
    ctx.stroke();
  }
  ctx.restore();
}

function drawTasks(ctx, project, frame) {
  for (const task of state.tasks) {
    const point = project(task);
    const status = resolveTaskStatus(task, frame);
    ctx.beginPath();
    if (status === "completed") ctx.fillStyle = "#c9d1d9";
    else if (status === "assigned") ctx.fillStyle = "#f1b44c";
    else ctx.fillStyle = "#1b8a6b";
    ctx.arc(point.x, point.y, state.zoom >= 5 ? 4.5 : 3.2, 0, Math.PI * 2);
    ctx.fill();
  }
}

function resolveTaskStatus(task, frame) {
  return frame.task_statuses?.[task.id] ?? task.status ?? "waiting";
}

function drawRoutes(ctx, project, frame) {
  for (const [index, drone] of frame.drones.entries()) {
    if (!drone.target) continue;
    const from = project(drone);
    const to = project(drone.target);
    ctx.save();
    ctx.strokeStyle = colors[index % colors.length];
    ctx.globalAlpha = drone.target.phase === "pickup" ? 0.38 : 0.7;
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
    if (state.zoom >= 1.5) {
      ctx.font = "600 11px sans-serif";
      ctx.fillStyle = "#20242a";
      ctx.fillText(drone.id.replace("drone-", "D"), point.x + 11, point.y + 4);
    }
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
  if (!points.length) {
    return { minX: 0, maxX: 1, minY: 0, maxY: 1 };
  }
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

init();
