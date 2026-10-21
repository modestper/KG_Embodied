import argparse
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass


DEFAULT_SOURCE_URI = "bolt://127.0.0.1:17687"
DEFAULT_TARGET_URI = "bolt://127.0.0.1:37687"
TEMP_LABEL = "__LaDeRunCopy"
TEMP_PROPERTY = "_lade_source_element_id_v1"
TEMP_CONSTRAINT = "lade_run_copy_source_element_id_v1"


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class GraphStats:
    node_counts: dict[tuple[str, ...], int]
    relationship_counts: dict[str, int]
    boundary_relationship_counts: dict[str, int]

    @property
    def total_nodes(self):
        return sum(self.node_counts.values())

    @property
    def total_relationships(self):
        return sum(self.relationship_counts.values())

    @property
    def total_boundary_relationships(self):
        return sum(self.boundary_relationship_counts.values())


def quote_identifier(value):
    return "`" + str(value).replace("`", "``") + "`"


def selection_predicate(alias):
    return (
        f"(coalesce({alias}.run_id, '') IN $run_ids "
        f"OR ({alias}:Run AND {alias}.id IN $run_ids))"
    )


def normalize_run_ids(values):
    normalized = []
    seen = set()
    for value in values:
        value = value.strip()
        if not value:
            raise MigrationError("run_id 不能为空")
        if value not in seen:
            normalized.append(value)
            seen.add(value)
    if not normalized:
        raise MigrationError("至少需要一个 --run-id")
    return normalized


def make_driver(uri, user, password, fetch_size):
    from neo4j import GraphDatabase

    return GraphDatabase.driver(
        uri,
        auth=(user, password),
        fetch_size=fetch_size,
    )


def session_kwargs(database):
    return {"database": database} if database else {}


def server_version(driver, database):
    with driver.session(**session_kwargs(database)) as session:
        record = session.run(
            "CALL dbms.components() YIELD name, versions "
            "RETURN name, versions[0] AS version LIMIT 1"
        ).single()
    return dict(record) if record else {"name": "unknown", "version": "unknown"}


def selected_run_ids(driver, database, run_ids):
    with driver.session(**session_kwargs(database)) as session:
        rows = session.run(
            "MATCH (r:Run) WHERE r.id IN $run_ids RETURN r.id AS run_id",
            run_ids=run_ids,
        )
        return sorted(row["run_id"] for row in rows)


def database_node_count(driver, database):
    with driver.session(**session_kwargs(database)) as session:
        record = session.run("MATCH (n) RETURN count(n) AS count").single()
    return int(record["count"])


def collect_graph_stats(driver, database, run_ids):
    selected_n = selection_predicate("n")
    selected_a = selection_predicate("a")
    selected_b = selection_predicate("b")
    with driver.session(**session_kwargs(database)) as session:
        node_rows = session.run(
            f"""
            MATCH (n)
            WHERE {selected_n}
            RETURN labels(n) AS labels, count(*) AS count
            """,
            run_ids=run_ids,
        )
        node_counts = {
            tuple(sorted(row["labels"] or [])): int(row["count"])
            for row in node_rows
        }

        relationship_rows = session.run(
            f"""
            MATCH (a)-[r]->(b)
            WHERE {selected_a} AND {selected_b}
            RETURN type(r) AS relationship_type, count(*) AS count
            """,
            run_ids=run_ids,
        )
        relationship_counts = {
            row["relationship_type"]: int(row["count"])
            for row in relationship_rows
        }

        boundary_rows = session.run(
            f"""
            MATCH (a)-[r]->(b)
            WHERE ({selected_a} AND NOT {selected_b})
               OR (NOT {selected_a} AND {selected_b})
            RETURN type(r) AS relationship_type, count(*) AS count
            """,
            run_ids=run_ids,
        )
        boundary_counts = {
            row["relationship_type"]: int(row["count"])
            for row in boundary_rows
        }

    return GraphStats(node_counts, relationship_counts, boundary_counts)


def print_stats(title, stats):
    print(f"\n{title}")
    print("=" * len(title))
    print(f"节点总数: {stats.total_nodes}")
    for labels, count in sorted(
        stats.node_counts.items(), key=lambda item: (-item[1], item[0])
    ):
        label_text = ":".join(labels) if labels else "<no-label>"
        print(f"  {label_text}: {count}")
    print(f"关系总数: {stats.total_relationships}")
    for relationship_type, count in sorted(
        stats.relationship_counts.items(), key=lambda item: (-item[1], item[0])
    ):
        print(f"  {relationship_type}: {count}")
    print(f"跨越目标子图边界的关系: {stats.total_boundary_relationships}")
    for relationship_type, count in sorted(
        stats.boundary_relationship_counts.items(),
        key=lambda item: (-item[1], item[0]),
    ):
        print(f"  {relationship_type}: {count}")


