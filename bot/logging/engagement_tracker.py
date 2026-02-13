"""Per-engagement combat record tracking for ML training data.

Detects engagement start/end and produces EngagementRecord dicts
with before/after army snapshots, sim prediction vs. actual outcome,
duration, position, and economy context.

An engagement starts when the executor finds enemies nearby and runs
a combat sim. It ends when:
  - No enemies within range for 3+ seconds, OR
  - Combat mode transitions to DEFEND/REGROUP (force_end), OR
  - Game ends (force_end)
"""

from __future__ import annotations

import math
from typing import Any

from bot.strategy.unit_counter_db import compute_army_value, get_unit_value

# How long enemies must be absent before ending an engagement
DISENGAGE_COOLDOWN = 3.0

# Supply-based outcome thresholds
WIN_THRESHOLD = 0.8   # own_lost < enemy_lost * 0.8 → won
LOSS_THRESHOLD = 1.5  # own_lost > enemy_lost * 1.5 → lost
MIN_CASUALTIES = 1.0  # below this, classify as "disengaged"


def _get_supply(unit) -> float:
    """Get supply cost from either a real SC2 Unit or a mock with supply_cost."""
    # Real SC2 units: _type_data._proto.food_required
    try:
        return unit._type_data._proto.food_required
    except AttributeError:
        pass
    # Mock units (tests): supply_cost attribute
    return getattr(unit, "supply_cost", 0) or 0


def _snapshot_own_army(army_dict: dict) -> dict[str, Any]:
    """Snapshot own army as {type_name: count}, supply, value."""
    units: dict[str, int] = {}
    total_supply = 0.0
    total_value = 0.0
    for unit_type, unit_list in army_dict.items():
        name = unit_type.name if hasattr(unit_type, "name") else str(unit_type)
        count = len(unit_list) if hasattr(unit_list, "__len__") else 0
        if count > 0:
            units[name] = count
            total_value += count * get_unit_value(unit_type)
            for u in unit_list:
                total_supply += _get_supply(u)
    return {"units": units, "supply": round(total_supply, 1), "value": round(total_value, 1)}


def _snapshot_enemy(enemy_units: list) -> dict[str, Any]:
    """Snapshot enemy units list."""
    units: dict[str, int] = {}
    total_supply = 0.0
    total_value = 0.0
    for u in enemy_units:
        try:
            name = u.type_id.name
            units[name] = units.get(name, 0) + 1
            total_value += get_unit_value(u.type_id)
            total_supply += _get_supply(u)
        except Exception:
            pass
    return {"units": units, "supply": round(total_supply, 1), "value": round(total_value, 1)}


