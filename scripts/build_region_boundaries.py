import argparse
import json
import math
import sys
from pathlib import Path

VIEWER_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = VIEWER_ROOT.parents[0]
sys.path.insert(0, str(VIEWER_ROOT))

from scripts.build_delivery_sim import CITY_FILES
from simulation.delivery_sim import load_delivery_tasks

OUT = VIEWER_ROOT / "data" / "region_boundaries.json"
TASK_LIMIT = 10_000_000
DEFAULT_SIMPLIFY_TOLERANCE = 50.0
DEFAULT_OVERLAP_THRESHOLD = 0.01


def build_voronoi_regions(
    grouped_points,
    padding=240,
    simplify_tolerance=DEFAULT_SIMPLIFY_TOLERANCE,
    overlap_threshold=DEFAULT_OVERLAP_THRESHOLD,
    quiet=True,
):
    all_points = [point for points in grouped_points.values() for point in points]
    if not all_points:
        return []

    bounds = padded_bounds(all_points, padding)
    centers = {
        region_id: {
            "x": sum(point["x"] for point in points) / len(points),
            "y": sum(point["y"] for point in points) / len(points),
            "count": len(points),
        }
        for region_id, points in grouped_points.items()
        if points
    }

    raw_regions = []
    for region_id in sorted(centers):
        polygon = bounds_polygon(bounds)
        center = centers[region_id]
        for other_id, other in centers.items():
            if other_id == region_id:
                continue
            polygon = clip_to_nearest_center(polygon, center, other)
            if not polygon:
                break
        raw_regions.append(
            {
                "region_id": region_id,
                "task_count": center["count"],
                "centroid": {"x": round(center["x"], 3), "y": round(center["y"], 3)},
                "polygon": polygon_to_coords(polygon),
            }
        )

    dissolved = dissolve_regions(raw_regions)
    regions = simplify_regions_with_overlap_guard(dissolved, simplify_tolerance, overlap_threshold)
    warnings = overlap_warnings(regions, overlap_threshold)
    for warning in warnings:
        print(warning, file=sys.stderr)
    if not quiet:
        print_point_count_stats(dissolved, regions)
    return regions


def padded_bounds(points, padding):
    xs = [point["x"] for point in points]
    ys = [point["y"] for point in points]
    return {
        "min_x": min(xs) - padding,
        "max_x": max(xs) + padding,
        "min_y": min(ys) - padding,
        "max_y": max(ys) + padding,
    }


def bounds_polygon(bounds):
    return [
        {"x": bounds["min_x"], "y": bounds["min_y"]},
        {"x": bounds["max_x"], "y": bounds["min_y"]},
        {"x": bounds["max_x"], "y": bounds["max_y"]},
        {"x": bounds["min_x"], "y": bounds["max_y"]},
    ]


def polygon_to_coords(polygon):
    return [[round(point["x"], 3), round(point["y"], 3)] for point in polygon]


def clip_to_nearest_center(polygon, center, other):
    if not polygon:
        return []
    clipped = []
    previous = polygon[-1]
    previous_inside = is_nearer(previous, center, other)
    for current in polygon:
        current_inside = is_nearer(current, center, other)
        if current_inside:
            if not previous_inside:
                clipped.append(bisector_intersection(previous, current, center, other))
            clipped.append(current)
        elif previous_inside:
            clipped.append(bisector_intersection(previous, current, center, other))
        previous = current
        previous_inside = current_inside
    return clipped


def is_nearer(point, center, other):
    return squared_distance(point, center) <= squared_distance(point, other)


def squared_distance(left, right):
    return (left["x"] - right["x"]) ** 2 + (left["y"] - right["y"]) ** 2


def bisector_intersection(start, end, center, other):
    dx = end["x"] - start["x"]
    dy = end["y"] - start["y"]
    a = 2 * (other["x"] - center["x"])
    b = 2 * (other["y"] - center["y"])
    c = other["x"] ** 2 + other["y"] ** 2 - center["x"] ** 2 - center["y"] ** 2
    denominator = a * dx + b * dy
    if denominator == 0:
        return start
    t = (c - a * start["x"] - b * start["y"]) / denominator
    t = max(0, min(1, t))
    return {"x": start["x"] + dx * t, "y": start["y"] + dy * t}


def group_task_points(tasks):
    grouped = {}
    for task in tasks:
        grouped.setdefault(task.region_id, []).append({"x": task.x, "y": task.y})
    return grouped


