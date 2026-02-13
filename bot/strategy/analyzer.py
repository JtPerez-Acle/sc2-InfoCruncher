"""Strategic Analyzer — environment-driven decision engine.

Reads game state every step, outputs a Strategy that drives all macro and
combat decisions. No hardcoded recipes — every decision is a calculation
from observable state.

Integration point:
    on_step():
        strategy = analyzer.evaluate()
        macro_manager.handle_macro(strategy)
        combat_manager.handle_combat(strategy)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sc2.ids.unit_typeid import UnitTypeId as UnitID
from sc2.position import Point2

from bot.strategy.composition import CompositionClassifier
from bot.strategy.economy_model import EconomyModel, EconomyPlan
from bot.strategy.memory import StrategicMemory
from bot.strategy.threat_model import ThreatAssessment, ThreatModel
from bot.strategy.unit_counter_db import compute_army_value

if TYPE_CHECKING:
    from ares import AresBot


@dataclass
class Strategy:
    """Complete strategic output for one game step."""

    threat: ThreatAssessment = field(default_factory=ThreatAssessment)
    economy: EconomyPlan = field(default_factory=EconomyPlan)
    army_comp: dict[UnitID, float] = field(default_factory=dict)
    tech_targets: list[UnitID] = field(default_factory=list)
    attack_ready: bool = False


class StrategicAnalyzer:
    """Reads game state, outputs strategic decisions.

    Called every on_step(). Caches analysis for 2 seconds to avoid
    redundant computation (game state doesn't change that fast).
    """

    ANALYSIS_INTERVAL = 2.0  # seconds between full re-analysis

    def __init__(self, ai: AresBot) -> None:
        self.ai = ai
        self.threat_model = ThreatModel()
        self.economy_model = EconomyModel()
        self.memory = StrategicMemory()
        self.classifier = CompositionClassifier()
        self._last_analysis_time: float = -999.0
        self._cached_strategy: Strategy = Strategy()

    def evaluate(self) -> Strategy:
        """Run full strategic analysis from current game state."""
        ai = self.ai
        now = ai.time

        # Use cache if recent enough
        if now - self._last_analysis_time < self.ANALYSIS_INTERVAL:
            return self._cached_strategy

        self._last_analysis_time = now

        # --- Gather raw observations ---
        own_structures = ai.mediator.get_own_structures_dict

        # Build enemy composition from visible units
        enemy_comp: dict[UnitID, int] = {}
        if ai.enemy_units:
            for u in ai.enemy_units:
                enemy_comp[u.type_id] = enemy_comp.get(u.type_id, 0) + 1

        # Build enemy structure set from visible structures
        enemy_struct_set: set[UnitID] = set()
        if ai.enemy_structures:
            for s in ai.enemy_structures:
                enemy_struct_set.add(s.type_id)

        # Threat counts near our bases
        ground_threats = ai.mediator.get_main_ground_threats_near_townhall
        air_threats = ai.mediator.get_main_air_threats_near_townhall
        ground_count = len(ground_threats) if ground_threats else 0
        air_count = len(air_threats) if air_threats else 0

        # Army values — computed from army dicts + our UNIT_PROFILES
        # (mediator.own_army_value / enemy_army_value don't exist on the
        # ares mediator API, so we compute them ourselves)
        own_army_dict = ai.mediator.get_own_army_dict
        own_army_value = compute_army_value(own_army_dict)

        try:
            enemy_army_dict = ai.mediator.get_enemy_army_dict
            enemy_army_value = compute_army_value(enemy_army_dict)
        except Exception:
            enemy_army_value = 0.0

        # Enemy expanded?
        try:
            enemy_expanded = ai.mediator.get_enemy_expanded
        except Exception:
            enemy_expanded = False

        # --- Phase 0: Update strategic memory ---
        self.memory.update(
            enemy_comp=enemy_comp,
            enemy_structures=enemy_struct_set,
            enemy_army_value=enemy_army_value,
            game_time=now,
        )
        estimated_value = self.memory.get_estimated_enemy_value(now)
        cumulative_comp = self.memory.get_cumulative_enemy_comp()

        # --- Phase 0.5: Composition classification ---
        composition = self.classifier.classify(
            cumulative_comp=cumulative_comp,
            structures_ever=self.memory.enemy_structures_ever,
            enemy_race=self.threat_model._infer_race(
                enemy_comp, enemy_struct_set
            ),
            game_time=now,
            enemy_base_count=self.threat_model._count_enemy_bases(
                enemy_struct_set
            ),
            visible_army_count=sum(enemy_comp.values()),
        )

        # --- Phase 1: Threat assessment (with memory data + composition) ---
        threat = self.threat_model.evaluate(
            enemy_comp=enemy_comp,
            enemy_structures=enemy_struct_set,
            own_structures=own_structures,
            own_army_value=own_army_value,
            enemy_army_value=enemy_army_value,
            game_time=now,
            ground_threats_near_base=ground_count,
            air_threats_near_base=air_count,
            enemy_expanded=enemy_expanded,
            estimated_enemy_value=estimated_value,
            cumulative_enemy_comp=cumulative_comp,
            composition=composition,
        )

        # --- Phase 1.5: Spending diagnosis ---
        larva_count = len(ai.larva) if ai.larva else 0
        spending = self.memory.diagnose_spending(
            minerals=ai.minerals,
            vespene=ai.vespene,
            supply_used=ai.supply_used,
            supply_cap=ai.supply_cap,
            larva_count=larva_count,
            recommended_comp=threat.recommended_comp,
            own_structures=own_structures,
            game_time=now,
        )

        # --- Phase 2: Economy plan (with spending diagnosis) ---
        num_extractors = len(own_structures.get(UnitID.EXTRACTOR, []))
        economy = self.economy_model.evaluate(
            threat=threat,
            supply_workers=ai.supply_workers,
            supply_army=ai.supply_used - ai.supply_workers,
            num_bases=len(ai.townhalls),
            minerals=ai.minerals,
            vespene=ai.vespene,
            num_extractors=num_extractors,
            spending=spending,
            composition=composition,
        )

        # --- Phase 3: Attack readiness ---
        attack_ready = self._should_attack(threat, economy, ai)

        # --- Build strategy ---
        strategy = Strategy(
            threat=threat,
            economy=economy,
            army_comp=threat.recommended_comp,
            tech_targets=threat.recommended_tech,
            attack_ready=attack_ready,
        )

        self._cached_strategy = strategy

        # --- Log strategic analysis ---
        logger = getattr(ai, "game_logger", None)
        if logger and int(now) % 5 < 0.2:
            logger.log_decision("strategy", "analysis", {
                "threat_level": round(threat.threat_level, 2),
                "army_advantage": round(threat.army_advantage, 2),
                "enemy_race": threat.enemy_race,
                "enemy_tech": threat.enemy_tech_level,
                "enemy_air": threat.enemy_has_air,
                "enemy_aggressive": threat.enemy_is_aggressive,
                "dominant_enemy": (
                    threat.dominant_enemy_type.name
                    if threat.dominant_enemy_type else None
                ),
                "recommended_comp": {
                    k.name: round(v, 2)
                    for k, v in threat.recommended_comp.items()
                },
                "tech_targets": [t.name for t in threat.recommended_tech],
                "army_ratio": round(economy.army_ratio, 2),
                "target_drones": economy.target_drones,
                "target_gas": economy.target_gas,
                "target_bases": economy.target_bases,
                "attack_ready": attack_ready,
            })
            # Log composition classification
            logger.log_decision("strategy", "composition", {
                "archetype": composition.archetype.name,
                "confidence": round(composition.confidence, 2),
                "air_fraction": round(composition.air_fraction, 2),
                "aa_floor": round(composition.recommended_aa_floor, 2),
                "is_mech": composition.is_mech_heavy,
                "anti_armor": composition.anti_armor_priority,
            })
            # Log memory state for diagnostics
            logger.log_decision("strategy", "memory_state", {
                "estimated_enemy_value": round(
                    self.memory.estimated_enemy_value, 0
                ),
                "enemy_types_seen": len(self.memory.enemy_history),
                "structures_ever_seen": sorted(
                    s.name for s in self.memory.enemy_structures_ever
                ),
                "is_larva_bound": spending.is_larva_bound,
                "is_tech_bound": spending.is_tech_bound,
                "is_gas_bound": spending.is_gas_bound,
                "is_supply_bound": spending.is_supply_bound,
                "float_duration": round(spending.float_duration, 1),
                "mineral_float": spending.mineral_float,
            })

        return strategy

    # ------------------------------------------------------------------
    # Attack timing — relative to enemy, not absolute
    # ------------------------------------------------------------------

    def _should_attack(
        self,
        threat: ThreatAssessment,
        economy: EconomyPlan,
        ai: AresBot,
    ) -> bool:
        """Determine if we should initiate an attack.

        Based on relative army strength, not magic supply numbers.
        Has supply-based fallbacks for when army_advantage data is unreliable.
        """
        army_supply = ai.supply_used - ai.supply_workers

        # Minimum army to move out
        if army_supply < 15:
            return False

        # Maxed out: must attack, can't grow more
        if ai.supply_used >= 190:
            return True

        # Strong advantage: attack now, don't let them catch up
        if threat.army_advantage > 1.5 and army_supply > 25:
            return True

        # Decent advantage with good army size
        if threat.army_advantage > 1.0 and army_supply > 50:
            return True

        # Enemy is expanding/droning: punish with timing attack
        if threat.enemy_is_expanding and army_supply > 35:
            if threat.army_advantage > 0.8:
                return True

        # Enemy is turtling: expand and outmacro, or attack with big army
        if threat.enemy_is_turtling and army_supply > 80:
            return True

        # --- Supply-based fallbacks (when advantage data is uncertain) ---

        # Large army: attack regardless of advantage estimate
        # If we have 80+ army supply, we should be fighting
        if army_supply >= 80:
            return True

        # Medium army with decent size: go if not under direct threat
        if army_supply >= 50 and threat.threat_level < 0.5:
            return True

        # We have resources and can't spend them: attack
        if ai.minerals > 1000 and army_supply > 30:
            return True

        return False
