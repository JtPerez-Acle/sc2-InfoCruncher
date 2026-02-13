"""Macro manager — executes economy and production from Strategy.

All targets (drones, gas, bases, army comp, army ratio) come from the
StrategicAnalyzer via the Strategy dataclass. This module just executes.

Tech structures are driven by strategy.tech_targets — the analyzer
tells us what to build based on counter analysis, not hardcoded recipes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId as UnitID
from sc2.ids.upgrade_id import UpgradeId

from ares.behaviors.macro import (
    AutoSupply,
    BuildStructure,
    BuildWorkers,
    ExpansionController,
    GasBuildingController,
    MacroPlan,
    SpawnController,
    TechUp,
    UpgradeController,
)

from bot.strategy.unit_counter_db import UNIT_PROFILES

if TYPE_CHECKING:
    from ares import AresBot
    from bot.strategy.analyzer import Strategy


# ---------------------------------------------------------------------------
# Morph mapping: target → (source building type, morph ability)
# Covers every Zerg structure that morphs from another building.
# ---------------------------------------------------------------------------
_MORPH_MAP: dict[UnitID, tuple[UnitID, AbilityId]] = {
    UnitID.LAIR: (UnitID.HATCHERY, AbilityId.UPGRADETOLAIR_LAIR),
    UnitID.HIVE: (UnitID.LAIR, AbilityId.UPGRADETOHIVE_HIVE),
    UnitID.GREATERSPIRE: (UnitID.SPIRE, AbilityId.UPGRADETOGREATERSPIRE_GREATERSPIRE),
}

# All morph targets (used for type checks)
_MORPH_STRUCTURES = set(_MORPH_MAP.keys())

# ---------------------------------------------------------------------------
# Prerequisite map: structure → list of required structures.
# A build request is blocked until ALL prerequisites exist or are pending.
# ---------------------------------------------------------------------------
_STRUCT_PREREQUISITES: dict[UnitID, list[UnitID]] = {
    UnitID.HYDRALISKDEN: [UnitID.LAIR],
    UnitID.SPIRE: [UnitID.LAIR],
    UnitID.INFESTATIONPIT: [UnitID.LAIR],
    UnitID.LURKERDENMP: [UnitID.HYDRALISKDEN],
    UnitID.ULTRALISKCAVERN: [UnitID.HIVE],
    UnitID.GREATERSPIRE: [UnitID.HIVE, UnitID.SPIRE],
    UnitID.BANELINGNEST: [UnitID.SPAWNINGPOOL],
    UnitID.ROACHWARREN: [UnitID.SPAWNINGPOOL],
}

# LAIR/HIVE equivalences for prerequisite checking
_LAIR_EQUIVALENTS = {UnitID.LAIR, UnitID.HIVE}
_HIVE_EQUIVALENTS = {UnitID.HIVE}

# Always build evo chambers for upgrades (alongside tech_targets)
_ALWAYS_WANT_EVO = True


# ---------------------------------------------------------------------------
# Tech Execution Tracker — monitors tech requests and escalates on failure
# ---------------------------------------------------------------------------

@dataclass
class _TechRequest:
    """Tracks a single tech structure request."""

    structure_id: UnitID
    requested_at: float
    last_attempt: float
    attempts: int = 1
    escalated: bool = False  # True once we've tried force-morph


class TechExecutionTracker:
    """Monitors tech structure requests and escalates when they stall.

    Problem: TechUp silently returns False when the source building is busy
    (producing larvae/units/queens). BuildStructure can also silently fail
    (no placement, drone pathing issues). Both just return False every frame
    with no indication of failure.

    Solution: Track every tech request. If the structure hasn't appeared
    after a timeout:
      - For morphs (LAIR, HIVE, GREATERSPIRE): find an idle source building
        and issue the morph command directly.
      - For regular structures: re-request via BuildStructure.
    """

    # After this many seconds without the structure appearing, escalate
    ESCALATION_TIMEOUT = 10.0
    # How often to retry force-morph (source might still be busy)
    FORCE_RETRY_INTERVAL = 5.0

    def __init__(self) -> None:
        self._requests: dict[UnitID, _TechRequest] = {}

    def track(self, structure_id: UnitID, game_time: float) -> None:
        """Record that we requested a tech structure."""
        existing = self._requests.get(structure_id)
        if existing is None:
            self._requests[structure_id] = _TechRequest(
                structure_id=structure_id,
                requested_at=game_time,
                last_attempt=game_time,
            )
        else:
            existing.last_attempt = game_time
            existing.attempts += 1

    def clear(self, structure_id: UnitID) -> None:
        """Structure was built — stop tracking."""
        self._requests.pop(structure_id, None)

    def get_stalled_morphs(
        self, game_time: float
    ) -> list[_TechRequest]:
        """Return morph requests that have stalled and need escalation."""
        stalled = []
        for req in self._requests.values():
            if req.structure_id not in _MORPH_STRUCTURES:
                continue
            elapsed = game_time - req.requested_at
            since_last = game_time - req.last_attempt
            if elapsed >= self.ESCALATION_TIMEOUT and since_last >= self.FORCE_RETRY_INTERVAL:
                stalled.append(req)
        return stalled

    def get_stalled_builds(
        self, game_time: float
    ) -> list[_TechRequest]:
        """Return non-morph build requests that have stalled."""
        stalled = []
        for req in self._requests.values():
            if req.structure_id in _MORPH_STRUCTURES:
                continue
            elapsed = game_time - req.requested_at
            since_last = game_time - req.last_attempt
            # Give regular builds more time (placement + drone walk)
            if elapsed >= self.ESCALATION_TIMEOUT * 1.5 and since_last >= self.FORCE_RETRY_INTERVAL:
                stalled.append(req)
        return stalled


class MacroManager:
    """Executes macro from Strategy — no hardcoded decisions here.

    Strategy provides: army_comp, army_ratio, target_drones, target_gas,
    target_bases, tech_targets. This module registers ares behaviors.
    """

    STRUCT_RETRY_COOLDOWN = 15.0

    def __init__(self, ai: AresBot) -> None:
        self.ai = ai
        self._pending_requests: dict[UnitID, float] = {}
        self._tech_tracker = TechExecutionTracker()

    def handle_macro(self, strategy: Strategy) -> None:
        """Execute macro using strategy-provided targets."""
        ai = self.ai
        base = ai.start_location

        # All targets come from strategy
        target_drones = strategy.economy.target_drones
        target_bases = strategy.economy.target_bases
        target_gas = strategy.economy.target_gas
        army_ratio = strategy.economy.army_ratio

        # Convert recommended comp (proportions) to SpawnController format
        army_comp = self._to_spawn_format(strategy.army_comp)

        # Gas spending override: boost gas-heavy units when vespene banks
        army_comp = self._apply_gas_spending_override(
            army_comp, ai.minerals, ai.vespene
        )

        # Log macro context every 10 seconds
        logger = getattr(ai, "game_logger", None)
        if logger and int(ai.time) % 10 < 0.2:
            logger.log_decision("macro", "macro_plan", {
                "target_drones": target_drones,
                "target_bases": target_bases,
                "target_gas": target_gas,
                "army_comp": {k.name: v for k, v in army_comp.items()},
                "army_ratio": round(army_ratio, 2),
                "minerals": ai.minerals,
                "vespene": ai.vespene,
                "supply": f"{ai.supply_used}/{ai.supply_cap}",
                "threat_level": round(strategy.threat.threat_level, 2),
                "army_advantage": round(strategy.threat.army_advantage, 2),
                "attack_ready": strategy.attack_ready,
            })

        # --- Economy path ---
        # NOTE: MacroPlan stops at first behavior that returns True
        # (spends resources). Expansion/gas must be SEPARATE behaviors
        # so AutoSupply doesn't block them every frame.
        econ_plan = MacroPlan()
        econ_plan.add(AutoSupply(base))
        if army_ratio < 1.0:
            econ_plan.add(BuildWorkers(to_count=target_drones))
        ai.register_behavior(econ_plan)

        # Expansion and gas as independent behaviors
        ai.register_behavior(ExpansionController(to_count=target_bases))
        ai.register_behavior(GasBuildingController(to_count=target_gas))

        # --- Army production ---
        if army_comp:
            ai.register_behavior(
                SpawnController(
                    army_comp,
                    freeflow_mode=False,
                    ignore_proportions_below_unit_count=6,
                )
            )

        # Emergency reinforcement at high threat or floating resources
        if strategy.threat.threat_level > 0.5 or ai.minerals > 600:
            if army_comp and ai.minerals > 200:
                ai.register_behavior(
                    SpawnController(army_comp, freeflow_mode=True)
                )

        # --- Tech structures from strategy ---
        self._handle_tech_from_strategy(strategy)

        # --- Escalate stalled tech requests ---
        self._escalate_stalled_tech()

        # --- Evo chambers (always want for upgrades) ---
        self._handle_evo_chambers()

        # --- Upgrades ---
        self._handle_upgrades()

    # ------------------------------------------------------------------
    # Convert strategy proportions to SpawnController format
    # ------------------------------------------------------------------

    @staticmethod
    def _to_spawn_format(
        comp: dict[UnitID, float],
    ) -> dict[UnitID, dict]:
        """Convert {UnitID: proportion} to SpawnController format.

        SpawnController expects:
          {UnitID: {"proportion": float, "priority": int}}
        SpawnController asserts proportions sum to exactly 1.0.
        """
        if not comp:
            return {UnitID.ZERGLING: {"proportion": 1.0, "priority": 0}}

        # Filter negligible proportions first
        filtered = {k: v for k, v in comp.items() if v >= 0.05}
        if not filtered:
            return {UnitID.ZERGLING: {"proportion": 1.0, "priority": 0}}

        # Re-normalize so proportions sum to exactly 1.0
        total = sum(filtered.values())
        normalized = {k: v / total for k, v in filtered.items()}

        result = {}
        # Sort by proportion descending — highest proportion = highest priority (0)
        sorted_items = sorted(normalized.items(), key=lambda x: x[1], reverse=True)
        for priority, (unit_type, proportion) in enumerate(sorted_items):
            result[unit_type] = {
                "proportion": round(proportion, 2),
                "priority": priority,
            }

        # Fix rounding drift: adjust the largest proportion so sum == 1.0
        if result:
            rounding_sum = sum(v["proportion"] for v in result.values())
            drift = round(1.0 - rounding_sum, 2)
            if drift != 0.0:
                # Add drift to the highest-proportion unit (priority 0)
                for v in result.values():
                    if v["priority"] == 0:
                        v["proportion"] = round(v["proportion"] + drift, 2)
                        break

        return result

    # ------------------------------------------------------------------
    # Gas spending override — boost gas-heavy units when vespene banks
    # ------------------------------------------------------------------

    def _apply_gas_spending_override(
        self, army_comp: dict[UnitID, dict], minerals: int, vespene: int,
    ) -> dict[UnitID, dict]:
        """Shift army comp toward gas-heavy units when vespene is banking.

        Only triggers when vespene > 500 and vespene > minerals.
        Finds the highest gas-ratio unit we can build right now and boosts
        its proportion by up to 15%, stealing from zero-gas units.
        """
        if vespene <= 500 or vespene <= minerals:
            return army_comp
        if not army_comp:
            return army_comp

        # Find the best gas-heavy unit in the current comp
        best_gas_unit: UnitID | None = None
        best_gas_ratio = 0.0

        for unit_type in army_comp:
            profile = UNIT_PROFILES.get(unit_type)
            if not profile or profile.gas_cost == 0:
                continue
            total = profile.mineral_cost + profile.gas_cost
            if total > 0:
                ratio = profile.gas_cost / total
                if ratio > best_gas_ratio:
                    best_gas_ratio = ratio
                    best_gas_unit = unit_type

        if best_gas_unit is None:
            return army_comp

        # Calculate boost amount (up to 15%)
        boost = min(0.15, (vespene - 500) / 3000)

        # Steal from zero-gas units (lings)
        zero_gas_units = [
            uid for uid in army_comp
            if UNIT_PROFILES.get(uid) and UNIT_PROFILES[uid].gas_cost == 0
        ]
        if not zero_gas_units:
            return army_comp

        # Apply the override
        result = dict(army_comp)
        stolen = 0.0
        for uid in zero_gas_units:
            entry = dict(result[uid])
            can_steal = min(entry["proportion"] - 0.05, boost - stolen)
            if can_steal > 0:
                entry["proportion"] = round(entry["proportion"] - can_steal, 2)
                stolen += can_steal
                result[uid] = entry

        if stolen > 0:
            entry = dict(result[best_gas_unit])
            new_prop = entry["proportion"] + stolen
            entry["proportion"] = round(new_prop, 2)
            result[best_gas_unit] = entry

            # Fix rounding drift so proportions sum to exactly 1.0
            prop_sum = sum(v["proportion"] for v in result.values())
            drift = round(1.0 - prop_sum, 2)
            if drift != 0.0:
                # Add drift to the largest proportion unit
                biggest = max(result, key=lambda k: result[k]["proportion"])
                e = dict(result[biggest])
                e["proportion"] = round(e["proportion"] + drift, 2)
                result[biggest] = e

            logger = getattr(self.ai, "game_logger", None)
            if logger:
                logger.log_decision("macro", "gas_spending_override", {
                    "boosted": best_gas_unit.name,
                    "boost_amount": round(stolen, 3),
                    "vespene": vespene,
                    "minerals": minerals,
                })

        return result

    # ------------------------------------------------------------------
    # Tech from strategy.tech_targets
    # ------------------------------------------------------------------

    def _handle_tech_from_strategy(self, strategy: Strategy) -> None:
        """Build tech structures recommended by the strategic analyzer.

        strategy.tech_targets is an ordered list of structures we need
        but don't have. We attempt to build them in priority order.
        """
        ai = self.ai
        base = ai.start_location
        logger = getattr(ai, "game_logger", None)
        structs = ai.mediator.get_own_structures_dict

        # Always build roach warren once we have pool + natural
        # (enables the counter-based comp to have options)
        has_pool = len(structs.get(UnitID.SPAWNINGPOOL, [])) > 0
        has_roach_warren = len(structs.get(UnitID.ROACHWARREN, [])) > 0
        num_bases = len(ai.townhalls)

        if has_pool and num_bases >= 2 and not has_roach_warren:
            if self._should_request(UnitID.ROACHWARREN):
                ai.register_behavior(
                    BuildStructure(
                        base_location=base, structure_id=UnitID.ROACHWARREN
                    )
                )
                self._mark_requested(UnitID.ROACHWARREN)
                self._tech_tracker.track(UnitID.ROACHWARREN, ai.time)
                if logger:
                    logger.log_decision("macro", "build_tech_structure", {
                        "structure": "ROACHWARREN",
                        "trigger": "baseline_tech",
                    })

        # Build structures from tech_targets list
        for structure_id in strategy.tech_targets:
            # Clear tracker for structures that now exist
            if ai.structure_present_or_pending(structure_id):
                self._tech_tracker.clear(structure_id)
                continue

            # Gate: don't request structures whose prerequisites aren't met
            if structure_id not in _MORPH_STRUCTURES and not self._prerequisites_met(structure_id):
                continue

            if not self._should_request(structure_id):
                continue

            if structure_id in _MORPH_STRUCTURES:
                ai.register_behavior(
                    TechUp(structure_id, base_location=base)
                )
            else:
                ai.register_behavior(
                    BuildStructure(
                        base_location=base, structure_id=structure_id
                    )
                )
            self._mark_requested(structure_id)
            self._tech_tracker.track(structure_id, ai.time)

            if logger:
                logger.log_decision("macro", "build_tech_structure", {
                    "structure": structure_id.name,
                    "trigger": "strategy_tech_target",
                })

    # ------------------------------------------------------------------
    # Escalate stalled tech — the core fix for TechUp silent failures
    # ------------------------------------------------------------------

    def _escalate_stalled_tech(self) -> None:
        """Check for stalled tech requests and escalate.

        For morphs: find an idle source building and issue the morph
        command directly, bypassing TechUp.
        For regular builds: re-request via BuildStructure.
        """
        ai = self.ai
        now = ai.time
        base = ai.start_location
        logger = getattr(ai, "game_logger", None)
        structs = ai.mediator.get_own_structures_dict

        # --- Clean up: remove requests for structures that now exist ---
        completed = [
            sid for sid in self._tech_tracker._requests
            if ai.structure_present_or_pending(sid)
        ]
        for sid in completed:
            self._tech_tracker.clear(sid)

        # --- Escalate stalled morphs ---
        for req in self._tech_tracker.get_stalled_morphs(now):
            morph_info = _MORPH_MAP.get(req.structure_id)
            if morph_info is None:
                continue

            source_type, ability = morph_info
            success = self._force_morph(source_type, req.structure_id, ability, structs)

            if success:
                req.escalated = True
                req.last_attempt = now
                if logger:
                    logger.log_decision("macro", "force_morph", {
                        "target": req.structure_id.name,
                        "source": source_type.name,
                        "ability": ability.name,
                        "elapsed": round(now - req.requested_at, 1),
                        "attempts": req.attempts,
                    })
            else:
                # Couldn't find idle source — update attempt time so we
                # retry after FORCE_RETRY_INTERVAL
                req.last_attempt = now
                if logger:
                    logger.log_decision("macro", "force_morph_failed", {
                        "target": req.structure_id.name,
                        "source": source_type.name,
                        "reason": "no_idle_source",
                        "elapsed": round(now - req.requested_at, 1),
                    })

        # --- Escalate stalled regular builds ---
        for req in self._tech_tracker.get_stalled_builds(now):
            ai.register_behavior(
                BuildStructure(
                    base_location=base, structure_id=req.structure_id
                )
            )
            req.last_attempt = now
            req.attempts += 1
            if logger:
                logger.log_decision("macro", "retry_build", {
                    "structure": req.structure_id.name,
                    "elapsed": round(now - req.requested_at, 1),
                    "attempts": req.attempts,
                })

    def _force_morph(
        self,
        source_type: UnitID,
        target_type: UnitID,
        ability: AbilityId,
        structs: dict,
    ) -> bool:
        """Find a source building and issue a morph command directly.

        SC2 accepts morph commands on busy hatcheries — the morph queues
        behind current production. So we try idle first, then fall back
        to any ready building.

        Returns True if a morph command was issued, False otherwise.
        """
        ai = self.ai

        # Don't double-morph — already_pending covers in-progress morphs
        if ai.already_pending(target_type) > 0:
            return False

        # Don't issue if we can't afford it
        if not ai.can_afford(target_type):
            return False

        source_buildings = [b for b in structs.get(source_type, []) if b.is_ready]
        if not source_buildings:
            return False

        # Prefer idle, fall back to any ready building
        for building in source_buildings:
            if building.is_idle:
                building(ability)
                return True

        # No idle building — SC2 queues morph behind current production
        source_buildings[0](ability)
        return True

    # ------------------------------------------------------------------
    # Evo chambers — always useful for upgrades
    # ------------------------------------------------------------------

    def _handle_evo_chambers(self) -> None:
        """Build evolution chambers for upgrades."""
        ai = self.ai
        base = ai.start_location
        structs = ai.mediator.get_own_structures_dict
        logger = getattr(ai, "game_logger", None)

        has_roach_warren = len(structs.get(UnitID.ROACHWARREN, [])) > 0
        has_lair = any(
            len(structs.get(t, [])) > 0
            for t in [UnitID.LAIR, UnitID.HIVE]
        )
        evo_count = len(structs.get(UnitID.EVOLUTIONCHAMBER, []))

        # First evo: once we have roach warren
        if has_roach_warren and evo_count == 0:
            if not ai.structure_present_or_pending(UnitID.EVOLUTIONCHAMBER):
                if self._should_request(UnitID.EVOLUTIONCHAMBER):
                    ai.register_behavior(
                        BuildStructure(
                            base_location=base,
                            structure_id=UnitID.EVOLUTIONCHAMBER,
                        )
                    )
                    self._mark_requested(UnitID.EVOLUTIONCHAMBER)
                    if logger:
                        logger.log_decision("macro", "build_tech_structure", {
                            "structure": "EVOLUTIONCHAMBER",
                            "trigger": "upgrades_needed",
                        })

        # Second evo: once lair exists
        if has_lair and evo_count == 1:
            if ai.structure_pending(UnitID.EVOLUTIONCHAMBER) == 0:
                ai.register_behavior(
                    BuildStructure(
                        base_location=base,
                        structure_id=UnitID.EVOLUTIONCHAMBER,
                        to_count=2,
                    )
                )

    # ------------------------------------------------------------------
    # Upgrades — based on what structures we have
    # ------------------------------------------------------------------

    def _handle_upgrades(self) -> None:
        """Queue upgrades based on available structures."""
        ai = self.ai
        base = ai.start_location
        structs = ai.mediator.get_own_structures_dict

        has_evo = len(structs.get(UnitID.EVOLUTIONCHAMBER, [])) > 0
        has_lair = any(
            len(structs.get(t, [])) > 0
            for t in [UnitID.LAIR, UnitID.HIVE]
        )
        has_hive = len(structs.get(UnitID.HIVE, [])) > 0
        has_hydra_den = len(structs.get(UnitID.HYDRALISKDEN, [])) > 0
        has_lurker_den = len(structs.get(UnitID.LURKERDENMP, [])) > 0

        upgrades = []

        if has_evo:
            upgrades.append(UpgradeId.ZERGMISSILEWEAPONSLEVEL1)
            upgrades.append(UpgradeId.ZERGGROUNDARMORSLEVEL1)

        if has_lair and has_evo:
            upgrades.append(UpgradeId.ZERGMISSILEWEAPONSLEVEL2)
            upgrades.append(UpgradeId.ZERGGROUNDARMORSLEVEL2)

        if has_hive and has_evo:
            upgrades.append(UpgradeId.ZERGMISSILEWEAPONSLEVEL3)
            upgrades.append(UpgradeId.ZERGGROUNDARMORSLEVEL3)

        if has_hydra_den:
            upgrades.append(UpgradeId.EVOLVEGROOVEDSPINES)

        if has_lurker_den:
            upgrades.append(UpgradeId.LURKERRANGE)

        if upgrades:
            ai.register_behavior(
                UpgradeController(upgrades, base_location=base)
            )
            logger = getattr(ai, "game_logger", None)
            if logger and int(ai.time) % 10 < 0.2:
                logger.log_decision("macro", "upgrade_queue", {
                    "upgrades": [u.name for u in upgrades],
                })

    # ------------------------------------------------------------------
    # Prerequisite checking
    # ------------------------------------------------------------------

    def _prerequisites_met(self, structure_id: UnitID) -> bool:
        """Check if all prerequisite structures exist or are in progress.

        Uses LAIR/HIVE equivalence (HIVE counts as LAIR) and accepts
        already_pending morphs so downstream structures can be requested
        while LAIR is morphing.
        """
        prereqs = _STRUCT_PREREQUISITES.get(structure_id)
        if not prereqs:
            return True  # No prerequisites defined

        ai = self.ai
        for prereq in prereqs:
            # Check for equivalences (HIVE counts as LAIR)
            equiv_set = set()
            if prereq == UnitID.LAIR:
                equiv_set = _LAIR_EQUIVALENTS
            elif prereq == UnitID.HIVE:
                equiv_set = _HIVE_EQUIVALENTS
            else:
                equiv_set = {prereq}

            # Check if any equivalent exists or is pending
            found = False
            for equiv in equiv_set:
                if ai.structure_present_or_pending(equiv):
                    found = True
                    break
                if ai.already_pending(equiv) > 0:
                    found = True
                    break
            if not found:
                return False
        return True

    # ------------------------------------------------------------------
    # Request tracking
    # ------------------------------------------------------------------

    def _should_request(self, structure_id: UnitID) -> bool:
        """Check if we should request this structure (not recently requested)."""
        ai = self.ai
        now = ai.time

        if ai.structure_present_or_pending(structure_id):
            self._pending_requests.pop(structure_id, None)
            return False

        last_request = self._pending_requests.get(structure_id)
        if last_request is not None:
            if now - last_request < self.STRUCT_RETRY_COOLDOWN:
                return False

        return True

    def _mark_requested(self, structure_id: UnitID) -> None:
        """Record that we just issued a build request."""
        self._pending_requests[structure_id] = self.ai.time
