import csv
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "viewer" / "data" / "summary.json"
MAX_POINTS_PER_CITY = 260
MAX_ROADS_PER_CITY = 90
WEB_MERCATOR_RADIUS = 6378137

CITY_NAMES = {
    "Shanghai": "上海市",
    "Hangzhou": "杭州市",
    "Chongqing": "重庆市",
    "Jilin": "吉林市",
    "Yantai": "烟台市",
}


def parse_hour(value):
    if not value or " " not in value:
        return None
    try:
        return int(value.split(" ", 1)[1].split(":", 1)[0])
    except ValueError:
        return None


def parse_minutes(value):
    if not value or " " not in value:
        return None
    try:
        _, clock = value.split(" ", 1)
        hour, minute, _ = clock.split(":")
        return int(hour) * 60 + int(minute)
    except ValueError:
        return None


def elapsed_minutes(start, end):
    start_minutes = parse_minutes(start)
    end_minutes = parse_minutes(end)
    if start_minutes is None or end_minutes is None:
        return None
    delta = end_minutes - start_minutes
    if delta < 0:
        delta += 24 * 60
    return delta


def as_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number):
        return None
    return number


def normalize_coordinate(lng, lat):
    lng = as_float(lng)
    lat = as_float(lat)
    if lng is None or lat is None:
        return None, None
    if abs(lng) <= 180 and abs(lat) <= 90:
        clamped_lat = max(min(lat, 85.05112878), -85.05112878)
        x = WEB_MERCATOR_RADIUS * math.radians(lng)
        y = WEB_MERCATOR_RADIUS * math.log(math.tan(math.pi / 4 + math.radians(clamped_lat) / 2))
        return round(x, 3), round(y, 3)
    return round(lng, 3), round(lat, 3)


def city_name(value):
    return CITY_NAMES.get(value, value)


def first_value(row, keys):
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return ""


def normalize_task_row(row, task_type):
    if "from_city_name" in row:
        city = row["from_city_name"]
        courier = row["delivery_user_id"]
        typecode = row.get("typecode", "")
        if task_type == "delivery":
            start_time = row.get("receipt_time")
            end_time = row.get("sign_time")
            sample_lng = row.get("receipt_lng")
            sample_lat = row.get("receipt_lat")
        else:
            start_time = row.get("accept_time")
            end_time = row.get("got_time")
            sample_lng = row.get("poi_lng")
            sample_lat = row.get("poi_lat")
    else:
        city = city_name(row["city"])
        courier = row["courier_id"]
        typecode = row.get("aoi_type", "")
        start_time = row.get("accept_time")
        if task_type == "delivery":
            end_time = row.get("delivery_time")
        else:
            end_time = row.get("pickup_time")
        sample_lng = first_value(row, ["lng", f"{task_type}_gps_lng"])
        sample_lat = first_value(row, ["lat", f"{task_type}_gps_lat"])

    return {
        "city": city,
        "courier": courier,
        "aoi": row.get("aoi_id", ""),
        "typecode": typecode,
        "start_time": start_time,
        "end_time": end_time,
        "sample_lng": sample_lng,
        "sample_lat": sample_lat,
    }


def update_city_metric(cities, city, task_type, row, duration):
    metric = cities[city]
    metric[task_type] += 1
    metric["couriers"].add(row["courier"])
    metric["aoi"].add(row["aoi"])
    metric["typecodes"][task_type][row["typecode"]] += 1
    if duration is not None:
        metric[f"{task_type}_duration_total"] += duration
        metric[f"{task_type}_duration_count"] += 1


def add_sample(samples, task_type, row):
    city = row["city"]
    if len(samples[task_type][city]) >= MAX_POINTS_PER_CITY:
        return
    lng, lat = normalize_coordinate(row.get("sample_lng"), row.get("sample_lat"))
    if lng is None or lat is None:
        return
    samples[task_type][city].append(
        {
            "city": city,
            "lng": round(lng, 3),
            "lat": round(lat, 3),
            "aoi": row["aoi"][:8],
            "hour": parse_hour(row.get("start_time")),
        }
    )


