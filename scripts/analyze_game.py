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
# Section builders — each returns a list of report lines
# ===========================================================================

def section_header(title: str) -> list[str]:
    return [f"\n{'='*70}", f"  {title}", "="*70]


def analyze_summary(summary: dict) -> list[str]:
    lines = section_header("GAME RESULT")
    s = summary
    result = s.get("result", "Unknown")
    emoji = "WIN" if result == "Victory" else "LOSS"
    lines.append(f"  [{emoji}] {result} on {s.get('map_name', '?')} vs {s.get('enemy_race', '?')}")
    gt = s.get("game_time", 0)
    lines.append(f"  Duration: {gt:.0f}s ({gt/60:.1f}min) | Real time: {s.get('real_duration', 0):.0f}s")
    lines.append("")
    lines.append(f"  Peak supply: {s.get('peak_supply', 0)}  |  Peak army: {s.get('peak_army_supply', 0)}  |  Peak workers: {s.get('peak_worker_count', 0)}")
    lines.append(f"  Build order done: {s.get('build_order_completed_time', 0):.0f}s")
    fa = s.get("first_attack_time", 0)
    fe = s.get("first_expansion_time", 0)
    lines.append(f"  First attack: {fa:.0f}s  |  First expand: {fe:.0f}s")
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
        lines.append(f"  Offensive %: {(chase+attack)/total_combat*100:.0f}% of combat time")
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
    lines.append(f"  Enemy killed:  {killed}")
    lines.append(f"  Structures:    {s.get('structures_built', {})}")
    lines.append(f"  Struct lost:   {s.get('structures_lost', {})}")

    return lines


