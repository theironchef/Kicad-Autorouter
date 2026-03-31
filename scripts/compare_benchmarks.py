#!/usr/bin/env python3
"""Compare benchmark results across runs.

Usage:
    python scripts/compare_benchmarks.py                    # Compare last 2 runs
    python scripts/compare_benchmarks.py --runs 5           # Compare last 5 runs
    python scripts/compare_benchmarks.py --board "Arduino Nano"  # Filter to one board
"""

import argparse
import json
import sys
from pathlib import Path


def load_results(path: Path) -> list[dict]:
    """Load benchmark results from JSON file."""
    if not path.exists():
        print(f"No benchmark results found at {path}")
        sys.exit(1)
    return json.loads(path.read_text())


def format_delta(old: float, new: float, lower_is_better: bool = True) -> str:
    """Format a delta with arrow indicator."""
    if old == 0:
        return f"{new}"
    delta = new - old
    pct = (delta / old) * 100 if old != 0 else 0
    if abs(pct) < 0.1:
        return f"{new} (=)"
    arrow = "↓" if delta < 0 else "↑"
    good = (delta < 0 and lower_is_better) or (delta > 0 and not lower_is_better)
    symbol = "+" if good else "-"
    return f"{new} ({symbol}{arrow}{abs(pct):.1f}%)"


def print_comparison(
    runs: list[dict], board_filter: str | None = None
) -> None:
    """Print comparison table."""
    if len(runs) < 1:
        print("No runs to compare.")
        return

    for run_idx, run in enumerate(runs):
        ts = run.get("run_timestamp", "unknown")
        boards = run.get("boards", [])

        if board_filter:
            boards = [
                b
                for b in boards
                if board_filter.lower() in b["board_name"].lower()
            ]

        print(f"\n{'='*70}")
        sha = boards[0].get("git_sha", "?") if boards else "?"
        branch = boards[0].get("git_branch", "?") if boards else "?"
        print(f"Run {run_idx + 1}: {ts} (git: {sha} on {branch})")
        print(f"{'='*70}")

        header = (
            f"{'Board':<25} {'Routed':<12} {'Compl%':<8} {'Traces':<8} "
            f"{'Vias':<6} {'DRC Err':<8} {'Time(s)':<8}"
        )
        print(header)
        print("-" * 70)

        for b in boards:
            name = b["board_name"][:24]
            routed = f"{b['connections_routed']}/{b['total_connections']}"
            compl = f"{b['completion_pct']:.1f}%"
            traces = str(b["trace_count"])
            vias = str(b["via_count"])
            drc = str(b["drc_errors"])
            time_s = f"{b['routing_time_s']:.1f}"
            print(
                f"{name:<25} {routed:<12} {compl:<8} {traces:<8} "
                f"{vias:<6} {drc:<8} {time_s:<8}"
            )

    # Print deltas if we have 2+ runs
    if len(runs) >= 2:
        prev_run = runs[-2]
        curr_run = runs[-1]
        prev_boards = {b["board_name"]: b for b in prev_run.get("boards", [])}
        curr_boards = {b["board_name"]: b for b in curr_run.get("boards", [])}

        if board_filter:
            prev_boards = {
                k: v
                for k, v in prev_boards.items()
                if board_filter.lower() in k.lower()
            }
            curr_boards = {
                k: v
                for k, v in curr_boards.items()
                if board_filter.lower() in k.lower()
            }

        common = set(prev_boards.keys()) & set(curr_boards.keys())
        if common:
            print(f"\n{'='*70}")
            print("DELTAS (latest vs previous)")
            print(f"{'='*70}")
            header = (
                f"{'Board':<25} {'Completion':<15} {'Vias':<15} "
                f"{'DRC Errors':<15} {'Time':<15}"
            )
            print(header)
            print("-" * 70)

            for name in sorted(common):
                p = prev_boards[name]
                c = curr_boards[name]
                compl = format_delta(
                    p["completion_pct"], c["completion_pct"], lower_is_better=False
                )
                vias = format_delta(p["via_count"], c["via_count"], lower_is_better=True)
                drc = format_delta(p["drc_errors"], c["drc_errors"], lower_is_better=True)
                time_s = format_delta(
                    p["routing_time_s"], c["routing_time_s"], lower_is_better=True
                )
                print(
                    f"{name[:24]:<25} {compl:<15} {vias:<15} "
                    f"{drc:<15} {time_s:<15}"
                )


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Compare benchmark results across runs"
    )
    parser.add_argument(
        "--file",
        default="benchmark-results.json",
        help="Path to results JSON (default: benchmark-results.json)",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=2,
        help="Number of recent runs to show (default: 2)",
    )
    parser.add_argument(
        "--board",
        default=None,
        help="Filter to a specific board name (substring match)",
    )
    args = parser.parse_args()

    results = load_results(Path(args.file))
    recent = results[-args.runs :] if len(results) >= args.runs else results
    print_comparison(recent, args.board)


if __name__ == "__main__":
    main()