def read_schema(driver, database):
    with driver.session(**session_kwargs(database)) as session:
        constraints = [
            dict(row)
            for row in session.run(
                """
                SHOW CONSTRAINTS
                YIELD name, type, entityType, labelsOrTypes, properties
                RETURN name, type, entityType, labelsOrTypes, properties
                ORDER BY name
                """
            )
        ]
        indexes = [
            dict(row)
            for row in session.run(
                """
                SHOW INDEXES
                YIELD name, type, entityType, labelsOrTypes, properties, owningConstraint
                RETURN name, type, entityType, labelsOrTypes, properties, owningConstraint
                ORDER BY name
                """
            )
        ]
    return constraints, indexes


def build_constraint_statement(item):
    if item.get("type") != "UNIQUENESS":
        return None
    if item.get("entityType") != "NODE":
        return None
    labels = item.get("labelsOrTypes") or []
    properties = item.get("properties") or []
    if len(labels) != 1 or not properties:
        return None
    name = quote_identifier(item["name"])
    label = quote_identifier(labels[0])
    property_expressions = [f"n.{quote_identifier(prop)}" for prop in properties]
    expression = (
        property_expressions[0]
        if len(property_expressions) == 1
        else "(" + ", ".join(property_expressions) + ")"
    )
    return (
        f"CREATE CONSTRAINT {name} IF NOT EXISTS "
        f"FOR (n:{label}) REQUIRE {expression} IS UNIQUE"
    )


def build_index_statement(item):
    if item.get("owningConstraint"):
        return None
    index_type = item.get("type")
    if index_type not in {"RANGE", "TEXT", "POINT"}:
        return None
    labels_or_types = item.get("labelsOrTypes") or []
    properties = item.get("properties") or []
    if len(labels_or_types) != 1 or not properties:
        return None
    name = quote_identifier(item["name"])
    token = quote_identifier(labels_or_types[0])
    keyword = f"{index_type} INDEX"
    if item.get("entityType") == "NODE":
        pattern = f"(n:{token})"
    elif item.get("entityType") == "RELATIONSHIP":
        pattern = f"()-[n:{token}]-()"
    else:
        return None
    property_expressions = ", ".join(
        f"n.{quote_identifier(prop)}" for prop in properties
    )
    return (
        f"CREATE {keyword} {name} IF NOT EXISTS "
        f"FOR {pattern} ON ({property_expressions})"
    )


def copy_schema(target_driver, target_database, constraints, indexes):
    statements = []
    skipped = []
    for item in constraints:
        statement = build_constraint_statement(item)
        if statement:
            statements.append(("constraint", item["name"], statement))
        else:
            skipped.append(("constraint", item.get("name"), item.get("type")))
    for item in indexes:
        statement = build_index_statement(item)
        if statement:
            statements.append(("index", item["name"], statement))
        elif item.get("type") != "LOOKUP" and not item.get("owningConstraint"):
            skipped.append(("index", item.get("name"), item.get("type")))

    with target_driver.session(**session_kwargs(target_database)) as session:
        for kind, name, statement in statements:
            print(f"创建 {kind}: {name}")
            session.run(statement).consume()

    if skipped:
        print("以下 schema 类型未自动复制，请人工核对:")
        for kind, name, item_type in skipped:
            print(f"  {kind} {name}: {item_type}")
    return len(statements), skipped


def ensure_temp_namespace_is_free(source_driver, source_database, run_ids):
    selected_n = selection_predicate("n")
    temp_label = quote_identifier(TEMP_LABEL)
    temp_property = quote_identifier(TEMP_PROPERTY)
    with source_driver.session(**session_kwargs(source_database)) as session:
        record = session.run(
            f"""
            MATCH (n)
            WHERE {selected_n}
              AND (n:{temp_label} OR n.{temp_property} IS NOT NULL)
            RETURN count(n) AS count
            """,
            run_ids=run_ids,
        ).single()
    if record and record["count"]:
        raise MigrationError(
            f"源数据已有迁移保留标签/属性，冲突节点数={record['count']}"
        )