def analyze_strategy_decisions(decisions: list[dict]) -> list[str]:
    """Analyze strategy module decisions over time."""
    lines = section_header("STRATEGY ANALYSIS (Phase 4)")

    strategy_decs = [d for d in decisions if d.get("module") == "strategy"]
    if not strategy_decs:
        lines.append("  !! No strategy decisions found in log!")
        return lines

    lines.append(f"  Total strategy evaluations: {len(strategy_decs)}")

    # Collect time-series data
    threat_levels = []
    army_advantages = []
    comps_over_time = []
    tech_targets_over_time = []
    army_ratios = []
    drone_targets = []
    gas_targets = []
    base_targets = []
    attack_ready_times = []
    enemy_races = set()
    enemy_techs = []
    dominant_enemies = []

    for d in strategy_decs:
        det = d.get("details", {})
        t = d.get("game_time", 0)
        threat_levels.append((t, det.get("threat_level", 0)))
        army_advantages.append((t, det.get("army_advantage", 1)))
        comps_over_time.append((t, det.get("recommended_comp", {})))
        tech_targets_over_time.append((t, det.get("tech_targets", [])))
        army_ratios.append((t, det.get("army_ratio", 0.5)))
        drone_targets.append((t, det.get("target_drones", 0)))
        gas_targets.append((t, det.get("target_gas", 0)))
        base_targets.append((t, det.get("target_bases", 0)))
        if det.get("attack_ready"):
            attack_ready_times.append(t)
        if det.get("enemy_race"):
            enemy_races.add(det["enemy_race"])
        enemy_techs.append((t, det.get("enemy_tech", "?")))
        if det.get("dominant_enemy"):
            dominant_enemies.append(det["dominant_enemy"])

    lines.append(f"  Enemy race detected: {', '.join(enemy_races)}")
    if dominant_enemies:
        dom_counts = Counter(dominant_enemies)
        lines.append(f"  Dominant enemy units: {dict(dom_counts.most_common(5))}")

    # Tech level progression
    tech_transitions = []
    prev_tech = None
    for t, tech in enemy_techs:
        if tech != prev_tech:
            tech_transitions.append(f"{t:.0f}s={tech}")
            prev_tech = tech
    lines.append(f"  Enemy tech progression: {' -> '.join(tech_transitions)}")

    # Threat level timeline
    lines.append("")
    lines.append("  --- Threat Level Timeline ---")
    threat_buckets = _bucket_timeline(threat_levels, buckets=8)
    for label, val in threat_buckets:
        bar = "#" * int(val * 20) + "." * (20 - int(val * 20))
        lines.append(f"    {label:>6s}: [{bar}] {val:.2f}")

    # Army advantage timeline
    lines.append("")
    lines.append("  --- Army Advantage Timeline ---")
    adv_buckets = _bucket_timeline(army_advantages, buckets=8)
    for label, val in adv_buckets:
        capped = min(val, 5.0)
        bar_len = int(capped / 5.0 * 20)
        bar = "#" * bar_len + "." * (20 - bar_len)
        marker = " <-EVEN" if 0.9 <= val <= 1.1 else (" <-AHEAD" if val > 1.1 else " <-BEHIND")
        lines.append(f"    {label:>6s}: [{bar}] {val:.1f}x{marker}")

    # Comp evolution — show transitions
    lines.append("")
    lines.append("  --- Recommended Comp Evolution ---")
    comp_changes = []
    prev_comp = None
    for t, comp in comps_over_time:
        comp_key = str(sorted(comp.items()))
        if comp_key != prev_comp:
            comp_str = ", ".join(f"{k}:{v:.0%}" for k, v in sorted(comp.items(), key=lambda x: -x[1]))
            comp_changes.append(f"    {t:>5.0f}s: {comp_str}")
            prev_comp = comp_key
    if len(comp_changes) > 10:
        lines.extend(comp_changes[:5])
        lines.append(f"    ... ({len(comp_changes) - 10} more transitions) ...")
        lines.extend(comp_changes[-5:])
    else:
        lines.extend(comp_changes)

    # Tech target evolution
    lines.append("")
    lines.append("  --- Tech Targets Requested ---")
    prev_targets = None
    tech_lines = []
    for t, targets in tech_targets_over_time:
        if targets != prev_targets and targets:
            tech_lines.append(f"    {t:>5.0f}s: {targets}")
            prev_targets = targets
    if tech_lines:
        lines.extend(tech_lines[:10])
    else:
        lines.append("    (none requested)")

    # Economy decisions
    lines.append("")
    lines.append("  --- Economy Decisions ---")
    drone_range = [v for _, v in drone_targets]
    gas_range = [v for _, v in gas_targets]
    base_range = [v for _, v in base_targets]
    ratio_range = [v for _, v in army_ratios]
    lines.append(f"    Drone target: {min(drone_range)}-{max(drone_range)} (avg {sum(drone_range)/len(drone_range):.0f})")
    lines.append(f"    Gas target:   {min(gas_range)}-{max(gas_range)} (avg {sum(gas_range)/len(gas_range):.1f})")
    lines.append(f"    Base target:  {min(base_range)}-{max(base_range)}")
    lines.append(f"    Army ratio:   {min(ratio_range):.2f}-{max(ratio_range):.2f} (avg {sum(ratio_range)/len(ratio_range):.2f})")

    # Attack readiness
    lines.append("")
    if attack_ready_times:
        lines.append(f"  First attack_ready=True at: {min(attack_ready_times):.0f}s")
        lines.append(f"  Total times attack_ready: {len(attack_ready_times)}/{len(strategy_decs)}")
    else:
        lines.append("  !! attack_ready was NEVER True!")

    return lines


def analyze_combat_decisions(decisions: list[dict]) -> list[str]:
    """Analyze combat module decisions."""
    lines = section_header("COMBAT ANALYSIS")

    combat_decs = [d for d in decisions if d.get("module") == "combat"]
    if not combat_decs:
        lines.append("  No combat decisions logged")
        return lines

    action_counts = Counter(d.get("action", "?") for d in combat_decs)
    lines.append(f"  Decision counts: {dict(action_counts.most_common())}")

    # Engagement evaluations
    evals = [d for d in combat_decs if d.get("action") == "evaluate_engagement"]
    if evals:
        sim_results = Counter(d["details"].get("sim_result", "?") for d in evals)
        mode_changes = sum(1 for d in evals if d["details"].get("mode_changed"))
        lines.append(f"  Engagement evals: {len(evals)} | Mode changes: {mode_changes}")
        lines.append(f"  Sim results: {dict(sim_results.most_common())}")

        comp_at_eval = Counter()
        for d in evals:
            for unit, count in d["details"].get("army_comp", {}).items():
                comp_at_eval[unit] += count
        lines.append(f"  Our army in combat: {dict(comp_at_eval.most_common(10))}")

        enemy_at_eval = Counter()
        for d in evals:
            for unit, count in d["details"].get("enemy_comp", {}).items():
                enemy_at_eval[unit] += count
        lines.append(f"  Enemy in combat:    {dict(enemy_at_eval.most_common(10))}")

    # Regroup events
    regroups = [d for d in combat_decs if d.get("action") == "regroup"]
    if regroups:
        lines.append(f"\n  Regroup events: {len(regroups)}")
        for d in regroups[:5]:
            det = d["details"]
            lines.append(f"    {d['game_time']:>5.0f}s: supply={det.get('army_supply',0):.0f} "
                         f"peak={det.get('peak_supply',0):.0f} "
                         f"ready={det.get('attack_ready','?')}")

    # Defense events
    defends = [d for d in combat_decs if d.get("action") == "defend"]
    if defends:
        lines.append(f"\n  Defense events: {len(defends)}")
        for d in defends[:5]:
            det = d["details"]
            lines.append(f"    {d['game_time']:>5.0f}s: {det.get('threat_type','?')} "
                         f"x{det.get('threat_count',0)} "
                         f"army={det.get('army_count',0)}")

    return lines