def dissolve_regions(regions):
    grouped = {}
    for region in regions:
        grouped.setdefault(region["region_id"], []).append(region)

    dissolved = []
    for region_id in sorted(grouped):
        parts = grouped[region_id]
        if len(parts) == 1:
            dissolved.append(parts[0])
            continue

        # The current global Voronoi generator emits one convex cell per region.
        # If future inputs contain duplicate region parts and Shapely is not
        # available, keep the largest valid part instead of emitting overlaps.
        largest = max(parts, key=lambda part: abs(polygon_area(part["polygon"])))
        dissolved.append(
            {
                "region_id": region_id,
                "task_count": sum(part["task_count"] for part in parts),
                "centroid": largest["centroid"],
                "polygon": largest["polygon"],
            }
        )
    return dissolved


def simplify_regions_with_overlap_guard(regions, tolerance, overlap_threshold):
    tolerance = max(0.0, float(tolerance))
    attempts = [tolerance]
    while attempts[-1] > 0:
        next_tolerance = attempts[-1] / 2
        if next_tolerance < 1:
            break
        attempts.append(next_tolerance)
    if 0 not in attempts:
        attempts.append(0)

    last_candidate = regions
    for candidate_tolerance in attempts:
        candidate = [
            {
                **region,
                "polygon": simplify_polygon(region["polygon"], candidate_tolerance),
            }
            for region in regions
        ]
        candidate = [{**region, "polygon": make_valid_polygon(region["polygon"])} for region in candidate]
        last_candidate = candidate
        if not overlap_warnings(candidate, overlap_threshold):
            return candidate
    return last_candidate


def simplify_polygon(polygon, tolerance):
    cleaned = make_valid_polygon(polygon)
    if tolerance <= 0 or len(cleaned) <= 3:
        return cleaned

    ring = cleaned + [cleaned[0]]
    simplified = rdp(ring, tolerance)
    if len(simplified) > 1 and same_point(simplified[0], simplified[-1]):
        simplified = simplified[:-1]
    simplified = make_valid_polygon(simplified)
    return simplified if len(simplified) >= 3 else cleaned


def rdp(points, tolerance):
    if len(points) <= 2:
        return points

    start = points[0]
    end = points[-1]
    max_distance = -1
    index = 0
    for current_index, point in enumerate(points[1:-1], start=1):
        distance = point_line_distance(point, start, end)
        if distance > max_distance:
            max_distance = distance
            index = current_index

    if max_distance <= tolerance:
        return [start, end]

    left = rdp(points[: index + 1], tolerance)
    right = rdp(points[index:], tolerance)
    return left[:-1] + right


def point_line_distance(point, start, end):
    px, py = point
    sx, sy = start
    ex, ey = end
    dx = ex - sx
    dy = ey - sy
    if dx == 0 and dy == 0:
        return math.hypot(px - sx, py - sy)
    return abs(dy * px - dx * py + ex * sy - ey * sx) / math.hypot(dx, dy)


def make_valid_polygon(polygon):
    cleaned = []
    for point in polygon:
        if len(point) < 2:
            continue
        x = float(point[0])
        y = float(point[1])
        if not math.isfinite(x) or not math.isfinite(y):
            continue
        rounded = [round(x, 3), round(y, 3)]
        if not cleaned or not same_point(cleaned[-1], rounded):
            cleaned.append(rounded)
    if len(cleaned) > 1 and same_point(cleaned[0], cleaned[-1]):
        cleaned.pop()
    if len(cleaned) >= 3 and polygon_area(cleaned) < 0:
        cleaned.reverse()
    return cleaned


def overlap_warnings(regions, threshold):
    warnings = []
    for left_index, left in enumerate(regions):
        for right in regions[left_index + 1 :]:
            area = convex_intersection_area(left["polygon"], right["polygon"])
            if area > threshold:
                warnings.append(
                    "WARNING: region polygons overlap: "
                    f"{left['region_id']} vs {right['region_id']} area={area:.3f}"
                )
    return warnings


def convex_intersection_area(left, right):
    subject = ensure_ccw(make_valid_polygon(left))
    clipper = ensure_ccw(make_valid_polygon(right))
    if len(subject) < 3 or len(clipper) < 3:
        return 0.0

    output = subject
    for index, start in enumerate(clipper):
        end = clipper[(index + 1) % len(clipper)]
        input_polygon = output
        output = []
        if not input_polygon:
            break
        previous = input_polygon[-1]
        previous_inside = is_left_of_edge(previous, start, end)
        for current in input_polygon:
            current_inside = is_left_of_edge(current, start, end)
            if current_inside:
                if not previous_inside:
                    output.append(line_intersection(previous, current, start, end))
                output.append(current)
            elif previous_inside:
                output.append(line_intersection(previous, current, start, end))
            previous = current
            previous_inside = current_inside
    return abs(polygon_area(output))


