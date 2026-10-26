import { buildDynamicRegions, outlinePoints } from "./dynamic_regions.mjs";
import {
  assistanceRoleForDrone,
  assistanceVisuals,
} from "./dynamic_assistance.mjs";
import {
  FrameTimeline,
  frameStepIndex,
} from "./dynamic_timeline.mjs?v=20261021-step";
import { initGraphQueryPanel } from "./dynamic_query.mjs";

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
import {
  escapeHtml,
  statusBadge,
  droneStatusBadge,
  initDisclosures,
  initTabs,
} from "./ui.mjs";

let canvasRenderRequest = 0;

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
  assistanceAutoPaused: false,
  assistanceRoles: new Map(),
  staticBounds: null,
  taskPage: 0,
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
  playSpeed: document.querySelector("#playSpeed"),
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
  currentStep: document.querySelector("#currentStep"),
  stepCaption: document.querySelector("#stepCaption"),
  wallElapsed: document.querySelector("#wallElapsed"),
  frameCaption: document.querySelector("#frameCaption"),
  goLive: document.querySelector("#goLive"),
  timelineProgress: document.querySelector("#timelineProgress"),
  timelineCaption: document.querySelector("#timelineCaption"),
  zoomIn: document.querySelector("#zoomIn"),
  zoomOut: document.querySelector("#zoomOut"),
  zoomReset: document.querySelector("#zoomReset"),
  zoomLabel: document.querySelector("#zoomLabel"),
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
  completionProgress: document.querySelector("#completionProgress"),
  taskFilter: document.querySelector("#taskFilter"),
  taskSearch: document.querySelector("#taskSearch"),
  taskPrev: document.querySelector("#taskPrev"),
  taskNext: document.querySelector("#taskNext"),
  taskPageInfo: document.querySelector("#taskPageInfo"),
  toggleRunDetails: document.querySelector("#toggleRunDetails"),
  runDetailsContent: document.querySelector("#runDetailsContent"),
  llmCaption: document.querySelector("#llmCaption"),
  llmInput: document.querySelector("#llmInput"),
  llmOutput: document.querySelector("#llmOutput"),
};

function init() {
  initDisclosures();
  initTabs(document.querySelector("#statusTabs"));
  bindEvents();
  updateSettingsVisibility();
  void initGraphQueryPanel();
  connect();
  renderEmpty();
}

