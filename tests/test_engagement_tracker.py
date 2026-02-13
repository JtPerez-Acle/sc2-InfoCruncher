"""Tests for the EngagementTracker (Phase 4 ML logging)."""

from __future__ import annotations

import pytest

from bot.logging.engagement_tracker import (
    DISENGAGE_COOLDOWN,
    EngagementTracker,
    _compute_losses,
    _determine_outcome,
    _snapshot_enemy,
    _snapshot_own_army,
)


# ---------------------------------------------------------------------------
# Helpers — lightweight mocks for unit objects
# ---------------------------------------------------------------------------

class _MockUnit:
    """Minimal unit mock with type_id, supply_cost."""

    def __init__(self, type_name: str, supply_cost: float = 2.0):
        self.type_id = type(
            "TypeId", (), {"name": type_name, "value": hash(type_name)}
        )()
        self.supply_cost = supply_cost


class _MockUnitType:
    """Minimal UnitTypeId stand-in with .name."""

    def __init__(self, name: str):
        self.name = name


def _make_own_army(composition: dict[str, tuple[int, float]]) -> dict:
    """Build own army dict: {type_obj: [unit, ...]} from {name: (count, supply_each)}."""
    army: dict = {}
    for name, (count, supply_each) in composition.items():
        unit_type = _MockUnitType(name)
        army[unit_type] = [_MockUnit(name, supply_each) for _ in range(count)]
    return army


def _make_enemy_units(composition: dict[str, tuple[int, float]]) -> list:
    """Build enemy units list from {name: (count, supply_each)}."""
    units = []
    for name, (count, supply_each) in composition.items():
        for _ in range(count):
            units.append(_MockUnit(name, supply_each))
    return units


# ---------------------------------------------------------------------------
# Default combat frame kwargs
# ---------------------------------------------------------------------------

def _default_frame_kwargs(**overrides) -> dict:
    """Provide sensible defaults for on_combat_frame calls."""
    kw = dict(
        own_army_dict=_make_own_army({"ROACH": (10, 2.0)}),
        enemy_units=_make_enemy_units({"MARINE": (10, 1.0)}),
        sim_result_name="VICTORY_CLOSE",
        micro_style="attack",
        position=[50.0, 50.0],
        game_time=100.0,
        step=1000,
        combat_objective="ATTACK_BASE",
        profile_name="BIO",
        approach="FRONTAL",
        was_committed=True,
        confidence=0.65,
        minerals=400,
        vespene=200,
        income_minerals=1200.0,
        income_vespene=400.0,
        supply_used=80,
        workers=50,
        home_position=[30.0, 30.0],
    )
    kw.update(overrides)
    return kw


# ===========================================================================
# Tests: Engagement lifecycle
# ===========================================================================


class TestEngagementLifecycle:
    """Engagement start, continue, and end detection."""

    def test_engagement_starts_on_enemies(self):
        tracker = EngagementTracker()
        result = tracker.on_combat_frame(has_enemies=True, **_default_frame_kwargs())
        assert result is None  # No record yet — engagement just started
        assert tracker._active is True

    def test_no_engagement_when_no_enemies(self):
        tracker = EngagementTracker()
        result = tracker.on_combat_frame(has_enemies=False, **_default_frame_kwargs())
        assert result is None
        assert tracker._active is False

    def test_engagement_ends_after_cooldown(self):
        tracker = EngagementTracker()
        # Start engagement
        tracker.on_combat_frame(has_enemies=True, **_default_frame_kwargs(game_time=100.0))
        # Enemies disappear, wait past cooldown
        result = tracker.on_combat_frame(
            has_enemies=False,
            **_default_frame_kwargs(game_time=100.0 + DISENGAGE_COOLDOWN + 0.5),
        )
        assert result is not None
        assert result["type"] == "engagement"
        assert tracker._active is False

    def test_engagement_continues_during_cooldown(self):
        tracker = EngagementTracker()
        # Start engagement
        tracker.on_combat_frame(has_enemies=True, **_default_frame_kwargs(game_time=100.0))
        # Enemies disappear but within cooldown
        result = tracker.on_combat_frame(
            has_enemies=False,
            **_default_frame_kwargs(game_time=100.0 + DISENGAGE_COOLDOWN - 0.5),
        )
        assert result is None
        assert tracker._active is True

    def test_engagement_id_increments(self):
        tracker = EngagementTracker()
        records = []
        for i in range(3):
            t_start = 100.0 + i * 20.0
            tracker.on_combat_frame(has_enemies=True, **_default_frame_kwargs(game_time=t_start))
            record = tracker.on_combat_frame(
                has_enemies=False,
                **_default_frame_kwargs(game_time=t_start + DISENGAGE_COOLDOWN + 1.0),
            )
            assert record is not None
            records.append(record)
        assert [r["engagement_id"] for r in records] == [0, 1, 2]


