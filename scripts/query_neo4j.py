import argparse
import os


DEFAULT_URI = "bolt://127.0.0.1:17687"
DEFAULT_USER = "neo4j"
DEFAULT_PASSWORD = "neo4j123456"


def scalar(session, query, **params):
    row = session.run(query, **params).single()
    return row["count"] if row else 0


def print_rows(title, rows, formatter):
    print(f"\n{title}")
    print("-" * len(title))
    printed = False
    for row in rows:
        printed = True
        print(formatter(row))
    if not printed:
        print("(无)")


def print_summary(driver, run_id=None, recent_events=10):
    with driver.session() as session:
        node_count = scalar(session, "MATCH (n) RETURN count(n) AS count")
        rel_count = scalar(session, "MATCH ()-[r]->() RETURN count(r) AS count")

        print("Neo4j 图谱概览")
        print("=" * 16)
        print(f"总节点数: {node_count}")
        print(f"总关系数: {rel_count}")

        label_rows = session.run(
            """
            MATCH (n)
            RETURN labels(n) AS labels, count(n) AS count
            ORDER BY count DESC
            """
        )
        print_rows(
            "按节点标签统计",
            label_rows,
            lambda row: f"{'/'.join(row['labels']) or '(无标签)'}: {row['count']}",
        )

        rel_rows = session.run(
            """
            MATCH ()-[r]->()
            RETURN type(r) AS relationship_type, count(r) AS count
            ORDER BY count DESC
            """
        )
        print_rows(
            "按关系类型统计",
            rel_rows,
            lambda row: f"{row['relationship_type']}: {row['count']}",
        )

        run_rows = session.run(
            """
            MATCH (run:Run)
            RETURN run.id AS run_id
            ORDER BY run_id
            """
        )
        print_rows("Run 列表", run_rows, lambda row: row["run_id"])

        if run_id:
            print(f"\n指定 run: {run_id}")
            run_label_rows = session.run(
                """
                MATCH (n)-[:IN_RUN]->(:Run {id: $run_id})
                RETURN labels(n) AS labels, count(n) AS count
                ORDER BY count DESC
                """,
                run_id=run_id,
            )
            print_rows(
                "该 run 下的节点统计",
                run_label_rows,
                lambda row: f"{'/'.join(row['labels']) or '(无标签)'}: {row['count']}",
            )

            event_rows = session.run(
                """
                MATCH (e:Event)-[:IN_RUN]->(:Run {id: $run_id})
                RETURN e.type AS type, e.time AS time, e.task_id AS task_id, e.drone_id AS drone_id
                ORDER BY e.time_minute DESC
                LIMIT $limit
                """,
                run_id=run_id,
                limit=recent_events,
            )
            print_rows(
                f"最近事件 TOP {recent_events}",
                event_rows,
                lambda row: (
                    f"{row.get('time')} {row.get('type')} "
                    f"task={row.get('task_id')} drone={row.get('drone_id')}"
                ),
            )


def make_driver(uri, user, password):
    from neo4j import GraphDatabase

    return GraphDatabase.driver(uri, auth=(user, password))


def parse_args():
    parser = argparse.ArgumentParser(description="Query Neo4j graph summary for the LaDe viewer project.")
    parser.add_argument("--uri", default=os.environ.get("NEO4J_URI", DEFAULT_URI))
    parser.add_argument("--user", default=os.environ.get("NEO4J_USER", DEFAULT_USER))
    parser.add_argument("--password", default=os.environ.get("NEO4J_PASSWORD", DEFAULT_PASSWORD))
    parser.add_argument("--run-id", default=os.environ.get("KG_RUN_ID"))
    parser.add_argument("--recent-events", type=int, default=10)
    return parser.parse_args()


def main():
    args = parse_args()
    driver = make_driver(args.uri, args.user, args.password)
    try:
        print(f"连接: {args.uri} user={args.user}")
        print_summary(driver, run_id=args.run_id, recent_events=args.recent_events)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
