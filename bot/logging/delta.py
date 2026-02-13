"""Frame-over-frame delta computation and surprise scoring.

DeltaTracker stores the previous state snapshot and computes a diff
each time a new one arrives. The resulting `surprise_score` is the
direct input for Phase 3's info-gain attention scheduler.

Design principle: enemy state changes are weighted 2x own changes
because enemy state is harder to predict (partial observability).
"""

from __future__ import annotations

import math
from typing import Any


# --- Weights for surprise score ---
# Enemy changes matter more (harder to predict under fog of war)
_W_ECON = 0.01  # per mineral/gas change
_W_OWN_UNIT = 1.0  # per own unit count change
_W_ENEMY_UNIT = 2.0  # per enemy unit count change  (2x own)
_W_OWN_STRUCT = 1.5
_W_ENEMY_STRUCT = 3.0  # enemy structure changes are very informative
_W_ARMY_MOVE = 0.1  # per map-unit moved
_W_ENEMY_MOVE = 0.2
_W_UPGRADE = 5.0  # upgrades are rare, high info


def _dict_delta(prev: dict[str, int], curr: dict[str, int]) -> dict[str, int]:
    """Return {key: change} for all keys that changed between two dicts."""
    all_keys = set(prev) | set(curr)
    delta: dict[str, int] = {}
    for k in all_keys:
        diff = curr.get(k, 0) - prev.get(k, 0)
        if diff != 0:
            delta[k] = diff
    return delta


def _point_distance(
    p1: list[float] | None, p2: list[float] | None
) -> float:
    """Euclidean distance between two [x,y] points, or 0.0 if either is None."""
    if p1 is None or p2 is None:
        return 0.0
    dx = p1[0] - p2[0]
    dy = p1[1] - p2[1]
    return math.sqrt(dx * dx + dy * dy)


class DeltaTracker:
    """Computes frame-over-frame deltas and a scalar surprise score."""

    def __init__(self) -> None:
        self._prev: dict[str, Any] | None = None

    def compute(self, snapshot: dict[str, Any]) -> dict[str, Any] | None:
        """Given a new state snapshot dict, return the delta record.

        Returns None on the first call (no previous state to diff against).
        """
        prev = self._prev
        self._prev = snapshot

        if prev is None:
            return None

        # Economy delta
        econ_fields = ("minerals", "vespene", "supply_used", "supply_cap", "larva_count")
        economy_delta: dict[str, float] = {}
        for field in econ_fields:
            diff = snapshot.get(field, 0) - prev.get(field, 0)
            if diff != 0:
                economy_delta[field] = diff

        # Unit / structure deltas
        own_units_delta = _dict_delta(
            prev.get("own_units", {}), snapshot.get("own_units", {})
        )
        enemy_units_delta = _dict_delta(
            prev.get("enemy_units", {}), snapshot.get("enemy_units", {})
        )
        own_structures_delta = _dict_delta(
            prev.get("own_structures", {}), snapshot.get("own_structures", {})
        )
        enemy_structures_delta = _dict_delta(
            prev.get("enemy_structures", {}), snapshot.get("enemy_structures", {})
        )

        # Army movement
        own_army_moved = _point_distance(
            prev.get("own_army_center"), snapshot.get("own_army_center")
        )
        enemy_army_moved = _point_distance(
            prev.get("enemy_army_center"), snapshot.get("enemy_army_center")
        )

        # New upgrades
        prev_upgrades = set(prev.get("upgrades", []))
        curr_upgrades = set(snapshot.get("upgrades", []))
        new_upgrades = sorted(curr_upgrades - prev_upgrades)

        # --- Surprise score: weighted L1 norm ---
        score = 0.0
        for v in economy_delta.values():
            score += abs(v) * _W_ECON
        for v in own_units_delta.values():
            score += abs(v) * _W_OWN_UNIT
        for v in enemy_units_delta.values():
            score += abs(v) * _W_ENEMY_UNIT
        for v in own_structures_delta.values():
            score += abs(v) * _W_OWN_STRUCT
        for v in enemy_structures_delta.values():
            score += abs(v) * _W_ENEMY_STRUCT
        score += own_army_moved * _W_ARMY_MOVE
        score += enemy_army_moved * _W_ENEMY_MOVE
        score += len(new_upgrades) * _W_UPGRADE

        delta: dict[str, Any] = {
            "economy_delta": economy_delta,
            "own_units_delta": own_units_delta,
            "enemy_units_delta": enemy_units_delta,
            "own_structures_delta": own_structures_delta,
            "enemy_structures_delta": enemy_structures_delta,
            "own_army_moved": round(own_army_moved, 2),
            "enemy_army_moved": round(enemy_army_moved, 2),
            "new_upgrades": new_upgrades,
            "surprise_score": round(score, 3),
        }
        return delta
