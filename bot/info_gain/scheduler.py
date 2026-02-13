"""Info-gain observation scheduler — decides per-frame whether to observe or skip.

The core Phase 3 component. Uses a composite priority score that combines:
  1. Adjusted surprise (raw surprise minus production noise)
  2. Combat guard (force-observe mode transitions and threat appearances)
  3. Phase-aware weighting (build order is lower priority than combat)
  4. Enemy change bonuses (scouting reveals are high value)

Budget enforcement uses an adaptive threshold derived from a sliding window
of recent priority scores. A feedback loop keeps the actual observation rate
near the configured budget target.
"""

from __future__ import annotations

from collections import deque
from typing import Any


# Unit types whose own_units_delta is production noise (high count change,
# zero decision value). These are discounted in the priority calculation.
_PRODUCTION_NOISE_TYPES = frozenset({
    "EGG", "LARVA", "DRONE", "OVERLORD", "OVERLORDCOCOON",
})

# Weight used for own unit delta in the original surprise_score computation
# (must match _W_OWN_UNIT in bot/logging/delta.py)
_W_OWN_UNIT = 1.0


class ObservationScheduler:
    """Per-frame observation filter using composite priority scoring.

    Called by GameLogger.on_step() before writing the snapshot.
    Returns True to observe (write full snapshot), False to skip.
    """

    def __init__(self, config: dict) -> None:
        self._budget: float = config.get("ObservationBudget", 1.0)
        self._enabled: bool = config.get("Enabled", False)
        self._combat_guard: bool = config.get("CombatGuard", True)
        self._production_discount: float = config.get("ProductionDiscount", 0.7)
        self._max_consecutive_skips: int = config.get("MinObserveInterval", 3)

        # Budget tracking
        self._total_opportunities: int = 0
        self._total_observed: int = 0
        self._total_skipped: int = 0
        self._consecutive_skips: int = 0

        # Phase counters
        self._bo_observed: int = 0
        self._bo_skipped: int = 0
        self._post_bo_observed: int = 0
        self._post_bo_skipped: int = 0

        # Signal tracking for combat guard
        self._prev_combat_mode: str | None = None
        self._prev_threat_count: int = 0

        # Adaptive threshold from sliding window
        self._priority_window: deque[float] = deque(maxlen=50)
        self._current_threshold: float = 0.0

        # Last decision metadata (for get_skip_metadata)
        self._last_priority: float = 0.0
        self._last_reason: str = ""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def should_observe(
        self, snapshot: dict[str, Any], delta: dict[str, Any] | None
    ) -> bool:
        """Decide whether to observe this frame.

        Args:
            snapshot: Full state snapshot dict (with combat_mode, threats, etc.)
            delta: The raw delta dict from DeltaTracker, or None for the first frame.

        Returns:
            True = write full snapshot. False = write skip marker only.
        """
        if not self._enabled or self._budget >= 1.0:
            self._last_reason = "full_budget"
            self._update_signal_state(snapshot)
            return True

        self._total_opportunities += 1

        # First frame: always observe
        if delta is None:
            return self._record_observe(float("inf"), "first_frame", snapshot)

        priority = self._compute_priority(snapshot, delta)
        self._update_threshold(priority)

        # Combat guard: force-observe critical transitions
        if self._combat_guard and self._is_combat_critical(snapshot):
            return self._record_observe(priority, "combat_guard", snapshot)

        # Max consecutive skip limit
        if self._consecutive_skips >= self._max_consecutive_skips:
            return self._record_observe(priority, "max_skip_limit", snapshot)

        # Normal: compare priority to adaptive threshold
        if priority >= self._current_threshold:
            return self._record_observe(priority, "above_threshold", snapshot)
        else:
            return self._record_skip(priority, "below_threshold", snapshot)

    def get_skip_metadata(self) -> dict[str, Any]:
        """Return metadata about the last decision (for skip marker logging)."""
        return {
            "priority": round(self._last_priority, 3),
            "reason": self._last_reason,
            "threshold": round(self._current_threshold, 3),
            "observation_rate": self._observation_rate(),
        }

    def get_stats(self) -> dict[str, Any]:
        """Return cumulative stats for the game summary."""
        return {
            "budget": self._budget,
            "total_opportunities": self._total_opportunities,
            "total_observed": self._total_observed,
            "total_skipped": self._total_skipped,
            "observation_rate": round(self._observation_rate(), 4),
            "skip_rate": round(1.0 - self._observation_rate(), 4),
            "bo_observed": self._bo_observed,
            "bo_skipped": self._bo_skipped,
            "post_bo_observed": self._post_bo_observed,
            "post_bo_skipped": self._post_bo_skipped,
            "final_threshold": round(self._current_threshold, 3),
        }

    # ------------------------------------------------------------------
    # Priority scoring
    # ------------------------------------------------------------------

    def _compute_priority(
        self, snapshot: dict[str, Any], delta: dict[str, Any]
    ) -> float:
        """Compute composite priority score for this frame."""
        raw_surprise = delta.get("surprise_score", 0.0)

        # 1. Production noise discount
        own_delta = delta.get("own_units_delta", {})
        production_noise = 0.0
        for unit_type, count in own_delta.items():
            if unit_type in _PRODUCTION_NOISE_TYPES:
                production_noise += abs(count) * _W_OWN_UNIT
        adjusted_surprise = max(
            raw_surprise - production_noise * self._production_discount, 0.0
        )

        # 2. Phase-aware weighting
        in_build_order = not snapshot.get("build_order_completed", True)
        phase_factor = 0.5 if in_build_order else 1.0

        # 3. Combat mode change bonus
        combat_mode = snapshot.get("combat_mode", "IDLE")
        mode_changed = (
            self._prev_combat_mode is not None
            and combat_mode != self._prev_combat_mode
        )
        combat_bonus = 10.0 if mode_changed else 0.0

        # 4. Threat appearance bonus
        threat_count = snapshot.get("ground_threat_count", 0) + snapshot.get(
            "air_threat_count", 0
        )
        threat_appeared = threat_count > 0 and self._prev_threat_count == 0
        threat_bonus = 10.0 if threat_appeared else 0.0

        # 5. Enemy change bonus
        enemy_bonus = 0.0
        if delta.get("enemy_units_delta"):
            enemy_bonus += 3.0
        if delta.get("enemy_structures_delta"):
            enemy_bonus += 5.0

        return (
            adjusted_surprise * phase_factor
            + combat_bonus
            + threat_bonus
            + enemy_bonus
        )

    # ------------------------------------------------------------------
    # Combat guard
    # ------------------------------------------------------------------

    def _is_combat_critical(self, snapshot: dict[str, Any]) -> bool:
        """Check if this frame has a critical combat transition."""
        combat_mode = snapshot.get("combat_mode", "IDLE")
        threat_count = snapshot.get("ground_threat_count", 0) + snapshot.get(
            "air_threat_count", 0
        )

        mode_changed = (
            self._prev_combat_mode is not None
            and combat_mode != self._prev_combat_mode
        )
        threat_appeared = threat_count > 0 and self._prev_threat_count == 0

        # Enemy structure scouting reveal
        delta = snapshot.get("delta")
        scouting_reveal = (
            delta is not None and bool(delta.get("enemy_structures_delta"))
        )

        return mode_changed or threat_appeared or scouting_reveal

    # ------------------------------------------------------------------
    # Adaptive threshold
    # ------------------------------------------------------------------

    def _update_threshold(self, priority: float) -> None:
        """Update adaptive threshold based on recent priority distribution."""
        self._priority_window.append(priority)

        if len(self._priority_window) < 10:
            self._current_threshold = 0.0
            return

        sorted_priorities = sorted(self._priority_window)
        skip_fraction = 1.0 - self._budget

        # Feedback: adjust based on current vs target rate
        current_rate = self._observation_rate()
        rate_error = current_rate - self._budget
        adjusted_skip = min(
            max(skip_fraction + rate_error * 0.3, 0.0), 0.95
        )

        idx = int(adjusted_skip * (len(sorted_priorities) - 1))
        self._current_threshold = sorted_priorities[idx]

    # ------------------------------------------------------------------
    # Recording helpers
    # ------------------------------------------------------------------

    def _record_observe(
        self, priority: float, reason: str, snapshot: dict[str, Any]
    ) -> bool:
        self._total_observed += 1
        self._consecutive_skips = 0
        self._last_priority = priority
        self._last_reason = reason
        self._update_signal_state(snapshot)
        self._update_phase_counters(snapshot, observed=True)
        return True

    def _record_skip(
        self, priority: float, reason: str, snapshot: dict[str, Any]
    ) -> bool:
        self._total_skipped += 1
        self._consecutive_skips += 1
        self._last_priority = priority
        self._last_reason = reason
        self._update_signal_state(snapshot)
        self._update_phase_counters(snapshot, observed=False)
        return False

    def _update_signal_state(self, snapshot: dict[str, Any]) -> None:
        """Update tracked state for next frame's guard checks."""
        self._prev_combat_mode = snapshot.get("combat_mode", "IDLE")
        self._prev_threat_count = snapshot.get(
            "ground_threat_count", 0
        ) + snapshot.get("air_threat_count", 0)

    def _update_phase_counters(
        self, snapshot: dict[str, Any], observed: bool
    ) -> None:
        in_bo = not snapshot.get("build_order_completed", True)
        if in_bo:
            if observed:
                self._bo_observed += 1
            else:
                self._bo_skipped += 1
        else:
            if observed:
                self._post_bo_observed += 1
            else:
                self._post_bo_skipped += 1

    def _observation_rate(self) -> float:
        if self._total_opportunities == 0:
            return 1.0
        return self._total_observed / self._total_opportunities
