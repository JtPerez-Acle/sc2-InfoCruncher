"""Extracts a full game state snapshot from the AresBot instance.

Pure function — no side effects. Uses only cached mediator properties
that are already computed each step by ares (no extra computation).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from sc2.ids.unit_typeid import UnitTypeId as UnitID

from bot.strategy.unit_counter_db import compute_army_value

if TYPE_CHECKING:
    from ares import AresBot


def _count_by_type(unit_dict: dict) -> dict[str, int]:
    """Convert {UnitTypeId: [Unit, ...]} to {type_name: count}."""
    result: dict[str, int] = {}
    for unit_type, units in unit_dict.items():
        name = unit_type.name if hasattr(unit_type, "name") else str(unit_type)
        count = len(units) if hasattr(units, "__len__") else 0
        if count > 0:
            result[name] = count
    return result


def _units_center(units) -> list[float] | None:
    """Get center of mass as [x, y] or None if empty."""
    try:
        if units and len(units) > 0:
            center = units.center
            return [round(center.x, 1), round(center.y, 1)]
    except Exception:
        pass
    return None


def collect_state_snapshot(ai: AresBot, step: int) -> dict[str, Any]:
    """Collect a complete state snapshot from the bot.

    Returns a plain dict matching the StateSnapshot schema.
    All data comes from cached properties — this is fast (<0.1ms).
    """
    snapshot: dict[str, Any] = {
        "type": "state",
        "game_time": round(ai.time, 1),
        "step": step,
        "real_time": round(time.time(), 3),
    }

    # --- Economy ---
    snapshot["minerals"] = ai.minerals
    snapshot["vespene"] = ai.vespene
    snapshot["supply_used"] = ai.supply_used
    snapshot["supply_cap"] = ai.supply_cap
    snapshot["supply_workers"] = ai.supply_workers
    snapshot["supply_army"] = ai.supply_used - ai.supply_workers
    snapshot["larva_count"] = len(ai.larva) if ai.larva else 0

    # --- Income rates (from SC2 score API) ---
    try:
        snapshot["income_minerals"] = float(
            ai.state.score.collection_rate_minerals
        )
        snapshot["income_vespene"] = float(
            ai.state.score.collection_rate_vespene
        )
    except Exception:
        snapshot["income_minerals"] = 0.0
        snapshot["income_vespene"] = 0.0

    # --- Saturation ratio ---
    num_bases = len(ai.townhalls) if ai.townhalls else 1
    snapshot["saturation_ratio"] = round(
        ai.supply_workers / max(num_bases * 16, 1), 3
    )

    # --- Own units ---
    own_army_dict = ai.mediator.get_own_army_dict
    snapshot["own_units"] = _count_by_type(own_army_dict)

    # --- Enemy units ---
    try:
        enemy_army_dict = ai.mediator.get_enemy_army_dict
        snapshot["enemy_units"] = _count_by_type(enemy_army_dict)
    except Exception:
        # Enemy dict may not always be available
        snapshot["enemy_units"] = {}

    # --- Own structures ---
    own_structs_dict = ai.mediator.get_own_structures_dict
    snapshot["own_structures"] = _count_by_type(own_structs_dict)

    # --- Enemy structures (visible) ---
    enemy_structs: dict[str, int] = {}
    if ai.enemy_structures:
        for struct in ai.enemy_structures:
            name = struct.type_id.name
            enemy_structs[name] = enemy_structs.get(name, 0) + 1
    snapshot["enemy_structures"] = enemy_structs

    # --- Army positions ---
    try:
        own_army = ai.mediator.get_own_army
        snapshot["own_army_center"] = _units_center(own_army)
    except Exception:
        snapshot["own_army_center"] = None

    try:
        if ai.enemy_units and len(ai.enemy_units) > 0:
            center = ai.enemy_units.center
            snapshot["enemy_army_center"] = [round(center.x, 1), round(center.y, 1)]
        else:
            snapshot["enemy_army_center"] = None
    except Exception:
        snapshot["enemy_army_center"] = None

    # --- Zerg-specific ---
    queens = own_army_dict.get(UnitID.QUEEN, [])
    snapshot["queen_count"] = len(queens)
    # inject/creep queen counts come from role assignments
    try:
        from ares.consts import UnitRole
        inject_queens = ai.mediator.get_units_from_role(
            role=UnitRole.QUEEN_INJECT, unit_type=UnitID.QUEEN
        )
        creep_queens = ai.mediator.get_units_from_role(
            role=UnitRole.QUEEN_CREEP, unit_type=UnitID.QUEEN
        )
        snapshot["inject_queens"] = len(inject_queens) if inject_queens else 0
        snapshot["creep_queens"] = len(creep_queens) if creep_queens else 0
    except Exception:
        snapshot["inject_queens"] = 0
        snapshot["creep_queens"] = 0

    # Creep coverage
    try:
        snapshot["creep_coverage"] = round(ai.mediator.get_creep_coverage, 3)
    except Exception:
        snapshot["creep_coverage"] = 0.0

    # --- Upgrades ---
    try:
        snapshot["upgrades"] = sorted(
            [u.name for u in ai.state.upgrades] if ai.state.upgrades else []
        )
    except Exception:
        snapshot["upgrades"] = []

    # --- Build order status ---
    try:
        snapshot["build_order_completed"] = ai.build_order_runner.build_completed
        snapshot["build_order_step"] = ai.build_order_runner.build_step
    except Exception:
        snapshot["build_order_completed"] = False
        snapshot["build_order_step"] = 0

    # --- Threat assessment (for Phase 3 info-gain analysis) ---
    try:
        ground_threats = ai.mediator.get_main_ground_threats_near_townhall
        snapshot["ground_threat_count"] = len(ground_threats) if ground_threats else 0
    except Exception:
        snapshot["ground_threat_count"] = 0

    try:
        air_threats = ai.mediator.get_main_air_threats_near_townhall
        snapshot["air_threat_count"] = len(air_threats) if air_threats else 0
    except Exception:
        snapshot["air_threat_count"] = 0

    # Compute army values from dicts (mediator properties don't exist)
    try:
        enemy_army_dict = ai.mediator.get_enemy_army_dict
        snapshot["enemy_army_value"] = compute_army_value(enemy_army_dict)
    except Exception:
        snapshot["enemy_army_value"] = 0
    snapshot["own_army_value"] = compute_army_value(own_army_dict)

    try:
        snapshot["did_enemy_rush"] = ai.mediator.get_did_enemy_rush
    except Exception:
        snapshot["did_enemy_rush"] = False

    # --- Combat state (for Phase 3 — correlate micro mode with outcomes) ---
    try:
        cs = ai.combat_system
        snapshot["combat_mode"] = cs.combat_mode
        ctx = cs.combat_context
        snapshot["combat_objective"] = ctx.get("objective", "UNKNOWN")
        snapshot["combat_profile"] = ctx.get("profile", "DEFAULT")
        snapshot["last_engagement_sim"] = None
        snapshot["last_engagement_value"] = None
    except Exception:
        snapshot["combat_mode"] = "IDLE"
        snapshot["combat_objective"] = None
        snapshot["combat_profile"] = None
        snapshot["last_engagement_sim"] = None
        snapshot["last_engagement_value"] = None

    # --- Strategic intelligence (if analyzer available) ---
    try:
        analyzer = ai.strategic_analyzer
        strat = analyzer._cached_strategy

        # Composition classification
        if hasattr(strat, "threat") and strat.threat is not None:
            snapshot["threat_level"] = round(strat.threat.threat_level, 3)
            snapshot["army_advantage"] = round(strat.threat.army_advantage, 3)
            snapshot["enemy_tech_level"] = strat.threat.enemy_tech_level
            if hasattr(strat.threat, "enemy_archetype"):
                snapshot["enemy_archetype"] = strat.threat.enemy_archetype
            elif hasattr(analyzer, "classifier"):
                # Fall back to classifier's last result
                comp = getattr(analyzer.classifier, "_last_analysis", None)
                if comp is not None:
                    snapshot["enemy_archetype"] = comp.archetype.name
                    snapshot["enemy_archetype_confidence"] = round(
                        comp.confidence, 3
                    )

        # Info staleness
        snapshot["info_staleness"] = round(
            analyzer.memory.get_info_staleness(ai.time), 1
        )

        # Spending diagnosis (cached on memory to avoid recomputation)
        spending = getattr(analyzer.memory, "last_spending", None)
        if spending is not None:
            snapshot["is_larva_bound"] = spending.is_larva_bound
            snapshot["is_tech_bound"] = spending.is_tech_bound
            snapshot["is_supply_bound"] = spending.is_supply_bound
            snapshot["is_gas_bound"] = spending.is_gas_bound
            snapshot["mineral_float"] = spending.mineral_float
            snapshot["float_duration"] = round(spending.float_duration, 1)
    except Exception:
        pass  # All strategic fields remain absent (total=False in schema)

    return snapshot
