"""Recompute and compare context benchmark JSONL files without database access.

The analysis uses the standard library; plotting also requires matplotlib.
Run from the repository root and pass --output-prefix for generated artifacts.
Overlap estimates use millisecond log timestamps and are not server telemetry.
"""

import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


def digest(value):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def stamp(raw):
    dt = datetime.fromisoformat(raw)
    return (dt - dt.replace(hour=0, minute=0, second=0, microsecond=0)).total_seconds()


def percentile(values, pct):
    ordered = sorted(values)
    rank = (len(ordered) - 1) * pct / 100
    lower, upper = math.floor(rank), math.ceil(rank)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)


def stats(values):
    return {"count": len(values), "avg_ms": sum(values) / len(values),
            "p50_ms": percentile(values, 50), "p95_ms": percentile(values, 95),
            "p99_ms": percentile(values, 99), "max_ms": max(values)}


def overlap(intervals, origin, step=1.0):
    end = max(b for a, b in intervals)
    buckets = [0.0] * math.ceil((end - origin) / step)
    events = []
    for a, b in intervals:
        events.extend(((a, 1), (b, -1)))
        first, last = int((a - origin) / step), int((b - origin) / step)
        for index in range(first, min(last + 1, len(buckets))):
            buckets[index] += max(0.0, min(b, origin + (index + 1) * step) - max(a, origin + index * step)) / step
    active = peak = 0
    for _, change in sorted(events):
        active += change
        peak = max(peak, active)
    return {"peak": peak, "per_second_avg": buckets,
            "span_s": end - origin,
            "mean_over_span": sum(b - a for a, b in intervals) / (end - origin)}


