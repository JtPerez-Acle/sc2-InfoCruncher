"""Analyze info-gain scheduling effectiveness across game logs.

Reads JSONL game logs and answers the core Phase 3 research question:
Can we skip 50%+ of observations without missing critical frames?

Modes:
  --simulate BUDGET  Replay scheduler offline against existing logs
  --compare          Compare multiple logs across budget levels
  (default)          Analyze the most recent log

Usage:
    poetry run python scripts/analyze_infogain.py
    poetry run python scripts/analyze_infogain.py data/logs/X.jsonl
    poetry run python scripts/analyze_infogain.py --simulate 0.5
    poetry run python scripts/analyze_infogain.py --simulate 0.5 data/logs/X.jsonl
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Project root on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bot.info_gain.scheduler import ObservationScheduler


# -------------------------------------------------------------------------
# Data loading
# -------------------------------------------------------------------------

def load_jsonl(filepath: str) -> list[dict]:
    """Load all records from a JSONL file."""
    records = []
    with open(filepath, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def find_latest_log(log_dir: str = "data/logs") -> str | None:
    """Find the most recently modified JSONL file."""
    log_path = Path(log_dir)
    if not log_path.exists():
        return None
    files = sorted(log_path.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    return str(files[-1]) if files else None


# -------------------------------------------------------------------------
# Critical frame detection
# -------------------------------------------------------------------------

def find_critical_frames(records: list[dict]) -> set[float]:
    """Identify game_times of state frames that are critical (must not be skipped).

    A frame is critical if:
    - combat_mode changed from the previous state snapshot
    - ground/air threat_count went from 0 to nonzero
    - enemy_structures_delta has new keys (first scouting reveal)

    Note: Decision records are always logged regardless of state skipping,
    so they are NOT included in the critical frame set.
    """
    states = [r for r in records if r.get("type") == "state"]

    critical_times: set[float] = set()
    prev_mode = None
    prev_threat = 0

    for s in states:
        t = s["game_time"]
        mode = s.get("combat_mode", "IDLE")
        threat = s.get("ground_threat_count", 0) + s.get("air_threat_count", 0)

        # Combat mode change
        if prev_mode is not None and mode != prev_mode:
            critical_times.add(t)

        # Threat appearance
        if threat > 0 and prev_threat == 0:
            critical_times.add(t)

        # Enemy structures scouted for first time
        delta = s.get("delta")
        if delta and delta.get("enemy_structures_delta"):
            critical_times.add(t)

        prev_mode = mode
        prev_threat = threat

    return critical_times


# -------------------------------------------------------------------------
# Simulation
# -------------------------------------------------------------------------

@dataclass
class SimulationResult:
    budget: float
    total_frames: int
    observed: int
    skipped: int
    critical_total: int
    critical_missed: list[float] = field(default_factory=list)

    @property
    def skip_rate(self) -> float:
        return self.skipped / max(self.total_frames, 1)

    @property
    def observation_rate(self) -> float:
        return self.observed / max(self.total_frames, 1)

    @property
    def critical_miss_rate(self) -> float:
        return len(self.critical_missed) / max(self.critical_total, 1)


def simulate_budget(records: list[dict], budget: float) -> SimulationResult:
    """Replay scheduler decisions against recorded state snapshots."""
    config = {
        "Enabled": True,
        "ObservationBudget": budget,
        "CombatGuard": True,
        "ProductionDiscount": 0.7,
        "MinObserveInterval": 3,
    }
    scheduler = ObservationScheduler(config)

    states = [r for r in records if r.get("type") == "state"]
    critical_times = find_critical_frames(records)

    observed_times: list[float] = []
    skipped_times: list[float] = []
    missed_critical: list[float] = []

    for state in states:
        delta = state.get("delta")
        game_time = state["game_time"]
        result = scheduler.should_observe(state, delta)

        if result:
            observed_times.append(game_time)
        else:
            skipped_times.append(game_time)
            if game_time in critical_times:
                missed_critical.append(game_time)

    return SimulationResult(
        budget=budget,
        total_frames=len(states),
        observed=len(observed_times),
        skipped=len(skipped_times),
        critical_total=len(critical_times),
        critical_missed=missed_critical,
    )


# -------------------------------------------------------------------------
# Reporting
# -------------------------------------------------------------------------

def print_simulation_report(result: SimulationResult, filepath: str) -> None:
    """Print a formatted simulation report."""
    print(f"\n{'='*60}")
    print(f"INFO-GAIN SIMULATION: budget={result.budget}")
    print(f"Log: {os.path.basename(filepath)}")
    print(f"{'='*60}")
    print(f"  Total frames:     {result.total_frames}")
    print(f"  Observed:         {result.observed} ({result.observation_rate:.1%})")
    print(f"  Skipped:          {result.skipped} ({result.skip_rate:.1%})")
    print(f"  Critical frames:  {result.critical_total}")
    print(f"  Critical missed:  {len(result.critical_missed)} ({result.critical_miss_rate:.1%})")

    if result.critical_missed:
        print(f"\n  MISSED CRITICAL FRAMES:")
        for t in result.critical_missed[:10]:
            print(f"    t={t:.1f}s")

    # Verdict
    print(f"\n  VERDICT: ", end="")
    if result.skip_rate >= 0.50 and len(result.critical_missed) == 0:
        print("PASS - 50%+ skip rate with zero critical misses")
    elif len(result.critical_missed) == 0:
        print(f"SAFE but below target ({result.skip_rate:.1%} < 50%)")
    else:
        print(f"FAIL - {len(result.critical_missed)} critical misses")
    print()


def print_analysis_report(records: list[dict], filepath: str) -> None:
    """Analyze a log that was generated with the scheduler active."""
    states = [r for r in records if r.get("type") == "state"]
    skips = [r for r in records if r.get("type") == "skip"]
    summary = next((r for r in records if r.get("type") == "summary"), None)

    total = len(states) + len(skips)
    print(f"\n{'='*60}")
    print(f"INFO-GAIN ANALYSIS: {os.path.basename(filepath)}")
    print(f"{'='*60}")
    print(f"  Total opportunities: {total}")
    print(f"  Observed (state):    {len(states)} ({len(states)/max(total,1):.1%})")
    print(f"  Skipped:             {len(skips)} ({len(skips)/max(total,1):.1%})")

    if summary and "info_gain" in summary:
        ig = summary["info_gain"]
        print(f"\n  Scheduler stats from summary:")
        for k, v in ig.items():
            print(f"    {k}: {v}")

    if skips:
        reasons = {}
        for s in skips:
            r = s.get("reason", "unknown")
            reasons[r] = reasons.get(r, 0) + 1
        print(f"\n  Skip reasons:")
        for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
            print(f"    {reason}: {count}")

    if summary:
        print(f"\n  Game result: {summary.get('result', '?')}")
        print(f"  Game time: {summary.get('game_time', 0):.1f}s")
    print()


# -------------------------------------------------------------------------
# Main
# -------------------------------------------------------------------------

def main():
    args = sys.argv[1:]

    simulate_budget_val = None
    filepath = None

    i = 0
    while i < len(args):
        if args[i] == "--simulate" and i + 1 < len(args):
            simulate_budget_val = float(args[i + 1])
            i += 2
        elif args[i] == "--compare":
            print("Comparison mode not yet implemented. Run simulation at multiple budgets.")
            return
        else:
            filepath = args[i]
            i += 1

    if filepath is None:
        filepath = find_latest_log()
        if filepath is None:
            print("No log files found in data/logs/")
            return

    print(f"Loading: {filepath}")
    records = load_jsonl(filepath)
    print(f"Loaded {len(records)} records")

    if simulate_budget_val is not None:
        # Simulation mode: run at specified budget
        result = simulate_budget(records, simulate_budget_val)
        print_simulation_report(result, filepath)

        # Also run sweep at standard budgets
        print(f"\n{'='*60}")
        print("BUDGET SWEEP")
        print(f"{'='*60}")
        for budget in [1.0, 0.75, 0.5, 0.25, 0.1]:
            r = simulate_budget(records, budget)
            missed_str = f" MISSED: {len(r.critical_missed)}" if r.critical_missed else ""
            print(
                f"  budget={budget:.2f}  "
                f"observed={r.observed:4d} ({r.observation_rate:.1%})  "
                f"skipped={r.skipped:4d} ({r.skip_rate:.1%})  "
                f"critical={r.critical_total}{missed_str}"
            )
    else:
        # Check if this log has skip records (scheduler was active)
        has_skips = any(r.get("type") == "skip" for r in records)
        if has_skips:
            print_analysis_report(records, filepath)
        else:
            # No scheduler — offer simulation
            print("No scheduler data found. Running simulation sweep...")
            critical = find_critical_frames(records)
            states = [r for r in records if r.get("type") == "state"]
            print(f"  State frames: {len(states)}")
            print(f"  Critical frames: {len(critical)}")

            print(f"\n{'='*60}")
            print("BUDGET SWEEP")
            print(f"{'='*60}")
            for budget in [1.0, 0.75, 0.5, 0.25, 0.1]:
                r = simulate_budget(records, budget)
                missed_str = f" MISSED: {len(r.critical_missed)}" if r.critical_missed else ""
                status = "PASS" if r.skip_rate >= 0.50 and not r.critical_missed else "    "
                print(
                    f"  {status} budget={budget:.2f}  "
                    f"observed={r.observed:4d} ({r.observation_rate:.1%})  "
                    f"skipped={r.skipped:4d} ({r.skip_rate:.1%})  "
                    f"critical={r.critical_total}{missed_str}"
                )


if __name__ == "__main__":
    main()
