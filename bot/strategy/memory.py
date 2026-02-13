"""Strategic Memory — persistent game state across frames.

The strategy system was stateless: when enemies left vision, they ceased
to exist. This module provides persistent memory that:
  - Remembers everything ever scouted (cumulative enemy intel)
  - Estimates unseen army growth over time
  - Diagnoses spending bottlenecks (larva-bound, tech-bound, etc.)

All data is accumulated incrementally — never reset during a game.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sc2.ids.unit_typeid import UnitTypeId as UnitID

from bot.strategy.unit_counter_db import (
    TECH_REQUIREMENTS,
    UNIT_PROFILES,
    can_build_unit,
    get_unit_value,
)


@dataclass
class EnemySnapshot:
    """Last known state of an enemy unit type."""

    count: int
    last_seen_time: float
    total_value: float  # count * unit_value at time of observation
    first_seen_time: float = 0.0  # when this type was first observed


@dataclass
class SpendingDiagnosis:
    """What's bottlenecking our spending."""

    is_larva_bound: bool = False   # floating mins + low larvae → need hatches
    is_tech_bound: bool = False    # have resources but only lings buildable
    is_supply_bound: bool = False  # near cap → need overlords
    is_gas_bound: bool = False     # need gas units but low vespene
    mineral_float: int = 0         # current mineral bank
    float_duration: float = 0.0    # seconds we've been floating >400 minerals


