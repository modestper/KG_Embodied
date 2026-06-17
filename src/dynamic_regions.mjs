export function buildDynamicRegions(tasks = [], padding = 120) {
  const grouped = new Map();
  for (const task of tasks) {
    if (task?.region_id == null || !Number.isFinite(task.x) || !Number.isFinite(task.y)) continue;
    const regionTasks = grouped.get(task.region_id) ?? [];
    regionTasks.push(task);
    grouped.set(task.region_id, regionTasks);
  }

  return [...grouped.entries()]
    .sort(([left], [right]) => String(left).localeCompare(String(right)))
    .map(([regionId, regionTasks]) => {
      const centroid = {
        x: regionTasks.reduce((sum, task) => sum + task.x, 0) / regionTasks.length,
        y: regionTasks.reduce((sum, task) => sum + task.y, 0) / regionTasks.length,
      };
      const hull = convexHull(regionTasks.map((task) => ({ x: task.x, y: task.y })));
      const outline = hull.length < 3 ? circleOutline(centroid, padding) : paddedOutline(hull, centroid, padding);
      return {
        region_id: regionId,
        task_count: regionTasks.length,
        centroid: roundPoint(centroid),
        outline: outline.map((point) => [round(point.x), round(point.y)]),
      };
    });
}

export function outlinePoints(region) {
  return (region?.outline ?? [])
    .map(([x, y]) => ({ x, y }))
    .filter((point) => Number.isFinite(point.x) && Number.isFinite(point.y));
}

function convexHull(points) {
  const unique = [...new Map(points.map((point) => [`${point.x}:${point.y}`, point])).values()].sort(
    (left, right) => left.x - right.x || left.y - right.y,
  );
  if (unique.length <= 1) return unique;

  const lower = [];
  for (const point of unique) {
    while (lower.length >= 2 && cross(lower.at(-2), lower.at(-1), point) <= 0) lower.pop();
    lower.push(point);
  }

  const upper = [];
  for (const point of [...unique].reverse()) {
    while (upper.length >= 2 && cross(upper.at(-2), upper.at(-1), point) <= 0) upper.pop();
    upper.push(point);
  }

  return lower.slice(0, -1).concat(upper.slice(0, -1));
}

function cross(origin, left, right) {
  return (left.x - origin.x) * (right.y - origin.y) - (left.y - origin.y) * (right.x - origin.x);
}

function paddedOutline(points, centroid, padding) {
  return points.map((point) => {
    const dx = point.x - centroid.x;
    const dy = point.y - centroid.y;
    const length = Math.hypot(dx, dy);
    if (length === 0) return point;
    return {
      x: point.x + (dx / length) * padding,
      y: point.y + (dy / length) * padding,
    };
  });
}

function circleOutline(centroid, radius, segments = 12) {
  return Array.from({ length: segments }, (_, index) => {
    const angle = (Math.PI * 2 * index) / segments;
    return {
      x: centroid.x + Math.cos(angle) * radius,
      y: centroid.y + Math.sin(angle) * radius,
    };
  });
}

function roundPoint(point) {
  return { x: round(point.x), y: round(point.y) };
}

function round(value) {
  return Math.round((value + Number.EPSILON) * 1000) / 1000;
}
