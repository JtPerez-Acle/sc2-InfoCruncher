"""Economy model — computes drone, gas, and expansion targets from game state.

All decisions derived from:
  - Threat level (from ThreatModel)
  - Army advantage (relative, not absolute)
  - Saturation (workers vs available mining slots)
  - Resource float (are we banking too much?)
  - What we need to build (gas demand from recommended comp)

No time gates. No hardcoded tables.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sc2.ids.unit_typeid import UnitTypeId as UnitID

from bot.strategy.threat_model import ThreatAssessment
from bot.strategy.unit_counter_db import UNIT_PROFILES

if TYPE_CHECKING:
    from bot.strategy.composition import CompositionAnalysis
    from bot.strategy.memory import SpendingDiagnosis


@dataclass
class EconomyPlan:
    """Economy targets computed from game state."""

    target_drones: int = 44
    target_gas: int = 2
    target_bases: int = 2
    army_ratio: float = 0.5     # 0.0 = all drones, 1.0 = all army
    safe_to_expand: bool = True


class EconomyModel:
    """Computes economy plan from threat assessment + game state."""

    # Per-base mineral saturation (2 workers per patch × ~8 patches)
    IDEAL_WORKERS_PER_BASE = 16
    # Per-gas saturation
    WORKERS_PER_GAS = 3

    def evaluate(
        self,
        threat: ThreatAssessment,
        supply_workers: int,
        supply_army: int,
        num_bases: int,
        minerals: int,
        vespene: int,
        num_extractors: int = 0,
        spending: SpendingDiagnosis | None = None,
        composition: CompositionAnalysis | None = None,
    ) -> EconomyPlan:
        """Compute economy targets from current state + threat + spending."""
        plan = EconomyPlan()

        plan.target_drones = self._compute_target_drones(
            threat, num_bases, supply_workers, spending
        )
        plan.target_gas = self._compute_target_gas(
            threat.recommended_comp, num_bases, supply_army, spending,
            composition, minerals, vespene,
        )
        plan.target_bases = self._compute_target_bases(
            threat, num_bases, supply_workers, minerals, spending
        )
        plan.army_ratio = self._compute_army_ratio(
            threat, supply_workers, plan.target_drones, minerals, spending
        )
        plan.safe_to_expand = threat.threat_level < 0.5

        return plan

    # ------------------------------------------------------------------
    # Drone targets
    # ------------------------------------------------------------------

    def _compute_target_drones(
        self,
        threat: ThreatAssessment,
        num_bases: int,
        supply_workers: int,
        spending: SpendingDiagnosis | None = None,
    ) -> int:
        """Target drone count based on bases + threat.

        Principle: mine at optimal rate, but not when under pressure.
        """
        # Optimal mineral saturation for current bases
        optimal = num_bases * self.IDEAL_WORKERS_PER_BASE

        # Cap at reasonable maximum (66 is ~4 base full saturation + gas)
        optimal = min(optimal, 72)

        # Under pressure: reduce drone target
        if threat.threat_level > 0.7:
            # Severe threat: only maintain minimum economy
            return min(supply_workers, max(16, optimal - 20))
        if threat.threat_level > 0.5:
            # Moderate threat: slow down droning
            return min(optimal, max(24, optimal - 8))

        # Behind on army: don't overdrone
        if threat.army_advantage < 0.6:
            return min(supply_workers + 2, optimal - 8)

        # Tech-bound: don't add more drones, spend on army + tech instead
        if spending and spending.is_tech_bound:
            return min(supply_workers + 2, max(16, optimal - 4))

        return optimal

    # ------------------------------------------------------------------
    # Gas targets
    # ------------------------------------------------------------------

    def _compute_target_gas(
        self,
        recommended_comp: dict[UnitID, float],
        num_bases: int,
        supply_army: int,
        spending: SpendingDiagnosis | None = None,
        composition: CompositionAnalysis | None = None,
        minerals: int = 0,
        vespene: int = 0,
    ) -> int:
        """Gas count follows what we NEED to build.

        Calculate gas demand from recommended composition,
        then scale extractors to match. Reduces gas workers when
        vespene is banking far ahead of minerals.
        """
        if not recommended_comp:
            return min(2, num_bases)

        # Calculate gas intensity of recommended composition
        total_mineral_demand = 0.0
        total_gas_demand = 0.0

        for unit_type, proportion in recommended_comp.items():
            profile = UNIT_PROFILES.get(unit_type)
            if profile:
                total_mineral_demand += proportion * profile.mineral_cost
                total_gas_demand += proportion * profile.gas_cost

        total_cost = total_mineral_demand + total_gas_demand
        if total_cost <= 0:
            return min(2, num_bases)

        # Gas intensity: 0.0 (all mineral) to 1.0 (all gas)
        gas_intensity = total_gas_demand / total_cost

        # Maximum extractors available (2 per base)
        max_gas = num_bases * 2

        # Scale extractors by gas intensity
        # Pure mineral comp (lings) → 0-1 gas
        # Gas heavy comp (hydra/lurker) → scale up
        if gas_intensity < 0.1:
            target = 1  # Minimal gas for upgrades
        elif gas_intensity < 0.25:
            target = max(2, int(max_gas * 0.4))
        elif gas_intensity < 0.40:
            target = max(3, int(max_gas * 0.6))
        else:
            target = max(4, int(max_gas * 0.8))

        # Air-heavy enemy needs more gas (Corruptors = 150g each)
        if composition and composition.air_fraction > 0.5:
            target = max(target, min(4, max_gas))

        # Gas-bound: ensure minimum gas when comp needs it
        if spending and spending.is_gas_bound:
            target = max(target, min(3, max_gas))

        # Gas banking feedback: pull workers off gas when vespene >> minerals
        if vespene > 400 and vespene > minerals * 2:
            target = max(1, target - 1)
        if vespene > 800 and vespene > minerals * 1.5:
            target = max(1, target - 2)
        if vespene > 2000:
            target = max(1, target - 3)
        if vespene > 4000:
            target = max(1, target - 5)

        # Don't over-gas early: cap at what we can mine
        return min(target, max_gas)

    # ------------------------------------------------------------------
    # Base count targets
    # ------------------------------------------------------------------

    def _compute_target_bases(
        self,
        threat: ThreatAssessment,
        num_bases: int,
        supply_workers: int,
        minerals: int,
        spending: SpendingDiagnosis | None = None,
    ) -> int:
        """Expand when SAFE and NEEDED, not on a timer."""
        # Always want natural
        if num_bases < 2:
            return 2

        # Don't expand above 8
        if num_bases >= 8:
            return num_bases

        # Don't expand under heavy pressure
        if threat.threat_level > 0.6:
            return num_bases

        # Larva-bound: need more hatches to spend minerals
        if spending and spending.is_larva_bound and spending.float_duration > 15.0:
            return num_bases + 1

        # Saturation check: workers vs available mining slots
        available_patches = num_bases * self.IDEAL_WORKERS_PER_BASE
        saturation = supply_workers / max(available_patches, 1)

        # Running out of mining space
        if saturation > 0.75:
            return num_bases + 1

        # Floating resources = invest in economy
        if minerals > 600 and threat.safe_to_drone:
            return num_bases + 1

        # Army way ahead + safe = greed
        if threat.army_advantage > 2.0 and threat.safe_to_drone:
            return num_bases + 1

        # Enemy is expanding: match or punish
        if threat.enemy_is_expanding and threat.enemy_base_count > num_bases:
            return num_bases + 1

        return num_bases

    # ------------------------------------------------------------------
    # Army ratio
    # ------------------------------------------------------------------

    def _compute_army_ratio(
        self,
        threat: ThreatAssessment,
        supply_workers: int,
        target_drones: int,
        minerals: int,
        spending: SpendingDiagnosis | None = None,
    ) -> float:
        """How much to prioritize army vs economy.

        0.0 = all drones, 1.0 = all army.
        Driven by threat, army advantage, saturation, resource float.
        """
        # Workers already at target: mostly army
        if supply_workers >= target_drones:
            # Larva-bound = still want drones when we expand
            if spending and spending.is_larva_bound:
                return 0.7
            return 1.0

        # Under severe pressure: full army
        if threat.threat_level > 0.7:
            return 1.0

        # Under moderate pressure: mostly army
        if threat.threat_level > 0.4:
            return 0.85

        # Army disadvantage: need more units
        if threat.army_advantage < 0.6:
            return 0.9

        # Floating minerals: spend on army
        if minerals > 800:
            return 0.8

        # Big army advantage + safe: drone hard
        if threat.army_advantage > 2.0 and threat.safe_to_drone:
            return 0.3

        # Slight advantage: balanced
        if threat.army_advantage > 1.3:
            return 0.5

        # Default: balanced leaning army
        return 0.6
