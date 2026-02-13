"""Tests for bot.macro — tech prerequisite gating, force morph, gas override.

Uses MockAresBot from conftest.py.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId as UnitID

from bot.macro import (
    MacroManager,
    TechExecutionTracker,
    _MORPH_MAP,
    _MORPH_STRUCTURES,
    _STRUCT_PREREQUISITES,
)
from tests.conftest import MockAresBot, MockUnit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bot(**overrides) -> MockAresBot:
    """Create a MockAresBot with optional overrides."""
    bot = MockAresBot()
    for key, val in overrides.items():
        setattr(bot, key, val)
    return bot


def _make_building(tag: int = 1, idle: bool = True, ready: bool = True):
    """Create a mock building."""
    b = MockUnit(tag)
    b.is_idle = idle
    b.is_ready = ready
    b._commands = []

    def _call(ability):
        b._commands.append(ability)

    b.__call__ = _call
    # Make it callable
    b.__class__ = type("MockBuilding", (), {
        "__call__": _call,
        "__getattr__": lambda self, name: getattr(b, name),
    })
    return b


class CallableUnit:
    """A mock unit that records ability commands."""

    def __init__(self, tag: int = 1, idle: bool = True, ready: bool = True):
        self.tag = tag
        self.is_idle = idle
        self.is_ready = ready
        self.commands: list = []
        self.type_id = SimpleNamespace(name="HATCHERY")
        self.position = SimpleNamespace(x=50.0, y=50.0)

    def __call__(self, ability):
        self.commands.append(ability)


# ===========================================================================
# Prerequisite gating tests
# ===========================================================================


class TestPrerequisitesMap:
    """Verify _STRUCT_PREREQUISITES data integrity."""

    def test_all_prereqs_are_valid_unit_ids(self):
        for structure, prereqs in _STRUCT_PREREQUISITES.items():
            assert isinstance(structure, UnitID), f"Key {structure} is not UnitID"
            for prereq in prereqs:
                assert isinstance(prereq, UnitID), f"Prereq {prereq} is not UnitID"

    def test_hydra_den_needs_lair(self):
        assert UnitID.LAIR in _STRUCT_PREREQUISITES[UnitID.HYDRALISKDEN]

    def test_spire_needs_lair(self):
        assert UnitID.LAIR in _STRUCT_PREREQUISITES[UnitID.SPIRE]

    def test_lurker_den_needs_hydra_den(self):
        assert UnitID.HYDRALISKDEN in _STRUCT_PREREQUISITES[UnitID.LURKERDENMP]

    def test_ultra_cavern_needs_hive(self):
        assert UnitID.HIVE in _STRUCT_PREREQUISITES[UnitID.ULTRALISKCAVERN]


class TestPrerequisitesMet:
    """Test MacroManager._prerequisites_met()."""

    def setup_method(self):
        self.bot = _make_bot()
        self.manager = MacroManager(self.bot)

    def test_no_prereqs_returns_true(self):
        """Structures without prerequisites always pass."""
        assert self.manager._prerequisites_met(UnitID.SPAWNINGPOOL) is True

    def test_hydra_den_blocked_without_lair(self):
        """HYDRALISKDEN requires LAIR — blocked when neither exists."""
        self.bot._structure_present_overrides[UnitID.LAIR] = False
        self.bot._structure_present_overrides[UnitID.HIVE] = False
        assert self.manager._prerequisites_met(UnitID.HYDRALISKDEN) is False

    def test_hydra_den_passes_with_lair(self):
        """HYDRALISKDEN passes when LAIR exists."""
        self.bot._structure_present_overrides[UnitID.LAIR] = True
        assert self.manager._prerequisites_met(UnitID.HYDRALISKDEN) is True

    def test_hydra_den_passes_with_hive(self):
        """HIVE counts as LAIR for prerequisite checks."""
        self.bot._structure_present_overrides[UnitID.HIVE] = True
        assert self.manager._prerequisites_met(UnitID.HYDRALISKDEN) is True

    def test_hydra_den_passes_with_lair_pending(self):
        """HYDRALISKDEN can be requested while LAIR is morphing."""
        self.bot._already_pending_overrides[UnitID.LAIR] = 1
        assert self.manager._prerequisites_met(UnitID.HYDRALISKDEN) is True

    def test_spire_blocked_without_lair(self):
        """SPIRE requires LAIR."""
        assert self.manager._prerequisites_met(UnitID.SPIRE) is False

    def test_lurker_den_needs_hydra_den(self):
        """LURKERDENMP needs HYDRALISKDEN."""
        self.bot._structure_present_overrides[UnitID.HYDRALISKDEN] = True
        assert self.manager._prerequisites_met(UnitID.LURKERDENMP) is True

        self.bot._structure_present_overrides[UnitID.HYDRALISKDEN] = False
        assert self.manager._prerequisites_met(UnitID.LURKERDENMP) is False

    def test_greater_spire_needs_both(self):
        """GREATERSPIRE needs HIVE + SPIRE."""
        # Neither
        assert self.manager._prerequisites_met(UnitID.GREATERSPIRE) is False

        # Only hive
        self.bot._structure_present_overrides[UnitID.HIVE] = True
        assert self.manager._prerequisites_met(UnitID.GREATERSPIRE) is False

        # Both
        self.bot._structure_present_overrides[UnitID.SPIRE] = True
        assert self.manager._prerequisites_met(UnitID.GREATERSPIRE) is True


# ===========================================================================
# Force morph tests
# ===========================================================================


class TestForceMorph:
    """Test MacroManager._force_morph()."""

    def setup_method(self):
        self.bot = _make_bot()
        self.manager = MacroManager(self.bot)

    def test_prefers_idle_building(self):
        """Should pick idle building over busy one."""
        idle_hatch = CallableUnit(tag=1, idle=True, ready=True)
        busy_hatch = CallableUnit(tag=2, idle=False, ready=True)
        structs = {UnitID.HATCHERY: [busy_hatch, idle_hatch]}

        result = self.manager._force_morph(
            UnitID.HATCHERY, UnitID.LAIR, AbilityId.UPGRADETOLAIR_LAIR, structs
        )
        assert result == ""
        assert len(idle_hatch.commands) == 1
        assert len(busy_hatch.commands) == 0

    def test_falls_back_to_busy_building(self):
        """When no idle building, should use first ready building."""
        busy_hatch = CallableUnit(tag=1, idle=False, ready=True)
        structs = {UnitID.HATCHERY: [busy_hatch]}

        result = self.manager._force_morph(
            UnitID.HATCHERY, UnitID.LAIR, AbilityId.UPGRADETOLAIR_LAIR, structs
        )
        assert result == ""
        assert len(busy_hatch.commands) == 1

    def test_blocked_by_already_pending(self):
        """Don't double-morph when one is already in progress."""
        hatch = CallableUnit(tag=1, idle=True, ready=True)
        structs = {UnitID.HATCHERY: [hatch]}
        self.bot._already_pending_overrides[UnitID.LAIR] = 1

        result = self.manager._force_morph(
            UnitID.HATCHERY, UnitID.LAIR, AbilityId.UPGRADETOLAIR_LAIR, structs
        )
        assert result == "already_pending"
        assert len(hatch.commands) == 0

    def test_blocked_by_cant_afford(self):
        """Don't morph if we can't afford it."""
        hatch = CallableUnit(tag=1, idle=True, ready=True)
        structs = {UnitID.HATCHERY: [hatch]}
        self.bot._can_afford_overrides[UnitID.LAIR] = False

        result = self.manager._force_morph(
            UnitID.HATCHERY, UnitID.LAIR, AbilityId.UPGRADETOLAIR_LAIR, structs
        )
        assert result == "cant_afford"
        assert len(hatch.commands) == 0

    def test_no_source_buildings(self):
        """Return failure reason when source type has no buildings."""
        structs = {UnitID.HATCHERY: []}
        result = self.manager._force_morph(
            UnitID.HATCHERY, UnitID.LAIR, AbilityId.UPGRADETOLAIR_LAIR, structs
        )
        assert result == "no_source_building"

    def test_skips_unready_buildings(self):
        """Unready (still building) buildings should be skipped."""
        unready = CallableUnit(tag=1, idle=True, ready=False)
        structs = {UnitID.HATCHERY: [unready]}

        result = self.manager._force_morph(
            UnitID.HATCHERY, UnitID.LAIR, AbilityId.UPGRADETOLAIR_LAIR, structs
        )
        assert result == "no_source_building"


