"""Combat System — three-layer combat architecture.

Public entry point for the combat system. Replaces the old CombatManager
with a three-layer pipeline:

  Layer 1: BattlefieldAwareness  → BattlefieldState  (perception)
  Layer 2: CombatPlanner         → CombatPlan         (decisions)
  Layer 3: TacticalExecutor      → ares behaviors     (execution)

Usage from main.py:
    combat_system = CombatSystem(self)
    combat_system.handle_combat(strategy)
    combat_system.handle_early_defense()
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sc2.ids.unit_typeid import UnitTypeId as UnitID
from sc2.position import Point2
from sc2.units import Units

from ares.consts import UnitRole

from bot.combat.awareness import BattlefieldAwareness
from bot.combat.constants import (
    CombatObjective,
    CombatPlan,
    MIN_ATTACK_ARMY_SUPPLY,
    RALLYING_ROLE,
    SquadRole,
)
from bot.combat.executor import TacticalExecutor
from bot.combat.planner import CombatPlanner

if TYPE_CHECKING:
    from ares import AresBot
    from bot.strategy.analyzer import Strategy


class CombatSystem:
    """Three-layer combat orchestrator.

    Wires BattlefieldAwareness → CombatPlanner → TacticalExecutor
    and manages unit role transitions between combat cycles.
    """

    def __init__(self, ai: AresBot) -> None:
        self.ai = ai
        self.awareness = BattlefieldAwareness(ai)
        self.planner = CombatPlanner(ai)
        self.executor = TacticalExecutor(ai)

        self._plan: CombatPlan = CombatPlan()
        self._prev_objective: CombatObjective = CombatObjective.REGROUP
        self._last_log_time: float = 0.0

    # ------------------------------------------------------------------
    # Backward compatibility properties
    # ------------------------------------------------------------------

    @property
    def combat_mode(self) -> str:
        """Backward compat: return mode string matching old CombatMode names."""
        obj = self._plan.objective
        if obj == CombatObjective.DEFEND:
            return "DEFEND"
        elif obj == CombatObjective.REGROUP:
            return "REGROUP"
        elif obj in (
            CombatObjective.ATTACK_BASE,
            CombatObjective.ATTACK_EXPANSION,
            CombatObjective.ATTACK_ARMY,
        ):
            return "ATTACK"
        elif obj == CombatObjective.HARASS:
            return "CHASE"
        elif obj == CombatObjective.CONTAIN:
            return "ATTACK"
        return "RETREAT"

    @property
    def is_attacking(self) -> bool:
        return self.combat_mode in ("ATTACK", "CHASE")

    @property
    def combat_context(self) -> dict:
        """Expose combat state for macro decisions."""
        return {
            "is_attacking": self.is_attacking,
            "combat_mode": self.combat_mode,
            "objective": self._plan.objective.name,
            "peak_army_supply": self.awareness.peak_army_supply,
            "army_wipes": self.awareness.army_wipes,
            "is_regrouping": self._plan.objective == CombatObjective.REGROUP,
            "profile": self._plan.profile_name,
        }

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def handle_combat(self, strategy: Strategy | None = None) -> None:
        """Run the full three-layer combat pipeline.

        1. Perceive: BattlefieldAwareness → BattlefieldState
        2. Decide:   CombatPlanner → CombatPlan
        3. Execute:  TacticalExecutor → ares behaviors
        4. Manage:   Role transitions (RALLYING→ATTACKING, etc.)
        """
        ai = self.ai

        # Layer 1: Perception
        battlefield = self.awareness.update(strategy)

        # Layer 2: Decision
        plan = self.planner.plan(battlefield, strategy)
        self._plan = plan

        # Force-end engagement only on TRANSITION to DEFEND/REGROUP
        prev_objective = self._prev_objective
        self._prev_objective = plan.objective
        if (plan.objective in (CombatObjective.DEFEND, CombatObjective.REGROUP)
                and prev_objective not in (CombatObjective.DEFEND, CombatObjective.REGROUP)):
            eng_tracker = getattr(ai, "_engagement_tracker", None)
            if eng_tracker:
                # Build combat-only dict (excludes drones/overlords/eggs)
                combat_dict: dict = {}
                for role in (UnitRole.ATTACKING, RALLYING_ROLE):
                    for u in (ai.mediator.get_units_from_role(role=role) or []):
                        combat_dict.setdefault(u.type_id, []).append(u)
                record = eng_tracker.force_end(
                    ai.time,
                    ai.state.game_loop,
                    combat_dict,
                    list(ai.enemy_units or []),
                )
                if record is not None:
                    eng_logger = getattr(ai, "game_logger", None)
                    if eng_logger:
                        eng_logger._write(record)

        # Layer 3: Execution
        summary = self.executor.execute(plan, strategy)

        # Role management
        self._manage_roles(plan)

        # Track combat state for logging
        logger = getattr(ai, "game_logger", None)
        if logger:
            logger.track_attack_state(self.is_attacking)
            logger.track_combat_mode(self.combat_mode)

        # Periodic combat summary log
        now = ai.time
        if logger and now - self._last_log_time >= 5.0:
            self._last_log_time = now
            logger.log_decision("combat_system", "cycle_summary", {
                "objective": plan.objective.name,
                "profile": plan.profile_name,
                "approach": plan.approach.name if hasattr(plan, "approach") else "FRONTAL",
                "army_supply": battlefield.our_army_supply,
                "peak_supply": round(battlefield.peak_army_supply, 1),
                "confidence": round(battlefield.confidence, 2),
                "enemy_confidence": round(battlefield.enemy_army.confidence, 2),
                "threats": len(battlefield.threatened_bases),
                "targets": len(battlefield.opportunity_targets),
                "micro_summary": summary,
                "squads": len(plan.squads),
            })

    def handle_early_defense(self) -> None:
        """Simplified defense for build-order phase.

        Preserved exactly from old CombatManager — simple attack commands
        when base is threatened during the build order.
        """
        ai = self.ai
        ground_threats: Units = ai.mediator.get_main_ground_threats_near_townhall
        if not ground_threats or len(ground_threats) == 0:
            return

        # Find the actual threatened base
        th_tag = ai.mediator.get_th_tag_with_largest_ground_threat
        defend_position = ai.start_location
        for th in ai.townhalls:
            if th.tag == th_tag:
                defend_position = th.position
                break

        threat_center = ground_threats.center
        for unit_type in (UnitID.ZERGLING, UnitID.ROACH, UnitID.QUEEN):
            for unit in ai.mediator.get_own_army_dict.get(unit_type, []):
                if unit.type_id == UnitID.QUEEN:
                    if unit.distance_to(defend_position) < 30:
                        unit.attack(threat_center)
                elif unit.is_idle or unit.distance_to(defend_position) < 40:
                    unit.attack(threat_center)

        logger = getattr(ai, "game_logger", None)
        if logger:
            logger.log_decision("combat", "early_defense", {
                "threat_count": len(ground_threats),
                "defend_position": [
                    round(defend_position.x, 1), round(defend_position.y, 1),
                ],
            })

    # ------------------------------------------------------------------
    # Role management
    # ------------------------------------------------------------------

    def _manage_roles(self, plan: CombatPlan) -> None:
        """Handle role transitions based on the current plan.

        - DEFEND: promote all to DEFENDING (handled in executor)
        - REGROUP: keep RALLYING_ROLE units rallying
        - ATTACK: promote RALLYING→ATTACKING, DEFENDING→ATTACKING
        """
        ai = self.ai

        if plan.objective == CombatObjective.DEFEND:
            # Defense role switches are handled in executor._execute_defense
            return

        if plan.objective == CombatObjective.REGROUP:
            # Keep rallying units in RALLYING_ROLE
            return

        # Attack modes: promote rallying to attacking only when we have enough supply
        rallying: Units = ai.mediator.get_units_from_role(role=RALLYING_ROLE)
        if rallying and len(rallying) > 0:
            attacking: Units = ai.mediator.get_units_from_role(role=UnitRole.ATTACKING)
            attacking_supply = sum(u._type_data._proto.food_required for u in attacking) if attacking else 0
            rallying_supply = sum(u._type_data._proto.food_required for u in rallying)
            total_supply = attacking_supply + rallying_supply

            # Only promote if combined army meets minimum threshold
            if total_supply >= MIN_ATTACK_ARMY_SUPPLY:
                ai.mediator.switch_roles(
                    from_role=RALLYING_ROLE, to_role=UnitRole.ATTACKING,
                )
                logger = getattr(ai, "game_logger", None)
                if logger:
                    logger.log_decision("combat_system", "promote_rallying", {
                        "count": len(rallying),
                        "total_supply": round(total_supply, 1),
                        "objective": plan.objective.name,
                    })

        # Also promote any lingering defenders back to attacking
        defenders: Units = ai.mediator.get_units_from_role(role=UnitRole.DEFENDING)
        if defenders and len(defenders) > 0:
            ai.mediator.switch_roles(
                from_role=UnitRole.DEFENDING, to_role=UnitRole.ATTACKING,
            )
