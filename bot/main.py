from __future__ import annotations

from typing import Optional

from sc2.ids.unit_typeid import UnitTypeId as UnitID
from sc2.unit import Unit

from ares import AresBot
from ares.behaviors.macro import Mining, SpawnController
from ares.consts import UnitRole

from bot.combat import CombatSystem, RALLYING_ROLE
from bot.combat.constants import CombatObjective
from bot.logging import GameLogger
from bot.logging.engagement_tracker import EngagementTracker
from bot.macro import MacroManager
from bot.queens import QueenManager
from bot.scouting import ScoutingManager
from bot.strategy.analyzer import StrategicAnalyzer

# Combat unit types that should receive the ATTACKING role
ARMY_TYPES = {
    UnitID.ZERGLING,
    UnitID.BANELING,
    UnitID.ROACH,
    UnitID.RAVAGER,
    UnitID.HYDRALISK,
    UnitID.LURKERMP,
    UnitID.MUTALISK,
    UnitID.CORRUPTOR,
    UnitID.BROODLORD,
    UnitID.INFESTOR,
    UnitID.SWARMHOSTMP,
    UnitID.VIPER,
    UnitID.ULTRALISK,
}


class MyBot(AresBot):
    macro_manager: MacroManager
    combat_system: CombatSystem
    queen_manager: QueenManager
    game_logger: GameLogger
    strategic_analyzer: StrategicAnalyzer
    scouting_manager: ScoutingManager

    def __init__(self, game_step_override: Optional[int] = None):
        super().__init__(game_step_override)

    async def on_start(self) -> None:
        await super(MyBot, self).on_start()
        self.macro_manager = MacroManager(self)
        self.combat_system = CombatSystem(self)
        self.queen_manager = QueenManager(self)
        self.game_logger = GameLogger(self, self.config)
        self.strategic_analyzer = StrategicAnalyzer(self)
        self.scouting_manager = ScoutingManager(self)
        self._engagement_tracker = EngagementTracker()

    async def on_step(self, iteration: int) -> None:
        await super(MyBot, self).on_step(iteration)

        # Mining is always needed
        self.register_behavior(Mining())

        # Tag new combat units with roles
        self._assign_army_roles()

        # Safety valve: force-complete build order if stuck
        self._check_build_order_timeout()

        if not self.build_order_runner.build_completed:
            # Build order phase: framework handles production.
            self.queen_manager.handle_queens()
            self.combat_system.handle_early_defense()
            self._emergency_production()
        else:
            # Full bot logic — strategy drives everything
            strategy = self.strategic_analyzer.evaluate()
            self.macro_manager.handle_macro(strategy)
            self.queen_manager.handle_queens()
            self.combat_system.handle_combat(strategy)
            self.scouting_manager.handle_scouting()

        # Instrumentation: log game state (always last — captures decisions made above)
        self.game_logger.on_step(iteration)

    def _check_build_order_timeout(self) -> None:
        """Force-complete build order if stuck too long."""
        if self.build_order_runner.build_completed:
            return

        bo_step = self.build_order_runner.build_step
        bo_total = len(self.build_order_runner.build_order)
        logger = getattr(self, "game_logger", None)

        if self.time > 300:
            self.build_order_runner.set_build_completed()
            if logger:
                logger.log_decision("main", "force_complete_build_order", {
                    "reason": "timeout_5min",
                    "stuck_step": bo_step,
                    "total_steps": bo_total,
                })
        elif self.minerals > 1000 and bo_step >= bo_total - 2:
            self.build_order_runner.set_build_completed()
            if logger:
                logger.log_decision("main", "force_complete_build_order", {
                    "reason": "minerals_floating_near_end",
                    "minerals": self.minerals,
                    "stuck_step": bo_step,
                })

    def _emergency_production(self) -> None:
        """Produce army units during build order if threatened or floating."""
        ground_threats = self.mediator.get_main_ground_threats_near_townhall
        under_attack = ground_threats and len(ground_threats) > 0
        floating = self.minerals > 500

        if not under_attack and not floating:
            return

        structs = self.mediator.get_own_structures_dict
        has_pool = len(structs.get(UnitID.SPAWNINGPOOL, [])) > 0
        if not has_pool:
            return

        has_roach_warren = len(structs.get(UnitID.ROACHWARREN, [])) > 0
        if has_roach_warren:
            comp = {
                UnitID.ROACH: {"proportion": 0.6, "priority": 0},
                UnitID.ZERGLING: {"proportion": 0.4, "priority": 1},
            }
        else:
            comp = {UnitID.ZERGLING: {"proportion": 1.0, "priority": 0}}

        self.register_behavior(SpawnController(comp, freeflow_mode=True))

        logger = getattr(self, "game_logger", None)
        if logger:
            logger.log_decision("main", "emergency_production", {
                "under_attack": under_attack,
                "floating": floating,
                "minerals": self.minerals,
            })

    def _assign_army_roles(self) -> None:
        """Assign roles to new combat units.

        During REGROUP mode, new units get RALLYING_ROLE so they consolidate
        at the rally point instead of trickling forward one-by-one.
        """
        role_dict = self.mediator.get_unit_role_dict
        # Build a set of all tags that already have any role
        all_assigned_tags: set[int] = set()
        for tags in role_dict.values():
            all_assigned_tags.update(tags)

        # During regroup, rally new units instead of sending them to attack
        is_regrouping = (
            hasattr(self, "combat_system")
            and self.build_order_runner.build_completed
            and self.combat_system._plan.objective == CombatObjective.REGROUP
        )
        new_role = RALLYING_ROLE if is_regrouping else UnitRole.ATTACKING

        for unit_type in ARMY_TYPES:
            for unit in self.mediator.get_own_army_dict.get(unit_type, []):
                if unit.tag not in all_assigned_tags:
                    self.mediator.assign_role(tag=unit.tag, role=new_role)

    async def on_unit_created(self, unit: Unit) -> None:
        await super(MyBot, self).on_unit_created(unit)
        self.game_logger.log_event(
            event="unit_created",
            unit_type=unit.type_id.name,
            unit_tag=unit.tag,
            is_own=True,
        )

    async def on_unit_destroyed(self, unit_tag: int) -> None:
        await super(MyBot, self).on_unit_destroyed(unit_tag)
        # Look up type from own tag map first
        unit_type = self.game_logger._tag_type_map.get(unit_tag)
        is_own = unit_type is not None
        if not is_own:
            # Check enemy tag map (populated by track_enemy_sightings)
            unit_type = self.game_logger._enemy_tags_seen.get(unit_tag)
            if unit_type is None:
                unit_type = "UNKNOWN"
                try:
                    for enemy in self.all_enemy_units:
                        if enemy.tag == unit_tag:
                            unit_type = enemy.type_id.name
                            break
                except Exception:
                    pass
        self.game_logger.log_event(
            event="unit_destroyed",
            unit_type=unit_type,
            unit_tag=unit_tag,
            is_own=is_own,
        )

    async def on_building_construction_started(self, unit: Unit) -> None:
        await super(MyBot, self).on_building_construction_started(unit)
        self.game_logger.log_event(
            event="building_started",
            unit_type=unit.type_id.name,
            unit_tag=unit.tag,
            is_own=True,
        )

    async def on_building_construction_complete(self, unit: Unit) -> None:
        await super(MyBot, self).on_building_construction_complete(unit)
        self.game_logger.log_building_completed(
            unit_type=unit.type_id.name,
            unit_tag=unit.tag,
        )

    async def on_end(self, game_result) -> None:
        """Called when the game ends."""
        # Force-end any active engagement before summary
        if hasattr(self, "_engagement_tracker"):
            # Build combat-only dict (excludes drones/overlords/eggs)
            combat_dict: dict = {}
            for role in (UnitRole.ATTACKING, RALLYING_ROLE):
                for u in (self.mediator.get_units_from_role(role=role) or []):
                    combat_dict.setdefault(u.type_id, []).append(u)
            record = self._engagement_tracker.force_end(
                self.time,
                self.state.game_loop,
                combat_dict,
                list(self.enemy_units or []),
            )
            if record is not None:
                self.game_logger._write(record)

        result_str = str(game_result).split(".")[-1] if game_result else "Undecided"
        self.game_logger.on_game_end(result_str)
        await super(MyBot, self).on_end(game_result)
