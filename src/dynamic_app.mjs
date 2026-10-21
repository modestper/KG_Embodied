import { buildDynamicRegions, outlinePoints } from "./dynamic_regions.mjs";
import {
  assistanceRoleForDrone,
  assistanceVisuals,
  frameHasActiveAssistance,
} from "./dynamic_assistance.mjs";
import { FrameTimeline } from "./dynamic_timeline.mjs";
import { initGraphQueryPanel } from "./dynamic_query.mjs";

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
  timeline: new FrameTimeline(),
  timelineScrubbing: false,
  frameIndex: 0,
  stepSeconds: null,
  playing: true,
  playbackTimer: null,
  streamDone: false,
  donePayload: null,
  wallStartedAt: null,
  wallElapsedMs: 0,
  wallTimer: null,
  assistancePauseTriggered: false,
  zoom: 1,
  panX: 0,
  panY: 0,
  drag: null,
  regionBoundary: {
    enabled: true,
    width: 0.8,
    opacity: 0.35,
    color: "#26323b",
  },
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
  goLive: document.querySelector("#goLive"),
  timelineProgress: document.querySelector("#timelineProgress"),
  timelineCaption: document.querySelector("#timelineCaption"),
  zoomIn: document.querySelector("#zoomIn"),
  zoomOut: document.querySelector("#zoomOut"),
  zoomReset: document.querySelector("#zoomReset"),
  zoomLabel: document.querySelector("#zoomLabel"),
  regionBoundaryMenu: document.querySelector("#regionBoundaryMenu"),
  regionBoundaryEnabled: document.querySelector("#regionBoundaryEnabled"),
  regionBoundaryWidth: document.querySelector("#regionBoundaryWidth"),
  regionBoundaryOpacity: document.querySelector("#regionBoundaryOpacity"),
  regionBoundaryColor: document.querySelector("#regionBoundaryColor"),
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
  void initGraphQueryPanel();
  connect();
  renderEmpty();
}