def analyze_macro_decisions(decisions: list[dict]) -> list[str]:
    """Analyze macro module decisions."""
    lines = section_header("MACRO ANALYSIS")

    macro_decs = [d for d in decisions if d.get("module") == "macro"]
    if not macro_decs:
        lines.append("  No macro decisions logged")
        return lines

    action_counts = Counter(d.get("action", "?") for d in macro_decs)
    lines.append(f"  Decision counts: {dict(action_counts.most_common())}")

    # Tech/build decisions
    tech_decs = [d for d in macro_decs
                 if any(kw in d.get("action", "").lower()
                        for kw in ("tech", "build", "expand", "structure", "lair", "hive"))]
    if tech_decs:
        lines.append(f"\n  Tech/Build decisions ({len(tech_decs)}):")
        for d in tech_decs[:15]:
            det_str = json.dumps(d.get("details", {}), default=str)
            if len(det_str) > 100:
                det_str = det_str[:100] + "..."
            lines.append(f"    {d['game_time']:>5.0f}s: {d['action']} {det_str}")

    return lines


def analyze_economy_timeline(states: list[dict]) -> list[str]:
    """Track economy progression from state snapshots."""
    lines = section_header("ECONOMY TIMELINE")

    if not states:
        lines.append("  No state snapshots")
        return lines

    game_dur = states[-1].get("game_time", 0)
    sample_times = list(range(0, int(game_dur) + 1, 60))
    if game_dur not in sample_times:
        sample_times.append(game_dur)

    lines.append(f"  {'Time':>6s}  {'Min':>5s}  {'Gas':>5s}  {'Sup':>7s}  {'Wrk':>4s}  {'Army':>4s}  {'Base':>4s}  {'Ext':>3s}  {'Mode':>8s}")
    lines.append(f"  {'----':>6s}  {'---':>5s}  {'---':>5s}  {'-------':>7s}  {'---':>4s}  {'----':>4s}  {'----':>4s}  {'---':>3s}  {'--------':>8s}")

    for target_time in sample_times:
        closest = min(states, key=lambda s: abs(s.get("game_time", 0) - target_time))
        t = closest.get("game_time", 0)
        mins = closest.get("minerals", 0)
        gas = closest.get("vespene", 0)
        sup = f"{closest.get('supply_used', 0)}/{closest.get('supply_cap', 0)}"
        workers = closest.get("supply_workers", 0)
        army = closest.get("supply_army", 0)
        structs = closest.get("own_structures", {})
        bases = sum(structs.get(b, 0) for b in ("HATCHERY", "LAIR", "HIVE"))
        extractors = structs.get("EXTRACTOR", 0)
        mode = closest.get("combat_mode", "?")

        lines.append(f"  {t:>5.0f}s  {mins:>5d}  {gas:>5d}  {sup:>7s}  {workers:>4d}  {army:>4d}  {bases:>4d}  {extractors:>3d}  {mode:>8s}")

    # Detect resource float
    float_times = [(s["game_time"], s["minerals"]) for s in states if s.get("minerals", 0) > 800]
    if float_times:
        lines.append(f"\n  !! Minerals > 800 for {len(float_times)} snapshots "
                     f"({float_times[0][0]:.0f}s - {float_times[-1][0]:.0f}s)")
        peak_float = max(float_times, key=lambda x: x[1])
        lines.append(f"     Peak mineral float: {peak_float[1]} at {peak_float[0]:.0f}s")

    # Supply blocks
    blocks = []
    for s in states:
        used = s.get("supply_used", 0)
        cap = s.get("supply_cap", 0)
        if used >= cap > 0 and cap < 200:
            blocks.append(s.get("game_time", 0))
    if blocks:
        # Deduplicate close timestamps
        deduped = [blocks[0]]
        for b in blocks[1:]:
            if b - deduped[-1] > 5:
                deduped.append(b)
        lines.append(f"\n  !! Supply blocked {len(deduped)} times: "
                     f"{', '.join(f'{t:.0f}s' for t in deduped[:10])}"
                     + (f" ... +{len(deduped)-10} more" if len(deduped) > 10 else ""))

    return lines