function bindEvents() {
  els.toggleRunDetails.addEventListener("click", () => {
    const collapse =
      els.toggleRunDetails.getAttribute("aria-expanded") === "true";
    els.runDetailsContent.hidden = collapse;
    document.querySelector("#statusTabs").hidden = collapse;
    els.toggleRunDetails.setAttribute("aria-expanded", String(!collapse));
    els.toggleRunDetails.setAttribute(
      "aria-label",
      `${collapse ? "展开" : "收起"}运行明细`,
    );
    els.toggleRunDetails.querySelector(".collapse-label").textContent = collapse
      ? "展开"
      : "收起";
    els.toggleRunDetails.closest(".data-panel").dataset.collapsed =
      String(collapse);
  });
  els.playPause.addEventListener("click", togglePlayback);
  els.goLive.addEventListener("click", goToLiveFrame);
  els.timelineProgress.addEventListener("pointerdown", beginTimelineScrub);
  els.timelineProgress.addEventListener("input", seekTimeline);
  els.timelineProgress.addEventListener("pointerup", endTimelineScrub);
  els.timelineProgress.addEventListener("pointercancel", endTimelineScrub);
  els.timelineProgress.addEventListener("change", endTimelineScrub);
  els.timelineProgress.addEventListener("keydown", beginKeyboardTimelineScrub);
  els.timelineProgress.addEventListener("keyup", endTimelineScrub);
  document
    .querySelector("#simulationSettings")
    .addEventListener("submit", (event) => {
      event.preventDefault();
      connect();
    });
  els.useLlm.addEventListener("change", updateSettingsVisibility);
  els.backend.addEventListener("change", updateSettingsVisibility);
  for (const control of [els.taskFilter, els.taskSearch]) {
    control.addEventListener(
      control === els.taskSearch ? "input" : "change",
      () => {
        state.taskPage = 0;
        renderTaskTable();
      },
    );
  }
  els.taskPrev.addEventListener("click", () => {
    state.taskPage = Math.max(0, state.taskPage - 1);
    renderTaskTable();
  });
  els.taskNext.addEventListener("click", () => {
    state.taskPage += 1;
    renderTaskTable();
  });
  els.playSpeed.addEventListener("change", restartPlaybackTimer);
  els.zoomPreset.addEventListener("change", () => {
    if (els.zoomPreset.value !== "custom")
      setZoom(Number(els.zoomPreset.value));
  });
  els.zoomIn.addEventListener("click", () => setZoom(state.zoom * 1.2));
  els.zoomOut.addEventListener("click", () => setZoom(state.zoom / 1.2));
  els.zoomReset.addEventListener("click", resetView);
  els.regionBoundaryEnabled.addEventListener(
    "change",
    updateRegionBoundaryStyle,
  );
  els.regionBoundaryWidth.addEventListener("input", updateRegionBoundaryStyle);
  els.regionBoundaryOpacity.addEventListener(
    "input",
    updateRegionBoundaryStyle,
  );
  els.regionBoundaryColor.addEventListener("input", updateRegionBoundaryStyle);
  bindCanvasViewport(els.canvas, state, scheduleCanvasRender);
  window.addEventListener("pointerup", endTimelineScrub);
  window.addEventListener("pointercancel", endTimelineScrub);
  window.addEventListener("resize", scheduleCanvasRender);
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
    state.tasks = payload.tasks ?? [];
    state.regions = payload.regions?.length
      ? payload.regions
      : buildDynamicRegions(state.tasks);
    const staticPoints = [
      ...state.tasks,
      ...state.tasks.map((task) => ({ x: task.pickup_x, y: task.pickup_y })),
      ...state.depots,
      ...state.regions.flatMap(outlinePoints),
    ];
    state.staticBounds = getBounds(staticPoints);
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
    renderRunStatus();
  });
  source.addEventListener("error", () => {
    if (els.connectionStatus.dataset.status !== "done")
      setConnection("reconnecting");
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
    wall_interval: String(
      Math.max(0, Number(els.wallIntervalMs.value) || 0) / 1000,
    ),
    use_llm: els.useLlm.checked ? "1" : "0",
    vllm_api_base: els.vllmApiBase.value.trim() || "http://127.0.0.1:6666/v1",
    vllm_model: els.vllmModel.value.trim() || "qwen2.5-7b",
    llm_max_tokens: normalizedInt(els.llmMaxTokens.value, 512),
  });
  if (els.backend.value) params.set("backend", els.backend.value);
  if (els.rayAddress.value.trim())
    params.set("ray_address", els.rayAddress.value.trim());
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
  state.assistanceAutoPaused = false;
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
  state.playbackTimer = window.setInterval(
    consumeFrame,
    Number(els.playSpeed.value),
  );
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
  return [hours, minutes, seconds]
    .map((part) => String(part).padStart(2, "0"))
    .join(":");
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
    state.timeline.followingLive &&
    !state.streamDone &&
    !state.timelineScrubbing;
  applyFramePayload(payload, { detectAssistance: false });
}

