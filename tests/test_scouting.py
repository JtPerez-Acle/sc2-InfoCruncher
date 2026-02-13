"""Tests for ScoutingManager — overlord positioning and stale-info scouting.

Covers:
  - Target list construction
  - Overlord assignment priority
  - Stale info scout triggers
  - Overlord retreat from threats
  - Graceful handling of edge cases
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from sc2.ids.unit_typeid import UnitTypeId as UnitID
from sc2.position import Point2

from bot.scouting import ScoutingManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class MockScoutUnit:
    """Mock unit for scouting tests."""

    def __init__(self, tag: int, x: float = 50.0, y: float = 50.0):
        self.tag = tag
        self.type_id = UnitID.OVERLORD
        self.position = Point2((x, y))
        self.is_flying = True

    def distance_to(self, other) -> float:
        if isinstance(other, Point2):
            ox, oy = other.x, other.y
        elif hasattr(other, "position"):
            ox, oy = other.position.x, other.position.y
        else:
            ox, oy = other.x, other.y
        dx = self.position.x - ox
        dy = self.position.y - oy
        return (dx * dx + dy * dy) ** 0.5


class MockScoutMediator:
    """Mock mediator with scouting-relevant APIs."""

    def __init__(self):
        self._own_army_dict: dict = {}
        self._roles: dict = {}
        self._assigned: list = []

    @property
    def get_own_army_dict(self) -> dict:
        return self._own_army_dict

    @property
    def get_enemy_nat(self) -> Point2:
        return Point2((60.0, 60.0))

    @property
    def get_ol_spots(self) -> list:
        return [Point2((40.0, 40.0)), Point2((70.0, 30.0))]

    @property
    def get_ol_spot_near_enemy_natural(self) -> Point2:
        return Point2((55.0, 58.0))

    @property
    def get_air_grid(self):
        raise AttributeError("no grid in tests")

    @property
    def get_ground_grid(self):
        return MagicMock()

    def get_closest_overlord_spot(self, from_pos=None):
        return Point2((45.0, 45.0))

    def get_units_from_role(self, role=None, unit_type=None):
        return []

    def assign_role(self, tag=None, role=None):
        self._assigned.append((tag, role))

    @property
    def get_main_ground_threats_near_townhall(self):
        return []

    @property
    def get_main_air_threats_near_townhall(self):
        return []


class MockScoutBot:
    """Mock bot for scouting tests."""

    def __init__(self, overlords=None):
        self.time = 300.0
        self.mediator = MockScoutMediator()
        if overlords:
            self.mediator._own_army_dict[UnitID.OVERLORD] = overlords
        self.enemy_units = []
        self.enemy_structures = []
        self.enemy_start_locations = [Point2((80.0, 80.0))]
        self.start_location = Point2((20.0, 20.0))
        self.game_info = SimpleNamespace(map_center=Point2((50.0, 50.0)))
        self.build_order_runner = SimpleNamespace(build_completed=True)
        self._behaviors: list = []

    def register_behavior(self, behavior):
        self._behaviors.append(behavior)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestScoutingManager:

    def test_builds_target_list(self):
        """First call populates scout target positions."""
        bot = MockScoutBot()
        mgr = ScoutingManager(bot)
        assert mgr._scout_targets == []
        mgr.handle_scouting()
        assert len(mgr._scout_targets) > 0
        # Should include enemy nat, enemy start, map center, ol_spots
        assert any(
            abs(t.x - 60.0) < 1 and abs(t.y - 60.0) < 1
            for t in mgr._scout_targets
        ), "Enemy nat should be in targets"

    def test_assigns_first_overlord_to_enemy_nat_spot(self):
        """First overlord gets the enemy natural vision spot."""
        ol1 = MockScoutUnit(100, 30.0, 30.0)
        bot = MockScoutBot(overlords=[ol1])
        mgr = ScoutingManager(bot)
        mgr.handle_scouting()
        # The overlord should be assigned
        assert ol1.tag in mgr._assigned_overlords
        # Target should be the enemy nat OL spot (55, 58)
        target = mgr._assigned_overlords[ol1.tag]
        assert abs(target.x - 55.0) < 1

    def test_assigns_second_overlord_to_different_position(self):
        """Second overlord gets a different position."""
        ol1 = MockScoutUnit(100, 30.0, 30.0)
        ol2 = MockScoutUnit(101, 32.0, 30.0)
        bot = MockScoutBot(overlords=[ol1, ol2])
        mgr = ScoutingManager(bot)
        mgr.handle_scouting()
        assert ol1.tag in mgr._assigned_overlords
        assert ol2.tag in mgr._assigned_overlords
        # Different targets
        t1 = mgr._assigned_overlords[ol1.tag]
        t2 = mgr._assigned_overlords[ol2.tag]
        assert t1 != t2

    def test_no_overlords_no_crash(self):
        """No overlords available -> no crash."""
        bot = MockScoutBot(overlords=[])
        mgr = ScoutingManager(bot)
        mgr.handle_scouting()  # should not raise
        assert mgr._assigned_overlords == {}

    def test_doesnt_reassign_enroute_overlords(self):
        """Already assigned overlords aren't reassigned."""
        ol1 = MockScoutUnit(100, 30.0, 30.0)
        bot = MockScoutBot(overlords=[ol1])
        mgr = ScoutingManager(bot)
        mgr.handle_scouting()
        first_target = mgr._assigned_overlords[ol1.tag]

        # Call again — overlord should keep same target
        bot._behaviors.clear()
        mgr.handle_scouting()
        assert mgr._assigned_overlords[ol1.tag] == first_target

    def test_stale_info_triggers_scout(self):
        """Info >90s stale triggers a scouting overlord."""
        ol1 = MockScoutUnit(100, 25.0, 25.0)
        ol2 = MockScoutUnit(101, 30.0, 30.0)
        bot = MockScoutBot(overlords=[ol1, ol2])
        bot.time = 400.0

        # Fake a strategic_analyzer with stale memory
        memory = SimpleNamespace(
            get_info_staleness=lambda t: 100.0,  # 100s stale
        )
        bot.strategic_analyzer = SimpleNamespace(memory=memory)

        mgr = ScoutingManager(bot)
        mgr._last_scout_time = 0.0  # Allow scout check
        mgr.handle_scouting()

        # At least one overlord should be sent toward enemy start
        targets = list(mgr._assigned_overlords.values())
        assert len(targets) >= 1

    def test_no_scout_when_info_fresh(self):
        """Fresh info (<90s) doesn't trigger additional scout."""
        ol1 = MockScoutUnit(100, 25.0, 25.0)
        bot = MockScoutBot(overlords=[ol1])
        bot.time = 200.0

        memory = SimpleNamespace(
            get_info_staleness=lambda t: 30.0,  # Fresh
        )
        bot.strategic_analyzer = SimpleNamespace(memory=memory)

        mgr = ScoutingManager(bot)
        mgr.handle_scouting()

        # Only regular assignment, no staleness-driven scout
        # The overlord gets one normal assignment
        assert len(mgr._assigned_overlords) == 1

    def test_overlord_retreat_from_threats(self):
        """Overlords near threats get a retreat behavior registered."""
        ol1 = MockScoutUnit(100, 50.0, 50.0)
        bot = MockScoutBot(overlords=[ol1])

        # Place a stalker near the overlord
        stalker = MockScoutUnit(200, 52.0, 50.0)
        stalker.type_id = UnitID.STALKER
        bot.enemy_units = [stalker]

        mgr = ScoutingManager(bot)
        mgr._assigned_overlords[ol1.tag] = Point2((55.0, 55.0))
        mgr.handle_scouting()

        # A retreat behavior should have been registered
        assert len(bot._behaviors) >= 1

    def test_cleans_up_dead_overlords(self):
        """Dead overlord tags are removed from assignments."""
        ol1 = MockScoutUnit(100, 30.0, 30.0)
        ol2 = MockScoutUnit(101, 35.0, 30.0)
        bot = MockScoutBot(overlords=[ol1, ol2])
        mgr = ScoutingManager(bot)
        mgr.handle_scouting()
        assert 100 in mgr._assigned_overlords

        # Now ol1 is dead — only ol2 remains
        bot.mediator._own_army_dict[UnitID.OVERLORD] = [ol2]
        mgr.handle_scouting()
        # ol1 tag should be cleaned up since it's no longer alive
        assert 100 not in mgr._assigned_overlords

    def test_pick_scout_overlord_skips_first(self):
        """_pick_scout_overlord never picks the first overlord (supply)."""
        ol1 = MockScoutUnit(100, 20.0, 20.0)
        ol2 = MockScoutUnit(101, 25.0, 25.0)
        bot = MockScoutBot(overlords=[ol1, ol2])
        mgr = ScoutingManager(bot)
        picked = mgr._pick_scout_overlord([ol1, ol2])
        assert picked is not None
        assert picked.tag != ol1.tag

    def test_pick_scout_overlord_needs_two(self):
        """With only 1 overlord, returns None (keep for supply)."""
        ol1 = MockScoutUnit(100, 20.0, 20.0)
        bot = MockScoutBot(overlords=[ol1])
        mgr = ScoutingManager(bot)
        picked = mgr._pick_scout_overlord([ol1])
        assert picked is None