class StrategicMemory:
    """Persistent game memory across frames.

    Accumulates scouting data, estimates unseen enemy growth,
    and diagnoses spending bottlenecks for the economy model.
    """

    # Assumed enemy army growth rate (value per second, ~1 roach every 6s)
    GROWTH_RATE: float = 15.0
    # Don't let estimated value exceed this multiple of last known
    MAX_GROWTH_MULTIPLIER: float = 2.0
    # If we saw the enemy this recently, don't apply growth
    RECENT_SIGHT_THRESHOLD: float = 10.0
    # Mineral threshold for "floating"
    FLOAT_THRESHOLD: int = 400
    # Before-any-data heuristic: conservative growth rate (value/sec)
    HEURISTIC_GROWTH: float = 3.0
    # Heuristic doesn't start until this game time (enemy is still droning)
    HEURISTIC_START: float = 180.0
    # Maximum heuristic value before real observations
    HEURISTIC_CAP: float = 500.0

    def __init__(self) -> None:
        # --- Enemy tracking ---
        self.enemy_history: dict[UnitID, EnemySnapshot] = {}
        self.enemy_structures_ever: set[UnitID] = set()

        # --- Army value estimation ---
        self._last_known_value: float = 0.0
        self._last_observation_time: float = 0.0
        self.estimated_enemy_value: float = 0.0

        # --- Spending tracking ---
        self._float_start_time: float | None = None
        self.last_spending: SpendingDiagnosis | None = None

    # ------------------------------------------------------------------
    # Update from current observations
    # ------------------------------------------------------------------

    def update(
        self,
        enemy_comp: dict[UnitID, int],
        enemy_structures: set[UnitID],
        enemy_army_value: float,
        game_time: float,
    ) -> None:
        """Merge current observations into persistent memory.

        - Enemy unit counts: keep the max ever seen per type
        - Enemy structures: accumulate into ever-seen set
        - Army value: update estimate with observation or growth
        """
        # --- Merge enemy unit counts (keep max) ---
        for unit_type, count in enemy_comp.items():
            existing = self.enemy_history.get(unit_type)
            value = count * get_unit_value(unit_type)

            if existing is None or count > existing.count:
                self.enemy_history[unit_type] = EnemySnapshot(
                    count=count,
                    last_seen_time=game_time,
                    total_value=value,
                    first_seen_time=(
                        existing.first_seen_time if existing else game_time
                    ),
                )
            elif count > 0:
                # Update last_seen even if count didn't increase
                existing.last_seen_time = game_time

        # --- Accumulate structures ---
        self.enemy_structures_ever.update(enemy_structures)

        # --- Update army value estimate ---
        if enemy_army_value > 0:
            # We can see enemy army — use actual value
            self._last_known_value = max(
                self._last_known_value, enemy_army_value
            )
            self._last_observation_time = game_time
            self.estimated_enemy_value = self._last_known_value
        else:
            # No visible enemy — apply growth estimate
            self.estimated_enemy_value = self.get_estimated_enemy_value(
                game_time
            )

    # ------------------------------------------------------------------
    # Enemy value estimation
    # ------------------------------------------------------------------

    def get_estimated_enemy_value(self, game_time: float) -> float:
        """Estimate current enemy army value from last known + growth.

        Returns last_known + growth_rate * elapsed, capped at 2x.
        Growth stops if we recently saw the enemy (within 10s).
        """
        if self._last_known_value <= 0:
            # No real data yet — use conservative heuristic.
            # Don't assume enemy has a massive army just because time passed.
            # This prevents army_advantage = 0.0 (can't attack) in early game.
            if game_time > self.HEURISTIC_START:
                elapsed = game_time - self.HEURISTIC_START
                return min(self.HEURISTIC_GROWTH * elapsed, self.HEURISTIC_CAP)
            return 0.0

        elapsed = game_time - self._last_observation_time

        # If we saw them recently, don't apply growth
        if elapsed < self.RECENT_SIGHT_THRESHOLD:
            return self._last_known_value

        # Apply growth, capped at 2x
        growth = self.GROWTH_RATE * elapsed
        cap = self._last_known_value * self.MAX_GROWTH_MULTIPLIER
        return min(self._last_known_value + growth, cap)

    # ------------------------------------------------------------------
    # Cumulative enemy composition
    # ------------------------------------------------------------------

    def get_cumulative_enemy_comp(self) -> dict[UnitID, int]:
        """Return the maximum observed count for each enemy unit type.

        This represents "everything we've ever scouted" — the best
        available picture of the enemy army composition.
        """
        return {
            uid: snapshot.count
            for uid, snapshot in self.enemy_history.items()
        }

    # ------------------------------------------------------------------
    # Information staleness
    # ------------------------------------------------------------------

    def get_info_staleness(self, game_time: float) -> float:
        """Seconds since we last observed any enemy army units."""
        if self._last_observation_time <= 0:
            return game_time  # never seen enemy
        return game_time - self._last_observation_time

    def get_discovery_timeline(self) -> list[tuple[float, str, int]]:
        """Return chronological list of (first_seen_time, unit_type_name, count).

        Sorted by first_seen_time. Used for opponent modeling features.
        """
        timeline = []
        for unit_type, snapshot in self.enemy_history.items():
            timeline.append((
                snapshot.first_seen_time,
                unit_type.name,
                snapshot.count,
            ))
        timeline.sort(key=lambda t: t[0])
        return timeline

    # ------------------------------------------------------------------
    # Spending diagnosis
    # ------------------------------------------------------------------

    def diagnose_spending(
        self,
        minerals: int,
        vespene: int,
        supply_used: int,
        supply_cap: int,
        larva_count: int,
        recommended_comp: dict[UnitID, float],
        own_structures: dict,
        game_time: float,
    ) -> SpendingDiagnosis:
        """Diagnose what's bottlenecking resource spending.

        Args:
            minerals: Current mineral bank
            vespene: Current vespene bank
            supply_used: Current supply used
            supply_cap: Current supply cap
            larva_count: Available larvae
            recommended_comp: Current recommended army composition
            own_structures: From mediator.get_own_structures_dict
            game_time: Current game time in seconds
        """
        diagnosis = SpendingDiagnosis()
        diagnosis.mineral_float = minerals

        # --- Track float duration ---
        if minerals > self.FLOAT_THRESHOLD:
            if self._float_start_time is None:
                self._float_start_time = game_time
            diagnosis.float_duration = game_time - self._float_start_time
        else:
            self._float_start_time = None
            diagnosis.float_duration = 0.0

        # --- Supply bound ---
        diagnosis.is_supply_bound = (supply_cap - supply_used) < 4

        # --- Larva bound ---
        # Floating minerals + low larvae + not supply capped
        diagnosis.is_larva_bound = (
            minerals > self.FLOAT_THRESHOLD
            and larva_count < 3
            and not diagnosis.is_supply_bound
        )

        # --- Tech bound ---
        # Floating minerals but can only build zergling-tier units
        if minerals > self.FLOAT_THRESHOLD:
            # Check what we can actually build beyond lings
            has_roach_warren = len(
                own_structures.get(UnitID.ROACHWARREN, [])
            ) > 0
            has_lair_tech = any(
                len(own_structures.get(t, [])) > 0
                for t in [UnitID.LAIR, UnitID.HIVE]
            )
            # Tech-bound = no roach warren AND no lair-tier tech
            diagnosis.is_tech_bound = not has_roach_warren and not has_lair_tech

        # --- Gas bound ---
        # Recommended comp needs gas-heavy units but vespene is low
        if recommended_comp and vespene < 100:
            gas_weight = 0.0
            for unit_type, proportion in recommended_comp.items():
                profile = UNIT_PROFILES.get(unit_type)
                if profile and profile.gas_cost > 0:
                    gas_weight += proportion
            diagnosis.is_gas_bound = gas_weight > 0.4

        self.last_spending = diagnosis
        return diagnosis
