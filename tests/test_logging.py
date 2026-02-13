"""Unit tests for the logging system.

Tests DeltaTracker, GameLogger, and snapshot helpers — all without SC2.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time

import pytest

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bot.logging.delta import DeltaTracker, _dict_delta, _point_distance


# =============================================================================
# Helper: create a snapshot dict for testing
# =============================================================================

def make_snapshot(
    minerals=400,
    vespene=150,
    supply_used=45,
    supply_cap=60,
    larva_count=3,
    own_units=None,
    enemy_units=None,
    own_structures=None,
    enemy_structures=None,
    own_army_center=None,
    enemy_army_center=None,
    upgrades=None,
):
    """Create a minimal snapshot dict for delta tests."""
    return {
        "minerals": minerals,
        "vespene": vespene,
        "supply_used": supply_used,
        "supply_cap": supply_cap,
        "larva_count": larva_count,
        "own_units": own_units or {},
        "enemy_units": enemy_units or {},
        "own_structures": own_structures or {"HATCHERY": 2},
        "enemy_structures": enemy_structures or {},
        "own_army_center": own_army_center or [50.0, 50.0],
        "enemy_army_center": enemy_army_center or [100.0, 100.0],
        "upgrades": upgrades or [],
    }


# =============================================================================
# DeltaTracker Tests
# =============================================================================

class TestDeltaTracker:

    def test_first_frame_returns_none(self):
        dt = DeltaTracker()
        result = dt.compute(make_snapshot())
        assert result is None

    def test_computes_economy_changes(self):
        dt = DeltaTracker()
        dt.compute(make_snapshot(minerals=500, vespene=200))
        delta = dt.compute(make_snapshot(minerals=600, vespene=150))
        assert delta is not None
        assert delta["economy_delta"]["minerals"] == 100
        assert delta["economy_delta"]["vespene"] == -50

    def test_computes_unit_changes(self):
        dt = DeltaTracker()
        dt.compute(make_snapshot(own_units={"ZERGLING": 12, "ROACH": 4}))
        delta = dt.compute(make_snapshot(own_units={"ZERGLING": 16, "ROACH": 6}))
        assert delta["own_units_delta"] == {"ZERGLING": 4, "ROACH": 2}

    def test_computes_enemy_unit_changes(self):
        dt = DeltaTracker()
        dt.compute(make_snapshot(enemy_units={"MARINE": 8}))
        delta = dt.compute(make_snapshot(enemy_units={"MARINE": 6, "MEDIVAC": 2}))
        assert delta["enemy_units_delta"] == {"MARINE": -2, "MEDIVAC": 2}

    def test_computes_structure_changes(self):
        dt = DeltaTracker()
        dt.compute(make_snapshot(own_structures={"HATCHERY": 2}))
        delta = dt.compute(make_snapshot(own_structures={"HATCHERY": 2, "ROACHWARREN": 1}))
        assert delta["own_structures_delta"] == {"ROACHWARREN": 1}

    def test_computes_enemy_structure_changes(self):
        dt = DeltaTracker()
        dt.compute(make_snapshot(enemy_structures={}))
        delta = dt.compute(make_snapshot(enemy_structures={"BARRACKS": 1}))
        assert delta["enemy_structures_delta"] == {"BARRACKS": 1}

    def test_tracks_army_movement(self):
        dt = DeltaTracker()
        dt.compute(make_snapshot(own_army_center=[50.0, 50.0]))
        delta = dt.compute(make_snapshot(own_army_center=[53.0, 54.0]))
        expected = math.sqrt(9 + 16)  # 5.0
        assert abs(delta["own_army_moved"] - expected) < 0.01

    def test_tracks_enemy_army_movement(self):
        dt = DeltaTracker()
        dt.compute(make_snapshot(enemy_army_center=[100.0, 100.0]))
        delta = dt.compute(make_snapshot(enemy_army_center=[95.0, 98.0]))
        expected = math.sqrt(25 + 4)  # ~5.39
        assert abs(delta["enemy_army_moved"] - expected) < 0.01

    def test_detects_new_upgrades(self):
        dt = DeltaTracker()
        dt.compute(make_snapshot(upgrades=[]))
        delta = dt.compute(make_snapshot(upgrades=["ZERGMISSILEWEAPONSLEVEL1"]))
        assert delta["new_upgrades"] == ["ZERGMISSILEWEAPONSLEVEL1"]

    def test_detects_multiple_new_upgrades(self):
        dt = DeltaTracker()
        dt.compute(make_snapshot(upgrades=["UPGRADE_A"]))
        delta = dt.compute(make_snapshot(upgrades=["UPGRADE_A", "UPGRADE_B", "UPGRADE_C"]))
        assert sorted(delta["new_upgrades"]) == ["UPGRADE_B", "UPGRADE_C"]

    def test_surprise_score_positive(self):
        dt = DeltaTracker()
        dt.compute(make_snapshot(minerals=500))
        delta = dt.compute(make_snapshot(minerals=600, own_units={"ZERGLING": 4}))
        assert delta["surprise_score"] > 0

    def test_surprise_weights_enemy_higher(self):
        """Same unit count change should yield higher surprise for enemy."""
        # Own unit change
        dt1 = DeltaTracker()
        dt1.compute(make_snapshot(own_units={}))
        delta1 = dt1.compute(make_snapshot(own_units={"ZERGLING": 5}))

        # Enemy unit change (same count)
        dt2 = DeltaTracker()
        dt2.compute(make_snapshot(enemy_units={}))
        delta2 = dt2.compute(make_snapshot(enemy_units={"MARINE": 5}))

        # Enemy should contribute more surprise (2x weight)
        # Both also have zero economy change, but the unit part should differ
        assert delta2["surprise_score"] > delta1["surprise_score"]

    def test_no_change_zero_surprise(self):
        dt = DeltaTracker()
        snap = make_snapshot()
        dt.compute(snap)
        delta = dt.compute(make_snapshot())  # identical
        assert delta["surprise_score"] == 0.0

    def test_handles_none_positions(self):
        dt = DeltaTracker()
        dt.compute(make_snapshot(own_army_center=None))
        delta = dt.compute(make_snapshot(own_army_center=[50.0, 50.0]))
        assert delta["own_army_moved"] == 0.0

    def test_handles_both_none_positions(self):
        dt = DeltaTracker()
        dt.compute(make_snapshot(own_army_center=None, enemy_army_center=None))
        delta = dt.compute(make_snapshot(own_army_center=None, enemy_army_center=None))
        assert delta["own_army_moved"] == 0.0
        assert delta["enemy_army_moved"] == 0.0


# =============================================================================
# Helper function tests
# =============================================================================

class TestHelperFunctions:

    def test_dict_delta_basic(self):
        prev = {"A": 5, "B": 3}
        curr = {"A": 7, "B": 3, "C": 2}
        delta = _dict_delta(prev, curr)
        assert delta == {"A": 2, "C": 2}  # B unchanged → not in delta

    def test_dict_delta_removal(self):
        prev = {"A": 5}
        curr = {}
        delta = _dict_delta(prev, curr)
        assert delta == {"A": -5}

    def test_dict_delta_empty(self):
        assert _dict_delta({}, {}) == {}

    def test_point_distance_basic(self):
        dist = _point_distance([0.0, 0.0], [3.0, 4.0])
        assert abs(dist - 5.0) < 0.001

    def test_point_distance_none_first(self):
        assert _point_distance(None, [1.0, 2.0]) == 0.0

    def test_point_distance_none_second(self):
        assert _point_distance([1.0, 2.0], None) == 0.0

    def test_point_distance_both_none(self):
        assert _point_distance(None, None) == 0.0

    def test_point_distance_same_point(self):
        assert _point_distance([5.0, 5.0], [5.0, 5.0]) == 0.0


# =============================================================================
# GameLogger Tests
# =============================================================================

class TestGameLogger:

    def test_creates_file_on_game_end(self, mock_bot, log_dir):
        from bot.logging.game_logger import GameLogger

        logger = GameLogger(mock_bot, mock_bot.config)
        logger.log_decision("test", "test_action", {"key": "value"})
        logger.on_game_end("Victory")

        files = list(log_dir.glob("*.jsonl"))
        assert len(files) == 1, f"Expected 1 JSONL file, found {len(files)}"
        assert files[0].stat().st_size > 0

    def test_disabled_writes_nothing(self, mock_bot, log_dir):
        from bot.logging.game_logger import GameLogger

        mock_bot.config["Logging"]["Enabled"] = False
        logger = GameLogger(mock_bot, mock_bot.config)
        logger.log_decision("test", "test_action", {"key": "value"})
        logger.on_game_end("Victory")

        files = list(log_dir.glob("*.jsonl"))
        assert len(files) == 0

    def test_decision_tracing(self, mock_bot, log_dir):
        from bot.logging.game_logger import GameLogger

        logger = GameLogger(mock_bot, mock_bot.config)
        logger.log_decision("macro", "build_tech_structure", {
            "structure": "ROACHWARREN",
            "trigger": "pool_ready_3min",
        })
        logger.on_game_end("Victory")

        records = _read_jsonl(log_dir)
        decisions = [r for r in records if r["type"] == "decision"]
        assert len(decisions) >= 1
        d = decisions[0]
        assert d["module"] == "macro"
        assert d["action"] == "build_tech_structure"
        assert d["details"]["structure"] == "ROACHWARREN"

    def test_event_tracking(self, mock_bot, log_dir):
        from bot.logging.game_logger import GameLogger

        logger = GameLogger(mock_bot, mock_bot.config)
        logger.log_event("unit_created", "ZERGLING", 42, is_own=True)
        logger.log_event("unit_destroyed", "MARINE", 99, is_own=False)
        logger.on_game_end("Victory")

        records = _read_jsonl(log_dir)
        events = [r for r in records if r["type"] == "event"]
        assert len(events) == 2
        assert events[0]["unit_type"] == "ZERGLING"
        assert events[0]["is_own"] is True
        assert events[1]["unit_type"] == "MARINE"
        assert events[1]["is_own"] is False

    def test_tag_type_map(self, mock_bot, log_dir):
        from bot.logging.game_logger import GameLogger

        logger = GameLogger(mock_bot, mock_bot.config)
        logger.log_event("unit_created", "ROACH", 555, is_own=True)
        assert logger.get_unit_type_for_tag(555) == "ROACH"
        assert logger.get_unit_type_for_tag(999) == "UNKNOWN"

    def test_attack_state_tracking(self, mock_bot, log_dir):
        from bot.logging.game_logger import GameLogger

        logger = GameLogger(mock_bot, mock_bot.config)
        assert logger._first_attack_time is None

        logger.track_attack_state(True)
        assert logger._first_attack_time == mock_bot.time

        logger.track_attack_state(True)  # no change → no log
        logger.track_attack_state(False)  # retreat

        logger.on_game_end("Defeat")

        records = _read_jsonl(log_dir)
        state_changes = [
            r for r in records
            if r["type"] == "decision" and r["action"] == "attack_state_change"
        ]
        assert len(state_changes) == 2  # attacking, then retreating
        assert state_changes[0]["details"]["new_state"] == "attacking"
        assert state_changes[1]["details"]["new_state"] == "retreating"

    def test_summary_has_required_fields(self, mock_bot, log_dir):
        from bot.logging.game_logger import GameLogger

        logger = GameLogger(mock_bot, mock_bot.config)
        logger.log_event("unit_created", "ZERGLING", 1, is_own=True)
        logger.log_event("unit_created", "ZERGLING", 2, is_own=True)
        logger.log_decision("macro", "expand", {})
        logger.on_game_end("Victory")

        records = _read_jsonl(log_dir)
        summaries = [r for r in records if r["type"] == "summary"]
        assert len(summaries) == 1
        s = summaries[0]

        required = [
            "game_time", "real_duration", "result", "map_name",
            "units_produced", "units_lost", "peak_supply",
            "total_snapshots", "total_decisions", "total_events",
            "avg_surprise_score", "max_surprise_score",
        ]
        for field in required:
            assert field in s, f"Missing summary field: {field}"

        assert s["result"] == "Victory"
        assert s["units_produced"]["ZERGLING"] == 2
        assert s["total_decisions"] >= 1
        assert s["total_events"] >= 2

    def test_jsonl_format_valid(self, mock_bot, log_dir):
        from bot.logging.game_logger import GameLogger

        logger = GameLogger(mock_bot, mock_bot.config)
        logger.log_decision("combat", "attack", {"target": "enemy_nat"})
        logger.log_event("unit_created", "ROACH", 100)
        logger.log_event("building_started", "SPAWNINGPOOL", 200)
        logger.on_game_end("Victory")

        files = list(log_dir.glob("*.jsonl"))
        assert len(files) == 1

        with open(files[0]) as f:
            for i, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    assert "type" in record, f"Line {i} missing 'type' field"
                except json.JSONDecodeError as e:
                    pytest.fail(f"Line {i} is invalid JSON: {e}\n  Content: {line[:200]}")

    def test_on_step_subsamples_snapshots(self, mock_bot, log_dir):
        """Verify snapshots only happen at interval multiples."""
        from bot.logging.game_logger import GameLogger

        mock_bot.config["Logging"]["StateSnapshotInterval"] = 5
        logger = GameLogger(mock_bot, mock_bot.config)

        # Call on_step for iterations 0..9
        for i in range(10):
            mock_bot.time = 10.0 + i * 0.1
            logger.on_step(i)

        logger.on_game_end("Victory")

        records = _read_jsonl(log_dir)
        snapshots = [r for r in records if r["type"] == "state"]
        # Iterations 0 and 5 should produce snapshots (0%5==0, 5%5==0)
        assert len(snapshots) == 2

    def test_building_completed_event(self, mock_bot, log_dir):
        from bot.logging.game_logger import GameLogger

        logger = GameLogger(mock_bot, mock_bot.config)
        logger.log_building_completed("SPAWNINGPOOL", 300)
        logger.on_game_end("Victory")

        records = _read_jsonl(log_dir)
        events = [r for r in records if r["type"] == "event"]
        completed = [e for e in events if e["event"] == "building_completed"]
        assert len(completed) == 1
        assert completed[0]["unit_type"] == "SPAWNINGPOOL"

    def test_peak_tracking(self, mock_bot, log_dir):
        """Verify peak supply/army/worker tracking."""
        from bot.logging.game_logger import GameLogger

        logger = GameLogger(mock_bot, mock_bot.config)

        # Simulate supply changes over multiple steps
        for i in range(5):
            mock_bot.supply_used = 40 + i * 10
            mock_bot.supply_workers = 30
            mock_bot.time = 60.0 + i * 30
            logger.on_step(i * 5)

        logger.on_game_end("Victory")
        records = _read_jsonl(log_dir)
        summary = [r for r in records if r["type"] == "summary"][0]
        assert summary["peak_supply"] == 80  # 40+4*10
        assert summary["peak_army_supply"] == 50  # 80-30


# =============================================================================
# ObservationScheduler Tests
# =============================================================================

from bot.info_gain.scheduler import ObservationScheduler


def _make_scheduler_snapshot(
    combat_mode="IDLE",
    build_order_completed=True,
    ground_threat_count=0,
    air_threat_count=0,
):
    """Create a minimal snapshot dict for scheduler tests."""
    return {
        "combat_mode": combat_mode,
        "build_order_completed": build_order_completed,
        "ground_threat_count": ground_threat_count,
        "air_threat_count": air_threat_count,
    }


def _make_delta(
    surprise_score=0.5,
    own_units_delta=None,
    enemy_units_delta=None,
    enemy_structures_delta=None,
):
    """Create a minimal delta dict for scheduler tests."""
    return {
        "surprise_score": surprise_score,
        "own_units_delta": own_units_delta or {},
        "enemy_units_delta": enemy_units_delta or {},
        "enemy_structures_delta": enemy_structures_delta or {},
        "own_structures_delta": {},
        "economy_delta": {},
        "own_army_moved": 0.0,
        "enemy_army_moved": 0.0,
        "new_upgrades": [],
    }


class TestObservationScheduler:

    def test_default_budget_observes_all(self):
        """Budget 1.0 should never skip."""
        sched = ObservationScheduler({"Enabled": True, "ObservationBudget": 1.0})
        snap = _make_scheduler_snapshot()
        delta = _make_delta(surprise_score=0.001)
        for _ in range(50):
            assert sched.should_observe(snap, delta) is True

    def test_disabled_observes_all(self):
        """Enabled=False should never skip."""
        sched = ObservationScheduler({"Enabled": False, "ObservationBudget": 0.1})
        snap = _make_scheduler_snapshot()
        delta = _make_delta(surprise_score=0.001)
        for _ in range(50):
            assert sched.should_observe(snap, delta) is True

    def test_first_frame_always_observed(self):
        """delta=None (first frame) always observed."""
        sched = ObservationScheduler({"Enabled": True, "ObservationBudget": 0.1})
        snap = _make_scheduler_snapshot()
        assert sched.should_observe(snap, None) is True

    def test_zero_delta_zero_priority(self):
        """No changes = priority 0."""
        sched = ObservationScheduler({"Enabled": True, "ObservationBudget": 1.0})
        snap = _make_scheduler_snapshot()
        delta = _make_delta(surprise_score=0.0)
        priority = sched._compute_priority(snap, delta)
        assert priority == 0.0

    def test_enemy_changes_boost_priority(self):
        """Enemy unit/structure deltas increase priority."""
        sched = ObservationScheduler({"Enabled": True, "ObservationBudget": 1.0})
        snap = _make_scheduler_snapshot()

        delta_base = _make_delta(surprise_score=1.0)
        delta_enemy = _make_delta(
            surprise_score=1.0, enemy_units_delta={"MARINE": 3}
        )
        delta_struct = _make_delta(
            surprise_score=1.0, enemy_structures_delta={"BARRACKS": 1}
        )

        p_base = sched._compute_priority(snap, delta_base)
        p_enemy = sched._compute_priority(snap, delta_enemy)
        p_struct = sched._compute_priority(snap, delta_struct)

        assert p_enemy > p_base
        assert p_struct > p_base
        assert p_struct > p_enemy

    def test_production_noise_discounted(self):
        """EGG/DRONE/LARVA own_units_delta is discounted."""
        sched = ObservationScheduler({
            "Enabled": True, "ObservationBudget": 1.0, "ProductionDiscount": 0.7,
        })
        snap = _make_scheduler_snapshot()

        delta_eggs = _make_delta(
            surprise_score=8.0, own_units_delta={"EGG": 8}
        )
        delta_real = _make_delta(
            surprise_score=16.0, enemy_units_delta={"MARINE": 8}
        )

        p_eggs = sched._compute_priority(snap, delta_eggs)
        p_real = sched._compute_priority(snap, delta_real)

        assert p_eggs < p_real
        assert p_eggs < 8.0

    def test_combat_mode_change_boosts_priority(self):
        """Priority spikes when combat_mode changes."""
        sched = ObservationScheduler({"Enabled": True, "ObservationBudget": 1.0})

        snap1 = _make_scheduler_snapshot(combat_mode="RETREAT")
        delta = _make_delta(surprise_score=0.1)

        sched.should_observe(snap1, delta)

        snap2 = _make_scheduler_snapshot(combat_mode="ATTACK")
        p = sched._compute_priority(snap2, delta)

        assert p >= 10.0

    def test_threat_appearance_boosts_priority(self):
        """Priority spikes when threat_count goes 0 -> nonzero."""
        sched = ObservationScheduler({"Enabled": True, "ObservationBudget": 1.0})

        snap_safe = _make_scheduler_snapshot(ground_threat_count=0)
        delta = _make_delta(surprise_score=0.1)

        sched.should_observe(snap_safe, delta)

        snap_threat = _make_scheduler_snapshot(ground_threat_count=5)
        p = sched._compute_priority(snap_threat, delta)

        assert p >= 10.0

    def test_build_order_phase_deprioritized(self):
        """Same delta has lower priority during build order."""
        sched = ObservationScheduler({"Enabled": True, "ObservationBudget": 1.0})
        delta = _make_delta(surprise_score=2.0)

        snap_bo = _make_scheduler_snapshot(build_order_completed=False)
        snap_post = _make_scheduler_snapshot(build_order_completed=True)

        p_bo = sched._compute_priority(snap_bo, delta)
        p_post = sched._compute_priority(snap_post, delta)

        assert p_bo < p_post

    def test_half_budget_skips_roughly_half(self):
        """Over 100 frames with budget=0.5, observe ~35-65%."""
        sched = ObservationScheduler({
            "Enabled": True, "ObservationBudget": 0.5,
            "MinObserveInterval": 5,
        })

        import random
        random.seed(42)
        for _ in range(100):
            snap = _make_scheduler_snapshot()
            delta = _make_delta(surprise_score=random.uniform(0.0, 5.0))
            sched.should_observe(snap, delta)

        stats = sched.get_stats()
        rate = stats["observation_rate"]
        assert 0.35 <= rate <= 0.65, f"Rate {rate} outside 35-65% for budget=0.5"

    def test_quarter_budget_skips_roughly_three_quarters(self):
        """Over 100 frames with budget=0.25, observe ~15-40%."""
        sched = ObservationScheduler({
            "Enabled": True, "ObservationBudget": 0.25,
            "MinObserveInterval": 5,
        })

        import random
        random.seed(42)
        for _ in range(100):
            snap = _make_scheduler_snapshot()
            delta = _make_delta(surprise_score=random.uniform(0.0, 5.0))
            sched.should_observe(snap, delta)

        stats = sched.get_stats()
        rate = stats["observation_rate"]
        assert 0.15 <= rate <= 0.40, f"Rate {rate} outside 15-40% for budget=0.25"

    def test_max_consecutive_skip_enforced(self):
        """Never skip more than MinObserveInterval consecutive frames."""
        max_skips = 3
        sched = ObservationScheduler({
            "Enabled": True, "ObservationBudget": 0.01,
            "MinObserveInterval": max_skips,
        })

        consecutive = 0
        max_seen = 0
        for _ in range(200):
            snap = _make_scheduler_snapshot()
            delta = _make_delta(surprise_score=0.001)
            result = sched.should_observe(snap, delta)
            if not result:
                consecutive += 1
                max_seen = max(max_seen, consecutive)
            else:
                consecutive = 0

        assert max_seen <= max_skips, (
            f"Saw {max_seen} consecutive skips, limit is {max_skips}"
        )

    def test_combat_guard_forces_observe(self):
        """RETREAT->ATTACK transition observed even at very low budget."""
        sched = ObservationScheduler({
            "Enabled": True, "ObservationBudget": 0.01,
            "CombatGuard": True, "MinObserveInterval": 100,
        })

        snap1 = _make_scheduler_snapshot(combat_mode="RETREAT")
        delta = _make_delta(surprise_score=0.001)
        sched.should_observe(snap1, delta)

        snap2 = _make_scheduler_snapshot(combat_mode="ATTACK")
        result = sched.should_observe(snap2, delta)
        assert result is True

    def test_stats_accumulate(self):
        """get_stats returns correct counts after multiple calls."""
        sched = ObservationScheduler({
            "Enabled": True, "ObservationBudget": 0.5,
        })

        for i in range(20):
            snap = _make_scheduler_snapshot()
            delta = _make_delta(surprise_score=float(i % 5))
            sched.should_observe(snap, delta)

        stats = sched.get_stats()
        assert stats["total_opportunities"] == 20
        assert stats["total_observed"] + stats["total_skipped"] == 20
        assert stats["budget"] == 0.5

    def test_skip_metadata_returned(self):
        """get_skip_metadata returns priority and reason."""
        sched = ObservationScheduler({
            "Enabled": True, "ObservationBudget": 0.5,
        })

        snap = _make_scheduler_snapshot()
        delta = _make_delta(surprise_score=1.0)
        sched.should_observe(snap, delta)

        meta = sched.get_skip_metadata()
        assert "priority" in meta
        assert "reason" in meta
        assert "threshold" in meta
        assert "observation_rate" in meta


# =============================================================================
# Utility
# =============================================================================

def _read_jsonl(log_dir) -> list[dict]:
    """Read all records from the JSONL file in a directory."""
    files = list(log_dir.glob("*.jsonl"))
    if not files:
        return []
    records = []
    with open(files[0]) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


# =============================================================================
# Phase 1: Enhanced snapshot fields
# =============================================================================


class TestEnhancedSnapshots:
    """Verify new ML-ready fields in state snapshots."""

    def test_snapshot_has_income_rates(self, mock_bot):
        from bot.logging.snapshot import collect_state_snapshot

        snap = collect_state_snapshot(mock_bot, step=10)
        assert snap["income_minerals"] == 1200.0
        assert snap["income_vespene"] == 400.0

    def test_snapshot_has_saturation_ratio(self, mock_bot):
        from bot.logging.snapshot import collect_state_snapshot

        # mock_bot has 1 townhall and 30 workers
        snap = collect_state_snapshot(mock_bot, step=10)
        expected = round(30 / (1 * 16), 3)  # 1.875
        assert snap["saturation_ratio"] == expected

    def test_snapshot_saturation_multi_base(self, mock_bot):
        from bot.logging.snapshot import collect_state_snapshot
        from tests.conftest import MockUnit

        # Add a second townhall
        mock_bot.townhalls = type(mock_bot.townhalls)(
            [MockUnit(1000), MockUnit(1001)]
        )
        snap = collect_state_snapshot(mock_bot, step=10)
        expected = round(30 / (2 * 16), 3)  # 0.938
        assert snap["saturation_ratio"] == expected

    def test_snapshot_income_missing_gracefully(self, mock_bot):
        """If score API lacks income fields, defaults to 0.0."""
        from bot.logging.snapshot import collect_state_snapshot
        from types import SimpleNamespace

        mock_bot.state.score = SimpleNamespace(
            collected_minerals=5000,
            collected_vespene=2000,
        )
        snap = collect_state_snapshot(mock_bot, step=10)
        assert snap["income_minerals"] == 0.0
        assert snap["income_vespene"] == 0.0


class TestFirstObservationLogging:
    """Verify first_observed event logging."""

    def test_first_observation_logged_once(self, mock_bot, log_dir):
        """Same unit type should only produce one first_observed event."""
        from bot.logging.game_logger import GameLogger

        logger = GameLogger(mock_bot, mock_bot.config)

        logger.log_first_observation("MARINE", 60.0, [50.0, 30.0])
        logger.log_first_observation("MARINE", 120.0, [55.0, 35.0])
        logger.log_first_observation("SIEGETANK", 90.0)
        logger.on_game_end("Victory")

        records = _read_jsonl(log_dir)
        first_obs = [
            r for r in records
            if r.get("type") == "event" and r.get("event") == "first_observed"
        ]
        assert len(first_obs) == 2
        types = {r["unit_type"] for r in first_obs}
        assert types == {"MARINE", "SIEGETANK"}
        # First one should have position
        marine = [r for r in first_obs if r["unit_type"] == "MARINE"][0]
        assert marine["extra"]["position"] == [50.0, 30.0]


class TestEnemySightingTracking:
    """Verify enemy tag sighting and kill resolution."""

    def test_enemy_sighting_new_tag(self, mock_bot, log_dir):
        """New enemy tags produce enemy_sighted events."""
        from bot.logging.game_logger import GameLogger
        from tests.conftest import MockUnit, MockUnits
        from types import SimpleNamespace

        logger = GameLogger(mock_bot, mock_bot.config)

        # Simulate visible enemies
        enemy1 = MockUnit(5001, type_id=SimpleNamespace(name="MARINE"))
        enemy2 = MockUnit(5002, type_id=SimpleNamespace(name="SIEGETANK"))
        mock_bot.all_enemy_units = MockUnits([enemy1, enemy2])

        logger.track_enemy_sightings()
        logger.on_game_end("Victory")

        records = _read_jsonl(log_dir)
        sightings = [
            r for r in records
            if r.get("type") == "event" and r.get("event") == "enemy_sighted"
        ]
        assert len(sightings) == 2
        types = {r["unit_type"] for r in sightings}
        assert types == {"MARINE", "SIEGETANK"}

    def test_enemy_sighting_known_tag_no_duplicate(self, mock_bot, log_dir):
        """Known enemy tags do not produce duplicate sighting events."""
        from bot.logging.game_logger import GameLogger
        from tests.conftest import MockUnit, MockUnits
        from types import SimpleNamespace

        logger = GameLogger(mock_bot, mock_bot.config)

        enemy1 = MockUnit(5001, type_id=SimpleNamespace(name="MARINE"))
        mock_bot.all_enemy_units = MockUnits([enemy1])

        logger.track_enemy_sightings()
        logger.track_enemy_sightings()  # Second call, same tag
        logger.on_game_end("Victory")

        records = _read_jsonl(log_dir)
        sightings = [
            r for r in records
            if r.get("type") == "event" and r.get("event") == "enemy_sighted"
        ]
        assert len(sightings) == 1

    def test_enemy_tag_resolves_kill_type(self, mock_bot):
        """get_unit_type_for_tag resolves enemy tags."""
        from bot.logging.game_logger import GameLogger

        logger = GameLogger(mock_bot, mock_bot.config)

        # Manually populate enemy tag map
        logger._enemy_tags_seen[5001] = "MARINE"

        assert logger.get_unit_type_for_tag(5001) == "MARINE"
        assert logger.get_unit_type_for_tag(9999) == "UNKNOWN"

        # Own tags take priority
        logger._tag_type_map[5001] = "ZERGLING"
        assert logger.get_unit_type_for_tag(5001) == "ZERGLING"
