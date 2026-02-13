"""Layer 2: Combat Planner — strategic decision layer.

Reads a BattlefieldState (from Layer 1) and the current Strategy,
and produces a CombatPlan that tells the executor what to do.

Every decision is data-driven: we use the engagement profile for the
enemy archetype, info confidence for timing, army advantage for boldness,
and terrain for approach selection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sc2.ids.unit_typeid import UnitTypeId as UnitID
from sc2.position import Point2
from sc2.units import Units

from ares.consts import UnitRole

from bot.combat.constants import (
    ATTACK_COMMIT_TIME,
    FAST_TYPES,
    FLYER_TYPES,
    HARASS_MAX_FRACTION,
    HARASS_MIN_ARMY_SUPPLY,
    MIN_ATTACK_ARMY_SUPPLY,
    MIN_ATTACK_SUPPLY,
    MIN_REGROUP_DURATION,
    PEAK_DECAY_RATE,
    RALLYING_ROLE,
    REGROUP_RATIO,
    WIPE_COOLDOWN,
    ApproachStyle,
    BattlefieldState,
    CombatObjective,
    CombatPlan,
    KiteStyle,
    SquadAssignment,
    SquadRole,
)
from bot.combat.profiles import EngagementProfile, get_profile

if TYPE_CHECKING:
    from ares import AresBot
    from bot.strategy.analyzer import Strategy


class CombatPlanner:
    """Layer 2 — decides WHAT to do with the army.

    Produces a CombatPlan each cycle based on:
    - BattlefieldState (Layer 1 output)
    - Strategy (strategic analyzer output)
    - Persistent state (attack commitment, timing)
    """

    def __init__(self, ai: AresBot) -> None:
        self.ai = ai
        self._attack_committed: bool = False
        self._commit_start_time: float = 0.0
        self._regroup_start_time: float = 0.0
        self._last_plan: CombatPlan = CombatPlan()

    @property
    def last_plan(self) -> CombatPlan:
        return self._last_plan

    def plan(
        self,
        battlefield: BattlefieldState,
        strategy: Strategy | None = None,
    ) -> CombatPlan:
        """Produce a CombatPlan from current battlefield + strategy."""
        ai = self.ai
        now = battlefield.game_time
        plan = CombatPlan()

        # --- Select engagement profile from enemy archetype ---
        archetype = "DEFAULT"
        if strategy is not None and hasattr(strategy, "threat"):
            archetype = strategy.threat.enemy_archetype or "DEFAULT"
        profile = get_profile(archetype)
        plan.profile_name = archetype

        # --- 1. Defense check (highest priority) ---
        if battlefield.threatened_bases:
            plan = self._plan_defense(battlefield, profile)
            self._last_plan = plan
            return plan

        # --- 2. Regroup check ---
        if self._should_regroup(battlefield, strategy, now):
            plan = self._plan_regroup(battlefield, profile)
            self._last_plan = plan
            return plan

        # --- 3. Attack planning ---
        plan = self._plan_attack(battlefield, strategy, profile, now)
        self._last_plan = plan
        return plan

    # ------------------------------------------------------------------
    # Defense
    # ------------------------------------------------------------------

    def _plan_defense(
        self,
        battlefield: BattlefieldState,
        profile: EngagementProfile,
    ) -> CombatPlan:
        """Defend threatened bases — highest priority, force engage."""
        # Defend the first threatened base
        defend_target = battlefield.threatened_bases[0]

        plan = CombatPlan(
            objective=CombatObjective.DEFEND,
            approach=ApproachStyle.FRONTAL,
            primary_target=defend_target,
            retreat_point=battlefield.staging_point or defend_target,
            force_engage=True,
            profile_name=profile.name,
        )

        # All units defend — single DEFENSE squad
        plan.squads = [SquadAssignment(
            role=SquadRole.DEFENSE,
            target=defend_target,
            kite_style=profile.kite_style,
            priority_targets=list(profile.focus_targets),
            avoid_targets=list(profile.avoid_targets),
        )]

        logger = getattr(self.ai, "game_logger", None)
        if logger:
            logger.log_decision("planner", "defend", {
                "target": [round(defend_target.x, 1), round(defend_target.y, 1)],
                "threat_count": battlefield.threat_units_near_bases,
                "profile": profile.name,
            })

        return plan

    # ------------------------------------------------------------------
    # Regroup
    # ------------------------------------------------------------------

    def _should_regroup(
        self,
        battlefield: BattlefieldState,
        strategy: Strategy | None,
        now: float,
    ) -> bool:
        """Determine if army should regroup instead of attacking."""
        supply = battlefield.our_army_supply

        # Absolute minimum
        if supply < MIN_ATTACK_SUPPLY:
            return True

        # Scale minimum with game phase — don't send 40 supply into late-game
        min_attack = MIN_ATTACK_SUPPLY
        if battlefield.peak_army_supply > 60:
            min_attack = max(MIN_ATTACK_SUPPLY, battlefield.peak_army_supply * 0.4)
        if supply < min_attack:
            return True

        # Enforce minimum regroup duration — don't exit regroup too early
        if self._regroup_start_time > 0:
            elapsed = now - self._regroup_start_time
            if elapsed < MIN_REGROUP_DURATION:
                return True

        # Tiered gate: strategy confidence lowers the threshold
        if strategy and strategy.attack_ready:
            # Strategy says go — allow attack at lower supply
            if supply < MIN_ATTACK_SUPPLY * 1.5:  # 30 supply
                return True
        else:
            # Strategy not ready — require full army
            if supply < MIN_ATTACK_ARMY_SUPPLY:
                return True

        # Attack commitment: stay committed for ATTACK_COMMIT_TIME
        if self._attack_committed:
            if now - self._commit_start_time < ATTACK_COMMIT_TIME:
                return False
            # Commitment expired — re-evaluate
            self._attack_committed = False

        # Recently wiped — need higher threshold
        if now - battlefield.last_wipe_time < WIPE_COOLDOWN:
            threshold = battlefield.peak_army_supply * REGROUP_RATIO
            if supply < threshold:
                return True

        # Strategy says not ready AND supply is borderline
        if strategy is not None:
            if not strategy.attack_ready and supply < MIN_ATTACK_ARMY_SUPPLY:
                return True

        return False

    def _plan_regroup(
        self,
        battlefield: BattlefieldState,
        profile: EngagementProfile,
    ) -> CombatPlan:
        """Regroup at staging point — rebuild army."""
        staging = battlefield.staging_point or self.ai.start_location

        # Record when regroup started (for minimum duration enforcement)
        if self._regroup_start_time == 0:
            self._regroup_start_time = battlefield.game_time

        plan = CombatPlan(
            objective=CombatObjective.REGROUP,
            approach=ApproachStyle.FRONTAL,
            primary_target=staging,
            retreat_point=staging,
            engage_if_sim_wins=True,
            profile_name=profile.name,
        )

        plan.squads = [SquadAssignment(
            role=SquadRole.RALLYING,
            target=staging,
            kite_style=KiteStyle.STUTTER_BACK,
        )]

        logger = getattr(self.ai, "game_logger", None)
        if logger:
            logger.log_decision("planner", "regroup", {
                "supply": battlefield.our_army_supply,
                "min_attack_supply": MIN_ATTACK_ARMY_SUPPLY,
                "regroup_elapsed": round(battlefield.game_time - self._regroup_start_time, 1),
                "peak": round(battlefield.peak_army_supply, 1),
                "threshold": round(battlefield.peak_army_supply * REGROUP_RATIO, 1),
                "staging": [round(staging.x, 1), round(staging.y, 1)],
            })

        return plan

    # ------------------------------------------------------------------
    # Attack
    # ------------------------------------------------------------------

    def _plan_attack(
        self,
        battlefield: BattlefieldState,
        strategy: Strategy | None,
        profile: EngagementProfile,
        now: float,
    ) -> CombatPlan:
        """Plan offensive action — objective, approach, squads."""
        ai = self.ai

        # Reset regroup timer when attacking
        self._regroup_start_time = 0.0

        # Choose objective from targets
        objective, primary_target = self._choose_objective(
            battlefield, strategy,
        )

        # Choose approach style
        approach = self._choose_approach(
            battlefield, strategy, profile,
        )

        # Commit to attack
        if not self._attack_committed:
            self._attack_committed = True
            self._commit_start_time = now

        plan = CombatPlan(
            objective=objective,
            approach=approach,
            primary_target=primary_target,
            retreat_point=battlefield.staging_point or ai.start_location,
            engage_if_sim_wins=True,
            profile_name=profile.name,
            attack_committed=True,
            commit_start_time=self._commit_start_time,
        )

        # Assign squads based on approach
        plan.squads = self._assign_squads(
            battlefield, strategy, profile, approach, primary_target,
        )

        # Set max harass supply
        if approach == ApproachStyle.MULTI_PRONG:
            plan.max_harass_supply = (
                battlefield.our_army_supply * HARASS_MAX_FRACTION
            )
            plan.allow_split = True

        logger = getattr(ai, "game_logger", None)
        if logger:
            logger.log_decision("planner", "attack", {
                "objective": objective.name,
                "approach": approach.name,
                "target": [round(primary_target.x, 1), round(primary_target.y, 1)]
                    if primary_target else None,
                "profile": profile.name,
                "supply": battlefield.our_army_supply,
                "confidence": round(battlefield.confidence, 2),
                "squads": len(plan.squads),
                "committed": self._attack_committed,
            })

        return plan

    def _choose_objective(
        self,
        battlefield: BattlefieldState,
        strategy: Strategy | None,
    ) -> tuple[CombatObjective, Point2 | None]:
        """Choose attack objective and primary target.

        Uses opportunity target scores, enemy archetype, and confidence.
        """
        ai = self.ai

        # Check for turtling enemy → contain
        if strategy is not None and strategy.threat.enemy_is_turtling:
            # Contain: don't attack into static defense, pressure expansions
            target = self._find_expansion_target(battlefield)
            if target is not None:
                return CombatObjective.CONTAIN, target

        # Best opportunity target
        if battlefield.opportunity_targets:
            best = battlefield.opportunity_targets[0]
            if best.target_type == "expansion":
                return CombatObjective.ATTACK_EXPANSION, best.position
            elif best.target_type == "mineral_line":
                # Mineral lines are harass targets, not main army objective
                # Use second best for main army
                if len(battlefield.opportunity_targets) > 1:
                    second = battlefield.opportunity_targets[1]
                    return CombatObjective.ATTACK_BASE, second.position
                return CombatObjective.ATTACK_BASE, best.position
            return CombatObjective.ATTACK_BASE, best.position

        # Fallback: attack enemy start location
        if ai.enemy_start_locations:
            return CombatObjective.ATTACK_BASE, ai.enemy_start_locations[0]

        return CombatObjective.ATTACK_BASE, ai.game_info.map_center

    def _find_expansion_target(
        self, battlefield: BattlefieldState,
    ) -> Point2 | None:
        """Find an enemy expansion to pressure (for contain objective)."""
        for target in battlefield.opportunity_targets:
            if target.target_type == "expansion" and target.risk < 40:
                return target.position
        return None

    def _choose_approach(
        self,
        battlefield: BattlefieldState,
        strategy: Strategy | None,
        profile: EngagementProfile,
    ) -> ApproachStyle:
        """Choose approach style based on enemy comp and our army.

        Rules:
        - Enemy mech → FLANK (never walk into siege line head-on)
        - Large army + fast units + profile allows → MULTI_PRONG
        - Default → FRONTAL
        """
        # Mech → flank
        if strategy is not None and strategy.threat.is_mech_heavy:
            return ApproachStyle.FLANK

        # Multi-prong: need enough army AND fast units AND profile allows harass
        if (battlefield.our_army_supply >= HARASS_MIN_ARMY_SUPPLY
                and profile.allow_harass):
            # Check if we have fast units available for split
            ai = self.ai
            army = ai.mediator.get_units_from_role(role=UnitRole.ATTACKING)
            if army:
                fast_count = sum(
                    1 for u in army if u.type_id in FAST_TYPES
                )
                if fast_count >= 6:  # need at least 6 fast units
                    return ApproachStyle.MULTI_PRONG

        return ApproachStyle.FRONTAL

    def _assign_squads(
        self,
        battlefield: BattlefieldState,
        strategy: Strategy | None,
        profile: EngagementProfile,
        approach: ApproachStyle,
        primary_target: Point2 | None,
    ) -> list[SquadAssignment]:
        """Create squad assignments based on approach."""
        squads: list[SquadAssignment] = []
        ai = self.ai

        if primary_target is None:
            primary_target = ai.enemy_start_locations[0] if ai.enemy_start_locations else ai.game_info.map_center

        # Main army squad (always present)
        main_squad = SquadAssignment(
            role=SquadRole.MAIN_ARMY,
            target=primary_target,
            kite_style=profile.kite_style,
            priority_targets=list(profile.focus_targets),
            avoid_targets=list(profile.avoid_targets),
        )

        if approach == ApproachStyle.MULTI_PRONG:
            # Split: fast units go as harass, rest as main army
            harass_target = self._find_harass_target(battlefield)
            if harass_target is not None:
                harass_squad = SquadAssignment(
                    role=SquadRole.HARASS,
                    target=harass_target,
                    kite_style=KiteStyle.STUTTER_BACK,
                )
                squads.append(harass_squad)

        squads.append(main_squad)
        return squads

    def _find_harass_target(
        self, battlefield: BattlefieldState,
    ) -> Point2 | None:
        """Find best harassment target — far from enemy army, high value.

        Prefers mineral lines and expansions that are poorly defended.
        """
        for target in battlefield.opportunity_targets:
            # Good harass target: away from enemy army, not super risky
            if target.enemy_army_distance > 40 and target.risk < 40:
                return target.position

        # Second pass: any expansion
        for target in battlefield.opportunity_targets:
            if target.target_type in ("expansion", "mineral_line"):
                return target.position

        return None
