"""Scouting Manager — active intelligence gathering via overlord positioning.

Assigns idle overlords to vision positions, periodically sends scouts
to check enemy bases, and retreats overlords from anti-air threats.

The scouting manager doesn't analyze what it sees — it just positions
units for vision. The existing StrategicMemory.update() processes
ai.enemy_units and ai.enemy_structures every frame. More vision →
more data flowing into memory → better classification.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sc2.ids.unit_typeid import UnitTypeId as UnitID
from sc2.position import Point2
from sc2.unit import Unit

from ares.behaviors.combat import CombatManeuver
from ares.behaviors.combat.individual import PathUnitToTarget
from ares.consts import UnitRole

if TYPE_CHECKING:
    from ares import AresBot


# Anti-air unit types that threaten overlords
_ANTI_AIR_TYPES = {
    # Protoss
    UnitID.STALKER, UnitID.PHOENIX, UnitID.VOIDRAY, UnitID.ARCHON,
    UnitID.TEMPEST, UnitID.CARRIER,
    # Terran
    UnitID.MARINE, UnitID.VIKING, UnitID.VIKINGFIGHTER, UnitID.THOR,
    UnitID.MISSILETURRET, UnitID.CYCLONE, UnitID.LIBERATOR,
    # Zerg
    UnitID.HYDRALISK, UnitID.MUTALISK, UnitID.CORRUPTOR, UnitID.QUEEN,
    UnitID.SPORECRAWLER,
}


class ScoutingManager:
    """Active intelligence gathering via overlord positioning.

    Assigns idle overlords to vision positions, periodically sends
    scouts to check enemy bases, and tracks information staleness.
    """

    SCOUT_INTERVAL: float = 60.0
    STALE_THRESHOLD: float = 90.0
    URGENT_THRESHOLD: float = 120.0
    OVERLORD_SAFE_DIST: float = 15.0

    def __init__(self, ai: AresBot) -> None:
        self.ai = ai
        self._last_scout_time: float = 0.0
        self._assigned_overlords: dict[int, Point2] = {}
        self._scout_targets: list[Point2] = []

    def handle_scouting(self) -> None:
        """Main entry point — called from on_step after build order completes."""
        ai = self.ai

        # Build target list on first call
        if not self._scout_targets:
            self._build_target_list()

        # Get overlords available for scouting
        overlords = self._get_available_overlords()
        if not overlords:
            return

        # Retreat overlords from anti-air threats first
        self._retreat_from_threats(overlords)

        # Assign overlords to vision positions
        self._assign_overlords(overlords)

        # Check info staleness — send scouts if needed
        self._check_staleness(overlords)

    def _build_target_list(self) -> None:
        """Compute scout positions from map geometry (once)."""
        ai = self.ai
        targets: list[Point2] = []

        try:
            enemy_nat = ai.mediator.get_enemy_nat
            if enemy_nat:
                targets.append(enemy_nat)
        except Exception:
            pass

        # Enemy start location
        if ai.enemy_start_locations:
            targets.append(ai.enemy_start_locations[0])

        # Map center
        map_center = ai.game_info.map_center
        targets.append(map_center)

        # Overlord safe spots
        try:
            ol_spots = ai.mediator.get_ol_spots
            if ol_spots:
                targets.extend(ol_spots)
        except Exception:
            pass

        self._scout_targets = targets

    def _get_available_overlords(self) -> list[Unit]:
        """Get overlords not assigned to build order scouting."""
        ai = self.ai
        own_army = ai.mediator.get_own_army_dict
        overlords = list(own_army.get(UnitID.OVERLORD, []))

        # Filter out build order scouts
        bo_scout_tags: set[int] = set()
        try:
            bo_scouts = ai.mediator.get_units_from_role(
                role=UnitRole.BUILD_RUNNER_SCOUT
            )
            if bo_scouts:
                bo_scout_tags = {u.tag for u in bo_scouts}
        except Exception:
            pass

        return [ol for ol in overlords if ol.tag not in bo_scout_tags]

    def _retreat_from_threats(self, overlords: list[Unit]) -> None:
        """Retreat overlords away from anti-air threats."""
        ai = self.ai
        if not ai.enemy_units:
            return

        for ol in overlords:
            # Find nearby anti-air threats
            threats = [
                u for u in ai.enemy_units
                if u.type_id in _ANTI_AIR_TYPES
                and u.distance_to(ol) < self.OVERLORD_SAFE_DIST
            ]
            if not threats:
                continue

            # Find closest safe spot to retreat to
            retreat_target = self._find_safe_retreat(ol)
            if retreat_target is None:
                continue

            maneuver = CombatManeuver()
            try:
                grid = ai.mediator.get_air_grid
                maneuver.add(PathUnitToTarget(
                    unit=ol, grid=grid, target=retreat_target,
                    success_at_distance=3.0,
                ))
            except Exception:
                # Fallback: simple move command
                maneuver.add(PathUnitToTarget(
                    unit=ol,
                    grid=ai.mediator.get_ground_grid,
                    target=retreat_target,
                    success_at_distance=3.0,
                ))
            ai.register_behavior(maneuver)

            # Remove from assignment so it gets reassigned later
            self._assigned_overlords.pop(ol.tag, None)

            logger = getattr(ai, "game_logger", None)
            if logger:
                logger.log_decision("scouting", "overlord_retreat", {
                    "overlord_tag": ol.tag,
                    "threat_type": "anti_air",
                    "retreat_to": [round(retreat_target.x, 1),
                                   round(retreat_target.y, 1)],
                })

    def _find_safe_retreat(self, unit: Unit) -> Point2 | None:
        """Find nearest safe overlord spot away from threats."""
        ai = self.ai
        try:
            return ai.mediator.get_closest_overlord_spot(
                from_pos=unit.position
            )
        except Exception:
            # Fallback: retreat toward our start location
            if ai.start_location:
                return ai.start_location
            return None

    def _assign_overlords(self, overlords: list[Unit]) -> None:
        """Assign unassigned overlords to vision positions."""
        ai = self.ai

        # Clean up stale assignments (dead overlords)
        alive_tags = {ol.tag for ol in overlords}
        self._assigned_overlords = {
            tag: pos for tag, pos in self._assigned_overlords.items()
            if tag in alive_tags
        }

        # Get unassigned overlords (not retreating, not already assigned)
        unassigned = [
            ol for ol in overlords
            if ol.tag not in self._assigned_overlords
        ]
        if not unassigned:
            return

        # Prioritized target list: enemy nat spot first
        priority_targets: list[Point2] = []
        try:
            enemy_nat_spot = ai.mediator.get_ol_spot_near_enemy_natural
            if enemy_nat_spot:
                priority_targets.append(enemy_nat_spot)
        except Exception:
            pass

        # Add remaining scout targets that aren't already covered
        assigned_positions = set()
        for pos in self._assigned_overlords.values():
            assigned_positions.add((round(pos.x), round(pos.y)))

        for target in self._scout_targets:
            key = (round(target.x), round(target.y))
            if key not in assigned_positions:
                priority_targets.append(target)

        # Assign overlords to targets in priority order
        for ol, target in zip(unassigned, priority_targets):
            self._send_overlord_to(ol, target, "vision_position")

    def _check_staleness(self, overlords: list[Unit]) -> None:
        """Send scouts to enemy base if info is stale."""
        ai = self.ai
        now = ai.time

        # Don't spam scouts
        if now - self._last_scout_time < self.SCOUT_INTERVAL:
            return

        # Check staleness via strategic analyzer memory
        staleness = self._get_info_staleness(now)
        if staleness < self.STALE_THRESHOLD:
            return

        # Find an overlord to send scouting
        # Prefer one that's already near our side of the map
        scout = self._pick_scout_overlord(overlords)
        if scout is None:
            return

        # Scout target: enemy start location
        target = (
            ai.enemy_start_locations[0]
            if ai.enemy_start_locations
            else ai.game_info.map_center
        )

        reason = "urgent_stale_info" if staleness > self.URGENT_THRESHOLD else "stale_info"
        self._send_overlord_to(scout, target, reason)
        self._last_scout_time = now

    def _get_info_staleness(self, game_time: float) -> float:
        """Get info staleness from strategic memory."""
        try:
            analyzer = self.ai.strategic_analyzer
            return analyzer.memory.get_info_staleness(game_time)
        except AttributeError:
            return game_time  # No analyzer = never seen enemy

    def _pick_scout_overlord(self, overlords: list[Unit]) -> Unit | None:
        """Pick best overlord to send scouting.

        Prefer overlords that are closer to our side (safer to sacrifice).
        Never pick the very first overlord (keep it for supply).
        """
        if len(overlords) < 2:
            return None

        # Sort by distance to our start (closest first = safer pick)
        start = self.ai.start_location
        candidates = sorted(
            overlords[1:],  # skip first overlord
            key=lambda ol: ol.distance_to(start) if start else 0,
        )
        return candidates[0] if candidates else None

    def _send_overlord_to(
        self, ol: Unit, target: Point2, reason: str
    ) -> None:
        """Send an overlord to a target position with safe pathing."""
        ai = self.ai
        self._assigned_overlords[ol.tag] = target
        ai.mediator.assign_role(tag=ol.tag, role=UnitRole.SCOUTING)

        maneuver = CombatManeuver()
        try:
            grid = ai.mediator.get_air_grid
            maneuver.add(PathUnitToTarget(
                unit=ol, grid=grid, target=target,
                success_at_distance=5.0,
            ))
        except Exception:
            maneuver.add(PathUnitToTarget(
                unit=ol,
                grid=ai.mediator.get_ground_grid,
                target=target,
                success_at_distance=5.0,
            ))
        ai.register_behavior(maneuver)

        logger = getattr(ai, "game_logger", None)
        if logger:
            logger.log_decision("scouting", "assign_overlord", {
                "overlord_tag": ol.tag,
                "target": [round(target.x, 1), round(target.y, 1)],
                "reason": reason,
            })