def load(folder):
    root = Path(folder)
    summary = json.loads((root / "summary.json").read_text())
    records, by_query, by_repeat = {}, defaultdict(list), defaultdict(list)
    row_totals, row_hist = Counter(), defaultdict(Counter)
    shared_values = defaultdict(set)
    intervals = []
    drone_counts, ends = Counter(), []
    for line in (root / "query_logs.jsonl").open():
        r = json.loads(line)
        key = (r["context_index"], r["query_index"])
        assert key not in records, (folder, "duplicate query", key)
        sig = digest({k: r.get(k) for k in (
            "catalog_version", "run_id", "drone_id", "step_index", "time_minute", "query_id",
            "query_name", "category", "temporal_scope", "success", "params", "uri", "database", "cypher")})
        records[key] = (sig, r["row_count"], r["elapsed_ms"], r["query_id"])
        assert r["success"], (folder, "failed query", key)
        by_query[r["query_id"]].append(r["elapsed_ms"])
        row_totals[r["query_id"]] += r["row_count"]
        row_hist[r["query_id"]][r["row_count"]] += 1
        drone_counts[r["drone_id"]] += 1
        for k in ("catalog_version", "run_id", "step_index", "time_minute", "uri", "database", "concurrency_mode"):
            shared_values[k].add(r.get(k))
        end = stamp(r["time"])
        ends.append(end)
        intervals.append((end - r["elapsed_ms"] / 1000, end))

    context_data, context_intervals, per_pid = {}, [], defaultdict(list)
    for line in (root / "contexts.jsonl").open():
        c = json.loads(line)
        idx = c["context_index"]
        assert idx not in context_data, (folder, "duplicate context", idx)
        results = c["queries"]
        ordered = {name: digest(rows) for name, rows in results.items()}
        unordered = {name: digest(sorted(digest(row) for row in rows)) for name, rows in results.items()}
        context_data[idx] = {"drone_id": c["drone_id"], "ordered": ordered, "unordered": unordered,
                             "elapsed_ms": c["total_elapsed_ms"], "pid": c.get("worker_pid")}
        a = stamp(c["time"])
        b = a + c["total_elapsed_ms"] / 1000
        context_intervals.append((a, b))
        per_pid[c.get("worker_pid")].append((a, b))
        by_repeat[idx // len(drone_counts)].append(c["total_elapsed_ms"])

    values = [r[2] for r in records.values()]
    overall = stats(values)
    assert len(records) == summary["total_queries"]
    assert len(context_data) == summary["context_count"]
    for field in ("avg_ms", "p50_ms", "p95_ms", "p99_ms"):
        assert abs(overall[field] - summary["query_" + field]) < 0.0006, (folder, field)
    assert abs(stats([c["elapsed_ms"] for c in context_data.values()])["avg_ms"] - summary["context_avg_ms"]) < 0.0006

    origin = min(a for a, b in context_intervals)
    context_overlap = overlap(context_intervals, origin)
    query_overlap = overlap(intervals, min(a for a, b in intervals))
    completions = Counter(int(end - origin) for end in ends)
    first_starts = sorted(min(a for a, b in tasks) - origin for pid, tasks in per_pid.items() if pid is not None)
    data = {
        "folder": folder, "summary": summary,
        "recomputed_query": overall,
        "by_query": {q: {**stats(v), "total_rows": row_totals[q], "row_histogram": dict(row_hist[q]),
                           "zero_result_fraction": row_hist[q][0] / len(v)} for q, v in by_query.items()},
        "metadata": {k: sorted(v, key=str) for k, v in shared_values.items()},
        "drone_count": len(drone_counts), "queries_per_drone_histogram": dict(Counter(drone_counts.values())),
        "context_indices": [min(context_data), max(context_data)],
        "context_concurrency": context_overlap, "query_concurrency_approx": query_overlap,
        "mean_active_contexts_over_wall": sum(c["elapsed_ms"] for c in context_data.values()) / summary["wall_ms"],
        "mean_timed_queries_over_wall": sum(values) / summary["wall_ms"],
        "uncovered_by_context_span_s": summary["wall_ms"] / 1000 - context_overlap["span_s"],
        "first_context_seconds_since_midnight": origin,
        "completions_per_second": [completions.get(i, 0) for i in range(math.ceil(context_overlap["span_s"]) + 1)],
        "pid_task_counts": dict(Counter(c["pid"] for c in context_data.values() if c["pid"] is not None)),
        "pid_first_start_spread_s": max(first_starts) - min(first_starts) if first_starts else None,
        "pid_first_starts_s": first_starts,
        "by_repeat_context": {rep: stats(v) for rep, v in sorted(by_repeat.items())},
    }
    return data, records, context_data, by_query


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--process-dir", default="test50")
    parser.add_argument("--thread-dir", default="th_test50")
    parser.add_argument("--output-prefix", required=True)
    args = parser.parse_args()
    p, pr, pc, pl = load(args.process_dir)
    t, tr, tc, tl = load(args.thread_dir)
    assert pr.keys() == tr.keys(), "Different query jobs"
    assert pc.keys() == tc.keys(), "Different context jobs"
    mismatch = {"metadata": [], "row_count": [], "ordered_payloads": Counter(), "unordered_payloads": Counter()}
    for key in pr:
        if pr[key][0] != tr[key][0]:
            mismatch["metadata"].append(key)
        if pr[key][1] != tr[key][1]:
            mismatch["row_count"].append(key)
    for idx, c in pc.items():
        assert c["drone_id"] == tc[idx]["drone_id"]
        assert c["ordered"].keys() == tc[idx]["ordered"].keys()
        for kind in ("ordered", "unordered"):
            for name, sig in c[kind].items():
                if sig != tc[idx][kind][name]:
                    mismatch[kind + "_payloads"][name] += 1
    rows = []
    for q in sorted(p["by_query"], key=lambda q: int(q[1:])):
        a, b = p["by_query"][q], t["by_query"][q]
        rows.append({"query_id": q, "process_avg_ms": a["avg_ms"], "thread_avg_ms": b["avg_ms"],
                     "avg_speedup": b["avg_ms"] / a["avg_ms"], "process_p50_ms": a["p50_ms"],
                     "thread_p50_ms": b["p50_ms"], "process_p95_ms": a["p95_ms"],
                     "thread_p95_ms": b["p95_ms"], "p95_speedup": b["p95_ms"] / a["p95_ms"],
                     "process_rows": a["total_rows"], "thread_rows": b["total_rows"],
                     "zero_result_fraction": a["zero_result_fraction"]})
    out = Path(args.output_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    report = {"process": p, "thread": t, "matching": mismatch, "query_comparison": rows,
              "notes": ["Query interval overlap is approximate: end timestamps are recorded after elapsed sampling and rounded to milliseconds.",
                        "Context overlap reconstructs start timestamps plus duration; it is not a measurement of server-side active Cypher queries.",
                        "Equal catalog version/metadata do not establish runtime Cypher text equality when logs omit cypher."]}
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    with out.with_suffix(".csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    # Standard plotting output; labels in English avoid platform font dependencies.
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 2, figsize=(15, 9), layout="constrained")
    for label, data, latencies, color in (("Process", p, pl, "#1768ac"), ("Thread", t, tl, "#e87416")):
        axes[0, 0].plot(range(56), [data["by_query"][f"i{i}"]["avg_ms"] for i in range(56)], label=label, color=color)
        axes[0, 1].plot(data["context_concurrency"]["per_second_avg"], label=label, color=color)
        all_latencies = sorted(v for group in latencies.values() for v in group)
        axes[1, 0].plot(all_latencies, [(i + 1) / len(all_latencies) for i in range(len(all_latencies))], label=label, color=color)
        total = 0
        cumulative = [0]
        for completed in data["completions_per_second"]:
            total += completed
            cumulative.append(total)
        axes[1, 1].plot(range(len(cumulative)), cumulative, label=label, color=color)
    axes[0, 0].set(title="Average latency by query", xlabel="Query index (i0-i55)", ylabel="Latency (ms)", yscale="log")
    axes[0, 1].set(title="Contexts executing (1-second mean)", xlabel="Seconds since first context started", ylabel="Active contexts")
    axes[1, 0].set(title="Distribution of client query latency", xlabel="Latency (ms)", ylabel="Cumulative fraction", xscale="log")
    axes[1, 1].set(title="Completed queries", xlabel="Seconds since first context started", ylabel="Queries completed")
    for ax in axes.flat:
        ax.grid(alpha=0.2)
        ax.legend()
    fig.suptitle("Same 112,000-query workload: 100 processes vs thread run", fontsize=15)
    fig.savefig(out.with_suffix(".png"), dpi=160)
    plt.close(fig)

    compact = {}
    for label, data in (("process", p), ("thread", t)):
        compact[label] = {k: data[k] for k in (
            "metadata", "drone_count", "queries_per_drone_histogram", "mean_active_contexts_over_wall",
            "mean_timed_queries_over_wall", "uncovered_by_context_span_s", "pid_first_start_spread_s")}
        compact[label]["context_overlap"] = {k:v for k,v in data["context_concurrency"].items() if k != "per_second_avg"}
        compact[label]["query_overlap_approx"] = {k:v for k,v in data["query_concurrency_approx"].items() if k != "per_second_avg"}
        compact[label]["pid_tasks_min_max"] = [min(data["pid_task_counts"].values()), max(data["pid_task_counts"].values())] if data["pid_task_counts"] else None
        compact[label]["repeat_context_avg_ms"] = [round(r["avg_ms"], 2) for r in data["by_repeat_context"].values()]
    compact["mismatches"] = mismatch
    compact["fastest_speedup"] = sorted(rows, key=lambda r: r["avg_speedup"], reverse=True)[:6]
    compact["slowest_speedup"] = sorted(rows, key=lambda r: r["avg_speedup"])[:6]
    print(json.dumps(compact, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
