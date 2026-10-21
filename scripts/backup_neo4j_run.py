import argparse
import gzip
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path


VIEWER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_ROOT))

from scripts.copy_neo4j_runs import (
    collect_graph_stats,
    make_driver,
    normalize_run_ids,
    selection_predicate,
    server_version,
    session_kwargs,
)


DEFAULT_URI = "bolt://127.0.0.1:17687"
DEFAULT_USER = "neo4j"
DEFAULT_PASSWORD = "neo4j123456"


class BackupError(RuntimeError):
    pass


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_jsonl_gzip(path, records):
    count = 0
    with gzip.open(path, "wt", encoding="utf-8", compresslevel=6) as handle:
        for record in records:
            handle.write(
                json.dumps(
                    record,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                )
            )
            handle.write("\n")
            count += 1
    with Path(path).open("rb") as handle:
        os.fsync(handle.fileno())
    return count


def node_records(session, run_ids):
    query = f"""
    MATCH (n)
    WHERE {selection_predicate('n')}
    RETURN elementId(n) AS source_element_id,
           labels(n) AS labels,
           properties(n) AS properties
    """
    for row in session.run(query, run_ids=run_ids):
        yield {
            "source_element_id": row["source_element_id"],
            "labels": list(row["labels"] or []),
            "properties": dict(row["properties"] or {}),
        }


def relationship_records(session, run_ids):
    query = f"""
    MATCH (a)-[r]->(b)
    WHERE {selection_predicate('a')} AND {selection_predicate('b')}
    RETURN elementId(a) AS start_source_element_id,
           elementId(b) AS end_source_element_id,
           type(r) AS relationship_type,
           properties(r) AS properties
    """
    for row in session.run(query, run_ids=run_ids):
        yield {
            "start_source_element_id": row["start_source_element_id"],
            "end_source_element_id": row["end_source_element_id"],
            "relationship_type": row["relationship_type"],
            "properties": dict(row["properties"] or {}),
        }


def stats_payload(stats):
    return {
        "node_counts": [
            {"labels": list(labels), "count": count}
            for labels, count in sorted(stats.node_counts.items())
        ],
        "relationship_counts": stats.relationship_counts,
        "boundary_relationship_counts": stats.boundary_relationship_counts,
        "total_nodes": stats.total_nodes,
        "total_relationships": stats.total_relationships,
    }


def create_backup(args):
    run_ids = normalize_run_ids(args.run_id)
    output_dir = Path(args.output_dir)
    if output_dir.exists():
        raise BackupError(f"backup path already exists: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent)
    )
    driver = make_driver(args.uri, args.user, args.password, args.fetch_size)
    try:
        driver.verify_connectivity()
        before = collect_graph_stats(driver, args.database, run_ids)
        if before.total_boundary_relationships:
            raise BackupError(
                "selected run has boundary relationships: "
                f"{before.boundary_relationship_counts}"
            )
        nodes_path = temp_dir / "nodes.jsonl.gz"
        relationships_path = temp_dir / "relationships.jsonl.gz"
        with driver.session(**session_kwargs(args.database)) as session:
            node_count = write_jsonl_gzip(
                nodes_path, node_records(session, run_ids)
            )
        print(f"backup nodes={node_count}", flush=True)
        with driver.session(**session_kwargs(args.database)) as session:
            relationship_count = write_jsonl_gzip(
                relationships_path, relationship_records(session, run_ids)
            )
        print(f"backup relationships={relationship_count}", flush=True)
        after = collect_graph_stats(driver, args.database, run_ids)
        if before != after:
            raise BackupError("source run changed while backup was being created")
        if node_count != before.total_nodes:
            raise BackupError(
                f"node count mismatch: expected={before.total_nodes} actual={node_count}"
            )
        if relationship_count != before.total_relationships:
            raise BackupError(
                "relationship count mismatch: "
                f"expected={before.total_relationships} actual={relationship_count}"
            )
        manifest = {
            "format": "lade-neo4j-run-jsonl-v1",
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "run_ids": run_ids,
            "source": {
                "uri": args.uri,
                "database": args.database,
                "server": server_version(driver, args.database),
            },
            "stats": stats_payload(before),
            "files": {
                "nodes.jsonl.gz": {
                    "records": node_count,
                    "bytes": nodes_path.stat().st_size,
                    "sha256": sha256_file(nodes_path),
                },
                "relationships.jsonl.gz": {
                    "records": relationship_count,
                    "bytes": relationships_path.stat().st_size,
                    "sha256": sha256_file(relationships_path),
                },
            },
        }
        manifest_path = temp_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        with manifest_path.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temp_dir, output_dir)
        print(f"backup complete path={output_dir}", flush=True)
        return manifest
    finally:
        driver.close()
        if temp_dir.exists():
            shutil.rmtree(temp_dir)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Create a compressed logical backup of selected LaDe run subgraphs."
    )
    parser.add_argument("--run-id", action="append", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--uri", default=os.environ.get("NEO4J_URI", DEFAULT_URI))
    parser.add_argument("--user", default=os.environ.get("NEO4J_USER", DEFAULT_USER))
    parser.add_argument("--password", default=os.environ.get("NEO4J_PASSWORD", DEFAULT_PASSWORD))
    parser.add_argument("--database", default=os.environ.get("NEO4J_DATABASE", "neo4j"))
    parser.add_argument("--fetch-size", type=int, default=1000)
    return parser.parse_args(argv)


def main():
    try:
        create_backup(parse_args())
    except BackupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
