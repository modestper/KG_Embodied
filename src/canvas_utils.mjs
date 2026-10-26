export const colors = [
  "#159779",
  "#4d78e6",
  "#d3a147",
  "#9372d0",
  "#ce637a",
  "#648b9b",
];
export const taskColors = Object.freeze({
  unreleased: "#cbd5e1",
  waiting: "#159779",
  assigned: "#e6aa3d",
  picked_up: "#4d78e6",
  completed: "#8b5cf6",
});

export function prepareCanvas(canvas) {
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(1, rect.width);
  const height = Math.max(1, rect.height);
  const ratio = window.devicePixelRatio || 1;
  const pixelWidth = Math.max(1, Math.round(width * ratio));
  const pixelHeight = Math.max(1, Math.round(height * ratio));
  if (canvas.width !== pixelWidth) canvas.width = pixelWidth;
  if (canvas.height !== pixelHeight) canvas.height = pixelHeight;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(pixelWidth / width, 0, 0, pixelHeight / height, 0, 0);
  ctx.globalAlpha = 1;
  ctx.lineWidth = 1;
  ctx.setLineDash([]);
  ctx.clearRect(0, 0, width, height);
  return { ctx, width, height };
}

export function drawGrid(ctx, width, height) {
  ctx.save();
  ctx.strokeStyle = "#edf0f7";
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

export function getBounds(points) {
  const valid = points.filter(
    (point) => point && Number.isFinite(point.x) && Number.isFinite(point.y),
  );
  if (!valid.length) return { minX: 0, maxX: 1, minY: 0, maxY: 1 };
  return valid.reduce(
    (bounds, point) => ({
      minX: Math.min(bounds.minX, point.x),
      maxX: Math.max(bounds.maxX, point.x),
      minY: Math.min(bounds.minY, point.y),
      maxY: Math.max(bounds.maxY, point.y),
    }),
    { minX: valid[0].x, maxX: valid[0].x, minY: valid[0].y, maxY: valid[0].y },
  );
}

export function makeProjector(
  bounds,
  width,
  height,
  zoom = 1,
  panX = 0,
  panY = 0,
) {
  const pad = Math.min(42, width / 8, height / 8);
  const xRange = bounds.maxX - bounds.minX || 1;
  const yRange = bounds.maxY - bounds.minY || 1;
  const scale =
    Math.min((width - pad * 2) / xRange, (height - pad * 2) / yRange) *
    0.86 *
    zoom;
  const xOffset = (width - xRange * scale) / 2;
  const yOffset = (height - yRange * scale) / 2;
  return (point) => ({
    x: xOffset + (point.x - bounds.minX) * scale + panX,
    y: height - yOffset - (point.y - bounds.minY) * scale + panY,
  });
}

export function clampZoom(value) {
  return Math.min(50, Math.max(0.25, value));
}

export function bindCanvasViewport(canvas, state, onChange) {
  let drag = null;
  const end = (event) => {
    drag = null;
    if (canvas.hasPointerCapture(event.pointerId))
      canvas.releasePointerCapture(event.pointerId);
  };
  canvas.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    drag = {
      x: event.clientX,
      y: event.clientY,
      panX: state.panX,
      panY: state.panY,
    };
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!drag) return;
    state.panX = drag.panX + event.clientX - drag.x;
    state.panY = drag.panY + event.clientY - drag.y;
    onChange();
  });
  canvas.addEventListener("pointerup", end);
  canvas.addEventListener("pointercancel", end);
  canvas.addEventListener("lostpointercapture", () => {
    drag = null;
  });
  canvas.addEventListener(
    "wheel",
    (event) => {
      event.preventDefault();
      const nextZoom = clampZoom(
        state.zoom * (event.deltaY < 0 ? 1.12 : 1 / 1.12),
      );
      const rect = canvas.getBoundingClientRect();
      const x = event.clientX - rect.left - rect.width / 2;
      const y = event.clientY - rect.top - rect.height / 2;
      const ratio = nextZoom / state.zoom;
      state.panX = x - (x - state.panX) * ratio;
      state.panY = y - (y - state.panY) * ratio;
      state.zoom = nextZoom;
      onChange();
    },
    { passive: false },
  );
  new ResizeObserver(onChange).observe(canvas);
}

export function roundRect(ctx, x, y, width, height, radius) {
  ctx.beginPath();
  ctx.moveTo(x + radius, y);
  ctx.arcTo(x + width, y, x + width, y + height, radius);
  ctx.arcTo(x + width, y + height, x, y + height, radius);
  ctx.arcTo(x, y + height, x, y, radius);
  ctx.arcTo(x, y, x + width, y, radius);
  ctx.closePath();
}

export function resolveTaskStatus(task, frame) {
  if (
    Number.isFinite(task.release) &&
    Number.isFinite(frame.time_minute) &&
    task.release > frame.time_minute
  )
    return "unreleased";
  if (
    typeof task.release === "string" &&
    typeof frame.time_label === "string" &&
    task.release > frame.time_label
  )
    return "unreleased";
  const status =
    frame.task_states?.[task.id]?.status ?? frame.task_statuses?.[task.id];
  if (status) return status;
  if (
    typeof task.completed_at === "string" &&
    task.completed_at <= frame.time_label
  )
    return "completed";
  return "waiting";
}
