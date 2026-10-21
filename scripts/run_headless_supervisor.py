import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


VIEWER_ROOT = Path(__file__).resolve().parents[1]
HEADLESS_SCRIPT = VIEWER_ROOT / "scripts" / "run_headless_sim.py"


def available_memory_gb(meminfo_path="/proc/meminfo"):
    try:
        values = {}
        with open(meminfo_path, encoding="ascii") as handle:
            for line in handle:
                name, value = line.split(":", 1)
                values[name] = int(value.strip().split()[0])
        return values["MemAvailable"] / (1024 * 1024)
    except (OSError, KeyError, ValueError):
        return None


def wait_for_memory(minimum_gb, poll_seconds, sleep=time.sleep):
    while minimum_gb > 0:
        available = available_memory_gb()
        if available is None or available >= minimum_gb:
            return available
        print(
            "[supervisor] waiting for host memory "
            f"available_gb={available:.1f} required_gb={minimum_gb:.1f} "
            f"poll_seconds={poll_seconds}",
            flush=True,
        )
        sleep(poll_seconds)
    return available_memory_gb()


def child_command(child_args):
    args = list(child_args)
    if args and args[0] == "--":
        args = args[1:]
    if "--resume" not in args:
        args.append("--resume")
    return [sys.executable, "-u", str(HEADLESS_SCRIPT), *args]


def run_supervisor(args, run=subprocess.run, sleep=time.sleep):
    command = child_command(args.child_args)
    restart_count = 0
    while True:
        available = wait_for_memory(
            args.min_available_memory_gb,
            args.memory_poll_interval,
            sleep=sleep,
        )
        print(
            "[supervisor] starting headless child "
            f"restart={restart_count} available_gb="
            f"{available:.1f}" if available is not None else
            f"[supervisor] starting headless child restart={restart_count} available_gb=unknown",
            flush=True,
        )
        result = run(command, cwd=VIEWER_ROOT, env=os.environ.copy())
        if result.returncode == 0:
            print("[supervisor] headless child completed successfully", flush=True)
            return 0

        restart_count += 1
        print(
            "[supervisor] headless child exited unexpectedly "
            f"returncode={result.returncode} restart={restart_count} "
            f"delay_seconds={args.restart_delay}",
            flush=True,
        )
        if args.max_restarts > 0 and restart_count > args.max_restarts:
            print("[supervisor] maximum restart count exceeded", flush=True)
            return result.returncode
        sleep(args.restart_delay)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Keep a checkpoint-enabled headless simulation alive across failures."
    )
    parser.add_argument(
        "--restart-delay",
        type=float,
        default=float(os.environ.get("HEADLESS_SUPERVISOR_RESTART_DELAY", "120")),
    )
    parser.add_argument(
        "--memory-poll-interval",
        type=float,
        default=float(os.environ.get("HEADLESS_SUPERVISOR_MEMORY_POLL", "60")),
    )
    parser.add_argument(
        "--min-available-memory-gb",
        type=float,
        default=float(os.environ.get("HEADLESS_SUPERVISOR_MIN_MEMORY_GB", "48")),
    )
    parser.add_argument(
        "--max-restarts",
        type=int,
        default=int(os.environ.get("HEADLESS_SUPERVISOR_MAX_RESTARTS", "0")),
        help="0 means unlimited restarts.",
    )
    parser.add_argument("child_args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if not args.child_args:
        parser.error("pass run_headless_sim.py arguments after --")
    return args


def main():
    raise SystemExit(run_supervisor(parse_args()))


if __name__ == "__main__":
    main()
