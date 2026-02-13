"""Layer 3: Tactical Executor — micro execution layer.

Reads a CombatPlan (from Layer 2) and converts it into ares-sc2 behaviors
registered with the bot. Handles per-unit micro, group movements,
focus fire, AOE abilities, and kiting styles.

Every micro decision is driven by the engagement profile for the current
enemy archetype — focus targets, kite style, spread distance, and
ability priorities all come from the profile.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sc2.ids.unit_typeid import UnitTypeId as UnitID
from sc2.position import Point2
from sc2.unit import Unit
from sc2.units import Units

from ares.behaviors.combat import CombatManeuver
from ares.behaviors.combat.individual import (
    AMove,
    AttackTarget,
    KeepUnitSafe,
    PathUnitToTarget,
    ShootTargetInRange,
    StutterUnitBack,
    StutterUnitForward,
)
from ares.behaviors.combat.individual.auto_use_aoe_ability import AutoUseAOEAbility
from ares.consts import (
    EngagementResult,
    UnitRole,
    LOSS_CLOSE_OR_WORSE,
    VICTORY_CLOSE_OR_BETTER,
    VICTORY_DECISIVE_OR_BETTER,
)

from bot.combat.constants import (
    AOE_TYPES,
    FAST_TYPES,
    FLYER_TYPES,
    MELEE_TYPES,
    RANGED_TYPES,
    RALLYING_ROLE,
    STATIC_DEFENSE_TYPES,
    WORKER_TYPES,
    CombatObjective,
    CombatPlan,
    KiteStyle,
    SquadAssignment,
    SquadRole,
)
from bot.combat.profiles import EngagementProfile, get_profile
from bot.combat.targeting import pick_best_target

if TYPE_CHECKING:
    from ares import AresBot
    from bot.strategy.analyzer import Strategy


class TacticalExecutor:
    """Layer 3 — registers ares behaviors from the CombatPlan.

    Handles:
    - Squad-based group movement (PathGroupToTarget for 3+ groups)
    - Profile-driven per-unit micro (kiting, focus fire, AOE)
    - Defense micro with combat sim
    - Harass squad with retreat logic
    - Rally micro for regrouping units
    """

    def __init__(self, ai: AresBot) -> None:
        self.ai = ai

    def execute(
        self,
        plan: CombatPlan,
        strategy: Strategy | None = None,
    ) -> dict:
        """Execute a CombatPlan by registering ares behaviors.

        Returns a micro summary dict for logging.
        """
        ai = self.ai
        profile = get_profile(plan.profile_name)
        summary: dict[str, int] = {}

        for squad in plan.squads:
            if squad.role == SquadRole.RALLYING:
                count = self._execute_rally(squad, plan)
                summary["rally"] = summary.get("rally", 0) + count
            elif squad.role == SquadRole.DEFENSE:
                count = self._execute_defense(squad, plan, profile)
                summary["defend"] = summary.get("defend", 0) + count
            elif squad.role == SquadRole.HARASS:
                count = self._execute_harass(squad, plan, profile)
                summary["harass"] = summary.get("harass", 0) + count
            elif squad.role == SquadRole.MAIN_ARMY:
                count = self._execute_main_army(squad, plan, profile, strategy)
                summary["main"] = summary.get("main", 0) + count

        return summary

    # ------------------------------------------------------------------
    # Rally — path to staging point, stay safe
    # ------------------------------------------------------------------

    def _execute_rally(
        self,
        squad: SquadAssignment,
        plan: CombatPlan,
    ) -> int:
        """Move rallying units toward staging point safely.

        Rallying units will fire at enemies in weapon range (self-defense)
        while pathing to the staging point.
        """
        ai = self.ai
        grid = ai.mediator.get_ground_grid
        target = squad.target

        # Get all rallying-role units
        units: Units = ai.mediator.get_units_from_role(role=RALLYING_ROLE)
        if not units or len(units) == 0:
            return 0

        # Find nearby enemies for self-defense
        nearby_enemies = (
            ai.enemy_units.closer_than(15, target)
            if ai.enemy_units and len(ai.enemy_units) > 0
            else []
        )
        enemy_list = list(nearby_enemies) if nearby_enemies else []

        for unit in units:
            maneuver = CombatManeuver()
            # Self-defense: shoot enemies in weapon range while rallying
            if enemy_list:
                maneuver.add(ShootTargetInRange(unit=unit, targets=enemy_list))
            maneuver.add(KeepUnitSafe(unit=unit, grid=grid))
            maneuver.add(PathUnitToTarget(
                unit=unit, grid=grid, target=target, success_at_distance=5.0,
            ))
            ai.register_behavior(maneuver)

        return len(units)

    # ------------------------------------------------------------------
    # Defense — engage at threatened base
    # ------------------------------------------------------------------

    def _execute_defense(
        self,
        squad: SquadAssignment,
        plan: CombatPlan,
        profile: EngagementProfile,
    ) -> int:
        """Defend a base — run combat sim to decide micro style."""
        ai = self.ai
        grid = ai.mediator.get_ground_grid

        # Get all combat units for defense (switch roles)
        army: Units = ai.mediator.get_units_from_role(role=UnitRole.ATTACKING)
        rallying: Units = ai.mediator.get_units_from_role(role=RALLYING_ROLE)

        # During defense, promote all to defending
        if army and len(army) > 0:
            ai.mediator.switch_roles(
                from_role=UnitRole.ATTACKING, to_role=UnitRole.DEFENDING,
            )
        if rallying and len(rallying) > 0:
            ai.mediator.switch_roles(
                from_role=RALLYING_ROLE, to_role=UnitRole.DEFENDING,
            )

        defenders: Units = ai.mediator.get_units_from_role(role=UnitRole.DEFENDING)
        if not defenders or len(defenders) == 0:
            return 0

        # Find threats
        ground_threats: Units = ai.mediator.get_main_ground_threats_near_townhall
        air_threats: Units = ai.mediator.get_main_air_threats_near_townhall
        threats = ground_threats if ground_threats and len(ground_threats) > 0 else air_threats
        if not threats or len(threats) == 0:
            # Signal no-enemy frame to engagement tracker (may finalize)
            eng_tracker = getattr(ai, "_engagement_tracker", None)
            if eng_tracker:
                combat_dict: dict = {}
                for u in defenders:
                    combat_dict.setdefault(u.type_id, []).append(u)
                record = eng_tracker.on_combat_frame(
                    has_enemies=False,
                    own_army_dict=combat_dict,
                    enemy_units=[],
                    sim_result_name="NONE",
                    micro_style="defend",
                    position=[round(squad.target.x, 1), round(squad.target.y, 1)],
                    game_time=ai.time,
                    step=ai.state.game_loop,
                )
                if record is not None:
                    eng_logger = getattr(ai, "game_logger", None)
                    if eng_logger:
                        eng_logger._write(record)
            return 0

        # Combat sim for defense micro style
        sim_result = ai.mediator.can_win_fight(
            own_units=defenders,
            enemy_units=threats,
            timing_adjust=True,
            good_positioning=True,
            workers_do_no_damage=True,
        )

        # Track engagement result
        logger = getattr(ai, "game_logger", None)
        if logger:
            logger.track_engagement_result(
                won=sim_result in VICTORY_CLOSE_OR_BETTER
            )

        threat_list = list(threats)
        threat_center = threats.center

        # Engagement tracking for defense
        eng_tracker = getattr(ai, "_engagement_tracker", None)
        if eng_tracker:
            combat_dict: dict = {}
            for u in defenders:
                combat_dict.setdefault(u.type_id, []).append(u)
            home = getattr(ai, "start_location", None)
            home_pos = [round(home.x, 1), round(home.y, 1)] if home else None
            try:
                inc_min = float(ai.state.score.collection_rate_minerals)
                inc_ves = float(ai.state.score.collection_rate_vespene)
            except Exception:
                inc_min, inc_ves = 0.0, 0.0
            record = eng_tracker.on_combat_frame(
                has_enemies=True,
                own_army_dict=combat_dict,
                enemy_units=threat_list,
                sim_result_name=sim_result.name,
                micro_style="defend",
                position=[round(squad.target.x, 1), round(squad.target.y, 1)],
                game_time=ai.time,
                step=ai.state.game_loop,
                combat_objective="DEFEND",
                profile_name=profile.name,
                approach="FRONTAL",
                was_committed=True,
                confidence=0.5,
                minerals=ai.minerals,
                vespene=ai.vespene,
                income_minerals=inc_min,
                income_vespene=inc_ves,
                supply_used=ai.supply_used,
                workers=ai.supply_workers,
                home_position=home_pos,
            )
            if record is not None:
                eng_logger = getattr(ai, "game_logger", None)
                if eng_logger:
                    eng_logger._write(record)

        air_grid = ai.mediator.get_air_grid

        for unit in defenders:
            maneuver = CombatManeuver()

            # Select correct grid for flying vs ground units
            unit_grid = air_grid if unit.type_id in FLYER_TYPES else grid

            # AOE first
            if unit.type_id in AOE_TYPES:
                maneuver.add(AutoUseAOEAbility(unit=unit, targets=threat_list))

            # Profile-driven focus fire target
            best = pick_best_target(unit, threat_list, profile, max_range=15.0)

            # Integrated attack + movement based on unit type and sim result
            if unit.type_id in RANGED_TYPES:
                kite_target = best or threats.closest_to(unit)
                if kite_target is not None:
                    if sim_result in VICTORY_DECISIVE_OR_BETTER:
                        maneuver.add(StutterUnitForward(
                            unit=unit, target=kite_target,
                        ))
                    else:
                        maneuver.add(StutterUnitBack(
                            unit=unit, target=kite_target, grid=unit_grid,
                        ))
                maneuver.add(ShootTargetInRange(unit=unit, targets=threat_list))
            elif unit.type_id in MELEE_TYPES:
                if best is not None:
                    maneuver.add(AttackTarget(unit=unit, target=best))
                maneuver.add(AMove(
                    unit=unit, target=threat_center, success_at_distance=0.0,
                ))
            else:
                # Unclassified unit
                if best is not None:
                    maneuver.add(AttackTarget(unit=unit, target=best))
                maneuver.add(ShootTargetInRange(unit=unit, targets=threat_list))

            # Movement fallback
            if sim_result in LOSS_CLOSE_OR_WORSE:
                maneuver.add(AMove(
                    unit=unit, target=squad.target, success_at_distance=3.0,
                ))
            else:
                maneuver.add(AMove(
                    unit=unit, target=threat_center, success_at_distance=0.0,
                ))

            ai.register_behavior(maneuver)

        return len(defenders)

    # ------------------------------------------------------------------
    # Harass — fast units raid mineral lines / expansions
    # ------------------------------------------------------------------

    def _execute_harass(
        self,
        squad: SquadAssignment,
        plan: CombatPlan,
        profile: EngagementProfile,
    ) -> int:
        """Execute harass squad — fast units path to target, retreat if hurt."""
        ai = self.ai
        grid = ai.mediator.get_ground_grid

        # Get fast units for harass
        army: Units = ai.mediator.get_units_from_role(role=UnitRole.ATTACKING)
        if not army or len(army) == 0:
            return 0

        # Select fast units for harass (up to max supply)
        harass_units: list[Unit] = []
        harass_supply = 0.0
        max_supply = plan.max_harass_supply

        # Prefer the profile's preferred harass unit
        preferred = profile.preferred_harass_unit

        for unit in army:
            if unit.type_id not in FAST_TYPES:
                continue
            if preferred and unit.type_id != preferred:
                continue
            if harass_supply >= max_supply:
                break
            harass_units.append(unit)
            harass_supply += unit._type_data._proto.food_required

        # If not enough preferred, take any fast units
        if len(harass_units) < 4 and preferred:
            for unit in army:
                if unit.type_id not in FAST_TYPES:
                    continue
                if unit in harass_units:
                    continue
                if harass_supply >= max_supply:
                    break
                harass_units.append(unit)
                harass_supply += unit._type_data._proto.food_required

        if len(harass_units) < 4:
            return 0  # not enough for a meaningful harass

        target = squad.target

        for unit in harass_units:
            maneuver = CombatManeuver()

            # Retreat if HP below 40%
            hp_frac = unit.health / max(unit.health_max, 1)
            if hp_frac < 0.4:
                retreat = plan.retreat_point or ai.start_location
                maneuver.add(KeepUnitSafe(unit=unit, grid=grid))
                maneuver.add(PathUnitToTarget(
                    unit=unit, grid=grid, target=retreat,
                ))
            else:
                # Path to target with danger sense
                maneuver.add(PathUnitToTarget(
                    unit=unit, grid=grid, target=target,
                    sense_danger=True, success_at_distance=3.0,
                ))
                maneuver.add(AMove(
                    unit=unit, target=target, success_at_distance=0.0,
                ))

            ai.register_behavior(maneuver)

        return len(harass_units)

    # ------------------------------------------------------------------
    # Main army — the core micro loop
    # ------------------------------------------------------------------

    def _execute_main_army(
        self,
        squad: SquadAssignment,
        plan: CombatPlan,
        profile: EngagementProfile,
        strategy: Strategy | None = None,
    ) -> int:
        """Execute main army micro — the heart of combat.

        1. Gather nearby enemies
        2. Include static defense if turtling
        3. Include structures when near enemy base
        4. If no enemies: squad-based pathing to target
        5. If enemies: combat sim → engagement style → per-unit micro
        """
        ai = self.ai
        grid = ai.mediator.get_ground_grid
        target = squad.target

        army: Units = ai.mediator.get_units_from_role(role=UnitRole.ATTACKING)
        if not army or len(army) == 0:
            return 0

        army_center = army.center

        # Build combat-only dict for engagement tracker (excludes drones/overlords/eggs)
        combat_dict: dict = {}
        for u in army:
            combat_dict.setdefault(u.type_id, []).append(u)

        # --- Gather enemies ---
        nearby_enemy: Units = ai.enemy_units.filter(
            lambda u: u.distance_to(army_center) < 25 and not u.is_structure
        ) if ai.enemy_units else Units([], ai)

        # Include static defense when enemy is turtling
        if (strategy and hasattr(strategy, "threat")
                and strategy.threat.enemy_is_turtling
                and ai.enemy_structures):
            static_defense = ai.enemy_structures.filter(
                lambda s: (
                    s.distance_to(army_center) < 15
                    and s.type_id in STATIC_DEFENSE_TYPES
                )
            )
            if static_defense and len(static_defense) > 0:
                nearby_enemy = (
                    nearby_enemy | static_defense
                    if nearby_enemy and len(nearby_enemy) > 0
                    else static_defense
                )

        # Near enemy base? Include structures as targets
        enemy_start = ai.enemy_start_locations[0] if ai.enemy_start_locations else None
        near_enemy_base = (
            enemy_start is not None
            and army_center.distance_to(enemy_start) < 40
        )
        nearby_structures: Units = Units([], ai)
        if near_enemy_base and ai.enemy_structures:
            nearby_structures = ai.enemy_structures.filter(
                lambda u: u.distance_to(army_center) < 20
            )

        # Combined target list for focus fire
        all_nearby = nearby_enemy
        if nearby_structures and len(nearby_structures) > 0:
            all_nearby = nearby_enemy | nearby_structures if nearby_enemy and len(nearby_enemy) > 0 else nearby_structures
        enemy_list = list(all_nearby) if all_nearby else []

        has_combat_enemies = nearby_enemy is not None and len(nearby_enemy) > 0
        structures_only = not has_combat_enemies and len(nearby_structures) > 0

        # --- No enemies nearby: path to target ---
        if not enemy_list:
            # Notify engagement tracker of no-enemy frame
            eng_tracker = getattr(ai, "_engagement_tracker", None)
            if eng_tracker:
                record = eng_tracker.on_combat_frame(
                    has_enemies=False,
                    own_army_dict=combat_dict,
                    enemy_units=[],
                    sim_result_name="NONE",
                    micro_style="patrol",
                    position=[round(army_center.x, 1), round(army_center.y, 1)],
                    game_time=ai.time,
                    step=ai.state.game_loop,
                )
                if record is not None:
                    eng_logger = getattr(ai, "game_logger", None)
                    if eng_logger:
                        eng_logger._write(record)

            self._path_army_to_target(army, target, grid)
            return len(army)

        # --- Structures only: push ---
        if structures_only:
            self._push_structures(army, target, enemy_list, profile, grid)
            return len(army)

        # --- Enemies present: run combat sim ---
        sim_result = ai.mediator.can_win_fight(
            own_units=army,
            enemy_units=nearby_enemy,
            timing_adjust=True,
            good_positioning=True,
            workers_do_no_damage=True,
        )

        # Track engagement result
        logger = getattr(ai, "game_logger", None)
        if logger:
            logger.track_engagement_result(
                won=sim_result in VICTORY_CLOSE_OR_BETTER
            )

        # Determine engagement style from sim result
        if sim_result in VICTORY_DECISIVE_OR_BETTER:
            style = "chase"
        elif sim_result in VICTORY_CLOSE_OR_BETTER:
            style = "attack"
        elif plan.force_engage or plan.attack_committed:
            style = "attack"  # committed — fight even if losing
        elif sim_result in LOSS_CLOSE_OR_WORSE:
            style = "retreat"
        else:
            style = "attack"  # marginal — keep current posture

        # Engagement tracking for ML data
        eng_tracker = getattr(ai, "_engagement_tracker", None)
        if eng_tracker:
            home = getattr(ai, "start_location", None)
            home_pos = (
                [round(home.x, 1), round(home.y, 1)] if home else None
            )
            try:
                inc_min = float(ai.state.score.collection_rate_minerals)
                inc_ves = float(ai.state.score.collection_rate_vespene)
            except Exception:
                inc_min, inc_ves = 0.0, 0.0

            record = eng_tracker.on_combat_frame(
                has_enemies=True,
                own_army_dict=combat_dict,
                enemy_units=list(nearby_enemy),
                sim_result_name=sim_result.name,
                micro_style=style,
                position=[round(army_center.x, 1), round(army_center.y, 1)],
                game_time=ai.time,
                step=ai.state.game_loop,
                combat_objective=plan.objective.name,
                profile_name=profile.name,
                approach=plan.approach.name if hasattr(plan, "approach") else "FRONTAL",
                was_committed=plan.attack_committed,
                confidence=getattr(plan, "confidence", 0.5),
                minerals=ai.minerals,
                vespene=ai.vespene,
                income_minerals=inc_min,
                income_vespene=inc_ves,
                supply_used=ai.supply_used,
                workers=ai.supply_workers,
                home_position=home_pos,
            )
            if record is not None:
                eng_logger = getattr(ai, "game_logger", None)
                if eng_logger:
                    eng_logger._write(record)

        # Execute per-unit micro
        self._micro_units(
            army, style, target, nearby_enemy, enemy_list, profile, grid,
        )

        # Log summary periodically
        logger = getattr(ai, "game_logger", None)
        if logger and int(ai.time * 2) % 10 < 1:
            logger.log_decision("executor", "main_army_micro", {
                "style": style,
                "sim_result": sim_result.name,
                "army_size": len(army),
                "enemy_nearby": len(nearby_enemy),
                "structures_nearby": len(nearby_structures),
                "target": [round(target.x, 1), round(target.y, 1)],
                "profile": profile.name,
            })

        return len(army)

    # ------------------------------------------------------------------
    # Movement helpers
    # ------------------------------------------------------------------

    def _path_army_to_target(
        self, army: Units, target: Point2, grid,
    ) -> None:
        """Move army to target when no enemies nearby.

        Uses individual PathUnitToTarget for all units.
        """
        ai = self.ai
        for unit in army:
            maneuver = CombatManeuver()
            maneuver.add(PathUnitToTarget(
                unit=unit, grid=grid, target=target,
                sense_danger=True, success_at_distance=3.0,
            ))
            maneuver.add(AMove(
                unit=unit, target=target, success_at_distance=0.0,
            ))
            ai.register_behavior(maneuver)

    def _push_structures(
        self,
        army: Units,
        target: Point2,
        enemy_list: list[Unit],
        profile: EngagementProfile,
        grid,
    ) -> None:
        """Push through structures — no kiting needed."""
        ai = self.ai
        for unit in army:
            maneuver = CombatManeuver()

            # AOE on structures
            if unit.type_id in AOE_TYPES:
                maneuver.add(AutoUseAOEAbility(unit=unit, targets=enemy_list))

            # Focus fire structures from profile
            best = pick_best_target(unit, enemy_list, profile, max_range=10.0)
            if best is not None:
                maneuver.add(AttackTarget(unit=unit, target=best))
            maneuver.add(ShootTargetInRange(unit=unit, targets=enemy_list))

            maneuver.add(AMove(
                unit=unit, target=target, success_at_distance=0.0,
            ))
            maneuver.add(PathUnitToTarget(
                unit=unit, grid=grid, target=target, sense_danger=False,
            ))
            ai.register_behavior(maneuver)

    # ------------------------------------------------------------------
    # Per-unit micro with profile-driven decisions
    # ------------------------------------------------------------------

    def _micro_units(
        self,
        army: Units,
        style: str,
        target: Point2,
        nearby_enemy: Units,
        enemy_list: list[Unit],
        profile: EngagementProfile,
        grid,
    ) -> None:
        """Execute per-unit micro based on engagement style and profile.

        Behavior chain per unit:
          1. AutoUseAOEAbility — cast AOE if available (returns True only on cast)
          2. Style-specific attack+movement (integrated Stutter for ranged,
             AttackTarget for melee) — best target from pick_best_target
             is passed into style functions for focus fire
          3. ShootTargetInRange — fallback opportunistic fire
          4. AMove/PathUnitToTarget — movement fallback
        """
        ai = self.ai
        air_grid = ai.mediator.get_air_grid

        for unit in army:
            maneuver = CombatManeuver()

            # Pick best focus fire target once per unit
            best: Unit | None = None
            if enemy_list:
                best = pick_best_target(unit, enemy_list, profile, max_range=15.0)

            # --- Priority 1: AOE abilities ---
            if unit.type_id in AOE_TYPES and enemy_list:
                maneuver.add(AutoUseAOEAbility(unit=unit, targets=enemy_list))

            # --- Priority 2+3: Integrated attack + movement per style ---
            # Select correct grid for flying vs ground units
            unit_grid = air_grid if unit.type_id in FLYER_TYPES else grid

            if style == "chase":
                self._micro_chase(
                    unit, maneuver, target, nearby_enemy, enemy_list,
                    profile, unit_grid, best,
                )
            elif style == "attack":
                self._micro_attack(
                    unit, maneuver, target, nearby_enemy, enemy_list,
                    profile, unit_grid, best,
                )
            elif style == "retreat":
                self._micro_retreat(
                    unit, maneuver, target, enemy_list, profile, unit_grid, best,
                )

            ai.register_behavior(maneuver)

    def _micro_chase(
        self,
        unit: Unit,
        maneuver: CombatManeuver,
        target: Point2,
        nearby_enemy: Units,
        enemy_list: list[Unit],
        profile: EngagementProfile,
        grid,
        best: Unit | None,
    ) -> None:
        """Chase micro — winning decisively, pursue aggressively.

        Ranged: StutterForward(best target) — attacks when ready, chases between.
        Melee: AttackTarget(best) + AMove to target.
        """
        has_nearby = nearby_enemy is not None and len(nearby_enemy) > 0

        if unit.type_id in RANGED_TYPES and has_nearby:
            chase_target = best or nearby_enemy.closest_to(unit)
            if chase_target is not None:
                maneuver.add(StutterUnitForward(unit=unit, target=chase_target))
            maneuver.add(ShootTargetInRange(unit=unit, targets=enemy_list))
        elif unit.type_id in MELEE_TYPES:
            if best is not None:
                maneuver.add(AttackTarget(unit=unit, target=best))
            maneuver.add(AMove(
                unit=unit, target=target, success_at_distance=0.0,
            ))
        else:
            # Unclassified unit — opportunistic fire + move
            if best is not None:
                maneuver.add(AttackTarget(unit=unit, target=best))
            if enemy_list:
                maneuver.add(ShootTargetInRange(unit=unit, targets=enemy_list))

        # Final fallback: path to target through danger
        maneuver.add(PathUnitToTarget(
            unit=unit, grid=grid, target=target, sense_danger=False,
        ))

    def _micro_attack(
        self,
        unit: Unit,
        maneuver: CombatManeuver,
        target: Point2,
        nearby_enemy: Units,
        enemy_list: list[Unit],
        profile: EngagementProfile,
        grid,
        best: Unit | None,
    ) -> None:
        """Attack micro — winning but kite appropriately per profile.

        Ranged: Profile kite_style determines StutterBack/Forward/Hold.
        Melee: AttackTarget(best) + AMove.
        """
        has_nearby = nearby_enemy is not None and len(nearby_enemy) > 0

        if unit.type_id in RANGED_TYPES and has_nearby:
            kite_target = best or nearby_enemy.closest_to(unit)
            if kite_target is not None:
                if profile.kite_style == KiteStyle.STUTTER_FORWARD:
                    maneuver.add(StutterUnitForward(unit=unit, target=kite_target))
                elif profile.kite_style == KiteStyle.HOLD_POSITION:
                    maneuver.add(ShootTargetInRange(
                        unit=unit, targets=enemy_list,
                    ))
                elif profile.kite_style == KiteStyle.AGGRESSIVE:
                    maneuver.add(StutterUnitForward(unit=unit, target=kite_target))
                else:
                    # Default: STUTTER_BACK — kite away between shots
                    maneuver.add(StutterUnitBack(
                        unit=unit, target=kite_target, grid=grid,
                    ))
            # Fallback: shoot anything in range
            maneuver.add(ShootTargetInRange(unit=unit, targets=enemy_list))
        elif unit.type_id in MELEE_TYPES:
            if best is not None:
                maneuver.add(AttackTarget(unit=unit, target=best))
            maneuver.add(AMove(
                unit=unit, target=target, success_at_distance=0.0,
            ))
        else:
            # Unclassified unit
            if best is not None:
                maneuver.add(AttackTarget(unit=unit, target=best))
            if enemy_list:
                maneuver.add(ShootTargetInRange(unit=unit, targets=enemy_list))

        # Pathing fallback with reduced danger avoidance
        maneuver.add(PathUnitToTarget(
            unit=unit, grid=grid, target=target, danger_distance=10.0,
        ))

    def _micro_retreat(
        self,
        unit: Unit,
        maneuver: CombatManeuver,
        target: Point2,
        enemy_list: list[Unit],
        profile: EngagementProfile,
        grid,
        best: Unit | None,
    ) -> None:
        """Retreat micro — disengage safely, shoot while retreating.

        Ranged: StutterUnitBack — shoots when weapon ready, kites away.
        All: ShootTargetInRange (opportunistic) + KeepUnitSafe + path to rally.
        """
        retreat_target = self.ai.start_location
        try:
            rally = self.ai.mediator.get_own_nat
            if rally is not None:
                retreat_target = rally
        except Exception:
            pass

        if unit.type_id in RANGED_TYPES and enemy_list:
            # Find best kite target (prefer profile pick, fallback to closest)
            kite_target = best
            if kite_target is None:
                min_dist = float("inf")
                for e in enemy_list:
                    d = unit.distance_to(e)
                    if d < min_dist:
                        min_dist = d
                        kite_target = e
            if kite_target is not None:
                maneuver.add(StutterUnitBack(
                    unit=unit, target=kite_target, grid=grid,
                ))

        if enemy_list:
            maneuver.add(ShootTargetInRange(unit=unit, targets=enemy_list))

        maneuver.add(KeepUnitSafe(unit=unit, grid=grid))
        maneuver.add(PathUnitToTarget(
            unit=unit, grid=grid, target=retreat_target,
        ))
