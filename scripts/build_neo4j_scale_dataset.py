import argparse
import csv
import os
import time
from pathlib import Path


DEFAULT_URI = "bolt://127.0.0.1:17687"
DEFAULT_USER = "neo4j"
DEFAULT_PASSWORD = "neo4j123456"
VIEWER_ROOT = Path(__file__).resolve().parents[1]
DATASET_ROOT = VIEWER_ROOT.parents[0]

CITY_FILES = {
    "Jilin": DATASET_ROOT / "delivery" / "delivery_jl.csv",
    "Shanghai": DATASET_ROOT / "delivery" / "delivery_sh.csv",
    "Hangzhou": DATASET_ROOT / "delivery" / "delivery_hz.csv",
    "Chongqing": DATASET_ROOT / "delivery" / "delivery_cq.csv",
    "Yantai": DATASET_ROOT / "delivery" / "delivery_yt.csv",
    "all": DATASET_ROOT / "delivery_five_cities.csv",
}


CONSTRAINTS = [
    "CREATE CONSTRAINT lade_scale_run_id IF NOT EXISTS FOR (n:Run) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT lade_scale_task_key IF NOT EXISTS FOR (n:Task) REQUIRE (n.id, n.run_id) IS UNIQUE",
    "CREATE CONSTRAINT lade_scale_event_key IF NOT EXISTS FOR (n:Event) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT lade_scale_region_key IF NOT EXISTS FOR (n:Region) REQUIRE (n.id, n.run_id) IS UNIQUE",
    "CREATE CONSTRAINT lade_scale_courier_key IF NOT EXISTS FOR (n:Courier) REQUIRE (n.id, n.run_id) IS UNIQUE",
]


def parse_time_to_minute(value):
    if not value:
        return None
    time_part = value.strip().split()[-1]
    pieces = time_part.split(":")
    if len(pieces) < 2:
        return None
    try:
        return int(pieces[0]) * 60 + int(pieces[1])
    except ValueError:
        return None