def _compute_losses(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    """Diff two unit count dicts to get losses (only positive losses)."""
    losses: dict[str, int] = {}
    for name, count_before in before.items():
        count_after = after.get(name, 0)
        lost = count_before - count_after
        if lost > 0:
            losses[name] = lost
    return losses


def _determine_outcome(
    own_supply_lost: float,
    enemy_supply_lost: float,
) -> str:
    """Determine engagement outcome."""
    total_casualties = own_supply_lost + enemy_supply_lost
    if total_casualties < MIN_CASUALTIES:
        return "disengaged"
    if enemy_supply_lost <= 0:
        return "lost" if own_supply_lost > MIN_CASUALTIES else "disengaged"
    ratio = own_supply_lost / enemy_supply_lost
    if ratio < WIN_THRESHOLD:
        return "won"
    if ratio > LOSS_THRESHOLD:
        return "lost"
    return "draw"


class EngagementTracker:
    """Detects engagement start/end and produces EngagementRecord dicts."""

    def __init__(self) -> None:
        self._next_id: int = 0
        self._active: bool = False

        # Before-snapshots
        self._start_time: float = 0.0
        self._start_step: int = 0
        self._own_before: dict[str, int] = {}
        self._own_supply_before: float = 0.0
        self._own_value_before: float = 0.0
        self._enemy_before: dict[str, int] = {}
        self._enemy_supply_before: float = 0.0
        self._enemy_value_before: float = 0.0
        self._position: list[float] = [0.0, 0.0]
        self._distance_from_home: float = 0.0

        # Combat context
        self._sim_prediction: str = "UNKNOWN"
        self._micro_style: str = "attack"
        self._combat_objective: str = "ATTACK_BASE"
        self._profile_name: str = "DEFAULT"
        self._approach: str = "FRONTAL"
        self._was_committed: bool = False
        self._confidence: float = 0.5

        # Economy at start
        self._minerals: int = 0
        self._vespene: int = 0
        self._income_minerals: float = 0.0
        self._income_vespene: float = 0.0
        self._supply_used: int = 0
        self._workers: int = 0

        # Disengage tracking
        self._last_enemy_time: float = 0.0

        # Stats
        self._total: int = 0
        self._wins: int = 0
        self._losses: int = 0
        self._draws: int = 0
        self._disengages: int = 0
        self._total_duration: float = 0.0
        self._correct_predictions: int = 0

    def on_combat_frame(
        self,
        has_enemies: bool,
        own_army_dict: dict,
        enemy_units: list,
        sim_result_name: str,
        micro_style: str,
        position: list[float],
        game_time: float,
        step: int,
        combat_objective: str = "ATTACK_BASE",
        profile_name: str = "DEFAULT",
        approach: str = "FRONTAL",
        was_committed: bool = False,
        confidence: float = 0.5,
        minerals: int = 0,
        vespene: int = 0,
        income_minerals: float = 0.0,
        income_vespene: float = 0.0,
        supply_used: int = 0,
        workers: int = 0,
        home_position: list[float] | None = None,
    ) -> dict | None:
        """Called every time _execute_main_army runs combat.

        Returns an EngagementRecord dict when an engagement ends, otherwise None.
        """
        if has_enemies:
            self._last_enemy_time = game_time

            if not self._active:
                # --- Start new engagement ---
                self._active = True
                self._start_time = game_time
                self._start_step = step
                self._position = position

                # Distance from home
                if home_position:
                    dx = position[0] - home_position[0]
                    dy = position[1] - home_position[1]
                    self._distance_from_home = round(math.sqrt(dx*dx + dy*dy), 1)
                else:
                    self._distance_from_home = 0.0

                # Snapshot armies
                own_snap = _snapshot_own_army(own_army_dict)
                enemy_snap = _snapshot_enemy(enemy_units)
                self._own_before = own_snap["units"]
                self._own_supply_before = own_snap["supply"]
                self._own_value_before = own_snap["value"]
                self._enemy_before = enemy_snap["units"]
                self._enemy_supply_before = enemy_snap["supply"]
                self._enemy_value_before = enemy_snap["value"]

                # Combat context
                self._sim_prediction = sim_result_name
                self._micro_style = micro_style
                self._combat_objective = combat_objective
                self._profile_name = profile_name
                self._approach = approach
                self._was_committed = was_committed
                self._confidence = confidence

                # Economy
                self._minerals = minerals
                self._vespene = vespene
                self._income_minerals = income_minerals
                self._income_vespene = income_vespene
                self._supply_used = supply_used
                self._workers = workers

            else:
                # Update micro style to latest
                self._micro_style = micro_style

            return None

        # No enemies this frame
        if not self._active:
            return None

        # Check disengage cooldown
        elapsed = game_time - self._last_enemy_time
        if elapsed < DISENGAGE_COOLDOWN:
            return None

        # --- End engagement ---
        return self._finalize(game_time, step, own_army_dict, enemy_units)

    def force_end(
        self,
        game_time: float,
        step: int,
        own_army_dict: dict,
        enemy_units: list,
    ) -> dict | None:
        """Force-end any active engagement (mode transition, game end)."""
        if not self._active:
            return None
        return self._finalize(game_time, step, own_army_dict, enemy_units)

    def _finalize(
        self,
        game_time: float,
        step: int,
        own_army_dict: dict,
        enemy_units: list,
    ) -> dict:
        """Build and return the EngagementRecord, reset state."""
        own_after = _snapshot_own_army(own_army_dict)
        enemy_after = _snapshot_enemy(enemy_units)

        own_losses = _compute_losses(self._own_before, own_after["units"])
        enemy_losses = _compute_losses(self._enemy_before, enemy_after["units"])

        own_supply_lost = max(0.0, self._own_supply_before - own_after["supply"])
        enemy_supply_lost = max(0.0, self._enemy_supply_before - enemy_after["supply"])

        outcome = _determine_outcome(own_supply_lost, enemy_supply_lost)
        duration = round(game_time - self._start_time, 1)

        # Check prediction accuracy
        sim_predicted_win = "VICTORY" in self._sim_prediction.upper()
        actual_win = outcome == "won"
        prediction_correct = (sim_predicted_win and actual_win) or (
            not sim_predicted_win and outcome in ("lost", "draw")
        )

        eid = self._next_id
        self._next_id += 1

        record: dict[str, Any] = {
            "type": "engagement",
            "engagement_id": eid,
            "game_time_start": round(self._start_time, 1),
            "game_time_end": round(game_time, 1),
            "duration": duration,
            "step_start": self._start_step,
            "step_end": step,
            "position": self._position,
            "distance_from_home": self._distance_from_home,
            "own_army_before": {
                "units": self._own_before,
                "supply": self._own_supply_before,
                "value": self._own_value_before,
            },
            "own_army_after": own_after,
            "enemy_army_before": {
                "units": self._enemy_before,
                "supply": self._enemy_supply_before,
                "value": self._enemy_value_before,
            },
            "enemy_army_after": enemy_after,
            "own_losses": own_losses,
            "enemy_losses": enemy_losses,
            "own_supply_lost": round(own_supply_lost, 1),
            "enemy_supply_lost": round(enemy_supply_lost, 1),
            "sim_prediction": self._sim_prediction,
            "actual_outcome": outcome,
            "prediction_correct": prediction_correct,
            "combat_objective": self._combat_objective,
            "engagement_profile": self._profile_name,
            "approach_style": self._approach,
            "micro_style": self._micro_style,
            "was_committed": self._was_committed,
            "confidence": round(self._confidence, 2),
            "own_minerals": self._minerals,
            "own_vespene": self._vespene,
            "income_minerals": round(self._income_minerals, 1),
            "income_vespene": round(self._income_vespene, 1),
            "own_supply_used": self._supply_used,
            "own_worker_count": self._workers,
        }

        # Update stats
        self._total += 1
        self._total_duration += duration
        if outcome == "won":
            self._wins += 1
        elif outcome == "lost":
            self._losses += 1
        elif outcome == "draw":
            self._draws += 1
        else:
            self._disengages += 1
        if prediction_correct:
            self._correct_predictions += 1

        # Reset
        self._active = False

        return record

    def get_stats(self) -> dict[str, Any]:
        """Summary statistics for game end."""
        return {
            "total": self._total,
            "won": self._wins,
            "lost": self._losses,
            "draw": self._draws,
            "disengaged": self._disengages,
            "avg_duration": round(
                self._total_duration / self._total, 1
            ) if self._total > 0 else 0.0,
            "prediction_accuracy": round(
                self._correct_predictions / self._total, 3
            ) if self._total > 0 else 0.0,
        }