# ===========================================================================
# Tests: Snapshots and losses
# ===========================================================================


class TestSnapshotsAndLosses:
    """Before/after army snapshots and loss computation."""

    def test_before_after_snapshots_captured(self):
        tracker = EngagementTracker()
        own_before = _make_own_army({"ROACH": (10, 2.0), "ZERGLING": (8, 0.5)})
        enemy_before = _make_enemy_units({"MARINE": (15, 1.0)})

        tracker.on_combat_frame(
            has_enemies=True,
            **_default_frame_kwargs(
                own_army_dict=own_before,
                enemy_units=enemy_before,
                game_time=100.0,
            ),
        )

        # End with fewer units
        own_after = _make_own_army({"ROACH": (7, 2.0), "ZERGLING": (3, 0.5)})
        enemy_after = _make_enemy_units({"MARINE": (5, 1.0)})

        record = tracker.on_combat_frame(
            has_enemies=False,
            **_default_frame_kwargs(
                own_army_dict=own_after,
                enemy_units=enemy_after,
                game_time=100.0 + DISENGAGE_COOLDOWN + 1.0,
            ),
        )

        assert record is not None
        assert record["own_army_before"]["units"]["ROACH"] == 10
        assert record["own_army_before"]["units"]["ZERGLING"] == 8
        assert record["enemy_army_before"]["units"]["MARINE"] == 15
        assert record["own_army_after"]["units"]["ROACH"] == 7
        assert record["enemy_army_after"]["units"]["MARINE"] == 5

    def test_losses_computed_correctly(self):
        before = {"ROACH": 10, "ZERGLING": 8}
        after = {"ROACH": 7, "ZERGLING": 3}
        losses = _compute_losses(before, after)
        assert losses == {"ROACH": 3, "ZERGLING": 5}

    def test_losses_no_negative(self):
        """Units gained (reinforcements) should not appear as negative losses."""
        before = {"ROACH": 5}
        after = {"ROACH": 8, "ZERGLING": 4}
        losses = _compute_losses(before, after)
        assert losses == {}  # Roach gained 3, zergling new — no losses


# ===========================================================================
# Tests: Outcome determination
# ===========================================================================


class TestOutcomeDetermination:

    def test_outcome_won(self):
        # Own lost 2 supply, enemy lost 10 → ratio = 0.2 < 0.8 → won
        assert _determine_outcome(2.0, 10.0) == "won"

    def test_outcome_lost(self):
        # Own lost 15 supply, enemy lost 5 → ratio = 3.0 > 1.5 → lost
        assert _determine_outcome(15.0, 5.0) == "lost"

    def test_outcome_draw(self):
        # Own lost 8, enemy lost 8 → ratio = 1.0 → draw
        assert _determine_outcome(8.0, 8.0) == "draw"

    def test_outcome_disengaged(self):
        # Total casualties below MIN_CASUALTIES
        assert _determine_outcome(0.3, 0.2) == "disengaged"

    def test_outcome_lost_when_enemy_zero_casualties(self):
        # Enemy lost nothing, we lost a lot
        assert _determine_outcome(5.0, 0.0) == "lost"


