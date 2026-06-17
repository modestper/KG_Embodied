import argparse
import math
import html
import json
import sys
from pathlib import Path

VIEWER_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = VIEWER_ROOT.parents[0]
sys.path.insert(0, str(VIEWER_ROOT))

from scripts.build_delivery_sim import CITY_FILES
from scripts.build_region_boundaries import group_task_points
from simulation.delivery_sim import load_delivery_tasks

OUT = VIEWER_ROOT / "data" / "region_partition_preview_jilin.html"
TASK_LIMIT = 10_000_000
PALETTE = [
    "#1b8a6b",
    "#2866b6",
    "#b7791f",
    "#8a3ffc",
    "#c43d4b",
    "#4f6f52",
    "#6f4e7c",
    "#2f6f8f",
    "#8a5a44",
    "#59656f",
]


def classify_grid(grouped_points, columns=160, rows=120, padding=240, max_points_per_region=900):
    sampled = sample_points(grouped_points, max_points_per_region)
    all_points = [point for points in sampled.values() for point in points]
    if not all_points:
        return {"bounds": None, "columns": columns, "rows": rows, "cells": []}

    bounds = padded_bounds(all_points, padding)
    cell_width = (bounds["max_x"] - bounds["min_x"]) / columns
    cell_height = (bounds["max_y"] - bounds["min_y"]) / rows
    cells = []
    indexed_points = [
        (region_id, point)
        for region_id, points in sampled.items()
        for point in points
    ]

    for row in range(rows):
        y = bounds["min_y"] + (row + 0.5) * cell_height
        for column in range(columns):
            x = bounds["min_x"] + (column + 0.5) * cell_width
            region_id = nearest_region({"x": x, "y": y}, indexed_points)
            cells.append({"column": column, "row": row, "region_id": region_id})

    return {
        "bounds": bounds,
        "columns": columns,
        "rows": rows,
        "cell_width": cell_width,
        "cell_height": cell_height,
        "cells": cells,
    }


def sample_points(grouped_points, max_points_per_region):
    sampled = {}
    for region_id, points in grouped_points.items():
        if len(points) <= max_points_per_region:
            sampled[region_id] = points
            continue
        step = len(points) / max_points_per_region
        sampled[region_id] = [points[int(index * step)] for index in range(max_points_per_region)]
    return sampled


def padded_bounds(points, padding):
    xs = [point["x"] for point in points]
    ys = [point["y"] for point in points]
    return {
        "min_x": min(xs) - padding,
        "max_x": max(xs) + padding,
        "min_y": min(ys) - padding,
        "max_y": max(ys) + padding,
    }


def nearest_region(point, indexed_points):
    return min(
        indexed_points,
        key=lambda item: (point["x"] - item[1]["x"]) ** 2 + (point["y"] - item[1]["y"]) ** 2,
    )[0]