# ===========================================================================
# Tech Execution Tracker tests
# ===========================================================================


class TestTechExecutionTracker:
    """Test TechExecutionTracker escalation timing."""

    def test_reduced_timeouts(self):
        """Verify reduced escalation timeouts (from 20/8 to 10/5)."""
        assert TechExecutionTracker.ESCALATION_TIMEOUT == 10.0
        assert TechExecutionTracker.FORCE_RETRY_INTERVAL == 5.0

    def test_stalled_morph_detection(self):
        """Morph request stalls after ESCALATION_TIMEOUT."""
        tracker = TechExecutionTracker()
        tracker.track(UnitID.LAIR, 100.0)

        # Not stalled yet
        stalled = tracker.get_stalled_morphs(105.0)
        assert len(stalled) == 0

        # After timeout + retry interval
        stalled = tracker.get_stalled_morphs(115.1)
        assert len(stalled) == 1
        assert stalled[0].structure_id == UnitID.LAIR

    def test_clear_removes_request(self):
        """Structure appearing clears the tracker."""
        tracker = TechExecutionTracker()
        tracker.track(UnitID.LAIR, 100.0)
        tracker.clear(UnitID.LAIR)
        stalled = tracker.get_stalled_morphs(200.0)
        assert len(stalled) == 0