def parse_float(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_int(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def row_to_task(row, row_number, run_id, id_prefix):
    raw_order_id = row.get("order_id") or str(row_number)
    task_id = f"{id_prefix}task-{raw_order_id}-{row_number}"
    city = row.get("city") or ""
    region_id = row.get("region_id") or "unknown"
    courier_id = row.get("courier_id") or "unknown"
    accept_minute = parse_time_to_minute(row.get("accept_time") or row.get("accept_gps_time"))
    delivery_minute = parse_time_to_minute(row.get("delivery_time") or row.get("delivery_gps_time"))
    return {
        "id": task_id,
        "run_id": run_id,
        "order_id": raw_order_id,
        "city": city,
        "region_id": region_id,
        "courier_id": f"{id_prefix}courier-{courier_id}",
        "aoi_id": row.get("aoi_id"),
        "aoi_type": parse_int(row.get("aoi_type")),
        "accept_time": row.get("accept_time"),
        "delivery_time": row.get("delivery_time"),
        "accept_minute": accept_minute,
        "delivery_minute": delivery_minute,
        "pickup_lng": parse_float(row.get("accept_gps_lng") or row.get("lng")),
        "pickup_lat": parse_float(row.get("accept_gps_lat") or row.get("lat")),
        "dropoff_lng": parse_float(row.get("delivery_gps_lng") or row.get("lng")),
        "dropoff_lat": parse_float(row.get("delivery_gps_lat") or row.get("lat")),
        "ds": row.get("ds"),
    }


def batched_csv_tasks(path, run_id, limit, batch_size, id_prefix):
    emitted = 0
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        batch = []
        for row_number, row in enumerate(reader, start=1):
            batch.append(row_to_task(row, row_number, run_id, id_prefix))
            emitted += 1
            if len(batch) >= batch_size:
                yield batch
                batch = []
            if limit is not None and emitted >= limit:
                break
        if batch:
            yield batch


def make_driver(uri, user, password):
    from neo4j import GraphDatabase

    return GraphDatabase.driver(uri, auth=(user, password))


def create_schema(session):
    for query in CONSTRAINTS:
        session.run(query).consume()


def write_tasks(session, tasks, run_id, source_name):
    session.run(
        """
        MERGE (run:Run {id: $run_id})
        SET run.kind = 'scale_benchmark',
            run.source = $source_name
        WITH run
        UNWIND $tasks AS row
        MERGE (region:Region {id: row.region_id, run_id: $run_id})
        SET region.city = row.city
        MERGE (courier:Courier {id: row.courier_id, run_id: $run_id})
        SET courier.city = row.city
        MERGE (task:Task {id: row.id, run_id: $run_id})
        SET task += row,
            task.status = 'completed'
        MERGE (region)-[:IN_RUN]->(run)
        MERGE (courier)-[:IN_RUN]->(run)
        MERGE (task)-[:IN_RUN]->(run)
        MERGE (task)-[:BELONGS_TO]->(region)
        MERGE (courier)-[:DELIVERED]->(task)
        MERGE (courier)-[:WORKS_IN]->(region)
        WITH run, region, courier, task, row
        CREATE (accept:Event {
            id: row.id + '-accept',
            run_id: $run_id,
            type: 'task_accepted',
            time_minute: row.accept_minute,
            time: row.accept_time,
            task_id: row.id,
            courier_id: row.courier_id,
            region_id: row.region_id,
            city: row.city,
            ds: row.ds
        })
        CREATE (delivered:Event {
            id: row.id + '-delivered',
            run_id: $run_id,
            type: 'task_delivered',
            time_minute: row.delivery_minute,
            time: row.delivery_time,
            task_id: row.id,
            courier_id: row.courier_id,
            region_id: row.region_id,
            city: row.city,
            ds: row.ds
        })
        CREATE (accept)-[:IN_RUN]->(run)
        CREATE (delivered)-[:IN_RUN]->(run)
        CREATE (accept)-[:IN_REGION]->(region)
        CREATE (delivered)-[:IN_REGION]->(region)
        CREATE (accept)-[:ABOUT_TASK]->(task)
        CREATE (delivered)-[:ABOUT_TASK]->(task)
        CREATE (accept)-[:ABOUT_COURIER]->(courier)
        CREATE (delivered)-[:ABOUT_COURIER]->(courier)
        """,
        run_id=run_id,
        source_name=source_name,
        tasks=tasks,
    ).consume()


def estimate_scale(task_count, approximate_regions=None, approximate_couriers=None):
    regions = approximate_regions if approximate_regions is not None else "按数据去重"
    couriers = approximate_couriers if approximate_couriers is not None else "按数据去重"
    return {
        "tasks": task_count,
        "events": task_count * 2,
        "regions": regions,
        "couriers": couriers,
        "minimum_nodes_without_region_courier": task_count * 3 + 1,
        "minimum_relationships_without_region_courier": task_count * 13,
    }


def count_csv_rows(path, limit=None):
    count = 0
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for count, _ in enumerate(reader, start=1):
            if limit is not None and count >= limit:
                return count
    return count


def import_csv(driver, args):
    path = args.csv_path or CITY_FILES[args.city]
    source_name = str(path)
    total_rows = count_csv_rows(path, args.limit) if args.dry_run else None
    if args.dry_run:
        estimate = estimate_scale(total_rows)
        print(f"模式: dry-run")
        print(f"CSV: {path}")
        print(f"run_id: {args.run_id}")
        print(f"任务行数: {total_rows}")
        print(f"预计 Task 节点: {estimate['tasks']}")
        print(f"预计 Event 节点: {estimate['events']}")
        print(f"预计最少节点数: {estimate['minimum_nodes_without_region_courier']} + Region/Courier 去重节点")
        print(f"预计最少关系数: {estimate['minimum_relationships_without_region_courier']} + Region/Courier 去重关系")
        print("追加 --apply 才会写入 Neo4j。")
        return

    imported = 0
    started = time.perf_counter()
    with driver.session(database=args.database) if args.database else driver.session() as session:
        if args.create_schema:
            create_schema(session)
        for batch in batched_csv_tasks(path, args.run_id, args.limit, args.batch_size, args.id_prefix):
            write_tasks(session, batch, args.run_id, source_name)
            imported += len(batch)
            if args.progress_every and imported % args.progress_every == 0:
                elapsed = time.perf_counter() - started
                print(f"已导入: {imported} tasks, elapsed={elapsed:.1f}s")
    elapsed = time.perf_counter() - started
    print(f"导入完成: {imported} tasks, elapsed={elapsed:.1f}s")


def parse_args():
    parser = argparse.ArgumentParser(description="Build a large Neo4j benchmark KG from LaDe delivery CSV.")
    parser.add_argument("--uri", default=os.environ.get("NEO4J_URI", DEFAULT_URI))
    parser.add_argument("--user", default=os.environ.get("NEO4J_USER", DEFAULT_USER))
    parser.add_argument("--password", default=os.environ.get("NEO4J_PASSWORD", DEFAULT_PASSWORD))
    parser.add_argument("--database", default=os.environ.get("NEO4J_DATABASE"))
    parser.add_argument("--city", choices=sorted(CITY_FILES), default="all")
    parser.add_argument("--csv-path", type=Path)
    parser.add_argument("--run-id", default="lade-scale-benchmark")
    parser.add_argument("--id-prefix", default="scale-", help="用于避免与现有节点 ID 冲突")
    parser.add_argument("--limit", type=int, default=333_334, help="默认约生成百万级节点：Task + 2*Event")
    parser.add_argument("--batch-size", type=int, default=5_000)
    parser.add_argument("--progress-every", type=int, default=50_000)
    parser.add_argument("--create-schema", action="store_true", help="写入前创建约束/索引")
    parser.add_argument("--dry-run", action="store_true", default=True, help="默认只估算规模，不写入")
    parser.add_argument("--apply", action="store_false", dest="dry_run", help="真正写入 Neo4j")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.dry_run:
        import_csv(None, args)
        return
    driver = make_driver(args.uri, args.user, args.password)
    try:
        import_csv(driver, args)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
