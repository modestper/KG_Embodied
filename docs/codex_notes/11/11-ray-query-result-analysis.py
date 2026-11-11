"""Compare existing Ray, process and thread benchmark outputs; no database calls.

Run from the repository root in a Python environment with matplotlib installed.
The shared analysis helper lives next to this file. Raw results are read only.
"""

import csv
import json
import runpy
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parent
HELPERS = runpy.run_path(str(ROOT / "11-context-concurrency-analysis.py"), run_name="analysis_helpers")


def match(a_records, b_records, a_contexts, b_contexts):
    result = {"missing_query_jobs": len(a_records.keys() ^ b_records.keys()),
              "missing_context_jobs": len(a_contexts.keys() ^ b_contexts.keys()),
              "metadata_differences": 0, "row_count_differences": 0,
              "ordered_payload_differences": Counter(), "unordered_payload_differences": Counter()}
    for key in a_records.keys() & b_records.keys():
        result["metadata_differences"] += a_records[key][0] != b_records[key][0]
        result["row_count_differences"] += a_records[key][1] != b_records[key][1]
    for key in a_contexts.keys() & b_contexts.keys():
        a, b = a_contexts[key], b_contexts[key]
        assert a["drone_id"] == b["drone_id"]
        assert a["ordered"].keys() == b["ordered"].keys()
        for kind in ("ordered", "unordered"):
            for name, signature in a[kind].items():
                if signature != b[kind][name]:
                    result[kind + "_payload_differences"][name] += 1
    return result


def audit_alternate_baseline():
    """Check the other process run separately: its database endpoint differs."""
    fields = ("catalog_version", "run_id", "drone_id", "step_index", "time_minute", "query_id",
              "query_name", "category", "temporal_scope", "success", "params", "database", "cypher")
    ray_records, ray_contexts = {}, {}
    for line in Path("test50ray/query_logs.jsonl").open():
        record = json.loads(line)
        key = (record["context_index"], record["query_index"])
        ray_records[key] = (HELPERS["digest"]({k: record.get(k) for k in fields}), record["row_count"])
    for line in Path("test50ray/contexts.jsonl").open():
        context = json.loads(line)
        ray_contexts[context["context_index"]] = HELPERS["digest"](context["queries"])
    root = Path("notest50")
    summary = json.loads((root / "index_summary.json").read_text())
    by_query, uris, seen = defaultdict(list), set(), set()
    metadata_differences = row_differences = payload_differences = 0
    for line in (root / "index_query_logs.jsonl").open():
        record = json.loads(line)
        key = (record["context_index"], record["query_index"])
        assert key not in seen
        seen.add(key)
        assert record["success"]
        signature = HELPERS["digest"]({k: record.get(k) for k in fields})
        metadata_differences += signature != ray_records[key][0]
        row_differences += record["row_count"] != ray_records[key][1]
        by_query[record["query_id"]].append(record["elapsed_ms"])
        uris.add(record["uri"])
    assert seen == ray_records.keys()
    context_ids = set()
    for line in (root / "index_contexts.jsonl").open():
        context = json.loads(line)
        key = context["context_index"]
        assert key not in context_ids
        context_ids.add(key)
        payload_differences += HELPERS["digest"](context["queries"]) != ray_contexts[key]
    assert context_ids == ray_contexts.keys()
    recomputed = HELPERS["stats"]([v for group in by_query.values() for v in group])
    assert abs(recomputed["avg_ms"] - summary["query_avg_ms"]) < .0006
    assert abs(recomputed["p95_ms"] - summary["query_p95_ms"]) < .0006
    return {"folder": str(root), "summary_file": "index_summary.json", "summary": summary,
            "uris": sorted(uris), "metadata_differences_excluding_uri": metadata_differences,
            "row_count_differences": row_differences, "context_payload_differences": payload_differences,
            "matched_query_jobs": len(seen), "matched_context_jobs": len(context_ids),
            "recomputed_by_query": {q: HELPERS["stats"](v) for q, v in by_query.items()},
            "comparison_caveat": "Different Neo4j endpoints: this is not a same-database Ray-vs-process comparison."}