def scan_tasks(path, task_type, cities, hourly, samples):
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            normalized = normalize_task_row(row, task_type)
            city = normalized["city"]
            duration = elapsed_minutes(normalized.get("start_time"), normalized.get("end_time"))
            hour = parse_hour(normalized.get("start_time"))
            add_sample(samples, task_type, normalized)
            update_city_metric(cities, city, task_type, normalized, duration)
            if hour is not None:
                hourly[task_type][hour] += 1


def parse_linestring(value):
    match = re.search(r"LINESTRING \((.+)\)", value or "")
    if not match:
        return []
    points = []
    for pair in match.group(1).split(","):
        pieces = pair.strip().split()
        if len(pieces) != 2:
            continue
        x = as_float(pieces[0])
        y = as_float(pieces[1])
        if x is not None and y is not None:
            points.append([round(x, 3), round(y, 3)])
    return points


def scan_roads(path):
    roads = defaultdict(list)
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            city = row["city"]
            if len(roads[city]) >= MAX_ROADS_PER_CITY:
                continue
            points = parse_linestring(row["geometry"])
            if len(points) >= 2:
                roads[city].append(
                    {
                        "city": city,
                        "class": row["fclass"],
                        "name": row["name"],
                        "points": points[:: max(1, len(points) // 24)],
                    }
                )
    return roads


def top_typecodes(counter):
    return [{"code": code[:8], "count": count} for code, count in counter.most_common(6)]


def main():
    cities = defaultdict(
        lambda: {
            "delivery": 0,
            "pickup": 0,
            "couriers": set(),
            "aoi": set(),
            "typecodes": {"delivery": Counter(), "pickup": Counter()},
            "delivery_duration_total": 0,
            "delivery_duration_count": 0,
            "pickup_duration_total": 0,
            "pickup_duration_count": 0,
        }
    )
    hourly = {"delivery": Counter(), "pickup": Counter()}
    samples = {"delivery": defaultdict(list), "pickup": defaultdict(list)}

    delivery_sources = sorted((ROOT / "delivery").glob("delivery_*.csv"))
    pickup_sources = sorted((ROOT / "pickup").glob("pickup_*.csv"))
    for path in delivery_sources:
        scan_tasks(path, "delivery", cities, hourly, samples)
    for path in pickup_sources:
        scan_tasks(path, "pickup", cities, hourly, samples)
    roads = scan_roads(ROOT / "road-network" / "roads.csv")

    city_rows = []
    all_couriers = set()
    all_aoi = set()
    for city, metric in sorted(cities.items()):
        all_couriers.update(metric["couriers"])
        all_aoi.update(metric["aoi"])
        city_rows.append(
            {
                "name": city,
                "delivery": metric["delivery"],
                "pickup": metric["pickup"],
                "couriers": len(metric["couriers"]),
                "aoi": len(metric["aoi"]),
                "avg_delivery_minutes": round(
                    metric["delivery_duration_total"] / metric["delivery_duration_count"], 1
                )
                if metric["delivery_duration_count"]
                else None,
                "avg_pickup_minutes": round(
                    metric["pickup_duration_total"] / metric["pickup_duration_count"], 1
                )
                if metric["pickup_duration_count"]
                else None,
                "top_delivery_types": top_typecodes(metric["typecodes"]["delivery"]),
                "top_pickup_types": top_typecodes(metric["typecodes"]["pickup"]),
            }
        )

    payload = {
        "generated_from": {
            "delivery": "delivery/*.csv",
            "pickup": "pickup/*.csv",
            "roads": "road-network/roads.csv",
        },
        "totals": {
            "delivery": sum(row["delivery"] for row in city_rows),
            "pickup": sum(row["pickup"] for row in city_rows),
            "cities": len(city_rows),
            "couriers": len(all_couriers),
            "aoi": len(all_aoi),
            "roads": sum(len(items) for items in roads.values()),
        },
        "cities": city_rows,
        "hourly": {
            task: [{"hour": hour, "count": count} for hour, count in sorted(counter.items())]
            for task, counter in hourly.items()
        },
        "samples": {
            task: [point for city_points in city_map.values() for point in city_points]
            for task, city_map in samples.items()
        },
        "roads": [road for city_roads in roads.values() for road in city_roads],
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
