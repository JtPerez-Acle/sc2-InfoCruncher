"""Game log analyzer — digests JSONL into a concise, actionable report.

Usage:
    poetry run python scripts/analyze_game.py                     # latest log
    poetry run python scripts/analyze_game.py path/to/file.jsonl  # specific log

Output: prints report to stdout AND saves .report.txt next to the log.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


# ===========================================================================
# ANSI colors for terminal output
# ===========================================================================

GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _c(text: str, color: str) -> str:
    """Colorize text (stripped when writing to file)."""
    return f"{color}{text}{RESET}"


# ===========================================================================
# I/O
# ===========================================================================

def load_records(path: Path) -> list[dict]:
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def find_latest_log() -> Path:
    log_dir = Path("data/logs")
    logs = sorted(log_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    if not logs:
        print("No log files found in data/logs/")
        sys.exit(1)
    return logs[-1]


# ===========================================================================
# Helpers
# ===========================================================================

def _fmt_dict(d: dict, top_n: int = 10) -> str:
    """Format dict sorted by value descending, limited to top_n."""
    items = sorted(d.items(), key=lambda x: -x[1])[:top_n]
    return ", ".join(f"{k}:{v}" for k, v in items) if items else "(none)"


def _fmt_time(secs: float) -> str:
    """Format seconds as M:SS."""
    m, s = divmod(int(secs), 60)
    return f"{m}:{s:02d}"


def _bucket_timeline(data: list[tuple[float, float]], buckets: int = 8) -> list[tuple[str, float]]:
    """Bucket time-series data into N equal intervals."""
    if not data:
        return []
    min_t = data[0][0]
    max_t = data[-1][0]
    if max_t <= min_t:
        return [(_fmt_time(min_t), data[0][1])]

    interval = (max_t - min_t) / buckets
    result = []
    for i in range(buckets):
        t_start = min_t + i * interval
        t_end = t_start + interval
        bucket_vals = [v for t, v in data if t_start <= t < t_end]
        if bucket_vals:
            avg = sum(bucket_vals) / len(bucket_vals)
            result.append((_fmt_time(t_start), avg))
    return result


def _bar(value: float, max_val: float, width: int = 20) -> str:
    """Simple ASCII bar chart."""
    if max_val <= 0:
        return "." * width
    filled = int(min(value / max_val, 1.0) * width)
    return "#" * filled + "." * (width - filled)


def section_header(title: str) -> list[str]:
    return [f"\n{'='*70}", f"  {title}", "=" * 70]


# ===========================================================================
# Section: Game Result
# ===========================================================================

def analyze_summary(summary: dict) -> list[str]:
    lines = section_header("GAME RESULT")
    s = summary
    result = s.get("result", "Unknown")
    tag = "WIN" if result == "Victory" else "LOSS" if result == "Defeat" else result.upper()
    lines.append(f"  [{tag}] {result} on {s.get('map_name', '?')} vs {s.get('enemy_race', '?')}")
    gt = s.get("game_time", 0)
    lines.append(f"  Duration: {_fmt_time(gt)} ({gt:.0f}s) | Real time: {s.get('real_duration', 0):.0f}s")
    lines.append("")
    lines.append(f"  Peak supply: {s.get('peak_supply', 0)}  |  Peak army: {s.get('peak_army_supply', 0)}  |  Peak workers: {s.get('peak_worker_count', 0)}")
    bo = s.get("build_order_completed_time", 0)
    lines.append(f"  Build order done: {_fmt_time(bo) if bo else 'N/A'}")
    fa = s.get("first_attack_time", 0)
    fe = s.get("first_expansion_time", 0)
    lines.append(f"  First attack: {_fmt_time(fa) if fa else 'N/A'}  |  First expand: {_fmt_time(fe) if fe else 'N/A'}")
    lines.append(f"  Resources collected: {s.get('total_minerals_collected', 0):.0f} min / {s.get('total_vespene_collected', 0):.0f} gas")
    lines.append("")

    # Combat time breakdown
    chase = s.get("time_in_chase", 0)
    attack = s.get("time_in_attack", 0)
    retreat = s.get("time_in_retreat", 0)
    regroup = s.get("time_in_regroup", 0)
    total_combat = chase + attack + retreat + regroup
    lines.append(f"  Combat time: CHASE {chase:.0f}s | ATTACK {attack:.0f}s | RETREAT {retreat:.0f}s | REGROUP {regroup:.0f}s")
    if total_combat > 0:
        lines.append(f"  Offensive %: {(chase + attack) / total_combat * 100:.0f}% of combat time")
    lines.append(f"  Army wipes: {s.get('army_wipes', 0)}")
    lines.append("")

    # Units
    produced = s.get("units_produced", {})
    lost = s.get("units_lost", {})
    killed = s.get("enemy_units_killed", {})
    skip = {"LARVA", "DRONE", "OVERLORD", "EGG"}
    combat_produced = {k: v for k, v in produced.items() if k not in skip}
    combat_lost = {k: v for k, v in lost.items() if k not in skip}

    lines.append(f"  Army produced: {_fmt_dict(combat_produced)}")
    lines.append(f"  Army lost:     {_fmt_dict(combat_lost)}")
    lines.append(f"  Enemy killed:  {_fmt_dict(killed)}")
    lines.append(f"  Structures:    {_fmt_dict(s.get('structures_built', {}))}")
    lines.append(f"  Struct lost:   {_fmt_dict(s.get('structures_lost', {}))}")

    # Enemy tracking stats
    lines.append("")
    lines.append(f"  Enemy tags tracked: {s.get('enemy_tags_tracked', 0)}")
    unknown_kills = killed.get("UNKNOWN", 0)
    total_kills = sum(killed.values()) if killed else 0
    if total_kills > 0:
        pct = unknown_kills / total_kills * 100
        lines.append(f"  Kill resolution: {total_kills - unknown_kills}/{total_kills} identified ({100 - pct:.0f}%)")

    return lines


# ===========================================================================
# Section: Engagement Analysis (Phase 4 ML data)
# ===========================================================================

def analyze_engagements(engagements: list[dict], summary: dict) -> list[str]:
    lines = section_header("ENGAGEMENT ANALYSIS")

    if not engagements:
        lines.append("  No engagement records found")
        # Check summary for tracker stats
        tracker_stats = summary.get("engagement_tracker", {})
        if tracker_stats:
            lines.append(f"  (Tracker stats from summary: {tracker_stats})")
        return lines

    lines.append(f"  Total engagements: {len(engagements)}")

    # Outcome distribution
    outcomes = Counter(e.get("actual_outcome", "?") for e in engagements)
    total = len(engagements)
    lines.append("")
    lines.append("  --- Outcomes ---")
    for outcome in ["won", "lost", "draw", "disengaged"]:
        count = outcomes.get(outcome, 0)
        pct = count / total * 100 if total else 0
        lines.append(f"    {outcome:>11s}: {count:>3d} ({pct:4.0f}%)  [{_bar(count, total)}]")

    # Prediction accuracy
    correct = sum(1 for e in engagements if e.get("prediction_correct"))
    meaningful = [e for e in engagements if e.get("actual_outcome") != "disengaged"]
    correct_meaningful = sum(1 for e in meaningful if e.get("prediction_correct"))
    lines.append("")
    lines.append(f"  Prediction accuracy: {correct}/{total} overall ({correct / total * 100:.0f}%)")
    if meaningful:
        lines.append(f"  Prediction accuracy: {correct_meaningful}/{len(meaningful)} excl. disengaged ({correct_meaningful / len(meaningful) * 100:.0f}%)")

    # Duration stats
    durations = [e.get("duration", 0) for e in engagements]
    lines.append("")
    lines.append(f"  Duration: avg {sum(durations) / len(durations):.1f}s | "
                 f"min {min(durations):.1f}s | max {max(durations):.1f}s | "
                 f"total {sum(durations):.0f}s")

    # Supply efficiency
    total_own_lost = sum(e.get("own_supply_lost", 0) for e in engagements)
    total_enemy_lost = sum(e.get("enemy_supply_lost", 0) for e in engagements)
    lines.append("")
    lines.append(f"  Supply traded: own {total_own_lost:.0f} lost | enemy {total_enemy_lost:.0f} lost")
    if total_own_lost > 0:
        lines.append(f"  Trade ratio: {total_enemy_lost / total_own_lost:.2f}x "
                     f"({'favorable' if total_enemy_lost > total_own_lost else 'unfavorable'})")

    # Per-engagement details (chronological, top 10 most impactful)
    lines.append("")
    lines.append("  --- Top Engagements (by total casualties) ---")
    sorted_eng = sorted(engagements, key=lambda e: e.get("own_supply_lost", 0) + e.get("enemy_supply_lost", 0), reverse=True)
    lines.append(f"  {'Time':>6s}  {'Dur':>4s}  {'Result':>10s}  {'Own Lost':>8s}  {'Ene Lost':>8s}  {'Sim':>12s}  {'Objective':>15s}  {'Profile':>10s}")
    lines.append(f"  {'----':>6s}  {'---':>4s}  {'------':>10s}  {'--------':>8s}  {'--------':>8s}  {'---':>12s}  {'---------':>15s}  {'-------':>10s}")
    for e in sorted_eng[:10]:
        t = _fmt_time(e.get("game_time_start", 0))
        dur = f"{e.get('duration', 0):.0f}s"
        outcome = e.get("actual_outcome", "?")
        own_lost = f"{e.get('own_supply_lost', 0):.0f}"
        ene_lost = f"{e.get('enemy_supply_lost', 0):.0f}"
        sim = e.get("sim_prediction", "?")[:12]
        obj = e.get("combat_objective", "?")[:15]
        profile = e.get("engagement_profile", "?")[:10]
        correct = "Y" if e.get("prediction_correct") else "N"
        lines.append(f"  {t:>6s}  {dur:>4s}  {outcome:>10s}  {own_lost:>8s}  {ene_lost:>8s}  {sim:>12s}  {obj:>15s}  {profile:>10s}")

    # Chronological engagement timeline
    lines.append("")
    lines.append("  --- Engagement Timeline ---")
    for e in engagements:
        t = _fmt_time(e.get("game_time_start", 0))
        outcome = e.get("actual_outcome", "?")
        own_lost = e.get("own_supply_lost", 0)
        ene_lost = e.get("enemy_supply_lost", 0)
        own_before = e.get("own_army_before", {})
        enemy_before = e.get("enemy_army_before", {})
        own_comp = _fmt_dict(own_before.get("units", {}), top_n=3)
        enemy_comp = _fmt_dict(enemy_before.get("units", {}), top_n=3)
        lines.append(f"    {t}: {outcome:>10s} | -{own_lost:.0f} own / -{ene_lost:.0f} enemy | "
                     f"us({own_comp}) vs them({enemy_comp})")

    # Profile effectiveness
    profile_stats: dict[str, dict] = {}
    for e in engagements:
        p = e.get("engagement_profile", "DEFAULT")
        if p not in profile_stats:
            profile_stats[p] = {"total": 0, "won": 0, "lost": 0, "own_lost": 0, "ene_lost": 0}
        profile_stats[p]["total"] += 1
        if e.get("actual_outcome") == "won":
            profile_stats[p]["won"] += 1
        elif e.get("actual_outcome") == "lost":
            profile_stats[p]["lost"] += 1
        profile_stats[p]["own_lost"] += e.get("own_supply_lost", 0)
        profile_stats[p]["ene_lost"] += e.get("enemy_supply_lost", 0)

    if len(profile_stats) > 1:
        lines.append("")
        lines.append("  --- Profile Effectiveness ---")
        for p, stats in sorted(profile_stats.items(), key=lambda x: -x[1]["total"]):
            wr = stats["won"] / stats["total"] * 100 if stats["total"] else 0
            trade = stats["ene_lost"] / stats["own_lost"] if stats["own_lost"] > 0 else float("inf")
            lines.append(f"    {p:>12s}: {stats['total']}x | WR {wr:.0f}% | Trade {trade:.2f}x")

    return lines


# ===========================================================================
# Section: Discovery Timeline (Phase 2)
# ===========================================================================

def analyze_discovery_timeline(events: list[dict], summary: dict) -> list[str]:
    lines = section_header("OPPONENT DISCOVERY TIMELINE")

    # First-observed events
    first_obs = [e for e in events if e.get("event") == "first_observed"]

    # Also check summary for discovery_timeline
    disc_timeline = summary.get("discovery_timeline", [])

    if not first_obs and not disc_timeline:
        lines.append("  No first-observation data found")
        return lines

    if first_obs:
        lines.append("  --- First Observations (from events) ---")
        first_obs.sort(key=lambda e: e.get("game_time", 0))
        for e in first_obs:
            t = _fmt_time(e.get("game_time", 0))
            ut = e.get("unit_type", "?")
            pos = e.get("extra", {}).get("position", [])
            pos_str = f" at ({pos[0]:.0f}, {pos[1]:.0f})" if len(pos) >= 2 else ""
            lines.append(f"    {t}: {ut}{pos_str}")

    if disc_timeline:
        lines.append("")
        lines.append("  --- Discovery Timeline (from summary, with peak counts) ---")
        for entry in disc_timeline:
            t = _fmt_time(entry.get("time", 0))
            ut = entry.get("unit_type", "?")
            peak = entry.get("peak_count", 0)
            lines.append(f"    {t}: {ut} (peak: {peak})")

    return lines


# ===========================================================================
# Section: Enemy Sighting Analysis (Phase 3)
# ===========================================================================

def analyze_enemy_sightings(events: list[dict]) -> list[str]:
    lines = section_header("ENEMY SIGHTING ANALYSIS")

    sightings = [e for e in events if e.get("event") == "enemy_sighted"]
    if not sightings:
        lines.append("  No enemy sighting events found")
        return lines

    lines.append(f"  Total unique enemy units sighted: {len(sightings)}")

    # By type
    type_counts = Counter(e.get("unit_type", "?") for e in sightings)
    lines.append("")
    lines.append("  --- Sighted by Type ---")
    for ut, count in type_counts.most_common(15):
        lines.append(f"    {ut:>20s}: {count:>3d}")

    # Sighting rate over time (bucketed)
    sighting_times = [e.get("game_time", 0) for e in sightings]
    if sighting_times:
        min_t = min(sighting_times)
        max_t = max(sighting_times)
        if max_t > min_t:
            bucket_size = max(30, (max_t - min_t) / 10)
            lines.append("")
            lines.append("  --- Sighting Rate (new units per interval) ---")
            t = min_t
            while t < max_t:
                count = sum(1 for st in sighting_times if t <= st < t + bucket_size)
                lines.append(f"    {_fmt_time(t):>6s}: {count:>3d} new  [{_bar(count, 20)}]")
                t += bucket_size

    return lines


# ===========================================================================
# Section: Enhanced Economy (Phase 1 data)
# ===========================================================================

def analyze_enhanced_economy(states: list[dict]) -> list[str]:
    lines = section_header("ECONOMY & SPENDING ANALYSIS")

    if not states:
        lines.append("  No state snapshots")
        return lines

    game_dur = states[-1].get("game_time", 0)

    # Economy timeline with income rates
    sample_times = list(range(0, int(game_dur) + 1, 60))
    if game_dur not in sample_times:
        sample_times.append(game_dur)

    has_income = any("income_minerals" in s for s in states)
    if has_income:
        lines.append(f"  {'Time':>6s}  {'Min':>5s}  {'Gas':>5s}  {'Sup':>7s}  {'Wrk':>4s}  {'Army':>4s}  {'Base':>4s}  {'Inc M':>6s}  {'Inc G':>6s}  {'Sat':>5s}  {'Mode':>8s}")
        lines.append(f"  {'----':>6s}  {'---':>5s}  {'---':>5s}  {'-------':>7s}  {'---':>4s}  {'----':>4s}  {'----':>4s}  {'-----':>6s}  {'-----':>6s}  {'---':>5s}  {'--------':>8s}")
    else:
        lines.append(f"  {'Time':>6s}  {'Min':>5s}  {'Gas':>5s}  {'Sup':>7s}  {'Wrk':>4s}  {'Army':>4s}  {'Base':>4s}  {'Ext':>3s}  {'Mode':>8s}")
        lines.append(f"  {'----':>6s}  {'---':>5s}  {'---':>5s}  {'-------':>7s}  {'---':>4s}  {'----':>4s}  {'----':>4s}  {'---':>3s}  {'--------':>8s}")

    for target_time in sample_times:
        closest = min(states, key=lambda s: abs(s.get("game_time", 0) - target_time))
        t = _fmt_time(closest.get("game_time", 0))
        mins = closest.get("minerals", 0)
        gas = closest.get("vespene", 0)
        sup = f"{closest.get('supply_used', 0)}/{closest.get('supply_cap', 0)}"
        workers = closest.get("supply_workers", 0)
        army = closest.get("supply_army", 0)
        structs = closest.get("own_structures", {})
        bases = sum(structs.get(b, 0) for b in ("HATCHERY", "LAIR", "HIVE"))
        mode = closest.get("combat_mode", "?")

        if has_income:
            inc_m = closest.get("income_minerals", 0)
            inc_g = closest.get("income_vespene", 0)
            sat = closest.get("saturation_ratio", 0)
            lines.append(f"  {t:>6s}  {mins:>5d}  {gas:>5d}  {sup:>7s}  {workers:>4d}  {army:>4d}  {bases:>4d}  {inc_m:>6.0f}  {inc_g:>6.0f}  {sat:>5.2f}  {mode:>8s}")
        else:
            extractors = structs.get("EXTRACTOR", 0)
            lines.append(f"  {t:>6s}  {mins:>5d}  {gas:>5d}  {sup:>7s}  {workers:>4d}  {army:>4d}  {bases:>4d}  {extractors:>3d}  {mode:>8s}")

    # Spending diagnosis timeline
    spending_states = [s for s in states if any(s.get(k) for k in ("is_larva_bound", "is_tech_bound", "is_supply_bound", "is_gas_bound"))]
    if spending_states:
        lines.append("")
        lines.append("  --- Spending Bottlenecks ---")
        bottleneck_counts = {
            "larva_bound": sum(1 for s in states if s.get("is_larva_bound")),
            "tech_bound": sum(1 for s in states if s.get("is_tech_bound")),
            "supply_bound": sum(1 for s in states if s.get("is_supply_bound")),
            "gas_bound": sum(1 for s in states if s.get("is_gas_bound")),
        }
        total_states = len(states)
        for name, count in sorted(bottleneck_counts.items(), key=lambda x: -x[1]):
            pct = count / total_states * 100
            lines.append(f"    {name:>14s}: {count:>4d} snapshots ({pct:4.1f}%)  [{_bar(count, total_states)}]")

    # Resource float detection
    float_times = [(s["game_time"], s["minerals"]) for s in states if s.get("minerals", 0) > 800]
    if float_times:
        lines.append(f"\n  !! Minerals > 800 for {len(float_times)} snapshots "
                     f"({_fmt_time(float_times[0][0])} - {_fmt_time(float_times[-1][0])})")
        peak_float = max(float_times, key=lambda x: x[1])
        lines.append(f"     Peak mineral float: {peak_float[1]} at {_fmt_time(peak_float[0])}")

    # Supply blocks
    blocks = []
    for s in states:
        used = s.get("supply_used", 0)
        cap = s.get("supply_cap", 0)
        if used >= cap > 0 and cap < 200:
            blocks.append(s.get("game_time", 0))
    if blocks:
        deduped = [blocks[0]]
        for b in blocks[1:]:
            if b - deduped[-1] > 5:
                deduped.append(b)
        lines.append(f"\n  !! Supply blocked {len(deduped)} times: "
                     f"{', '.join(_fmt_time(t) for t in deduped[:10])}"
                     + (f" ... +{len(deduped) - 10} more" if len(deduped) > 10 else ""))

    return lines


# ===========================================================================
# Section: Strategic Intelligence (Phase 1 enhanced data)
# ===========================================================================

def analyze_strategic_intel(states: list[dict], decisions: list[dict]) -> list[str]:
    lines = section_header("STRATEGIC INTELLIGENCE")

    # Archetype detection from snapshots
    archetype_states = [(s.get("game_time", 0), s.get("enemy_archetype", ""))
                        for s in states if s.get("enemy_archetype")]
    if archetype_states:
        lines.append("  --- Enemy Archetype Detection ---")
        prev_arch = None
        for t, arch in archetype_states:
            if arch != prev_arch:
                conf = 0
                for s in states:
                    if abs(s.get("game_time", 0) - t) < 1 and s.get("enemy_archetype") == arch:
                        conf = s.get("enemy_archetype_confidence", 0)
                        break
                lines.append(f"    {_fmt_time(t)}: {arch} (confidence: {conf:.2f})")
                prev_arch = arch

    # Threat level timeline
    threat_data = [(s.get("game_time", 0), s.get("threat_level", 0))
                   for s in states if "threat_level" in s]
    if threat_data:
        lines.append("")
        lines.append("  --- Threat Level Timeline ---")
        buckets = _bucket_timeline(threat_data, buckets=8)
        for label, val in buckets:
            lines.append(f"    {label:>6s}: [{_bar(val, 1.0)}] {val:.2f}")

    # Army advantage timeline
    adv_data = [(s.get("game_time", 0), s.get("army_advantage", 1.0))
                for s in states if "army_advantage" in s]
    if adv_data:
        lines.append("")
        lines.append("  --- Army Advantage Timeline ---")
        buckets = _bucket_timeline(adv_data, buckets=8)
        for label, val in buckets:
            capped = min(val, 5.0)
            marker = " <-EVEN" if 0.9 <= val <= 1.1 else (" <-AHEAD" if val > 1.1 else " <-BEHIND")
            lines.append(f"    {label:>6s}: [{_bar(capped, 5.0)}] {val:.1f}x{marker}")

    # Info staleness
    stale_data = [(s.get("game_time", 0), s.get("info_staleness", 0))
                  for s in states if "info_staleness" in s]
    if stale_data:
        lines.append("")
        lines.append("  --- Info Staleness ---")
        buckets = _bucket_timeline(stale_data, buckets=8)
        for label, val in buckets:
            warning = " !! STALE" if val > 90 else ""
            lines.append(f"    {label:>6s}: {val:>5.0f}s{warning}")

    # Strategy decisions (from analyzer)
    strategy_decs = [d for d in decisions if d.get("module") == "strategy"]
    if strategy_decs:
        lines.append("")
        lines.append(f"  Total strategy evaluations: {len(strategy_decs)}")

        # Enemy tech progression
        enemy_techs = [(d.get("game_time", 0), d.get("details", {}).get("enemy_tech", "?"))
                       for d in strategy_decs]
        tech_transitions = []
        prev_tech = None
        for t, tech in enemy_techs:
            if tech != prev_tech:
                tech_transitions.append(f"{_fmt_time(t)}={tech}")
                prev_tech = tech
        lines.append(f"  Enemy tech: {' -> '.join(tech_transitions)}")

        # Comp evolution
        lines.append("")
        lines.append("  --- Recommended Comp Evolution ---")
        comp_changes = []
        prev_comp = None
        for d in strategy_decs:
            t = d.get("game_time", 0)
            comp = d.get("details", {}).get("recommended_comp", {})
            comp_key = str(sorted(comp.items()))
            if comp_key != prev_comp and comp:
                comp_str = ", ".join(f"{k}:{v:.0%}" for k, v in sorted(comp.items(), key=lambda x: -x[1]))
                comp_changes.append(f"    {_fmt_time(t):>6s}: {comp_str}")
                prev_comp = comp_key
        if len(comp_changes) > 10:
            lines.extend(comp_changes[:5])
            lines.append(f"    ... ({len(comp_changes) - 10} more transitions) ...")
            lines.extend(comp_changes[-5:])
        else:
            lines.extend(comp_changes if comp_changes else ["    (no comp data)"])

        # Economy decisions
        drone_targets = [d.get("details", {}).get("target_drones", 0) for d in strategy_decs]
        gas_targets = [d.get("details", {}).get("target_gas", 0) for d in strategy_decs]
        base_targets = [d.get("details", {}).get("target_bases", 0) for d in strategy_decs]
        army_ratios = [d.get("details", {}).get("army_ratio", 0.5) for d in strategy_decs]

        if drone_targets and any(v > 0 for v in drone_targets):
            lines.append("")
            lines.append("  --- Economy Targets ---")
            lines.append(f"    Drone target: {min(drone_targets)}-{max(drone_targets)} (avg {sum(drone_targets) / len(drone_targets):.0f})")
            lines.append(f"    Gas target:   {min(gas_targets)}-{max(gas_targets)} (avg {sum(gas_targets) / len(gas_targets):.1f})")
            lines.append(f"    Base target:  {min(base_targets)}-{max(base_targets)}")
            lines.append(f"    Army ratio:   {min(army_ratios):.2f}-{max(army_ratios):.2f} (avg {sum(army_ratios) / len(army_ratios):.2f})")

        # Attack readiness
        attack_ready_times = [d.get("game_time", 0) for d in strategy_decs
                              if d.get("details", {}).get("attack_ready")]
        if attack_ready_times:
            lines.append(f"\n  First attack_ready=True at: {_fmt_time(min(attack_ready_times))}")
            lines.append(f"  Times attack_ready: {len(attack_ready_times)}/{len(strategy_decs)}")
        else:
            lines.append(f"\n  !! attack_ready was NEVER True!")

    if not archetype_states and not threat_data and not strategy_decs:
        lines.append("  No strategic intelligence data found")

    return lines


# ===========================================================================
# Section: Combat System Analysis (cycle_summary decisions)
# ===========================================================================

def analyze_combat_system(decisions: list[dict]) -> list[str]:
    lines = section_header("COMBAT SYSTEM")

    # New-style combat_system decisions
    cs_decs = [d for d in decisions if d.get("module") == "combat_system"]
    # Old-style combat decisions
    combat_decs = [d for d in decisions if d.get("module") == "combat"]

    if not cs_decs and not combat_decs:
        lines.append("  No combat decisions logged")
        return lines

    # Combat system cycle summaries
    cycles = [d for d in cs_decs if d.get("action") == "cycle_summary"]
    if cycles:
        lines.append(f"  Combat cycles logged: {len(cycles)}")

        # Objective distribution
        obj_counts = Counter(d.get("details", {}).get("objective", "?") for d in cycles)
        lines.append("")
        lines.append("  --- Objective Distribution ---")
        for obj, count in obj_counts.most_common():
            pct = count / len(cycles) * 100
            lines.append(f"    {obj:>20s}: {count:>4d} ({pct:4.1f}%)  [{_bar(count, len(cycles))}]")

        # Confidence over time
        conf_data = [(d.get("game_time", 0), d.get("details", {}).get("confidence", 0.5))
                     for d in cycles]
        if conf_data:
            lines.append("")
            lines.append("  --- Confidence Timeline ---")
            buckets = _bucket_timeline(conf_data, buckets=8)
            for label, val in buckets:
                lines.append(f"    {label:>6s}: [{_bar(val, 1.0)}] {val:.2f}")

        # Profile distribution
        profile_counts = Counter(d.get("details", {}).get("profile", "?") for d in cycles)
        if len(profile_counts) > 1:
            lines.append("")
            lines.append("  --- Profile Used ---")
            for profile, count in profile_counts.most_common(5):
                lines.append(f"    {profile:>15s}: {count:>4d}")

    # Role promotions
    promotions = [d for d in cs_decs if d.get("action") == "promote_rallying"]
    if promotions:
        total_promoted = sum(d.get("details", {}).get("count", 0) for d in promotions)
        lines.append(f"\n  Role promotions: {len(promotions)} batches, {total_promoted} total units")

    # Old-style combat decisions
    if combat_decs:
        action_counts = Counter(d.get("action", "?") for d in combat_decs)
        lines.append(f"\n  Legacy combat decisions: {dict(action_counts.most_common())}")

        evals = [d for d in combat_decs if d.get("action") == "evaluate_engagement"]
        if evals:
            sim_results = Counter(d["details"].get("sim_result", "?") for d in evals)
            lines.append(f"  Sim results: {dict(sim_results.most_common())}")

        defends = [d for d in combat_decs if d.get("action") in ("defend", "early_defense")]
        if defends:
            lines.append(f"  Defense events: {len(defends)}")
            for d in defends[:3]:
                det = d.get("details", {})
                lines.append(f"    {_fmt_time(d.get('game_time', 0))}: "
                             f"{det.get('threat_type', det.get('threat_count', '?'))} threats")

    return lines


# ===========================================================================
# Section: Army Composition Timeline
# ===========================================================================

def analyze_army_timeline(states: list[dict]) -> list[str]:
    lines = section_header("ARMY COMPOSITION TIMELINE")

    combat_types = {"ZERGLING", "BANELING", "ROACH", "RAVAGER", "HYDRALISK",
                    "LURKERMP", "MUTALISK", "CORRUPTOR", "INFESTOR", "VIPER",
                    "ULTRALISK", "BROODLORD", "SWARMHOSTMP"}

    game_dur = states[-1].get("game_time", 0) if states else 0
    sample_times = list(range(0, int(game_dur) + 1, 60))
    if game_dur not in sample_times:
        sample_times.append(game_dur)

    for target_time in sample_times:
        closest = min(states, key=lambda s: abs(s.get("game_time", 0) - target_time))
        t = _fmt_time(closest.get("game_time", 0))
        units = closest.get("own_units", {})
        army = {k: v for k, v in units.items() if k in combat_types and v > 0}
        if army:
            comp_str = ", ".join(f"{k}:{v}" for k, v in sorted(army.items(), key=lambda x: -x[1]))
            lines.append(f"  {t:>6s}: {comp_str}")
        else:
            lines.append(f"  {t:>6s}: (no army)")

    return lines


# ===========================================================================
# Section: Enemy Observations
# ===========================================================================

def analyze_enemy_observations(states: list[dict]) -> list[str]:
    lines = section_header("ENEMY OBSERVATIONS (from snapshots)")

    first_seen_units: dict[str, float] = {}
    first_seen_structs: dict[str, float] = {}

    for s in states:
        t = s.get("game_time", 0)
        for unit in s.get("enemy_units", {}):
            if unit not in first_seen_units:
                first_seen_units[unit] = t
        for struct in s.get("enemy_structures", {}):
            if struct not in first_seen_structs:
                first_seen_structs[struct] = t

    if first_seen_units:
        lines.append("  Enemy units first seen:")
        for unit, t in sorted(first_seen_units.items(), key=lambda x: x[1]):
            lines.append(f"    {_fmt_time(t):>6s}: {unit}")

    if first_seen_structs:
        lines.append("\n  Enemy structures first seen:")
        for struct, t in sorted(first_seen_structs.items(), key=lambda x: x[1]):
            lines.append(f"    {_fmt_time(t):>6s}: {struct}")

    return lines


# ===========================================================================
# Section: Macro Decisions
# ===========================================================================

def analyze_macro_decisions(decisions: list[dict]) -> list[str]:
    lines = section_header("MACRO DECISIONS")

    macro_decs = [d for d in decisions if d.get("module") == "macro"]
    if not macro_decs:
        lines.append("  No macro decisions logged")
        return lines

    action_counts = Counter(d.get("action", "?") for d in macro_decs)
    lines.append(f"  Total: {len(macro_decs)} | Actions: {dict(action_counts.most_common())}")

    # Tech/build decisions
    tech_decs = [d for d in macro_decs
                 if any(kw in d.get("action", "").lower()
                        for kw in ("tech", "build", "expand", "structure", "lair", "hive"))]
    if tech_decs:
        lines.append(f"\n  Tech/Build decisions ({len(tech_decs)}):")
        for d in tech_decs[:15]:
            t = _fmt_time(d.get("game_time", 0))
            det_str = json.dumps(d.get("details", {}), default=str)
            if len(det_str) > 80:
                det_str = det_str[:80] + "..."
            lines.append(f"    {t:>6s}: {d['action']} {det_str}")

    return lines


# ===========================================================================
# Section: Issues & Recommendations
# ===========================================================================

def analyze_issues(summary: dict, states: list[dict], decisions: list[dict],
                   engagements: list[dict]) -> list[str]:
    lines = section_header("ISSUES & RECOMMENDATIONS")
    issues: list[tuple[str, str]] = []

    s = summary
    gt = s.get("game_time", 0)

    # 1. Resource float
    float_states = [st for st in states if st.get("minerals", 0) > 800]
    if len(float_states) > 20:
        issues.append(("RED", f"MINERAL FLOAT: {len(float_states)} snapshots with >800 minerals. "
                       "Not spending fast enough."))

    # 2. Low army diversity
    produced = s.get("units_produced", {})
    skip = {"LARVA", "DRONE", "OVERLORD", "EGG"}
    combat_produced = {k: v for k, v in produced.items() if k not in skip}
    if len(combat_produced) <= 2 and gt > 300:
        issues.append(("RED", f"LOW DIVERSITY: Only {list(combat_produced.keys())} produced. "
                       "Counter system may not be working."))

    # 3. Late first attack
    if s.get("first_attack_time", 0) and s["first_attack_time"] > 360:
        issues.append(("YEL", f"LATE ATTACK: First attack at {_fmt_time(s['first_attack_time'])}."))

    # 4. Attack never authorized
    strategy_decs = [d for d in decisions if d.get("module") == "strategy"]
    attack_ready_count = sum(1 for d in strategy_decs if d.get("details", {}).get("attack_ready"))
    if strategy_decs and attack_ready_count == 0:
        issues.append(("RED", "ATTACK_READY NEVER TRUE: Strategy never authorized attack."))

    # 5. Too passive
    chase_attack = s.get("time_in_chase", 0) + s.get("time_in_attack", 0)
    if gt > 300 and chase_attack / gt < 0.15:
        issues.append(("YEL", f"PASSIVE: Only {chase_attack:.0f}s attacking ({chase_attack / gt * 100:.0f}% of game)."))

    # 6. No lair tech
    all_structs_ever = set()
    for st in states:
        all_structs_ever.update(st.get("own_structures", {}).keys())
    if "LAIR" not in all_structs_ever and "HIVE" not in all_structs_ever and gt > 300:
        issues.append(("RED", "NO LAIR: Never morphed Lair. Hydra/lurker/muta locked."))

    # 7. No third base
    final_state = states[-1] if states else {}
    structs = final_state.get("own_structures", {})
    bases = sum(structs.get(b, 0) for b in ("HATCHERY", "LAIR", "HIVE"))
    if bases <= 2 and gt > 300:
        issues.append(("YEL", f"NO 3RD BASE: Only {bases} bases at {_fmt_time(gt)}."))

    # 8. Bad trade ratio
    if engagements:
        own_lost = sum(e.get("own_supply_lost", 0) for e in engagements)
        ene_lost = sum(e.get("enemy_supply_lost", 0) for e in engagements)
        if own_lost > 0 and ene_lost / own_lost < 0.5:
            issues.append(("RED", f"BAD TRADES: Lost {own_lost:.0f} supply, killed only {ene_lost:.0f}. "
                           f"Trade ratio {ene_lost / own_lost:.2f}x — losing fights badly."))

    # 9. Engagement prediction accuracy
    if engagements:
        meaningful = [e for e in engagements if e.get("actual_outcome") != "disengaged"]
        if meaningful:
            accuracy = sum(1 for e in meaningful if e.get("prediction_correct")) / len(meaningful)
            if accuracy < 0.4:
                issues.append(("YEL", f"POOR SIM ACCURACY: {accuracy:.0%} prediction rate. "
                               "Combat sim may not match reality."))

    # 10. Trickle attack (short engagements with losses)
    if engagements:
        short_losses = [e for e in engagements
                        if e.get("duration", 0) < 5 and e.get("actual_outcome") == "lost"]
        if len(short_losses) >= 3:
            issues.append(("RED", f"TRICKLE ATTACK: {len(short_losses)} short (<5s) lost engagements. "
                           "Units may be arriving one-by-one instead of grouping."))

    # 11. Spending bottleneck
    if states:
        larva_pct = sum(1 for s in states if s.get("is_larva_bound")) / len(states)
        if larva_pct > 0.5:
            issues.append(("YEL", f"LARVA BOUND: {larva_pct:.0%} of snapshots larva-bound. "
                           "Need more hatcheries or better inject."))
        supply_pct = sum(1 for s in states if s.get("is_supply_bound")) / len(states)
        if supply_pct > 0.3:
            issues.append(("YEL", f"SUPPLY BOUND: {supply_pct:.0%} of snapshots supply-blocked."))

    # 12. Unknown enemy kills
    killed = s.get("enemy_units_killed", {})
    if killed:
        unknown = killed.get("UNKNOWN", 0)
        total_kills = sum(killed.values())
        if total_kills > 0 and unknown / total_kills > 0.3:
            issues.append(("YEL", f"UNKNOWN KILLS: {unknown}/{total_kills} enemy kills unidentified. "
                           "Enemy tag tracking may need improvement."))

    if not issues:
        issues.append(("GRN", "No major issues detected!"))

    for level, msg in issues:
        prefix = {"RED": "[!!]", "YEL": "[! ]", "GRN": "[OK]"}.get(level, "[??]")
        lines.append(f"  {prefix} {msg}")

    return lines


# ===========================================================================
# Section: ML Data Quality Summary
# ===========================================================================

def analyze_ml_readiness(records: list[dict], engagements: list[dict],
                         states: list[dict], events: list[dict],
                         summary: dict) -> list[str]:
    lines = section_header("ML DATA QUALITY")

    checks: list[tuple[str, str]] = []

    # 1. Engagement records
    if engagements:
        meaningful = [e for e in engagements if e.get("actual_outcome") != "disengaged"]
        checks.append(("OK", f"Engagement records: {len(engagements)} total, {len(meaningful)} with combat"))
    else:
        checks.append(("!!", "No engagement records — combat predictor has no training data"))

    # 2. Income rates
    has_income = sum(1 for s in states if "income_minerals" in s)
    if has_income:
        checks.append(("OK", f"Income rates: {has_income}/{len(states)} snapshots"))
    else:
        checks.append(("!!", "No income rate data in snapshots"))

    # 3. Spending diagnosis
    has_spending = sum(1 for s in states if any(s.get(k) is not None for k in
                       ("is_larva_bound", "is_tech_bound", "is_supply_bound")))
    if has_spending:
        checks.append(("OK", f"Spending diagnosis: {has_spending}/{len(states)} snapshots"))
    else:
        checks.append(("  ", "No spending diagnosis data"))

    # 4. Archetype classification
    has_arch = sum(1 for s in states if s.get("enemy_archetype"))
    if has_arch:
        checks.append(("OK", f"Archetype classification: {has_arch}/{len(states)} snapshots"))
    else:
        checks.append(("  ", "No archetype classification data"))

    # 5. First observations
    first_obs = [e for e in events if e.get("event") == "first_observed"]
    if first_obs:
        checks.append(("OK", f"First observations: {len(first_obs)} unique enemy types"))
    else:
        checks.append(("  ", "No first-observation events"))

    # 6. Enemy sighting resolution
    sightings = [e for e in events if e.get("event") == "enemy_sighted"]
    if sightings:
        checks.append(("OK", f"Enemy sightings: {len(sightings)} unique tags tracked"))
    else:
        checks.append(("  ", "No enemy sighting events"))

    # 7. Discovery timeline
    disc = summary.get("discovery_timeline", [])
    if disc:
        checks.append(("OK", f"Discovery timeline: {len(disc)} entries"))
    else:
        checks.append(("  ", "No discovery timeline in summary"))

    # 8. Engagement tracker stats
    eng_stats = summary.get("engagement_tracker", {})
    if eng_stats:
        checks.append(("OK", f"Engagement tracker: {eng_stats.get('total', 0)} tracked, "
                       f"accuracy {eng_stats.get('prediction_accuracy', 0):.1%}"))

    for level, msg in checks:
        prefix = {"OK": "[OK]", "!!": "[!!]"}.get(level, "[  ]")
        lines.append(f"  {prefix} {msg}")

    # Data volume
    file_records = len(records)
    lines.append("")
    lines.append(f"  Total records: {file_records}")
    lines.append(f"  States: {len(states)} | Engagements: {len(engagements)} | Events: {len(events)}")

    return lines


# ===========================================================================
# Main
# ===========================================================================

def main():
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
    else:
        path = find_latest_log()

    records = load_records(path)

    # Categorize records
    states = [r for r in records if r.get("type") == "state"]
    decisions = [r for r in records if r.get("type") == "decision"]
    events = [r for r in records if r.get("type") == "event"]
    engagements = [r for r in records if r.get("type") == "engagement"]
    summaries = [r for r in records if r.get("type") == "summary"]
    skips = [r for r in records if r.get("type") == "skip"]

    summary = summaries[0] if summaries else {}

    # Build report
    report: list[str] = []
    report.append(f"\n{'#' * 70}")
    report.append(f"  GAME ANALYSIS REPORT -- {path.name}")
    report.append(f"  {len(records)} records: {len(states)} states, {len(decisions)} decisions, "
                  f"{len(events)} events, {len(engagements)} engagements, {len(skips)} skips")
    report.append(f"{'#' * 70}")

    report.extend(analyze_summary(summary))
    report.extend(analyze_engagements(engagements, summary))
    report.extend(analyze_enhanced_economy(states))
    report.extend(analyze_strategic_intel(states, decisions))
    report.extend(analyze_combat_system(decisions))
    report.extend(analyze_army_timeline(states))
    report.extend(analyze_discovery_timeline(events, summary))
    report.extend(analyze_enemy_sightings(events))
    report.extend(analyze_enemy_observations(states))
    report.extend(analyze_macro_decisions(decisions))
    report.extend(analyze_issues(summary, states, decisions, engagements))
    report.extend(analyze_ml_readiness(records, engagements, states, events, summary))

    # Print and save
    output = "\n".join(report)
    print(output)

    report_path = path.with_suffix(".report.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(output)
    print(f"\n>> Report saved: {report_path}")


if __name__ == "__main__":
    main()