def main():
    folders = {"ray": "test50ray", "process": "test50", "thread": "th_test50"}
    loaded, data, raw_records, contexts = {}, {}, {}, {}
    for label, folder in folders.items():
        loaded[label] = HELPERS["load"](folder)
        data[label], raw_records[label], contexts[label], _ = loaded[label]
    comparison = {
        "ray_vs_process": match(raw_records["ray"], raw_records["process"], contexts["ray"], contexts["process"]),
        "ray_vs_thread": match(raw_records["ray"], raw_records["thread"], contexts["ray"], contexts["thread"]),
    }
    alternate = audit_alternate_baseline()
    s = data["ray"]["summary"]
    infos = s["worker_info"]
    workers = {info["worker_pid"]: info for info in infos}
    assert len(workers) == s["started_worker_count"] == 100
    assert len({info["cpu_id"] for info in infos}) == 100
    assert len({(info["physical_socket"], info["physical_core"]) for info in infos}) == 100
    assert all(info["affinity_cpus"] == [info["cpu_id"]] for info in infos)
    assert all(info["ray_assigned_resources"].get("CPU") == 1 for info in infos)
    assert abs(sum(s[k] for k in ("ray_startup_ms", "query_phase_wall_ms", "ray_cleanup_ms")) - s["wall_ms"]) <= .005
    threshold_counts, first_later = {}, {}
    for label, folder in folders.items():
        counts, groups = Counter(), defaultdict(list)
        for line in (Path(folder) / "query_logs.jsonl").open():
            r = json.loads(line)
            counts["over200ms"] += r["elapsed_ms"] > 200
            counts["over500ms"] += r["elapsed_ms"] > 500
            counts["zero_rows"] += r["row_count"] == 0
            if r["query_id"] in ("i0", "i2"):
                phase = "first100" if r["context_index"] < 100 else "later1900"
                groups[r["query_id"] + "_" + phase].append(r["elapsed_ms"])
            if label == "ray":
                info = workers[r["worker_pid"]]
                assert r["worker_index"] == info["worker_index"]
                assert r["cpu_id"] == info["cpu_id"]
                assert r["affinity_cpus"] == [info["cpu_id"]]
        threshold_counts[label] = counts
        first_later[label] = {key: sum(v) / len(v) for key, v in groups.items()}
    query_rows = []
    for query_id in sorted(data["ray"]["by_query"], key=lambda q: int(q[1:])):
        row = {"query_id": query_id}
        for label in folders:
            query = data[label]["by_query"][query_id]
            for field in ("avg_ms", "p50_ms", "p95_ms", "p99_ms", "total_rows"):
                row[label + "_" + field] = query[field]
        row["process_over_ray_avg"] = row["process_avg_ms"] / row["ray_avg_ms"]
        row["thread_over_ray_avg"] = row["thread_avg_ms"] / row["ray_avg_ms"]
        row["process_57687_avg_ms"] = alternate["recomputed_by_query"][query_id]["avg_ms"]
        row["process_57687_p95_ms"] = alternate["recomputed_by_query"][query_id]["p95_ms"]
        query_rows.append(row)
    report = {"groups": data, "matching": comparison, "threshold_counts": threshold_counts,
              "alternate_process_baseline": alternate,
              "first_later_query_avg_ms": first_later, "query_comparison": query_rows,
              "ray_cpu_binding_verified_records": s["total_queries"],
              "notes": ["Core binding is not CPU utilization or system-wide exclusivity.",
                        "Context overlap is an approximation based on timestamps, not server query concurrency.",
                        "Ray-only and binding-only effects cannot be isolated with these three observational runs."]}
    prefix = ROOT / "11-Ray查询结果对比数据"
    prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    with prefix.with_suffix(".csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=query_rows[0].keys())
        writer.writeheader()
        writer.writerows(query_rows)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    colors = {"ray": "#21864a", "process": "#1768ac", "thread": "#e87416"}
    fig, axes = plt.subplots(2, 2, figsize=(15, 9), layout="constrained")
    for label in folders:
        group = data[label]
        color = colors[label]
        axes[0, 0].plot(range(56), [group["by_query"][f"i{i}"]["avg_ms"] for i in range(56)], label=label, color=color)
        latencies = sorted(record[2] for record in raw_records[label].values())
        axes[0, 1].plot(latencies, [(i+1) / len(latencies) for i in range(len(latencies))], label=label, color=color)
        axes[1, 0].plot(group["context_concurrency"]["per_second_avg"], label=label, color=color)
    axes[0, 0].set(title="Average client query latency", xlabel="Query index (i0-i55)", ylabel="Latency (ms)", yscale="log")
    axes[0, 1].set(title="Client latency distribution", xlabel="Latency (ms)", ylabel="Cumulative fraction", xscale="log")
    axes[1, 0].set(title="Active contexts (1-second average)", xlabel="Seconds since first context", ylabel="Active contexts")
    ax = axes[1, 1]
    ax.barh(["thread", "process"], [data[k]["summary"]["wall_ms"]/1000 for k in ("thread", "process")],
            color=[colors[k] for k in ("thread", "process")], label="Unsplit total")
    left = 0
    for field, label, color in (("ray_startup_ms", "Ray startup", "#9fabb6"),
                                ("query_phase_wall_ms", "Ray query phase", colors["ray"]),
                                ("ray_cleanup_ms", "Ray cleanup", "#d2d8dd")):
        value = s[field] / 1000
        ax.barh("ray", value, left=left, color=color, label=label)
        if value > 5:
            ax.text(left + value/2, 2, f"{value:.2f}s", va="center", ha="center", fontsize=9)
        left += value
    ax.set(title="Whole-run wall time (Ray split by phase)", xlabel="Seconds")
    for ax in axes.flat:
        ax.legend(fontsize=9)
        ax.grid(alpha=.2)
    fig.suptitle("Identical workload: 100 drones x 20 contexts x 56 queries", fontsize=15)
    fig.savefig(prefix.with_suffix(".png"), dpi=160)
    plt.close(fig)

    concise = {"matching": comparison, "cpu_binding_records_verified": s["total_queries"],
               "ray_faster_than_process_query_count": sum(r["ray_avg_ms"] < r["process_avg_ms"] for r in query_rows),
               "threshold_counts": threshold_counts, "first_later_query_avg_ms": first_later}
    concise["alternate_process_baseline"] = {k:v for k,v in alternate.items() if k not in ("summary", "recomputed_by_query")}
    for label in folders:
        group = data[label]
        overlap = group["context_concurrency"]
        concise[label] = {
            "summary": {k:v for k,v in group["summary"].items() if k not in ("worker_info", "by_query", "by_category", "worker_pids", "cpu_ids")},
            "metadata": group["metadata"], "drone_count": group["drone_count"],
            "context_span_s": overlap["span_s"], "context_mean_over_span": overlap["mean_over_span"],
            "mean_active_contexts_over_wall": group["mean_active_contexts_over_wall"],
            "pid_first_start_spread_s": group["pid_first_start_spread_s"],
            "task_time_of_day_start": str(timedelta(seconds=group["first_context_seconds_since_midnight"])),
            "task_time_of_day_end": str(timedelta(seconds=group["first_context_seconds_since_midnight"] + overlap["span_s"])),
            "pid_tasks_min_max": [min(group["pid_task_counts"].values()), max(group["pid_task_counts"].values())] if group["pid_task_counts"] else None,
        }
    concise["ray_best_relative_to_process"] = sorted(query_rows, key=lambda r:r["process_over_ray_avg"], reverse=True)[:5]
    concise["ray_worst_relative_to_process"] = sorted(query_rows, key=lambda r:r["process_over_ray_avg"])[:5]
    print(json.dumps(concise, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