def is_left_of_edge(point, start, end):
    return ((end[0] - start[0]) * (point[1] - start[1]) - (end[1] - start[1]) * (point[0] - start[0])) >= -1e-9


def line_intersection(first_start, first_end, second_start, second_end):
    x1, y1 = first_start
    x2, y2 = first_end
    x3, y3 = second_start
    x4, y4 = second_end
    denominator = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(denominator) < 1e-12:
        return [round(x2, 3), round(y2, 3)]
    px = ((x1 * y2 - y1 * x2) * (x3 - x4) - (x1 - x2) * (x3 * y4 - y3 * x4)) / denominator
    py = ((x1 * y2 - y1 * x2) * (y3 - y4) - (y1 - y2) * (x3 * y4 - y3 * x4)) / denominator
    return [round(px, 3), round(py, 3)]


def ensure_ccw(polygon):
    if polygon_area(polygon) < 0:
        return list(reversed(polygon))
    return polygon


def polygon_area(polygon):
    if len(polygon) < 3:
        return 0.0
    total = 0.0
    for index, point in enumerate(polygon):
        next_point = polygon[(index + 1) % len(polygon)]
        total += point[0] * next_point[1] - next_point[0] * point[1]
    return total / 2


def same_point(left, right):
    return abs(left[0] - right[0]) < 1e-9 and abs(left[1] - right[1]) < 1e-9


def polygon_point_count(regions):
    return sum(len(region.get("polygon") or []) for region in regions)


def print_point_count_stats(before_regions, after_regions):
    before_by_region = {region["region_id"]: len(region.get("polygon") or []) for region in before_regions}
    print("Region boundary point counts:")
    for region in after_regions:
        before = before_by_region.get(region["region_id"], 0)
        after = len(region.get("polygon") or [])
        print(f"  {region['region_id']}: {before} -> {after}")


def build_city_boundaries(
    city,
    delivery_file,
    simplify_tolerance=DEFAULT_SIMPLIFY_TOLERANCE,
    overlap_threshold=DEFAULT_OVERLAP_THRESHOLD,
):
    tasks = load_delivery_tasks(
        DATA_ROOT / "delivery" / delivery_file,
        city=city,
        day=None,
        limit=TASK_LIMIT,
        sample_seed=None,
    )
    regions = build_voronoi_regions(
        group_task_points(tasks),
        simplify_tolerance=simplify_tolerance,
        overlap_threshold=overlap_threshold,
        quiet=False,
    )
    return {
        "city": tasks[0].city if tasks else city,
        "task_count": len(tasks),
        "region_count": len(regions),
        "regions": regions,
    }


def build_all_boundaries(
    simplify_tolerance=DEFAULT_SIMPLIFY_TOLERANCE,
    overlap_threshold=DEFAULT_OVERLAP_THRESHOLD,
):
    cities = {}
    for delivery_file in sorted(set(CITY_FILES.values())):
        aliases = sorted(city for city, file_name in CITY_FILES.items() if file_name == delivery_file)
        boundary = build_city_boundaries(
            aliases[0],
            delivery_file,
            simplify_tolerance=simplify_tolerance,
            overlap_threshold=overlap_threshold,
        )
        for alias in aliases:
            cities[alias] = boundary
        cities[boundary["city"]] = boundary
    return {
        "generated_from": sorted(set(CITY_FILES.values())),
        "method": "centroid_voronoi_clipped_to_city_bounds_simplified",
        "simplify_tolerance": simplify_tolerance,
        "overlap_threshold": overlap_threshold,
        "cities": cities,
    }


def main():
    parser = argparse.ArgumentParser(description="Build non-overlapping city region boundary polygons.")
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--simplify-tolerance", type=float, default=DEFAULT_SIMPLIFY_TOLERANCE)
    parser.add_argument("--overlap-threshold", type=float, default=DEFAULT_OVERLAP_THRESHOLD)
    args = parser.parse_args()
    payload = build_all_boundaries(
        simplify_tolerance=args.simplify_tolerance,
        overlap_threshold=args.overlap_threshold,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
