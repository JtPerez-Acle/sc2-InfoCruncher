from __future__ import annotations

from typing import TYPE_CHECKING

from sc2.ids.ability_id import AbilityId
from sc2.ids.buff_id import BuffId
from sc2.ids.unit_typeid import UnitTypeId as UnitID
from sc2.unit import Unit

from ares.behaviors.combat import CombatManeuver
from ares.behaviors.combat.individual import QueenSpreadCreep, UseTransfuse
from ares.consts import UnitRole

if TYPE_CHECKING:
    from ares import AresBot


class QueenManager:
    """Handles queen inject, creep spread, and transfuse."""

    def __init__(self, ai: AresBot) -> None:
        self.ai = ai
        # hatch_tag -> queen_tag
        self.inject_queen_tags: dict[int, int] = {}

    def handle_queens(self) -> None:
        queens: list[Unit] = list(
            self.ai.mediator.get_own_army_dict.get(UnitID.QUEEN, [])
        )
        if not queens:
            return

        self._assign_queen_roles(queens)
        self._do_injects(queens)
        self._do_creep_spread()

    def _assign_queen_roles(self, queens: list[Unit]) -> None:
        ai = self.ai
        hatcheries = [th for th in ai.townhalls if th.is_ready]
        queen_tags_alive = {q.tag for q in queens}
        hatch_tags_alive = {th.tag for th in ai.townhalls}

        # Clean up dead assignments
        self.inject_queen_tags = {
            h_tag: q_tag
            for h_tag, q_tag in self.inject_queen_tags.items()
            if h_tag in hatch_tags_alive and q_tag in queen_tags_alive
        }

        # Assign one queen per hatchery for inject
        assigned_queen_tags = set(self.inject_queen_tags.values())
        unassigned_queens = [q for q in queens if q.tag not in assigned_queen_tags]

        for th in hatcheries:
            if th.tag not in self.inject_queen_tags and unassigned_queens:
                closest_queen = min(
                    unassigned_queens, key=lambda q: q.distance_to(th.position)
                )
                self.inject_queen_tags[th.tag] = closest_queen.tag
                ai.mediator.assign_role(
                    tag=closest_queen.tag, role=UnitRole.QUEEN_INJECT
                )
                unassigned_queens.remove(closest_queen)

        # Remaining queens spread creep
        for queen in unassigned_queens:
            ai.mediator.assign_role(tag=queen.tag, role=UnitRole.QUEEN_CREEP)

    def _do_injects(self, queens: list[Unit]) -> None:
        ai = self.ai
        queen_by_tag = {q.tag: q for q in queens}

        for hatch_tag, queen_tag in self.inject_queen_tags.items():
            queen = queen_by_tag.get(queen_tag)
            if not queen:
                continue

            hatch = None
            for th in ai.townhalls:
                if th.tag == hatch_tag:
                    hatch = th
                    break
            if not hatch or not hatch.is_ready:
                continue

            # Skip if queen is already injecting
            if queen.is_using_ability(AbilityId.EFFECT_INJECTLARVA):
                continue

            if queen.energy >= 25 and not hatch.has_buff(BuffId.QUEENSPAWNLARVATIMER):
                queen(AbilityId.EFFECT_INJECTLARVA, hatch)
                logger = getattr(ai, "game_logger", None)
                if logger:
                    logger.log_decision("queens", "inject", {
                        "queen_tag": queen.tag,
                        "hatch_tag": hatch_tag,
                        "queen_energy": queen.energy,
                    })
            elif queen.distance_to(hatch.position) > 8:
                queen.move(hatch.position)

    def _do_creep_spread(self) -> None:
        ai = self.ai
        creep_queens = ai.mediator.get_units_from_role(
            role=UnitRole.QUEEN_CREEP, unit_type=UnitID.QUEEN
        )

        logger = getattr(ai, "game_logger", None)

        for queen in creep_queens:
            # Try transfuse on low-HP nearby units first
            nearby_damaged = ai.units.filter(
                lambda u: u.distance_to(queen.position) < 10
                and u.health_percentage < 0.4
                and u.tag != queen.tag
            )
            if nearby_damaged and queen.energy >= 50:
                maneuver = CombatManeuver()
                maneuver.add(UseTransfuse(unit=queen, targets=nearby_damaged))
                maneuver.add(QueenSpreadCreep(unit=queen))
                ai.register_behavior(maneuver)
                if logger:
                    target = nearby_damaged.closest_to(queen.position)
                    logger.log_decision("queens", "transfuse", {
                        "queen_tag": queen.tag,
                        "target_tag": target.tag,
                        "target_type": target.type_id.name,
                        "target_hp": round(target.health_percentage, 2),
                    })
            else:
                ai.register_behavior(QueenSpreadCreep(unit=queen))
