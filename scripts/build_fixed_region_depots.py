import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path


VIEWER_ROOT = Path(__file__).resolve().parents[1]
DATASET_ROOT = VIEWER_ROOT.parent
DEFAULT_OUTPUT = VIEWER_ROOT / "data" / "fixed_region_depots.json"
sys.path.insert(0, str(VIEWER_ROOT))

from scripts.build_summary import city_name, normalize_coordinate


CITY_FILES = {
    "Chongqing": "delivery_cq.csv",
    "Hangzhou": "delivery_hz.csv",
    "Jilin": "delivery_jl.csv",
    "Shanghai": "delivery_sh.csv",
    "Yantai": "delivery_yt.csv",
}


def aggregate_city(path, city):
    canonical_city = city_name(city)
    groups = defaultdict(lambda: {"sum_x": 0.0, "sum_y": 0.0, "task_count": 0})
    source_rows = 0
    valid_rows = 0
    with Path(path).open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            source_rows += 1
            if city_name(row.get("city")) != canonical_city:
                continue
            x, y = normalize_coordinate(row.get("lng"), row.get("lat"))
            if x is None or y is None:
                continue
            valid_rows += 1
            region = groups[str(row["region_id"])]
            region["sum_x"] += x
            region["sum_y"] += y
            region["task_count"] += 1

    regions = {}
    for region_id, group in groups.items():
        count = group["task_count"]
        regions[region_id] = {
            "region_id": region_id,
            "x": round(group["sum_x"] / count, 3),
            "y": round(group["sum_y"] / count, 3),
            "task_count": count,
        }
    return {
        "city": canonical_city,
        "source_file": Path(path).name,
        "source_rows": source_rows,
        "valid_coordinate_rows": valid_rows,
        "region_count": len(regions),
        "regions": regions,
    }


def build_catalog(dataset_root=DATASET_ROOT, city_files=CITY_FILES):
    cities = {}
    for city, filename in city_files.items():
        payload = aggregate_city(Path(dataset_root) / "delivery" / filename, city)
        cities[payload["city"]] = payload
        print(
            f"{payload['city']}: {payload['valid_coordinate_rows']} orders, "
            f"{payload['region_count']} regions",
            flush=True,
        )
    return {
        "schema_version": 1,
        "coordinate_system": "EPSG:3857",
        "source_coordinate_fields": ["lng", "lat"],
        "method": "arithmetic_mean_of_all_valid_order_coordinates_by_city_and_region",
        "cities": cities,
    }


def write_catalog(catalog, output_path):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(output_path)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Build deterministic region depot coordinates from all LaDe delivery orders."
    )
    parser.add_argument("--dataset-root", type=Path, default=DATASET_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main():
    args = parse_args()
    catalog = build_catalog(args.dataset_root)
    write_catalog(catalog, args.output)
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