function beginTimelineScrub() {
  state.assistanceAutoPaused = false;
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
  if (
    !["ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown"].includes(
      event.key,
    )
  ) {
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
  state.assistanceAutoPaused = false;
  const payload = state.timeline.goLive();
  if (!payload) return;
  state.playing = !state.streamDone;
  stopPlaybackTimer();
  applyFramePayload(payload, { detectAssistance: false });
}

function applyFramePayload(payload, { detectAssistance = true } = {}) {
  state.frameIndex = payload.frame_index ?? state.frameIndex + 1;
  state.frame = payload.frame;
  state.assistanceRoles = new Map(
    (state.frame?.drones ?? []).map((drone) => [
      drone.id,
      assistanceRoleForDrone(drone, state.frame?.task_states),
    ]),
  );
  state.metrics = payload.metrics ?? state.metrics;
  state.kgEvents = payload.kg_events ?? state.kgEvents;
  state.llmEvents = payload.llm_events ?? state.llmEvents;
  if (state.streamDone && state.timeline.followingLive) applyFinalSummary();
  if (
    detectAssistance &&
    !state.assistancePauseTriggered &&
    [...state.assistanceRoles.values()].some(Boolean)
  ) {
    state.assistancePauseTriggered = true;
    state.assistanceAutoPaused = true;
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
  els.playPause.textContent = state.playing ? "暂停画面" : "继续播放";
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
  state.assistanceAutoPaused = false;
  state.assistanceRoles = new Map();
  state.staticBounds = null;
  state.taskPage = 0;
  renderEmpty();
}

function setConnection(status) {
  const labels = {
    connecting: "正在连接",
    connected: "实时已连接",
    reconnecting: "正在重新连接",
    done: "运行已结束",
    superseded: "运行已被替换",
  };
  els.connectionStatus.textContent = labels[status] ?? status;
  els.connectionStatus.dataset.status = status;
  renderRunStatus();
}

function setZoom(value) {
  state.zoom = clampZoom(value);
  scheduleCanvasRender();
}

function resetView(shouldRender = true) {
  state.zoom = 1;
  state.panX = 0;
  state.panY = 0;
  els.zoomPreset.value = "1";
  if (shouldRender) scheduleCanvasRender();
}

function presetValue(value) {
  const preset = [1, 5, 20, 50].find(
    (entry) => Math.abs(entry - value) < 0.001,
  );
  els.zoomPreset.querySelector('[value="custom"]').textContent =
    `${Math.round(value * 100)}%`;
  return preset ? String(preset) : "custom";
}

function updateRegionBoundaryStyle() {
  state.regionBoundary = {
    enabled: els.regionBoundaryEnabled.checked,
    width: normalizedFloat(els.regionBoundaryWidth.value, 0.8),
    opacity: normalizedFloat(els.regionBoundaryOpacity.value, 0.35),
    color: els.regionBoundaryColor.value || "#26323b",
  };
  scheduleCanvasRender();
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
  els.agentTable.innerHTML =
    '<tr><td colspan="9" class="empty-state">等待无人机状态</td></tr>';
  els.taskCaption.textContent = "-";
  els.taskTable.innerHTML =
    '<tr><td colspan="10" class="empty-state">暂无已下发任务</td></tr>';
  els.taskPageInfo.textContent = "暂无任务";
  els.taskPrev.disabled = true;
  els.taskNext.disabled = true;
  els.completionProgress.style.setProperty("--progress", "0%");
  els.completionProgress.setAttribute("aria-valuenow", "0");
  els.eventCaption.textContent = "-";
  els.eventList.innerHTML = '<li class="empty-state">等待任务事件</li>';
  els.llmCaption.textContent = "等待仿真初始化";
  els.runCaption.textContent = "正在初始化场景";
  els.scenarioCaption.textContent = "正在连接仿真服务，等待场景数据。";
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
  const completed =
    state.frame?.tasks?.completed ?? metrics.completed_tasks ?? 0;
  const releasedTaskStates = Object.values(state.frame?.task_states ?? {});
  const releasedUnfinished = releasedTaskStates.filter(
    (task) => task.status !== "completed",
  ).length;
  const drones = state.frame?.drones ?? [];
  const idleDrones = drones.filter((drone) => drone.status === "idle").length;
  const total = metrics.total_tasks ?? state.tasks.length;
  els.completedTasks.textContent = `${completed} / ${total}`;
  const progress = total ? Math.min(100, (completed / total) * 100) : 0;
  els.completionProgress.style.setProperty("--progress", `${progress}%`);
  els.completionProgress.setAttribute(
    "aria-valuenow",
    String(Math.round(progress)),
  );
  els.releasedUnfinishedTasks.textContent = `已下发未完成 ${releasedUnfinished}`;
  els.droneCount.textContent = String(metrics.drones ?? "-");
  els.idleDroneCount.textContent = `当前空闲 ${idleDrones}`;
  els.lateTasks.textContent = String(metrics.late_tasks ?? 0);
  els.simTime.textContent = state.frame?.time_label ?? "-";
  renderFrameCaption();
  renderTimeline();
  els.zoomLabel.textContent = `${Math.round(state.zoom * 100)}%`;
  const city = state.config.city;
  els.runCaption.textContent = `${city} · ${state.config.day} · ${state.config.drone_count} 架无人机 · ${state.config.task_limit} 个任务`;
  els.scenarioCaption.textContent = `${city} · ${state.config.day} · ${state.config.region_count} 个区域 · ${state.config.llm_enabled ? "LLM 智能调度" : "规则协同调度"}`;
  els.scenarioCaption.title = [
    state.config.backend,
    rayConfigLabel(state.config),
    state.config.coordinator_class,
    state.config.planner,
  ]
    .filter(Boolean)
    .join(" · ");
  els.agentCaption.textContent = `${drones.length} 架无人机 · ${state.config.depot_mode === "region" ? "区域配送站" : "中心配送站"}`;

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
    els.frameCaption.textContent = "等待仿真帧";
    return;
  }
  els.frameCaption.textContent = `第 ${state.frameIndex} 帧 · ${state.stepSeconds ?? "-"} 秒/步 · 待回放 ${state.timeline.pending} 帧`;
}

function renderTimeline() {
  const timeline = state.timeline;
  const step = frameStepIndex(timeline.current);
  const latestStep = frameStepIndex(timeline.frames[timeline.lastIndex]);
  els.currentStep.textContent = step === null ? "—" : String(step);
  els.currentStep.title =
    "当前画面对应的 Step.step_index，可用于查询参数；历史回看时随所选帧更新。";
  els.stepCaption.textContent =
    step !== null && latestStep !== null && step !== latestStep
      ? `step_index · 最新接收 ${latestStep}`
      : "查询用 step_index";
  const hasFrames = timeline.length > 0;
  els.timelineProgress.disabled = !hasFrames;
  els.goLive.disabled = !hasFrames;
  if (!state.timelineScrubbing) {
    els.timelineProgress.max = String(Math.max(0, timeline.lastIndex));
    els.timelineProgress.value = String(Math.max(0, timeline.cursor));
  }
  els.goLive.dataset.active = String(hasFrames && timeline.followingLive);
  els.goLive.textContent =
    state.streamDone && timeline.followingLive ? "末帧" : "实时";
  if (!hasFrames) {
    els.timelineCaption.textContent = "等待首个仿真帧";
    return;
  }

  const currentFrame = timeline.current?.frame_index ?? state.frameIndex;
  const latestFrame =
    timeline.frames[timeline.lastIndex]?.frame_index ?? currentFrame;
  const timeLabel = timeline.current?.frame?.time_label ?? "-";
  const mode = timeline.followingLive
    ? state.streamDone
      ? "已结束"
      : "实时"
    : "回看";
  els.timelineCaption.textContent = `${currentFrame} / ${latestFrame} 帧 · ${timeLabel} · ${mode}`;
}

function renderRunStatus() {
  if (els.connectionStatus.dataset.status === "superseded") {
    els.runStatus.dataset.status = "reviewing";
    els.runStatusTitle.textContent = "本页运行已被替换";
    els.runStatusDetail.textContent =
      "其他连接已启动新运行，当前保留已接收的历史画面。";
    return;
  }
  const completed = state.metrics?.completed_tasks ?? 0;
  const total = state.metrics?.total_tasks ?? state.tasks.length;
  const unfinished =
    state.metrics?.unfinished_tasks ?? Math.max(0, total - completed);

  if (state.timeline.length && !state.timeline.followingLive) {
    const latestFrame =
      state.timeline.frames[state.timeline.lastIndex]?.frame_index ?? "-";
    const title = state.assistanceAutoPaused
      ? "检测到互助 · 画面已暂停"
      : state.timeline.cursor === state.timeline.lastIndex
        ? "画面已暂停"
        : "历史回看";
    els.runStatus.dataset.status = "reviewing";
    els.runStatusTitle.textContent = title;
    els.runStatusDetail.textContent = `${state.assistanceAutoPaused ? "首次互助已暂停画面。" : ""}当前第 ${state.frameIndex} 帧 · 后台已接收至 ${latestFrame} 帧${state.streamDone ? "，运行已结束" : "，仿真继续运行"}`;
    return;
  }

  if (!state.donePayload) {
    els.runStatus.dataset.status = "running";
    els.runStatusTitle.textContent = state.config
      ? "仿真运行中"
      : "等待仿真初始化";
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
  els.runStatusDetail.textContent = `达到最大步数 ${state.donePayload.steps_run ?? state.config?.max_steps ?? "-"}，完成 ${completed}/${total}，仍有 ${unfinished} 个任务未完成`;
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
  els.agentTable.innerHTML =
    drones
      .map((drone) => {
        const role = state.assistanceRoles.get(drone.id);
        const rowClass = role ? assistanceVisuals[role].rowClass : "";
        return `<tr class="${rowClass}">
      <td>${escapeHtml(drone.id)}</td><td>${droneStatusBadge(drone.status)}</td>
      <td>${role ? statusBadge(formatTakeover(drone) === "-" ? assistanceVisuals[role].label : formatTakeover(drone), role === "requester" ? "danger" : "success") : escapeHtml(formatTakeover(drone))}</td>
      <td>${escapeHtml(drone.region_id)}</td><td>${drone.queue?.length ?? 0}</td><td>${formatOnboard(drone)}</td>
      <td>${formatBattery(drone)}</td><td>${drone.completed}</td><td>${Math.round(drone.distance_m).toLocaleString()} m</td>
    </tr>`;
      })
      .join("") ||
    '<tr><td colspan="9" class="empty-state">等待无人机状态</td></tr>';
}

function formatOnboard(drone) {
  const onboard = drone.onboard_count ?? drone.onboard?.length ?? 0;
  const capacity = drone.cargo_capacity ?? 3;
  return `${onboard}/${capacity}`;
}

function formatBattery(drone) {
  const remaining = Number(drone.battery_remaining_m);
  const capacity = Number(drone.battery_capacity_m);
  if (
    !Number.isFinite(remaining) ||
    !Number.isFinite(capacity) ||
    capacity <= 0
  )
    return "—";
  const level = Math.max(0, Math.min(100, (remaining / capacity) * 100));
  return `<span class="battery-cell" data-low="${level <= 20}"><span>${(remaining / 1000).toFixed(1)} / ${(capacity / 1000).toFixed(0)} km</span><i class="battery-track" style="--level: ${level}%"></i></span>`;
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
  const released = state.tasks
    .map((task, index) => ({
      task,
      index,
      taskState: taskStates[task.id] ?? { status: "waiting" },
    }))
    .filter(
      ({ task }) =>
        Number.isFinite(currentMinute) && task.release <= currentMinute,
    );
  const filter = els.taskFilter.value;
  const search = els.taskSearch.value.trim().toLowerCase();
  const tasks = released.filter(({ task, taskState }) => {
    if (filter === "unfinished" && taskState.status === "completed")
      return false;
    if (filter === "completed" && taskState.status !== "completed")
      return false;
    if (
      filter === "assistance" &&
      !taskState.assistance_reason &&
      !taskState.assistance_source_drone_id &&
      !taskState.handover_from_drone_id
    )
      return false;
    return (
      !search ||
      [task.id, task.region_id, taskState.assigned_to].some((value) =>
        String(value ?? "")
          .toLowerCase()
          .includes(search),
      )
    );
  });
  const pages = Math.max(1, Math.ceil(tasks.length / 50));
  state.taskPage = Math.min(Math.max(0, state.taskPage), pages - 1);
  const start = state.taskPage * 50;
  els.taskCaption.textContent = `${released.length} 已下发 · ${released.filter(({ taskState }) => taskState.status === "completed").length} 已完成`;
  els.taskTable.innerHTML =
    tasks
      .slice(start, start + 50)
      .map(({ task, index, taskState }) => {
        const tone =
          taskState.status === "completed"
            ? "success"
            : taskState.picked_up || taskState.status === "picked_up"
              ? "active"
              : taskState.status === "assigned"
                ? "warning"
                : "neutral";
        const assistance = taskAssistanceLabel(taskState);
        return `<tr><td>${index + 1}</td><td>${escapeHtml(task.id)}</td><td>${escapeHtml(task.region_id)}</td>
      <td>${formatMinuteValue(task.release)}</td><td>${formatMinuteValue(task.deadline)}</td>
      <td>${statusBadge(taskStatusLabel(taskState), tone)}</td><td>${assistance === "-" ? "—" : statusBadge(assistance, "warning")}</td>
      <td>${escapeHtml(taskState.assigned_to)}</td><td>${formatMinuteValue(taskState.completed_at)}</td>
      <td>${taskState.status === "completed" ? statusBadge(taskTimelinessLabel(taskState), taskState.late ? "danger" : "success") : "—"}</td></tr>`;
      })
      .join("") ||
    '<tr><td colspan="10" class="empty-state">当前筛选下没有任务</td></tr>';
  els.taskPageInfo.textContent = tasks.length
    ? `共 ${tasks.length} 条 · 第 ${start + 1}–${Math.min(start + 50, tasks.length)} 条 · ${state.taskPage + 1}/${pages} 页`
    : "当前筛选下暂无任务";
  els.taskPrev.disabled = state.taskPage === 0;
  els.taskNext.disabled = state.taskPage >= pages - 1;
}

function taskStatusLabel(taskState) {
  if (taskState.status === "completed") return "已完成";
  if (taskState.handover_from_drone_id && !taskState.picked_up)
    return "前往交接";
  if (taskState.handover_from_drone_id && taskState.picked_up)
    return "接管配送";
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
  return [hour, minute, second]
    .map((part) => String(part).padStart(2, "0"))
    .join(":");
}

function renderEvents() {
  const events = state.kgEvents.slice(-8).reverse();
  const labels = {
    task_assigned: "任务已派发",
    task_completed: "任务已送达",
    task_picked_up: "已取货，开始配送",
    task_assistance_requested: "任务请求支援",
    assist_dispatched: "援助无人机已出发",
    task_handover_completed: "任务交接完成",
    task_taken_over: "任务已接管",
    drone_low_battery: "无人机电量偏低",
    drone_returning_to_charge: "无人机返航充电",
    drone_charged: "无人机充电完成",
  };
  els.eventCaption.textContent = `${events.length} 条`;
  els.eventList.innerHTML =
    events
      .map(
        (event) =>
          `<li><strong>${escapeHtml(labels[event.type] ?? event.type)}</strong><span>${escapeHtml(formatEvent(event))}</span></li>`,
      )
      .join("") || '<li class="empty-state">暂无近期事件</li>';
}

function renderLlmEvents() {
  const latest = state.llmEvents.at(-1);
  if (!latest) {
    const logName = state.config?.llm_log_path
      ? ` · ${basename(state.config.llm_log_path)}`
      : "";
    els.llmCaption.textContent = state.config?.llm_enabled
      ? `等待 LLM 调用${logName}`
      : "未启用智能调度 · 可在场景设置中开启";
    els.llmInput.textContent = "-";
    els.llmOutput.textContent = "-";
    return;
  }

  const status = latest.used_fallback ? "已回退规则调度" : "调用成功";
  const elapsed = latest.elapsed_ms == null ? "-" : `${latest.elapsed_ms} ms`;
  const logName = state.config?.llm_log_path
    ? ` · ${basename(state.config.llm_log_path)}`
    : "";
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
    event.region_id != null ? `区域 ${event.region_id}` : null,
    event.task_id ? `任务 ${event.task_id}` : null,
    event.source_drone_id && event.helper_drone_id
      ? `${event.source_drone_id} → ${event.helper_drone_id}`
      : null,
    { failure: "故障接管", low_battery: "低电支援", deadline_risk: "时效支援" }[
      event.reason
    ] ??
      event.reason ??
      null,
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
  const { ctx, width, height } = prepareCanvas(els.canvas);
  const bounds = state.staticBounds;
  const points = bounds
    ? [
        { x: bounds.minX, y: bounds.minY },
        { x: bounds.maxX, y: bounds.maxY },
        ...frame.drones,
      ]
    : frame.drones;
  const project = makeProjector(
    getBounds(points),
    width,
    height,
    state.zoom,
    state.panX,
    state.panY,
  );
  drawGrid(ctx, width, height);
  drawRegionOutlines(ctx, project, currentRegions());
  drawDepots(ctx, project);
  drawPickupLinks(ctx, project, frame);
  drawTasks(ctx, project, frame);
  drawRoutes(ctx, project, frame);
  drawDrones(ctx, project, frame);
}

function drawEmptyCanvas() {
  const { ctx, width, height } = prepareCanvas(els.canvas);
  drawGrid(ctx, width, height);
  ctx.fillStyle = "#a0acc2";
  ctx.font = "13px sans-serif";
  ctx.textAlign = "center";
  ctx.fillText("正在连接仿真，等待首帧数据", width / 2, height / 2);
  ctx.textAlign = "start";
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
  return state.regions;
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
    if (status === "completed" || status === "unreleased") continue;
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
    ctx.fillStyle = taskColors[status] ?? taskColors.waiting;
    ctx.arc(point.x, point.y, state.zoom >= 5 ? 4.5 : 3.2, 0, Math.PI * 2);
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
    const role = state.assistanceRoles.get(drone.id);
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

function updateSettingsVisibility() {
  document.querySelector("#llmSettings").hidden = !els.useLlm.checked;
  document.querySelector("#raySettings").hidden = els.backend.value === "local";
}

function scheduleCanvasRender() {
  els.zoomLabel.textContent = `${Math.round(state.zoom * 100)}%`;
  els.zoomPreset.value = presetValue(state.zoom);
  if (canvasRenderRequest) return;
  canvasRenderRequest = requestAnimationFrame(() => {
    canvasRenderRequest = 0;
    renderCanvas();
  });
}

init();