def create_temp_constraint(target_driver, target_database):
    statement = (
        f"CREATE CONSTRAINT {quote_identifier(TEMP_CONSTRAINT)} IF NOT EXISTS "
        f"FOR (n:{quote_identifier(TEMP_LABEL)}) "
        f"REQUIRE n.{quote_identifier(TEMP_PROPERTY)} IS UNIQUE"
    )
    with target_driver.session(**session_kwargs(target_database)) as session:
        session.run(statement).consume()


def write_node_batch(session, rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[tuple(sorted(row["labels"]))].append(row)
    for labels, group_rows in grouped.items():
        label_clause = "".join(f":{quote_identifier(label)}" for label in labels)
        label_statement = f"SET n{label_clause}" if label_clause else ""
        query = f"""
        UNWIND $rows AS row
        CREATE (n:{quote_identifier(TEMP_LABEL)} {{
            {quote_identifier(TEMP_PROPERTY)}: row.source_element_id
        }})
        SET n += row.properties
        {label_statement}
        """
        summary = session.run(query, rows=group_rows).consume()
        created = summary.counters.nodes_created
        if created != len(group_rows):
            raise MigrationError(
                f"节点批次写入不完整: expected={len(group_rows)} created={created}"
            )


def copy_nodes(
    source_driver,
    target_driver,
    source_database,
    target_database,
    run_ids,
    batch_size,
    progress_every,
):
    selected_n = selection_predicate("n")
    query = f"""
    MATCH (n)
    WHERE {selected_n}
    RETURN elementId(n) AS source_element_id,
           labels(n) AS labels,
           properties(n) AS properties
    """
    copied = 0
    next_progress = progress_every
    batch = []
    with source_driver.session(**session_kwargs(source_database)) as source_session:
        result = source_session.run(query, run_ids=run_ids)
        with target_driver.session(**session_kwargs(target_database)) as target_session:
            for record in result:
                properties = dict(record["properties"] or {})
                if TEMP_PROPERTY in properties:
                    raise MigrationError(
                        f"源节点属性与迁移属性冲突: {record['source_element_id']}"
                    )
                batch.append(
                    {
                        "source_element_id": record["source_element_id"],
                        "labels": list(record["labels"] or []),
                        "properties": properties,
                    }
                )
                if len(batch) >= batch_size:
                    write_node_batch(target_session, batch)
                    copied += len(batch)
                    batch = []
                    if progress_every and copied >= next_progress:
                        print(f"已复制节点: {copied}")
                        next_progress += progress_every
            if batch:
                write_node_batch(target_session, batch)
                copied += len(batch)
    print(f"节点复制完成: {copied}")
    return copied


def write_relationship_batch(session, rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["relationship_type"]].append(row)
    for relationship_type, group_rows in grouped.items():
        query = f"""
        UNWIND $rows AS row
        MATCH (a:{quote_identifier(TEMP_LABEL)} {{
            {quote_identifier(TEMP_PROPERTY)}: row.start_source_element_id
        }})
        MATCH (b:{quote_identifier(TEMP_LABEL)} {{
            {quote_identifier(TEMP_PROPERTY)}: row.end_source_element_id
        }})
        CREATE (a)-[r:{quote_identifier(relationship_type)}]->(b)
        SET r = row.properties
        """
        summary = session.run(query, rows=group_rows).consume()
        created = summary.counters.relationships_created
        if created != len(group_rows):
            raise MigrationError(
                "关系批次写入不完整: "
                f"type={relationship_type} expected={len(group_rows)} created={created}"
            )


def copy_relationships(
    source_driver,
    target_driver,
    source_database,
    target_database,
    run_ids,
    batch_size,
    progress_every,
):
    selected_a = selection_predicate("a")
    selected_b = selection_predicate("b")
    query = f"""
    MATCH (a)-[r]->(b)
    WHERE {selected_a} AND {selected_b}
    RETURN elementId(a) AS start_source_element_id,
           elementId(b) AS end_source_element_id,
           type(r) AS relationship_type,
           properties(r) AS properties
    """
    copied = 0
    next_progress = progress_every
    batch = []
    with source_driver.session(**session_kwargs(source_database)) as source_session:
        result = source_session.run(query, run_ids=run_ids)
        with target_driver.session(**session_kwargs(target_database)) as target_session:
            for record in result:
                batch.append(
                    {
                        "start_source_element_id": record[
                            "start_source_element_id"
                        ],
                        "end_source_element_id": record["end_source_element_id"],
                        "relationship_type": record["relationship_type"],
                        "properties": dict(record["properties"] or {}),
                    }
                )
                if len(batch) >= batch_size:
                    write_relationship_batch(target_session, batch)
                    copied += len(batch)
                    batch = []
                    if progress_every and copied >= next_progress:
                        print(f"已复制关系: {copied}")
                        next_progress += progress_every
            if batch:
                write_relationship_batch(target_session, batch)
                copied += len(batch)
    print(f"关系复制完成: {copied}")
    return copied


def cleanup_temp_metadata(target_driver, target_database, batch_size):
    with target_driver.session(**session_kwargs(target_database)) as session:
        session.run(
            f"DROP CONSTRAINT {quote_identifier(TEMP_CONSTRAINT)} IF EXISTS"
        ).consume()
        session.run(
            f"""
            MATCH (n:{quote_identifier(TEMP_LABEL)})
            CALL {{
              WITH n
              REMOVE n.{quote_identifier(TEMP_PROPERTY)}
              REMOVE n:{quote_identifier(TEMP_LABEL)}
            }} IN TRANSACTIONS OF {int(batch_size)} ROWS
            """
        ).consume()


def wait_for_indexes(driver, database, timeout_seconds):
    deadline = time.monotonic() + timeout_seconds
    while True:
        with driver.session(**session_kwargs(database)) as session:
            rows = [
                dict(row)
                for row in session.run(
                    """
                    SHOW INDEXES
                    YIELD name, state, populationPercent
                    WHERE state <> 'ONLINE'
                    RETURN name, state, populationPercent
                    ORDER BY name
                    """
                )
            ]
        failed = [row for row in rows if row["state"] == "FAILED"]
        if failed:
            raise MigrationError(f"目标索引建立失败: {failed}")
        if not rows:
            return
        if time.monotonic() >= deadline:
            raise MigrationError(f"等待目标索引 ONLINE 超时: {rows}")
        print(f"等待索引 ONLINE: {[(row['name'], row['state']) for row in rows]}")
        time.sleep(1)


def compare_stats(source_stats, target_stats):
    problems = []
    if source_stats.node_counts != target_stats.node_counts:
        problems.append(
            f"节点分组不一致: source={source_stats.node_counts} "
            f"target={target_stats.node_counts}"
        )
    if source_stats.relationship_counts != target_stats.relationship_counts:
        problems.append(
            "关系分组不一致: "
            f"source={source_stats.relationship_counts} "
            f"target={target_stats.relationship_counts}"
        )
    if target_stats.total_boundary_relationships:
        problems.append(
            "目标库中的复制子图仍有边界关系: "
            f"{target_stats.boundary_relationship_counts}"
        )
    return problems


def positive_int(value):
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("必须是大于 0 的整数")
    return parsed


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Copy selected LaDe run_id subgraphs between Neo4j instances."
    )
    parser.add_argument(
        "--source-uri",
        default=os.environ.get("SOURCE_NEO4J_URI", DEFAULT_SOURCE_URI),
    )
    parser.add_argument(
        "--source-user", default=os.environ.get("SOURCE_NEO4J_USER", "neo4j")
    )
    parser.add_argument(
        "--source-password", default=os.environ.get("SOURCE_NEO4J_PASSWORD")
    )
    parser.add_argument(
        "--source-database",
        default=os.environ.get("SOURCE_NEO4J_DATABASE", "neo4j"),
    )
    parser.add_argument(
        "--target-uri",
        default=os.environ.get("TARGET_NEO4J_URI", DEFAULT_TARGET_URI),
    )
    parser.add_argument(
        "--target-user", default=os.environ.get("TARGET_NEO4J_USER", "neo4j")
    )
    parser.add_argument(
        "--target-password", default=os.environ.get("TARGET_NEO4J_PASSWORD")
    )
    parser.add_argument(
        "--target-database",
        default=os.environ.get("TARGET_NEO4J_DATABASE", "neo4j"),
    )
    parser.add_argument("--run-id", action="append", required=True)
    parser.add_argument("--batch-size", type=positive_int, default=2_000)
    parser.add_argument("--fetch-size", type=positive_int, default=1_000)
    parser.add_argument("--progress-every", type=positive_int, default=50_000)
    parser.add_argument("--index-timeout-seconds", type=positive_int, default=900)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="执行复制；默认只预览")
    mode.add_argument("--verify-only", action="store_true", help="只核对源和目标计数")
    parser.add_argument(
        "--allow-boundary-relationships",
        action="store_true",
        help="允许并跳过一端不属于目标 run 的关系",
    )
    return parser.parse_args(argv)