def analyze_army_timeline(states: list[dict]) -> list[str]:
    """Track army composition over time."""
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
        t = closest.get("game_time", 0)
        units = closest.get("own_units", {})
        army = {k: v for k, v in units.items() if k in combat_types}
        if army:
            comp_str = ", ".join(f"{k}:{v}" for k, v in sorted(army.items(), key=lambda x: -x[1]))
            lines.append(f"  {t:>5.0f}s: {comp_str}")
        else:
            lines.append(f"  {t:>5.0f}s: (no army)")

    return lines


def analyze_enemy_timeline(states: list[dict]) -> list[str]:
    """Track enemy observations over time."""
    lines = section_header("ENEMY OBSERVATIONS")

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
            lines.append(f"    {t:>5.0f}s: {unit}")

    if first_seen_structs:
        lines.append("\n  Enemy structures first seen:")
        for struct, t in sorted(first_seen_structs.items(), key=lambda x: x[1]):
            lines.append(f"    {t:>5.0f}s: {struct}")

    return lines


def analyze_issues(summary: dict, states: list[dict], decisions: list[dict]) -> list[str]:
    """Identify problems and give actionable recommendations."""
    lines = section_header("ISSUES & RECOMMENDATIONS")
    issues = []

    s = summary
    gt = s.get("game_time", 0)

    # 1. Resource float
    float_states = [st for st in states if st.get("minerals", 0) > 800]
    if len(float_states) > 20:
        issues.append(("RED", f"MINERAL FLOAT: {len(float_states)} snapshots with >800 minerals. "
                       "Not spending fast enough — SpawnController may be starved or comp is wrong."))

    # 2. Low army diversity
    produced = s.get("units_produced", {})
    skip = {"LARVA", "DRONE", "OVERLORD", "EGG"}
    combat_produced = {k: v for k, v in produced.items() if k not in skip}
    if len(combat_produced) <= 2 and gt > 300:
        issues.append(("RED", f"LOW DIVERSITY: Only {len(combat_produced)} combat unit types "
                       f"({list(combat_produced.keys())}). Counter system may not be recommending variety."))

    # 3. No hydras
    if "HYDRALISK" not in produced and gt > 360:
        issues.append(("YEL", f"NO HYDRAS: Game lasted {gt:.0f}s without hydralisks. "
                       "Missing Lair/HydraDen? Check tech_targets."))

    # 4. Late first attack
    if s.get("first_attack_time", 0) > 360:
        issues.append(("YEL", f"LATE ATTACK: First attack at {s.get('first_attack_time',0):.0f}s."))

    # 5. Strategy attack_ready
    strategy_decs = [d for d in decisions if d.get("module") == "strategy"]
    attack_ready_count = sum(1 for d in strategy_decs
                             if d.get("details", {}).get("attack_ready"))
    if strategy_decs and attack_ready_count == 0:
        issues.append(("RED", "ATTACK_READY NEVER TRUE: Strategy never authorized attack. "
                       "Check _should_attack() thresholds."))

    # 6. Gas starvation
    gas_vals = [d.get("details", {}).get("target_gas", 0) for d in strategy_decs]
    if gas_vals and max(gas_vals) <= 1 and gt > 300:
        issues.append(("YEL", "LOW GAS TARGET: Never more than 1 extractor targeted. "
                       "Gas-heavy units (hydra/ravager) can't sustain. Check _compute_target_gas()."))

    # 7. Mono-composition
    comp_vals = [d.get("details", {}).get("recommended_comp", {}) for d in strategy_decs]
    if comp_vals:
        last_comp = comp_vals[-1]
        if len(last_comp) <= 1:
            issues.append(("RED", f"MONO-COMP: Final recommended comp = {last_comp}. "
                           "Counter system should produce diverse output."))

    # 8. No third base
    final_state = states[-1] if states else {}
    structs = final_state.get("own_structures", {})
    bases = sum(structs.get(b, 0) for b in ("HATCHERY", "LAIR", "HIVE"))
    if bases <= 2 and gt > 300:
        issues.append(("YEL", f"NO 3RD BASE: Only {bases} bases at {gt:.0f}s."))

    # 9. Too passive
    chase_attack = s.get("time_in_chase", 0) + s.get("time_in_attack", 0)
    if gt > 300 and chase_attack / gt < 0.15:
        issues.append(("YEL", f"PASSIVE: Only {chase_attack:.0f}s attacking "
                       f"({chase_attack/gt*100:.0f}% of {gt:.0f}s game)."))

    # 10. No lair tech
    all_structs_ever = set()
    for st in states:
        all_structs_ever.update(st.get("own_structures", {}).keys())
    if "LAIR" not in all_structs_ever and "HIVE" not in all_structs_ever and gt > 300:
        issues.append(("RED", "NO LAIR: Never morphed Lair. Hydra/lurker/muta locked. "
                       "Check _handle_tech_from_strategy() and tech_targets."))

    if not issues:
        issues.append(("GRN", "No major issues detected!"))

    for level, msg in issues:
        prefix = {"RED": "[!!]", "YEL": "[! ]", "GRN": "[OK]"}.get(level, "[??]")
        lines.append(f"  {prefix} {msg}")

    return lines