# ===========================================================================
# Tests: Prediction accuracy
# ===========================================================================


class TestPredictionAccuracy:

    def test_prediction_correct_victory_won(self):
        tracker = EngagementTracker()
        own_start = _make_own_army({"ROACH": (10, 2.0)})
        enemy_start = _make_enemy_units({"MARINE": (10, 1.0)})
        tracker.on_combat_frame(
            has_enemies=True,
            **_default_frame_kwargs(
                own_army_dict=own_start,
                enemy_units=enemy_start,
                sim_result_name="VICTORY_CLOSE",
                game_time=100.0,
            ),
        )
        # Big win: lost 1 supply, enemy lost 8
        own_end = _make_own_army({"ROACH": (9, 2.0)})
        enemy_end = _make_enemy_units({"MARINE": (2, 1.0)})
        record = tracker.on_combat_frame(
            has_enemies=False,
            **_default_frame_kwargs(
                own_army_dict=own_end,
                enemy_units=enemy_end,
                game_time=100.0 + DISENGAGE_COOLDOWN + 1.0,
            ),
        )
        assert record is not None
        assert record["actual_outcome"] == "won"
        assert record["prediction_correct"] is True

    def test_prediction_incorrect_victory_lost(self):
        tracker = EngagementTracker()
        own_start = _make_own_army({"ZERGLING": (20, 0.5)})
        enemy_start = _make_enemy_units({"MARINE": (5, 1.0)})
        tracker.on_combat_frame(
            has_enemies=True,
            **_default_frame_kwargs(
                own_army_dict=own_start,
                enemy_units=enemy_start,
                sim_result_name="VICTORY_DECISIVE",
                game_time=100.0,
            ),
        )
        # Actually lost badly: lost 9 supply, enemy lost 2
        own_end = _make_own_army({"ZERGLING": (2, 0.5)})
        enemy_end = _make_enemy_units({"MARINE": (3, 1.0)})
        record = tracker.on_combat_frame(
            has_enemies=False,
            **_default_frame_kwargs(
                own_army_dict=own_end,
                enemy_units=enemy_end,
                game_time=100.0 + DISENGAGE_COOLDOWN + 1.0,
            ),
        )
        assert record is not None
        assert record["actual_outcome"] == "lost"
        assert record["prediction_correct"] is False


# ===========================================================================
# Tests: Force end
# ===========================================================================


class TestForceEnd:

    def test_force_end_active(self):
        tracker = EngagementTracker()
        tracker.on_combat_frame(has_enemies=True, **_default_frame_kwargs(game_time=100.0))
        record = tracker.force_end(
            game_time=102.0,
            step=1020,
            own_army_dict=_make_own_army({"ROACH": (8, 2.0)}),
            enemy_units=_make_enemy_units({"MARINE": (5, 1.0)}),
        )
        assert record is not None
        assert record["type"] == "engagement"
        assert tracker._active is False

    def test_force_end_idle(self):
        tracker = EngagementTracker()
        record = tracker.force_end(
            game_time=100.0,
            step=1000,
            own_army_dict={},
            enemy_units=[],
        )
        assert record is None


# ===========================================================================
# Tests: Economy context and combat context
# ===========================================================================