# ===========================================================================
# Gas spending override tests
# ===========================================================================


class TestGasSpendingOverride:
    """Test MacroManager._apply_gas_spending_override()."""

    def setup_method(self):
        self.bot = _make_bot()
        self.manager = MacroManager(self.bot)

    def _make_comp(self) -> dict[UnitID, dict]:
        """Standard roach+ling comp."""
        return {
            UnitID.ROACH: {"proportion": 0.65, "priority": 0},
            UnitID.ZERGLING: {"proportion": 0.35, "priority": 1},
        }

    def test_no_override_when_vespene_low(self):
        """No change when vespene <= 500."""
        comp = self._make_comp()
        result = self.manager._apply_gas_spending_override(comp, 400, 400)
        assert result[UnitID.ROACH]["proportion"] == 0.65

    def test_no_override_when_vespene_less_than_minerals(self):
        """No change when vespene <= minerals."""
        comp = self._make_comp()
        result = self.manager._apply_gas_spending_override(comp, 800, 600)
        assert result[UnitID.ROACH]["proportion"] == 0.65

    def test_override_boosts_gas_heavy_unit(self):
        """Roach proportion should increase when vespene banks."""
        comp = self._make_comp()
        result = self.manager._apply_gas_spending_override(comp, 200, 800)
        assert result[UnitID.ROACH]["proportion"] > 0.65

    def test_override_reduces_zergling(self):
        """Zergling proportion should decrease (gas=0 unit)."""
        comp = self._make_comp()
        result = self.manager._apply_gas_spending_override(comp, 200, 800)
        assert result[UnitID.ZERGLING]["proportion"] < 0.35

    def test_override_boost_capped_at_15_percent(self):
        """Normal tier: boost capped at 15% when gas is only mildly over minerals."""
        comp = {
            UnitID.ROACH: {"proportion": 0.50, "priority": 0},
            UnitID.ZERGLING: {"proportion": 0.50, "priority": 1},
        }
        # vespene=800, minerals=600: 800 > 600 but NOT > 600*5=3000
        result = self.manager._apply_gas_spending_override(comp, 600, 800)
        # Normal tier: min(0.15, (800-500)/3000) = min(0.15, 0.1) = 0.1
        assert result[UnitID.ROACH]["proportion"] <= 0.65
        assert result[UnitID.ROACH]["proportion"] > 0.50

    def test_empty_comp_returns_unchanged(self):
        """Empty comp should pass through."""
        result = self.manager._apply_gas_spending_override({}, 100, 2000)
        assert result == {}

    def test_all_gas_units_no_steal(self):
        """If all units cost gas (above threshold), no override happens."""
        comp = {
            UnitID.HYDRALISK: {"proportion": 0.50, "priority": 0},
            UnitID.LURKERMP: {"proportion": 0.50, "priority": 1},
        }
        # Both units have gas_cost > 25, so no steal candidates even at extreme
        result = self.manager._apply_gas_spending_override(comp, 100, 2000)
        assert result[UnitID.HYDRALISK]["proportion"] == 0.50