# ===========================================================================
# Helpers
# ===========================================================================

def _fmt_dict(d: dict) -> str:
    """Format dict sorted by value descending."""
    return str(dict(sorted(d.items(), key=lambda x: -x[1])))


def _bucket_timeline(data: list[tuple[float, float]], buckets: int = 8) -> list[tuple[str, float]]:
    """Bucket time-series data into N equal intervals."""
    if not data:
        return []
    min_t = data[0][0]
    max_t = data[-1][0]
    if max_t <= min_t:
        return [(f"{min_t:.0f}s", data[0][1])]

    interval = (max_t - min_t) / buckets
    result = []
    for i in range(buckets):
        t_start = min_t + i * interval
        t_end = t_start + interval
        bucket_vals = [v for t, v in data if t_start <= t < t_end]
        if bucket_vals:
            avg = sum(bucket_vals) / len(bucket_vals)
            result.append((f"{t_start:.0f}s", avg))
    return result


# ===========================================================================
# Main
# ===========================================================================

def main():
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
    else:
        path = find_latest_log()

    records = load_records(path)

    states = [r for r in records if r.get("type") == "state"]
    decisions = [r for r in records if r.get("type") == "decision"]
    events = [r for r in records if r.get("type") == "event"]
    summaries = [r for r in records if r.get("type") == "summary"]

    summary = summaries[0] if summaries else {}

    # Build report
    report: list[str] = []
    report.append(f"\n{'#'*70}")
    report.append(f"  GAME ANALYSIS REPORT -- {path.name}")
    report.append(f"  {len(records)} records: {len(states)} states, {len(decisions)} decisions, "
                  f"{len(events)} events")
    report.append(f"{'#'*70}")

    report.extend(analyze_summary(summary))
    report.extend(analyze_strategy_decisions(decisions))
    report.extend(analyze_combat_decisions(decisions))
    report.extend(analyze_macro_decisions(decisions))
    report.extend(analyze_economy_timeline(states))
    report.extend(analyze_army_timeline(states))
    report.extend(analyze_enemy_timeline(states))
    report.extend(analyze_issues(summary, states, decisions))

    # Print and save
    output = "\n".join(report)
    print(output)

    report_path = path.with_suffix(".report.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(output)
    print(f"\n>> Report saved: {report_path}")


if __name__ == "__main__":
    main()