class TestContextCapture:

    def test_economy_context_captured(self):
        tracker = EngagementTracker()
        tracker.on_combat_frame(
            has_enemies=True,
            **_default_frame_kwargs(
                minerals=450,
                vespene=200,
                income_minerals=1200.0,
                income_vespene=400.0,
                supply_used=85,
                workers=55,
                game_time=100.0,
            ),
        )
        record = tracker.force_end(
            game_time=108.0,
            step=1080,
            own_army_dict=_make_own_army({"ROACH": (8, 2.0)}),
            enemy_units=[],
        )
        assert record is not None
        assert record["own_minerals"] == 450
        assert record["own_vespene"] == 200
        assert record["income_minerals"] == 1200.0
        assert record["income_vespene"] == 400.0
        assert record["own_supply_used"] == 85
        assert record["own_worker_count"] == 55

    def test_combat_context_captured(self):
        tracker = EngagementTracker()
        tracker.on_combat_frame(
            has_enemies=True,
            **_default_frame_kwargs(
                combat_objective="ATTACK_EXPANSION",
                profile_name="MECH",
                approach="FLANK",
                was_committed=True,
                confidence=0.72,
                game_time=100.0,
            ),
        )
        record = tracker.force_end(
            game_time=108.0,
            step=1080,
            own_army_dict=_make_own_army({"ROACH": (8, 2.0)}),
            enemy_units=[],
        )
        assert record is not None
        assert record["combat_objective"] == "ATTACK_EXPANSION"
        assert record["engagement_profile"] == "MECH"
        assert record["approach_style"] == "FLANK"
        assert record["was_committed"] is True
        assert record["confidence"] == 0.72

    def test_distance_from_home(self):
        tracker = EngagementTracker()
        tracker.on_combat_frame(
            has_enemies=True,
            **_default_frame_kwargs(
                position=[30.0, 40.0],
                home_position=[0.0, 0.0],
                game_time=100.0,
            ),
        )
        record = tracker.force_end(
            game_time=108.0,
            step=1080,
            own_army_dict=_make_own_army({"ROACH": (8, 2.0)}),
            enemy_units=[],
        )
        assert record is not None
        assert record["distance_from_home"] == 50.0  # 3-4-5 triangle


# ===========================================================================
# Tests: Summary stats
# ===========================================================================


class TestSummaryStats:

    def test_get_stats_empty(self):
        tracker = EngagementTracker()
        stats = tracker.get_stats()
        assert stats["total"] == 0
        assert stats["avg_duration"] == 0.0
        assert stats["prediction_accuracy"] == 0.0

    def test_get_stats_after_engagements(self):
        tracker = EngagementTracker()

        # Engagement 1: win (own lost 2 supply, enemy lost 8)
        own1 = _make_own_army({"ROACH": (10, 2.0)})
        enemy1 = _make_enemy_units({"MARINE": (10, 1.0)})
        tracker.on_combat_frame(
            has_enemies=True,
            **_default_frame_kwargs(
                own_army_dict=own1,
                enemy_units=enemy1,
                sim_result_name="VICTORY_CLOSE",
                game_time=100.0,
            ),
        )
        own1_after = _make_own_army({"ROACH": (9, 2.0)})
        enemy1_after = _make_enemy_units({"MARINE": (2, 1.0)})
        record1 = tracker.on_combat_frame(
            has_enemies=False,
            **_default_frame_kwargs(
                own_army_dict=own1_after,
                enemy_units=enemy1_after,
                game_time=100.0 + DISENGAGE_COOLDOWN + 1.0,
            ),
        )
        assert record1 is not None

        # Engagement 2: loss (own lost 8, enemy lost 2)
        own2 = _make_own_army({"ZERGLING": (20, 0.5)})
        enemy2 = _make_enemy_units({"COLOSSUS": (2, 6.0)})
        tracker.on_combat_frame(
            has_enemies=True,
            **_default_frame_kwargs(
                own_army_dict=own2,
                enemy_units=enemy2,
                sim_result_name="DEFEAT_CLOSE",
                game_time=120.0,
            ),
        )
        own2_after = _make_own_army({"ZERGLING": (4, 0.5)})
        enemy2_after = _make_enemy_units({"COLOSSUS": (2, 6.0)})
        record2 = tracker.on_combat_frame(
            has_enemies=False,
            **_default_frame_kwargs(
                own_army_dict=own2_after,
                enemy_units=enemy2_after,
                game_time=120.0 + DISENGAGE_COOLDOWN + 1.0,
            ),
        )
        assert record2 is not None

        stats = tracker.get_stats()
        assert stats["total"] == 2
        assert stats["won"] == 1
        assert stats["lost"] == 1
        assert stats["avg_duration"] > 0.0