def render_preview(city, grouped_points, grid):
    region_ids = sorted(grouped_points)
    color_by_region = {
        region_id: PALETTE[index % len(PALETTE)]
        for index, region_id in enumerate(region_ids)
    }
    cells = majority_filter_cells(grid["cells"], grid["columns"], grid["rows"], iterations=2)
    cell_lookup = {
        (cell["column"], cell["row"]): cell["region_id"]
        for cell in cells
    }
    cells_svg = []
    for cell in cells:
        color = color_by_region[cell["region_id"]]
        cells_svg.append(
            f'<rect x="{cell["column"]}" y="{cell["row"]}" width="1" height="1" fill="{color}" fill-opacity="0.22"/>'
        )

    boundary_segments = {}
    for cell in cells:
        column = cell["column"]
        row = cell["row"]
        region_id = cell["region_id"]
        right = cell_lookup.get((column + 1, row))
        down = cell_lookup.get((column, row + 1))
        if right is not None and right != region_id:
            pair = tuple(sorted((region_id, right)))
            boundary_segments.setdefault(pair, []).append(((column + 1, row), (column + 1, row + 1)))
        if down is not None and down != region_id:
            pair = tuple(sorted((region_id, down)))
            boundary_segments.setdefault(pair, []).append(((column, row + 1), (column + 1, row + 1)))

    boundary_svg = []
    for segments in boundary_segments.values():
        for chain in boundary_chains(segments):
            chain = simplify_chain(chain, tolerance=0.35)
            if len(chain) < 2:
                continue
            boundary_svg.append(f'<path d="{smooth_path(chain)}"/>')
    outer_svg = (
        f'<rect x="0" y="0" width="{grid["columns"]}" height="{grid["rows"]}" '
        'fill="none" class="outer-boundary"/>'
    )

    legend = "\n".join(
        f'<span><i style="background:{color_by_region[region_id]}"></i>R{html.escape(str(region_id))}</span>'
        for region_id in region_ids
    )
    counts = {
        region_id: len(points)
        for region_id, points in grouped_points.items()
    }
    metadata = html.escape(json.dumps({"city": city, "regions": counts}, ensure_ascii=False))
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>{html.escape(city)} region partition preview</title>
  <style>
    body {{ margin: 0; font-family: sans-serif; color: #20242a; background: #f6f8fa; }}
    main {{ padding: 20px; }}
    h1 {{ font-size: 18px; margin: 0 0 6px; }}
    p {{ margin: 0 0 14px; color: #68707c; }}
    svg {{ width: 100%; height: calc(100vh - 150px); min-height: 520px; background: white; border: 1px solid #d7dde3; }}
    .boundaries path {{ fill: none; stroke: #20242a; stroke-width: .42; stroke-opacity: .58; stroke-linecap: round; stroke-linejoin: round; vector-effect: non-scaling-stroke; }}
    .outer-boundary {{ stroke: #68707c; stroke-width: .32; stroke-opacity: .42; vector-effect: non-scaling-stroke; }}
    .legend {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 12px; font-size: 12px; }}
    .legend span {{ display: inline-flex; align-items: center; gap: 5px; }}
    .legend i {{ display: inline-block; width: 12px; height: 12px; border: 1px solid rgba(0,0,0,.12); }}
    details {{ margin-top: 10px; color: #68707c; font-size: 12px; }}
  </style>
</head>
<body>
  <main>
    <h1>{html.escape(city)} region partition preview</h1>
    <p>Global nearest-point partition on projected coordinates. Each grid cell has exactly one region_id, so cells do not overlap.</p>
    <svg viewBox="0 0 {grid["columns"]} {grid["rows"]}" preserveAspectRatio="xMidYMid meet">
      <g class="cells">
        {''.join(cells_svg)}
      </g>
      <g class="boundaries">
        {''.join(boundary_svg)}
        {outer_svg}
      </g>
    </svg>
    <div class="legend">{legend}</div>
    <details><summary>metadata</summary><pre>{metadata}</pre></details>
  </main>
</body>
</html>
"""


def boundary_chains(segments):
    unused = {normalized_segment(segment) for segment in segments}
    chains = []
    while unused:
        start, end = unused.pop()
        chain = [start, end]
        extend_chain(chain, unused, forward=True)
        extend_chain(chain, unused, forward=False)
        chains.append(chain)
    return chains


def majority_filter_cells(cells, columns, rows, iterations=1):
    current = {
        (cell["column"], cell["row"]): cell["region_id"]
        for cell in cells
    }
    for _ in range(iterations):
        next_grid = current.copy()
        for row in range(rows):
            for column in range(columns):
                region_counts = {}
                for y in range(max(0, row - 1), min(rows, row + 2)):
                    for x in range(max(0, column - 1), min(columns, column + 2)):
                        region_id = current.get((x, y))
                        if region_id is None:
                            continue
                        region_counts[region_id] = region_counts.get(region_id, 0) + 1
                winner, count = max(region_counts.items(), key=lambda item: (item[1], item[0]))
                own = current.get((column, row))
                if winner != own and count >= 5:
                    next_grid[(column, row)] = winner
        current = next_grid
    return [
        {"column": column, "row": row, "region_id": current[(column, row)]}
        for row in range(rows)
        for column in range(columns)
        if (column, row) in current
    ]


def simplify_chain(points, tolerance):
    if len(points) <= 2:
        return points
    start = points[0]
    end = points[-1]
    max_distance = -1
    max_index = 0
    for index, point in enumerate(points[1:-1], start=1):
        distance = perpendicular_distance(point, start, end)
        if distance > max_distance:
            max_distance = distance
            max_index = index
    if max_distance <= tolerance:
        return [start, end]
    return (
        simplify_chain(points[: max_index + 1], tolerance)[:-1]
        + simplify_chain(points[max_index:], tolerance)
    )


def perpendicular_distance(point, start, end):
    if start == end:
        return math.hypot(point[0] - start[0], point[1] - start[1])
    numerator = abs(
        (end[1] - start[1]) * point[0]
        - (end[0] - start[0]) * point[1]
        + end[0] * start[1]
        - end[1] * start[0]
    )
    denominator = math.hypot(end[1] - start[1], end[0] - start[0])
    return numerator / denominator


def normalized_segment(segment):
    start, end = segment
    return (start, end) if start <= end else (end, start)


def extend_chain(chain, unused, forward):
    while True:
        endpoint = chain[-1] if forward else chain[0]
        match = next((segment for segment in unused if endpoint in segment), None)
        if match is None:
            return
        unused.remove(match)
        other = match[1] if match[0] == endpoint else match[0]
        if forward:
            chain.append(other)
        else:
            chain.insert(0, other)


def smooth_path(points):
    if len(points) == 2:
        return f"M {points[0][0]} {points[0][1]} L {points[1][0]} {points[1][1]}"
    start = midpoint(points[0], points[1])
    commands = [f"M {start[0]} {start[1]}"]
    for index in range(1, len(points) - 1):
        control = points[index]
        end = midpoint(points[index], points[index + 1])
        commands.append(f"Q {control[0]} {control[1]} {end[0]} {end[1]}")
    commands.append(f"L {points[-1][0]} {points[-1][1]}")
    return " ".join(commands)


def midpoint(left, right):
    return ((left[0] + right[0]) / 2, (left[1] + right[1]) / 2)


def build_preview(city, delivery_file, columns=160, rows=120):
    tasks = load_delivery_tasks(
        DATA_ROOT / "delivery" / delivery_file,
        city=city,
        day=None,
        limit=TASK_LIMIT,
        sample_seed=None,
    )
    grouped = group_task_points(tasks)
    grid = classify_grid(grouped, columns=columns, rows=rows)
    return render_preview(tasks[0].city if tasks else city, grouped, grid)


def main():
    parser = argparse.ArgumentParser(description="Build a non-overlapping region partition preview HTML.")
    parser.add_argument("--city", default="Jilin", choices=sorted(CITY_FILES))
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--columns", type=int, default=180)
    parser.add_argument("--rows", type=int, default=130)
    args = parser.parse_args()
    html_text = build_preview(args.city, CITY_FILES[args.city], columns=args.columns, rows=args.rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html_text, encoding="utf-8")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