function bindEvents() {
  els.playPause.addEventListener("click", togglePlayback);
  els.goLive.addEventListener("click", goToLiveFrame);
  els.timelineProgress.addEventListener("pointerdown", beginTimelineScrub);
  els.timelineProgress.addEventListener("input", seekTimeline);
  els.timelineProgress.addEventListener("pointerup", endTimelineScrub);
  els.timelineProgress.addEventListener("pointercancel", endTimelineScrub);
  els.timelineProgress.addEventListener("change", endTimelineScrub);
  els.timelineProgress.addEventListener("keydown", beginKeyboardTimelineScrub);
  els.timelineProgress.addEventListener("keyup", endTimelineScrub);
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
  els.regionBoundaryEnabled.addEventListener("change", updateRegionBoundaryStyle);
  els.regionBoundaryWidth.addEventListener("input", updateRegionBoundaryStyle);
  els.regionBoundaryOpacity.addEventListener("input", updateRegionBoundaryStyle);
  els.regionBoundaryColor.addEventListener("input", updateRegionBoundaryStyle);
  els.canvas.addEventListener("pointerdown", startPan);
  els.canvas.addEventListener("pointermove", movePan);
  els.canvas.addEventListener("pointerup", endPan);
  els.canvas.addEventListener("pointerleave", endPan);
  els.canvas.addEventListener("wheel", zoomAtPointer, { passive: false });
  window.addEventListener("pointerup", endTimelineScrub);
  window.addEventListener("pointercancel", endTimelineScrub);
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
    if (!state.playing) state.timeline.hold();
    const livePayload = state.timeline.append(payload);
    if (state.playing && livePayload) {
      applyFramePayload(livePayload);
    } else {
      renderTimeline();
      renderFrameCaption();
      renderRunStatus();
    }
  });
  source.addEventListener("done", (event) => {
    const payload = JSON.parse(event.data);
    state.streamDone = true;
    state.donePayload = payload;
    if (state.timeline.followingLive) {
      applyFinalSummary();
      state.playing = false;
      stopPlaybackTimer();
    }
    stopWallTimer();
    setConnection("done");
    source.close();
    render();
  });
  source.addEventListener("superseded", () => {
    source.close();
    if (state.source !== source) return;
    state.playing = false;
    state.timeline.hold();
    stopPlaybackTimer();
    stopWallTimer();
    setConnection("superseded");
    renderPlaybackButton();
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
  if (state.streamDone && state.timeline.followingLive) return;
  if (state.playing) {
    state.playing = false;
    state.timeline.hold();
    stopPlaybackTimer();
  } else {
    state.playing = true;
    if (state.timeline.cursor === state.timeline.lastIndex) {
      const payload = state.timeline.goLive();
      if (payload) applyFramePayload(payload, { detectAssistance: false });
    }
    restartPlaybackTimer();
  }
  renderPlaybackButton();
  renderTimeline();
  renderRunStatus();
}

function restartPlaybackTimer() {
  stopPlaybackTimer();
  if (!state.playing || state.timeline.followingLive) {
    renderPlaybackButton();
    return;
  }
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
  const payload = state.timeline.advance();
  if (!payload) {
    if (state.streamDone) {
      state.playing = false;
      stopPlaybackTimer();
      renderPlaybackButton();
    }
    return;
  }
  applyFramePayload(payload);
  if (state.timeline.followingLive) {
    stopPlaybackTimer();
    if (state.streamDone) {
      state.playing = false;
      renderPlaybackButton();
      renderTimeline();
    }
  }
}

function seekTimeline() {
  const payload = state.timeline.seek(Number(els.timelineProgress.value));
  if (!payload) return;
  stopPlaybackTimer();
  if (state.timelineScrubbing) state.timeline.hold();
  state.playing =
    state.timeline.followingLive && !state.streamDone && !state.timelineScrubbing;
  applyFramePayload(payload, { detectAssistance: false });
}

function beginTimelineScrub() {
  if (!state.timeline.length) return;
  state.timelineScrubbing = true;
  state.playing = false;
  state.timeline.hold();
  stopPlaybackTimer();
  renderPlaybackButton();
  renderTimeline();
  renderRunStatus();
}

function beginKeyboardTimelineScrub(event) {
  if (!["ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown"].includes(event.key)) {
    return;
  }
  beginTimelineScrub();
}

function endTimelineScrub() {
  if (!state.timelineScrubbing) return;
  const endedAtRightEdge =
    Number(els.timelineProgress.value) === Number(els.timelineProgress.max);
  state.timelineScrubbing = false;
  if (endedAtRightEdge) {
    goToLiveFrame();
    return;
  }
  renderTimeline();
  renderRunStatus();
}

function goToLiveFrame() {
  const payload = state.timeline.goLive();
  if (!payload) return;
  state.playing = !state.streamDone;
  stopPlaybackTimer();
  applyFramePayload(payload, { detectAssistance: false });
}

function applyFramePayload(payload, { detectAssistance = true } = {}) {
  state.frameIndex = payload.frame_index ?? state.frameIndex + 1;
  state.frame = payload.frame;
  state.metrics = payload.metrics ?? state.metrics;
  state.kgEvents = payload.kg_events ?? state.kgEvents;
  state.llmEvents = payload.llm_events ?? state.llmEvents;
  if (state.streamDone && state.timeline.followingLive) applyFinalSummary();
  if (
    detectAssistance &&
    !state.assistancePauseTriggered &&
    frameHasActiveAssistance(state.frame)
  ) {
    state.assistancePauseTriggered = true;
    state.playing = false;
    state.timeline.hold();
    stopPlaybackTimer();
  }
  render();
}

function applyFinalSummary() {
  state.metrics = state.donePayload?.metrics ?? state.metrics;
  state.kgEvents = state.donePayload?.kg_events ?? state.kgEvents;
  state.llmEvents = state.donePayload?.llm_events ?? state.llmEvents;
}

function renderPlaybackButton() {
  els.playPause.textContent = state.playing ? "暂停" : "播放";
  els.playPause.disabled = state.streamDone && state.timeline.followingLive;
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
  state.timeline.reset();
  state.timelineScrubbing = false;
  state.frameIndex = 0;
  state.stepSeconds = null;
  state.streamDone = false;
  state.donePayload = null;
  state.wallStartedAt = null;
  state.wallElapsedMs = 0;
  state.assistancePauseTriggered = false;
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

function updateRegionBoundaryStyle() {
  state.regionBoundary = {
    enabled: els.regionBoundaryEnabled.checked,
    width: normalizedFloat(els.regionBoundaryWidth.value, 0.8),
    opacity: normalizedFloat(els.regionBoundaryOpacity.value, 0.35),
    color: els.regionBoundaryColor.value || "#26323b",
  };
  render();
}

function normalizedFloat(value, fallback) {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function renderEmpty() {
  els.completedTasks.textContent = "-";
  els.releasedUnfinishedTasks.textContent = "已下发未完成 -";
  els.droneCount.textContent = "-";
  els.idleDroneCount.textContent = "当前空闲 -";
  els.lateTasks.textContent = "-";
  els.simTime.textContent = "-";
  renderWallElapsed();
  renderFrameCaption();
  renderTimeline();
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
  renderFrameCaption();
  renderTimeline();
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
  renderPlaybackButton();
  renderCanvas();
}

function renderFrameCaption() {
  if (!state.timeline.length) {
    els.frameCaption.textContent = "frame 0";
    return;
  }
  els.frameCaption.textContent = `frame ${state.frameIndex} · ${state.stepSeconds ?? "-"}s/step · 待显示 ${state.timeline.pending}`;
}

function renderTimeline() {
  const timeline = state.timeline;
  const hasFrames = timeline.length > 0;
  els.timelineProgress.disabled = !hasFrames;
  els.goLive.disabled = !hasFrames;
  if (!state.timelineScrubbing) {
    els.timelineProgress.max = String(Math.max(0, timeline.lastIndex));
    els.timelineProgress.value = String(Math.max(0, timeline.cursor));
  }
  els.goLive.dataset.active = String(hasFrames && timeline.followingLive);
  els.goLive.textContent = state.streamDone && timeline.followingLive ? "末帧" : "实时";
  if (!hasFrames) {
    els.timelineCaption.textContent = "等待首个仿真帧";
    return;
  }

  const currentFrame = timeline.current?.frame_index ?? state.frameIndex;
  const latestFrame = timeline.frames[timeline.lastIndex]?.frame_index ?? currentFrame;
  const timeLabel = timeline.current?.frame?.time_label ?? "-";
  const mode = timeline.followingLive
    ? state.streamDone
      ? "已结束"
      : "实时"
    : "回看";
  els.timelineCaption.textContent = `frame ${currentFrame}/${latestFrame} · ${timeLabel} · ${mode}`;
}

function renderRunStatus() {
  const completed = state.metrics?.completed_tasks ?? 0;
  const total = state.metrics?.total_tasks ?? state.tasks.length;
  const unfinished = state.metrics?.unfinished_tasks ?? Math.max(0, total - completed);

  if (state.timeline.length && !state.timeline.followingLive) {
    const latestFrame = state.timeline.frames[state.timeline.lastIndex]?.frame_index ?? "-";
    const title = state.timeline.cursor === state.timeline.lastIndex ? "画面已暂停" : "历史回看";
    els.runStatus.dataset.status = "reviewing";
    els.runStatusTitle.textContent = title;
    els.runStatusDetail.textContent = `当前 frame ${state.frameIndex}，后台已接收至 frame ${latestFrame}`;
    return;
  }

  if (!state.donePayload) {
    els.runStatus.dataset.status = "running";
    els.runStatusTitle.textContent = state.config ? "仿真运行中" : "等待仿真初始化";
    els.runStatusDetail.textContent = state.config
      ? `已完成 ${completed}/${total}，其中迟到完成 ${state.metrics?.late_tasks ?? 0}`
      : "等待实时帧数据";
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
  const taskStates = state.frame?.task_states ?? {};
  els.agentTable.innerHTML = drones
    .map(
      (drone) => {
        const role = assistanceRoleForDrone(drone, taskStates);
        const rowClass = role ? assistanceVisuals[role].rowClass : "";
        return `
        <tr class="${rowClass}">
          <td>${drone.id}</td>
          <td>${drone.status}</td>
          <td>${formatTakeover(drone)}</td>
          <td>${drone.region_id ?? "-"}</td>
          <td>${drone.queue.length}</td>
          <td>${formatOnboard(drone)}</td>
          <td>${formatBattery(drone)}</td>
          <td>${drone.completed}</td>
          <td>${Math.round(drone.distance_m).toLocaleString()} m</td>
        </tr>
      `;
      },
    )
    .join("");
}

function formatOnboard(drone) {
  const onboard = drone.onboard_count ?? drone.onboard?.length ?? 0;
  const capacity = drone.cargo_capacity ?? 3;
  return `${onboard}/${capacity}`;
}

function formatBattery(drone) {
  const remaining = Number(drone.battery_remaining_m);
  const capacity = Number(drone.battery_capacity_m);
  if (!Number.isFinite(remaining) || !Number.isFinite(capacity) || capacity <= 0) return "-";
  return `${(remaining / 1000).toFixed(1)} / ${(capacity / 1000).toFixed(0)} km`;
}

function formatTakeover(drone) {
  if (drone.failure_mode) return `故障:${drone.failure_mode}`;
  if (drone.takeover_mode === "requesting_help") return "求助中";
  if (drone.takeover_mode === "assisting") return "支援中";
  if (drone.takeover_mode === "assisted") return "已获助";
  return "-";
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
          <td>${taskAssistanceLabel(taskState)}</td>
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
  if (taskState.handover_from_drone_id && !taskState.picked_up) return "前往交接";
  if (taskState.handover_from_drone_id && taskState.picked_up) return "接管配送";
  if (taskState.status === "picked_up" || taskState.picked_up) return "配送中";
  if (taskState.status === "assigned") return "前往取货";
  return "等待派单";
}

function taskAssistanceLabel(taskState) {
  const labels = {
    failure: "故障接管",
    low_battery: "低电接管",
    deadline_risk: "时效支援",
  };
  return labels[taskState.assistance_reason] ?? "-";
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
    event.source_drone_id && event.helper_drone_id
      ? `${event.source_drone_id} → ${event.helper_drone_id}`
      : null,
    event.reason ?? null,
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
  if (!regions.length || !state.regionBoundary.enabled) return;
  ctx.save();
  ctx.lineWidth = state.regionBoundary.width;
  ctx.globalAlpha = state.regionBoundary.opacity;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  for (const region of regions) {
    const points = outlinePoints(region);
    if (points.length < 2) continue;
    ctx.strokeStyle = state.regionBoundary.color;
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
    const role = assistanceRoleForDrone(drone, frame.task_states);
    ctx.save();
    ctx.fillStyle = colors[index % colors.length];
    ctx.beginPath();
    ctx.moveTo(point.x, point.y - 9);
    ctx.lineTo(point.x + 9, point.y + 8);
    ctx.lineTo(point.x, point.y + 4);
    ctx.lineTo(point.x - 9, point.y + 8);
    ctx.closePath();
    ctx.fill();
    if (role) {
      ctx.lineJoin = "round";
      ctx.strokeStyle = assistanceVisuals[role].borderColor;
      ctx.lineWidth = 9;
      ctx.stroke();
    } else {
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 3;
      ctx.stroke();
    }
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
  roundRect(ctx, 18, height - 98, 330, 78, 6);
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
    const x = 32 + index * 74;
    ctx.fillStyle = color;
    ctx.beginPath();
    if (label === "depot") {
      ctx.rect(x - 4, height - 78, 8, 8);
      ctx.fill();
    } else {
      ctx.arc(x, height - 74, 4, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.fillStyle = "#3e4650";
    ctx.fillText(label, x - 12, height - 55);
  }
  const assistanceItems = [
    [assistanceVisuals.requester.borderColor, assistanceVisuals.requester.label],
    [assistanceVisuals.helper.borderColor, assistanceVisuals.helper.label],
  ];
  for (const [index, [color, label]] of assistanceItems.entries()) {
    const x = 32 + index * 148;
    const y = height - 34;
    ctx.strokeStyle = color;
    ctx.lineWidth = 4;
    ctx.beginPath();
    ctx.moveTo(x, y - 7);
    ctx.lineTo(x + 7, y + 6);
    ctx.lineTo(x, y + 3);
    ctx.lineTo(x - 7, y + 6);
    ctx.closePath();
    ctx.stroke();
    ctx.fillStyle = "#3e4650";
    ctx.fillText(label, x + 13, y + 4);
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
