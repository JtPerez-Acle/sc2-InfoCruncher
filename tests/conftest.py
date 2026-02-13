"""Test fixtures for the logging system.

Provides a lightweight mock AresBot that has just enough attributes
for GameLogger, collect_state_snapshot, and decision logging to work —
without needing SC2 or the full ares framework.
"""

from __future__ import annotations

import os
import sys
import tempfile
from types import SimpleNamespace
from typing import Any

import pytest

# --- Ensure ares-sc2 submodule is importable (same as run.py) ---
# This allows tests to import bot.macro and other modules that depend on ares.
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _subpath in ["ares-sc2/src/ares", "ares-sc2/src", "ares-sc2"]:
    _full = os.path.join(_project_root, _subpath)
    if _full not in sys.path:
        sys.path.insert(0, _full)


class MockMediator:
    """Minimal mediator mock for snapshot/logger tests."""

    def __init__(self) -> None:
        self._own_army_dict: dict = {}
        self._enemy_army_dict: dict = {}
        self._own_structures_dict: dict = {}
        self._unit_role_dict: dict = {}

    @property
    def get_own_army_dict(self) -> dict:
        return self._own_army_dict

    @property
    def get_enemy_army_dict(self) -> dict:
        return self._enemy_army_dict

    @property
    def get_own_structures_dict(self) -> dict:
        return self._own_structures_dict

    @property
    def get_own_army(self):
        return MockUnits([])

    @property
    def get_creep_coverage(self) -> float:
        return 0.35

    @property
    def get_unit_role_dict(self) -> dict:
        return self._unit_role_dict

    def get_units_from_role(self, **kwargs):
        return MockUnits([])


class MockUnit:
    """Minimal unit mock."""

    def __init__(self, tag: int, type_id: Any = None, position: Any = None):
        self.tag = tag
        self.type_id = type_id or SimpleNamespace(name="ZERGLING")
        self.position = position or SimpleNamespace(x=50.0, y=50.0)
        self.is_flying = False
        self.can_attack_ground = False
        self.health_percentage = 1.0
        self.is_ready = True
        self.is_idle = True


class MockUnits:
    """Minimal units collection mock."""

    def __init__(self, units: list):
        self._units = units

    def __len__(self):
        return len(self._units)

    def __iter__(self):
        return iter(self._units)

    def __bool__(self):
        return len(self._units) > 0

    @property
    def center(self):
        if not self._units:
            return SimpleNamespace(x=0.0, y=0.0)
        avg_x = sum(u.position.x for u in self._units) / len(self._units)
        avg_y = sum(u.position.y for u in self._units) / len(self._units)
        return SimpleNamespace(x=avg_x, y=avg_y)

    def filter(self, pred):
        return MockUnits([u for u in self._units if pred(u)])

    def closest_to(self, position):
        if not self._units:
            return None
        return min(self._units, key=lambda u: abs(u.position.x - position.x))


class MockBuildOrderRunner:
    """Minimal build order runner mock."""

    def __init__(self):
        self.build_completed = False
        self.build_step = 3


class MockGameInfo:
    """Minimal game info mock."""

    def __init__(self):
        self.map_name = "TestMap"


class MockState:
    """Minimal game state mock."""

    def __init__(self):
        self.upgrades = []
        self.game_loop = 100
        self.score = SimpleNamespace(
            collected_minerals=5000,
            collected_vespene=2000,
            collection_rate_minerals=1200.0,
            collection_rate_vespene=400.0,
        )


class MockAresBot:
    """Lightweight mock of AresBot for logger tests.

    Has all attributes that GameLogger, snapshot, and decision logging access.
    """

    def __init__(self):
        self.time = 120.5
        self.minerals = 400
        self.vespene = 150
        self.supply_used = 45
        self.supply_cap = 60
        self.supply_workers = 30
        self.larva = MockUnits([MockUnit(i) for i in range(3)])
        self.mediator = MockMediator()
        self.enemy_structures = MockUnits([])
        self.enemy_units = MockUnits([])
        self.all_enemy_units = MockUnits([])
        self.units = MockUnits([])
        self.townhalls = MockUnits([MockUnit(1000)])
        self.start_location = SimpleNamespace(x=30.0, y=30.0)
        self.build_order_runner = MockBuildOrderRunner()
        self.game_info = MockGameInfo()
        self.state = MockState()
        self.enemy_race = "Zerg"
        self.config = {
            "Logging": {
                "Enabled": True,
                "StateSnapshotInterval": 5,
                "DecisionTracing": True,
                "EventLogging": True,
                "FlushInterval": 50,
                "DeltaTracking": True,
                "LogDirectory": "",  # Set per test
            }
        }

        # For macro tests — mock pending/afford state
        self._can_afford_overrides: dict = {}   # UnitID → bool
        self._already_pending_overrides: dict = {}  # UnitID → int
        self._structure_present_overrides: dict = {}  # UnitID → bool
        self._registered_behaviors: list = []

    def can_afford(self, unit_type: Any) -> bool:
        """Mock: check override dict, default True."""
        return self._can_afford_overrides.get(unit_type, True)

    def already_pending(self, unit_type: Any) -> int:
        """Mock: check override dict, default 0."""
        return self._already_pending_overrides.get(unit_type, 0)

    def structure_present_or_pending(self, unit_type: Any) -> bool:
        """Mock: check override dict, default False."""
        return self._structure_present_overrides.get(unit_type, False)

    def structure_pending(self, unit_type: Any) -> int:
        """Mock: return 0 by default."""
        return 0

    def register_behavior(self, behavior: Any) -> None:
        """Mock: track registered behaviors."""
        self._registered_behaviors.append(behavior)


@pytest.fixture
def mock_bot(tmp_path):
    """Provides a MockAresBot with a temp log directory."""
    bot = MockAresBot()
    bot.config["Logging"]["LogDirectory"] = str(tmp_path)
    return bot


@pytest.fixture
def log_dir(tmp_path):
    """Provides a temporary directory for log output."""
    return tmp_path
