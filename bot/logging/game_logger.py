"""Core game logger: buffered JSONL writing with async flush.

Public API used by bot modules:
    logger.on_step(iteration)          — called every step
    logger.log_decision(module, ...)   — called by macro/combat/queens
    logger.log_event(event, ...)       — called from lifecycle hooks
    logger.log_building_completed(...) — tracks tech timestamps
    logger.track_attack_state(...)     — tracks first attack + transitions
    logger.on_game_end(result)         — writes summary, final flush

Internal:
    - deque write buffer, json.dumps with compact separators
    - Background thread flush every N steps (never blocks on_step)
    - Sync flush on game end
    - Master on/off via config
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from datetime import datetime
from typing import TYPE_CHECKING, Any

from bot.logging.delta import DeltaTracker
from bot.logging.snapshot import collect_state_snapshot

if TYPE_CHECKING:
    from ares import AresBot


class GameLogger:
    """Buffered JSONL logger for SC2 game state and decisions."""

    def __init__(self, ai: AresBot, config: dict) -> None:
        self.ai = ai
        self._enabled: bool = True
        self._decision_tracing: bool = True
        self._event_logging: bool = True
        self._delta_tracking: bool = True
        self._snapshot_interval: int = 5
        self._flush_interval: int = 50

        # Read logging config
        log_config = config.get("Logging", {})
        if log_config:
            self._enabled = log_config.get("Enabled", True)
            self._snapshot_interval = log_config.get("StateSnapshotInterval", 5)
            self._decision_tracing = log_config.get("DecisionTracing", True)
            self._event_logging = log_config.get("EventLogging", True)
            self._flush_interval = log_config.get("FlushInterval", 50)
            self._delta_tracking = log_config.get("DeltaTracking", True)

        if not self._enabled:
            return

        # Set up output file
        log_dir = log_config.get("LogDirectory", "data/logs")
        os.makedirs(log_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        map_name = getattr(ai, "game_info", None)
        if map_name and hasattr(map_name, "map_name"):
            map_name = map_name.map_name.replace(" ", "_")
        else:
            map_name = "unknown_map"
        self._filepath = os.path.join(log_dir, f"{timestamp}_{map_name}.jsonl")

        # Write buffer
        self._buffer: deque[str] = deque()
        self._lock = threading.Lock()
        self._flush_thread: threading.Thread | None = None

        # Delta tracker
        self._delta_tracker = DeltaTracker() if self._delta_tracking else None

        # Info-gain observation scheduler (Phase 3)
        self._scheduler = None
        ig_config = config.get("InfoGain", {})
        if ig_config.get("Enabled", False):
            from bot.info_gain.scheduler import ObservationScheduler
            self._scheduler = ObservationScheduler(ig_config)

        # Summary accumulators
        self._start_time = time.time()
        self._total_snapshots = 0
        self._total_decisions = 0
        self._total_events = 0
        self._surprise_scores: list[float] = []
        self._peak_supply = 0
        self._peak_army_supply = 0
        self._peak_worker_count = 0
        self._units_produced: dict[str, int] = {}
        self._units_lost: dict[str, int] = {}
        self._enemy_units_killed: dict[str, int] = {}
        self._structures_built: dict[str, int] = {}
        self._structures_lost: dict[str, int] = {}
        self._build_order_completed_time: float | None = None
        self._first_attack_time: float | None = None
        self._first_expansion_time: float | None = None
        self._was_attacking: bool = False
        self._build_order_was_completed: bool = False

        # Tag -> unit type name mapping (for destroyed events)
        self._tag_type_map: dict[int, str] = {}

        # First-observation tracking (Phase 2: opponent modeling)
        self._enemy_types_first_logged: set[str] = set()

        # Enemy tag tracking (Phase 3: sighting + kill resolution)
        self._enemy_tags_seen: dict[int, str] = {}  # tag -> unit_type_name

        # Combat stats for summary
        self._total_engagements: int = 0
        self._engagements_won: int = 0
        self._engagements_lost: int = 0
        self._time_in_chase: float = 0.0
        self._time_in_attack: float = 0.0
        self._time_in_retreat: float = 0.0
        self._time_in_regroup: float = 0.0
        self._army_wipes: int = 0
        self._last_combat_mode: str | None = None
        self._last_combat_mode_time: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def on_step(self, iteration: int) -> None:
        """Called every game step. Subsamples snapshots and flushes buffer."""
        if not self._enabled:
            return

        ai = self.ai

        # Track peak values
        supply = ai.supply_used
        workers = ai.supply_workers
        army = supply - workers
        if supply > self._peak_supply:
            self._peak_supply = supply
        if army > self._peak_army_supply:
            self._peak_army_supply = army
        if workers > self._peak_worker_count:
            self._peak_worker_count = workers

        # Track build order completion
        if not self._build_order_was_completed:
            try:
                if ai.build_order_runner.build_completed:
                    self._build_order_was_completed = True
                    self._build_order_completed_time = ai.time
            except Exception:
                pass

        # Track first expansion
        if self._first_expansion_time is None and len(ai.townhalls) >= 2:
            # Check if any townhall besides start is ready/building
            for th in ai.townhalls:
                if th.position != ai.start_location:
                    self._first_expansion_time = ai.time
                    break

        # Track new enemy sightings (before snapshot so delta captures them)
        self.track_enemy_sightings()

        # State snapshot (subsampled)
        if iteration % self._snapshot_interval == 0:
            snapshot = collect_state_snapshot(ai, iteration)

            # Compute delta
            if self._delta_tracker is not None:
                delta = self._delta_tracker.compute(snapshot)
                snapshot["delta"] = delta
                if delta and "surprise_score" in delta:
                    self._surprise_scores.append(delta["surprise_score"])
            else:
                delta = None
                snapshot["delta"] = None

            # Info-gain scheduler: decide whether to write full snapshot
            if self._scheduler is not None:
                if self._scheduler.should_observe(snapshot, delta):
                    self._write(snapshot)
                    self._total_snapshots += 1
                else:
                    # Write lightweight skip marker
                    skip_meta = self._scheduler.get_skip_metadata()
                    self._write({
                        "type": "skip",
                        "game_time": round(ai.time, 1),
                        "step": iteration,
                        "surprise_score": delta["surprise_score"] if delta and "surprise_score" in delta else 0.0,
                        "priority": skip_meta.get("priority", 0.0),
                        "reason": skip_meta.get("reason", ""),
                        "threshold": skip_meta.get("threshold", 0.0),
                    })
            else:
                self._write(snapshot)
                self._total_snapshots += 1

        # Periodic flush
        if iteration % self._flush_interval == 0 and iteration > 0:
            self._async_flush()

    def log_decision(
        self, module: str, action: str, details: dict[str, Any] | None = None
    ) -> None:
        """Log a decision trace from a bot module."""
        if not self._enabled or not self._decision_tracing:
            return

        record = {
            "type": "decision",
            "game_time": round(self.ai.time, 1),
            "step": self.ai.state.game_loop,
            "module": module,
            "action": action,
            "details": details or {},
        }
        self._write(record)
        self._total_decisions += 1

    def log_event(
        self,
        event: str,
        unit_type: str,
        unit_tag: int,
        is_own: bool = True,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Log a discrete game event."""
        if not self._enabled or not self._event_logging:
            return

        # Maintain tag -> type mapping
        if event in ("unit_created", "building_started"):
            self._tag_type_map[unit_tag] = unit_type

        record = {
            "type": "event",
            "game_time": round(self.ai.time, 1),
            "step": self.ai.state.game_loop,
            "event": event,
            "unit_type": unit_type,
            "unit_tag": unit_tag,
            "is_own": is_own,
        }
        if extra:
            record["extra"] = extra

        self._write(record)
        self._total_events += 1

        # Accumulate summary stats
        if is_own:
            if event == "unit_created":
                self._units_produced[unit_type] = (
                    self._units_produced.get(unit_type, 0) + 1
                )
            elif event == "unit_destroyed":
                self._units_lost[unit_type] = (
                    self._units_lost.get(unit_type, 0) + 1
                )
            elif event == "building_started":
                self._structures_built[unit_type] = (
                    self._structures_built.get(unit_type, 0) + 1
                )
        else:
            if event == "unit_destroyed":
                self._enemy_units_killed[unit_type] = (
                    self._enemy_units_killed.get(unit_type, 0) + 1
                )

    def log_building_completed(self, unit_type: str, unit_tag: int) -> None:
        """Track building completion — useful for tech timestamps."""
        self.log_event(
            event="building_completed",
            unit_type=unit_type,
            unit_tag=unit_tag,
            is_own=True,
            extra={"game_time": round(self.ai.time, 1)},
        )

    def track_attack_state(self, is_attacking: bool) -> None:
        """Track attack state transitions and first attack time."""
        if not self._enabled:
            return

        if is_attacking and not self._was_attacking:
            if self._first_attack_time is None:
                self._first_attack_time = self.ai.time
            self.log_decision(
                "combat",
                "attack_state_change",
                {"new_state": "attacking", "game_time": round(self.ai.time, 1)},
            )
        elif not is_attacking and self._was_attacking:
            self.log_decision(
                "combat",
                "attack_state_change",
                {"new_state": "retreating", "game_time": round(self.ai.time, 1)},
            )
        self._was_attacking = is_attacking

    def track_combat_mode(self, mode_name: str) -> None:
        """Track time spent in each combat mode for game summary."""
        if not self._enabled:
            return

        now = self.ai.time
        if self._last_combat_mode is not None:
            elapsed = now - self._last_combat_mode_time
            if self._last_combat_mode == "CHASE":
                self._time_in_chase += elapsed
            elif self._last_combat_mode == "ATTACK":
                self._time_in_attack += elapsed
            elif self._last_combat_mode == "RETREAT":
                self._time_in_retreat += elapsed
            elif self._last_combat_mode == "REGROUP":
                self._time_in_regroup += elapsed

        self._last_combat_mode = mode_name
        self._last_combat_mode_time = now

    def track_army_wipe(self) -> None:
        """Increment army wipe counter for game summary."""
        if not self._enabled:
            return
        self._army_wipes += 1

    def track_engagement_result(self, won: bool) -> None:
        """Track engagement win/loss for game summary."""
        if not self._enabled:
            return
        self._total_engagements += 1
        if won:
            self._engagements_won += 1
        else:
            self._engagements_lost += 1

    def get_unit_type_for_tag(self, tag: int) -> str:
        """Look up unit type name from our tag maps (own + enemy)."""
        result = self._tag_type_map.get(tag)
        if result:
            return result
        result = self._enemy_tags_seen.get(tag)
        if result:
            return result
        return "UNKNOWN"

    def track_enemy_sightings(self) -> None:
        """Diff visible enemy units against known tags. Log new sightings."""
        if not self._enabled or not self._event_logging:
            return

        ai = self.ai
        try:
            all_enemy = ai.all_enemy_units
            if not all_enemy:
                return
        except Exception:
            return

        for unit in all_enemy:
            tag = unit.tag
            if tag not in self._enemy_tags_seen:
                type_name = unit.type_id.name
                self._enemy_tags_seen[tag] = type_name
                pos = [round(unit.position.x, 1), round(unit.position.y, 1)]
                self.log_event(
                    event="enemy_sighted",
                    unit_type=type_name,
                    unit_tag=tag,
                    is_own=False,
                    extra={"position": pos},
                )

    def log_first_observation(
        self,
        unit_type: str,
        game_time: float,
        position: list[float] | None = None,
    ) -> None:
        """Log first observation of an enemy unit type. Once per type per game."""
        if not self._enabled or not self._event_logging:
            return
        if unit_type in self._enemy_types_first_logged:
            return
        self._enemy_types_first_logged.add(unit_type)

        record: dict[str, Any] = {
            "type": "event",
            "game_time": round(game_time, 1),
            "step": self.ai.state.game_loop,
            "event": "first_observed",
            "unit_type": unit_type,
            "unit_tag": 0,
            "is_own": False,
        }
        if position:
            record["extra"] = {"position": position}
        self._write(record)
        self._total_events += 1

    def on_game_end(self, result: str) -> None:
        """Write game summary and perform final flush."""
        if not self._enabled:
            return

        ai = self.ai
        real_duration = time.time() - self._start_time

        # Compute surprise stats
        avg_surprise = 0.0
        max_surprise = 0.0
        if self._surprise_scores:
            avg_surprise = round(
                sum(self._surprise_scores) / len(self._surprise_scores), 3
            )
            max_surprise = round(max(self._surprise_scores), 3)

        summary = {
            "type": "summary",
            "game_time": round(ai.time, 1),
            "real_duration": round(real_duration, 1),
            "result": result,
            "map_name": getattr(ai.game_info, "map_name", "unknown"),
            "enemy_race": str(getattr(ai, "enemy_race", "unknown")),
            "enemy_name": getattr(ai, "_opponent_name", "unknown"),
            # Cumulative stats
            "units_produced": self._units_produced,
            "units_lost": self._units_lost,
            "enemy_units_killed": self._enemy_units_killed,
            "structures_built": self._structures_built,
            "structures_lost": self._structures_lost,
            # Key timestamps
            "build_order_completed_time": self._build_order_completed_time,
            "first_attack_time": self._first_attack_time,
            "first_expansion_time": self._first_expansion_time,
            # Peak values
            "peak_supply": self._peak_supply,
            "peak_army_supply": self._peak_army_supply,
            "peak_worker_count": self._peak_worker_count,
            # Combat stats
            "total_engagements": self._total_engagements,
            "engagements_won": self._engagements_won,
            "engagements_lost": self._engagements_lost,
            "time_in_chase": round(self._time_in_chase, 1),
            "time_in_attack": round(self._time_in_attack, 1),
            "time_in_retreat": round(self._time_in_retreat, 1),
            "time_in_regroup": round(self._time_in_regroup, 1),
            "army_wipes": self._army_wipes,
            # Enemy tracking
            "enemy_tags_tracked": len(self._enemy_tags_seen),
            # Logging meta
            "total_snapshots": self._total_snapshots,
            "total_decisions": self._total_decisions,
            "total_events": self._total_events,
            "avg_surprise_score": avg_surprise,
            "max_surprise_score": max_surprise,
        }

        # Try to get resource totals
        try:
            summary["total_minerals_collected"] = ai.state.score.collected_minerals
            summary["total_vespene_collected"] = ai.state.score.collected_vespene
        except Exception:
            pass

        # Info-gain scheduler stats
        if self._scheduler is not None:
            summary["info_gain"] = self._scheduler.get_stats()

        # Discovery timeline (opponent modeling)
        try:
            analyzer = self.ai.strategic_analyzer
            summary["discovery_timeline"] = [
                {"time": round(t, 1), "unit_type": name, "peak_count": count}
                for t, name, count in analyzer.memory.get_discovery_timeline()
            ]
        except Exception:
            pass

        # Engagement tracker stats
        try:
            eng_tracker = self.ai._engagement_tracker
            summary["engagement_tracker"] = eng_tracker.get_stats()
        except Exception:
            pass

        self._write(summary)
        self._sync_flush()

    # ------------------------------------------------------------------
    # Internal I/O
    # ------------------------------------------------------------------

    def _write(self, record: dict) -> None:
        """Serialize and append to buffer. Never blocks."""
        try:
            line = json.dumps(record, separators=(",", ":"))
            with self._lock:
                self._buffer.append(line)
        except Exception:
            pass  # Never crash the bot for logging

    def _sync_flush(self) -> None:
        """Flush buffer to disk synchronously. Used on game end."""
        with self._lock:
            lines = list(self._buffer)
            self._buffer.clear()

        if not lines:
            return

        try:
            with open(self._filepath, "a", encoding="utf-8") as f:
                for line in lines:
                    f.write(line)
                    f.write("\n")
        except Exception:
            pass

    def _async_flush(self) -> None:
        """Flush buffer to disk in a background thread."""
        # Don't spawn if a flush is already running
        if self._flush_thread is not None and self._flush_thread.is_alive():
            return

        with self._lock:
            if not self._buffer:
                return
            lines = list(self._buffer)
            self._buffer.clear()

        def _do_flush():
            try:
                with open(self._filepath, "a", encoding="utf-8") as f:
                    for line in lines:
                        f.write(line)
                        f.write("\n")
            except Exception:
                pass

        self._flush_thread = threading.Thread(target=_do_flush, daemon=True)
        self._flush_thread.start()
