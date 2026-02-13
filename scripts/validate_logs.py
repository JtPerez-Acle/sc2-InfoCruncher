#!/usr/bin/env python
"""Post-game log validator and analysis tool.

Reads a JSONL game log, validates its structure, and prints
a human-readable analysis for manual review.

Usage:
    python scripts/validate_logs.py                   # picks most recent log
    python scripts/validate_logs.py data/logs/X.jsonl  # specific file
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


# ── Colours (ANSI) ──────────────────────────────────────────────────────────

GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def ok(msg: str) -> str:
    return f"  {GREEN}[OK]{RESET} {msg}"


def warn(msg: str) -> str:
    return f"  {YELLOW}[WARN]{RESET} {msg}"


def fail(msg: str) -> str:
    return f"  {RED}[FAIL]{RESET} {msg}"


def header(msg: str) -> str:
    return f"\n{BOLD}{CYAN}{'='*60}\n  {msg}\n{'='*60}{RESET}"


# ── Find log file ───────────────────────────────────────────────────────────

def find_log_file(arg: str | None) -> Path:
    """Find the log file to validate."""
    if arg:
        p = Path(arg)
        if p.exists():
            return p
        print(fail(f"File not found: {arg}"))
        sys.exit(1)

    # Pick most recent in data/logs/
    log_dir = Path("data/logs")
    if not log_dir.exists():
        print(fail("data/logs/ directory not found"))
        sys.exit(1)

    files = sorted(log_dir.glob("*.jsonl"), key=lambda f: f.stat().st_mtime)
    if not files:
        print(fail("No .jsonl files in data/logs/"))
        sys.exit(1)

    return files[-1]


# ── Parse ────────────────────────────────────────────────────────────────────

def parse_log(path: Path) -> tuple[list[dict], list[str]]:
    """Parse JSONL, return (records, errors)."""
    records: list[dict] = []
    errors: list[str] = []

    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                if "type" not in record:
                    errors.append(f"Line {i}: missing 'type' field")
                records.append(record)
            except json.JSONDecodeError as e:
                errors.append(f"Line {i}: invalid JSON — {e}")

    return records, errors


# ── Validate ─────────────────────────────────────────────────────────────────

STATE_REQUIRED = {"minerals", "vespene", "supply_used", "own_units", "enemy_units"}
DECISION_REQUIRED = {"module", "action"}
EVENT_REQUIRED = {"event", "unit_type", "unit_tag"}
SUMMARY_REQUIRED = {"result", "peak_supply", "total_snapshots", "total_decisions"}


def validate(records: list[dict]) -> tuple[list[str], list[str]]:
    """Run structural checks. Returns (passes, warnings)."""
    passes: list[str] = []
    warnings: list[str] = []

    by_type: dict[str, list[dict]] = {}
    for r in records:
        t = r.get("type", "unknown")
        by_type.setdefault(t, []).append(r)

    # Check all 4 types present
    for expected in ("state", "decision", "event", "summary"):
        if expected in by_type:
            passes.append(f"Record type '{expected}' present ({len(by_type[expected])} records)")
        else:
            warnings.append(f"Record type '{expected}' MISSING")

    # Validate state snapshots
    for r in by_type.get("state", []):
        missing = STATE_REQUIRED - set(r.keys())
        if missing:
            warnings.append(f"State snapshot at t={r.get('game_time', '?')} missing: {missing}")
            break

    if by_type.get("state") and not any(
        w.startswith("State snapshot") for w in warnings
    ):
        passes.append("All state snapshots have required fields")

    # Validate decisions
    for r in by_type.get("decision", []):
        missing = DECISION_REQUIRED - set(r.keys())
        if missing:
            warnings.append(f"Decision at t={r.get('game_time', '?')} missing: {missing}")
            break

    if by_type.get("decision") and not any(
        w.startswith("Decision") for w in warnings
    ):
        passes.append("All decisions have required fields")

    # Validate events
    for r in by_type.get("event", []):
        missing = EVENT_REQUIRED - set(r.keys())
        if missing:
            warnings.append(f"Event at t={r.get('game_time', '?')} missing: {missing}")
            break

    if by_type.get("event") and not any(
        w.startswith("Event") for w in warnings
    ):
        passes.append("All events have required fields")

    # Validate summary
    summaries = by_type.get("summary", [])
    if len(summaries) == 1:
        s = summaries[0]
        missing = SUMMARY_REQUIRED - set(s.keys())
        if missing:
            warnings.append(f"Summary missing fields: {missing}")
        else:
            passes.append("Summary has all required fields")
    elif len(summaries) > 1:
        warnings.append(f"Multiple summaries found ({len(summaries)})")

    # Check surprise scores
    surprise_scores = []
    for r in by_type.get("state", []):
        delta = r.get("delta")
        if delta and "surprise_score" in delta:
            surprise_scores.append(delta["surprise_score"])

    if surprise_scores:
        if max(surprise_scores) > 0:
            passes.append(f"Surprise scores present and non-zero (max: {max(surprise_scores):.2f})")
        else:
            warnings.append("All surprise scores are 0.0 — delta tracking may be broken")
    elif by_type.get("state"):
        warnings.append("No surprise scores found in state snapshots")

    return passes, warnings


# ── Analysis ─────────────────────────────────────────────────────────────────

def analyze(records: list[dict], file_path: Path) -> None:
    """Print human-readable analysis."""
    by_type: dict[str, list[dict]] = {}
    for r in records:
        by_type.setdefault(r.get("type", "unknown"), []).append(r)

    # ── Overview ──
    print(header("GAME OVERVIEW"))
    summaries = by_type.get("summary", [])
    if summaries:
        s = summaries[0]
        print(f"  Result:    {BOLD}{s.get('result', '?')}{RESET}")
        print(f"  Map:       {s.get('map_name', '?')}")
        game_time = s.get("game_time", 0)
        minutes = int(game_time) // 60
        seconds = int(game_time) % 60
        print(f"  Duration:  {minutes}m {seconds}s (game time)")
        print(f"  Real time: {s.get('real_duration', 0):.0f}s")
        print(f"  Enemy:     {s.get('enemy_race', '?')} ({s.get('enemy_name', '?')})")
    else:
        print(f"  {YELLOW}No summary record found{RESET}")

    # ── Record counts ──
    print(header("RECORD COUNTS"))
    total = len(records)
    for rtype in ("state", "decision", "event", "summary"):
        count = len(by_type.get(rtype, []))
        pct = (count / total * 100) if total else 0
        print(f"  {rtype:12s}  {count:>6d}  ({pct:.1f}%)")
    print(f"  {'TOTAL':12s}  {total:>6d}")

    # File stats
    file_size = file_path.stat().st_size
    if file_size < 1024:
        size_str = f"{file_size} B"
    elif file_size < 1024 * 1024:
        size_str = f"{file_size / 1024:.1f} KB"
    else:
        size_str = f"{file_size / (1024*1024):.1f} MB"

    game_time = summaries[0].get("game_time", 1) if summaries else 1
    records_per_min = total / (game_time / 60) if game_time > 0 else 0
    print(f"\n  File size:       {size_str}")
    print(f"  Record density:  {records_per_min:.0f} records/min")

    # ── Decision breakdown ──
    decisions = by_type.get("decision", [])
    if decisions:
        print(header("DECISIONS BY MODULE"))
        by_module: dict[str, int] = {}
        by_action: dict[str, int] = {}
        for d in decisions:
            mod = d.get("module", "?")
            act = f"{mod}.{d.get('action', '?')}"
            by_module[mod] = by_module.get(mod, 0) + 1
            by_action[act] = by_action.get(act, 0) + 1

        for mod, count in sorted(by_module.items(), key=lambda x: -x[1]):
            print(f"  {mod:12s}  {count:>6d}")

        print(f"\n  Top actions:")
        for act, count in sorted(by_action.items(), key=lambda x: -x[1])[:10]:
            print(f"    {act:40s}  {count:>4d}")

    # ── Event breakdown ──
    events = by_type.get("event", [])
    if events:
        print(header("EVENTS"))
        by_event: dict[str, int] = {}
        for e in events:
            by_event[e.get("event", "?")] = by_event.get(e.get("event", "?"), 0) + 1
        for ev, count in sorted(by_event.items(), key=lambda x: -x[1]):
            print(f"  {ev:30s}  {count:>4d}")

    # ── Top surprise frames ──
    states = by_type.get("state", [])
    scored = []
    for r in states:
        delta = r.get("delta")
        if delta and "surprise_score" in delta:
            scored.append((delta["surprise_score"], r.get("game_time", 0), delta))

    if scored:
        print(header("TOP 5 SURPRISE FRAMES"))
        scored.sort(reverse=True)
        for i, (score, t, delta) in enumerate(scored[:5]):
            mins = int(t) // 60
            secs = int(t) % 60
            changes: list[str] = []
            if delta.get("enemy_units_delta"):
                changes.append(f"enemy: {delta['enemy_units_delta']}")
            if delta.get("own_units_delta"):
                changes.append(f"own: {delta['own_units_delta']}")
            if delta.get("enemy_structures_delta"):
                changes.append(f"enemy_struct: {delta['enemy_structures_delta']}")
            if delta.get("new_upgrades"):
                changes.append(f"upgrades: {delta['new_upgrades']}")
            if delta.get("enemy_army_moved", 0) > 5:
                changes.append(f"enemy_moved: {delta['enemy_army_moved']:.1f}")
            change_str = ", ".join(changes) if changes else "economy/position drift"
            print(f"  #{i+1}  score={score:>7.2f}  t={mins}:{secs:02d}  {change_str}")

    # ── Timeline ──
    if summaries:
        print(header("KEY MOMENTS"))
        s = summaries[0]
        moments = []
        if s.get("first_expansion_time") is not None:
            moments.append((s["first_expansion_time"], "First expansion"))
        if s.get("build_order_completed_time") is not None:
            moments.append((s["build_order_completed_time"], "Build order completed"))
        if s.get("first_attack_time") is not None:
            moments.append((s["first_attack_time"], "First attack"))

        moments.sort()
        for t, label in moments:
            mins = int(t) // 60
            secs = int(t) % 60
            print(f"  {mins}:{secs:02d}  {label}")

        print(f"\n  Peak supply:  {s.get('peak_supply', '?')}")
        print(f"  Peak army:    {s.get('peak_army_supply', '?')}")
        print(f"  Peak workers: {s.get('peak_worker_count', '?')}")

    # ── Surprise stats ──
    if scored:
        scores = [s for s, _, _ in scored]
        avg = sum(scores) / len(scores)
        print(header("SURPRISE STATISTICS"))
        print(f"  Min:    {min(scores):.3f}")
        print(f"  Max:    {max(scores):.3f}")
        print(f"  Mean:   {avg:.3f}")
        print(f"  Frames: {len(scores)}")
        # Distribution buckets
        buckets = {"0": 0, "0-5": 0, "5-15": 0, "15-30": 0, "30+": 0}
        for s in scores:
            if s == 0:
                buckets["0"] += 1
            elif s < 5:
                buckets["0-5"] += 1
            elif s < 15:
                buckets["5-15"] += 1
            elif s < 30:
                buckets["15-30"] += 1
            else:
                buckets["30+"] += 1
        print(f"\n  Distribution:")
        for bucket, count in buckets.items():
            bar = "#" * min(count, 50)
            print(f"    {bucket:>6s}  {count:>4d}  {bar}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    path = find_log_file(arg)

    print(f"\n{BOLD}Validating: {path}{RESET}\n")

    # Parse
    records, parse_errors = parse_log(path)

    if parse_errors:
        print(header("PARSE ERRORS"))
        for e in parse_errors:
            print(fail(e))
        if not records:
            print(fail("No valid records found. Aborting."))
            sys.exit(1)

    # Validate
    passes, warnings = validate(records)

    print(header("VALIDATION"))
    for p in passes:
        print(ok(p))
    for w in warnings:
        print(warn(w))

    total_checks = len(passes) + len(warnings)
    passed = len(passes)
    print(f"\n  {passed}/{total_checks} checks passed")

    if not parse_errors and not warnings:
        print(f"\n  {GREEN}{BOLD}ALL CHECKS PASSED{RESET}")

    # Analysis
    analyze(records, path)

    print()  # final newline


if __name__ == "__main__":
    main()