def validate_credentials(args):
    missing = []
    if not args.source_password:
        missing.append("SOURCE_NEO4J_PASSWORD/--source-password")
    if not args.target_password:
        missing.append("TARGET_NEO4J_PASSWORD/--target-password")
    if missing:
        raise MigrationError("缺少密码参数: " + ", ".join(missing))


def run(args):
    validate_credentials(args)
    run_ids = normalize_run_ids(args.run_id)
    source_driver = make_driver(
        args.source_uri, args.source_user, args.source_password, args.fetch_size
    )
    target_driver = make_driver(
        args.target_uri, args.target_user, args.target_password, args.fetch_size
    )
    try:
        source_driver.verify_connectivity()
        target_driver.verify_connectivity()
        source_component = server_version(source_driver, args.source_database)
        target_component = server_version(target_driver, args.target_database)
        print(f"源: {args.source_uri}/{args.source_database} {source_component}")
        print(f"目标: {args.target_uri}/{args.target_database} {target_component}")
        if source_component.get("version") != target_component.get("version"):
            raise MigrationError("源和目标 Neo4j 版本不同，停止复制")

        existing_runs = selected_run_ids(source_driver, args.source_database, run_ids)
        missing_runs = sorted(set(run_ids) - set(existing_runs))
        if missing_runs:
            raise MigrationError(f"源库缺少 Run 节点: {missing_runs}")

        source_stats = collect_graph_stats(
            source_driver, args.source_database, run_ids
        )
        print_stats("源子图统计", source_stats)
        if source_stats.total_boundary_relationships and not args.allow_boundary_relationships:
            raise MigrationError(
                "源子图存在跨 run/共享节点关系；"
                "默认停止以避免静默漏关系。"
                "核对后可显式传 --allow-boundary-relationships。"
            )

        if args.verify_only:
            target_stats = collect_graph_stats(
                target_driver, args.target_database, run_ids
            )
            print_stats("目标子图统计", target_stats)
            problems = compare_stats(source_stats, target_stats)
            if problems:
                raise MigrationError("；".join(problems))
            print("校验通过：源和目标的节点/关系分组计数一致。")
            return

        constraints, indexes = read_schema(source_driver, args.source_database)
        target_count = database_node_count(target_driver, args.target_database)
        print(f"目标库当前节点数: {target_count}")
        print(f"源 schema: constraints={len(constraints)} indexes={len(indexes)}")
        if not args.apply:
            print("预览完成：未写入目标库。追加 --apply 才会执行复制。")
            return

        if target_count != 0:
            raise MigrationError(
                f"目标库不是空库（{target_count} 个节点），拒绝写入。"
                "请使用全新的目标 volume。"
            )

        ensure_temp_namespace_is_free(
            source_driver, args.source_database, run_ids
        )
        create_temp_constraint(target_driver, args.target_database)
        copied_nodes = copy_nodes(
            source_driver,
            target_driver,
            args.source_database,
            args.target_database,
            run_ids,
            args.batch_size,
            args.progress_every,
        )
        if copied_nodes != source_stats.total_nodes:
            raise MigrationError(
                f"节点总数不一致: expected={source_stats.total_nodes} copied={copied_nodes}"
            )
        copied_relationships = copy_relationships(
            source_driver,
            target_driver,
            args.source_database,
            args.target_database,
            run_ids,
            args.batch_size,
            args.progress_every,
        )
        if copied_relationships != source_stats.total_relationships:
            raise MigrationError(
                "关系总数不一致: "
                f"expected={source_stats.total_relationships} "
                f"copied={copied_relationships}"
            )

        cleanup_temp_metadata(
            target_driver, args.target_database, args.batch_size
        )
        copied_schema_count, skipped_schema = copy_schema(
            target_driver, args.target_database, constraints, indexes
        )
        print(f"已自动复制 schema 项: {copied_schema_count}")
        if skipped_schema:
            print("存在未自动复制的 schema 项，性能测试前必须人工核对。")
        wait_for_indexes(
            target_driver, args.target_database, args.index_timeout_seconds
        )

        target_stats = collect_graph_stats(
            target_driver, args.target_database, run_ids
        )
        print_stats("目标子图统计", target_stats)
        problems = compare_stats(source_stats, target_stats)
        target_total = database_node_count(target_driver, args.target_database)
        if target_total != source_stats.total_nodes:
            problems.append(
                "目标库总节点数异常: "
                f"expected={source_stats.total_nodes} actual={target_total}"
            )
        if problems:
            raise MigrationError("；".join(problems))
        print(
            "复制和校验完成：目标库只包含指定 run 的节点，"
            "关系分组计数一致。"
        )
    finally:
        source_driver.close()
        target_driver.close()


def main(argv=None):
    args = parse_args(argv)
    try:
        run(args)
    except MigrationError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
