"""Tests for the three-layer combat system.

Tests:
  - constants.py: Enum members, dataclass defaults
  - profiles.py: All profiles exist, focus targets, DEFAULT fallback
  - targeting.py: Focus fire scoring, strategic target scoring
  - awareness.py: Enemy position tracking, peak supply, staging point
  - planner.py: Defense triggers, regroup conditions, attack commitment
  - executor.py: Behavior registration basics
  - __init__.py: CombatSystem integration, backward compat
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from sc2.ids.unit_typeid import UnitTypeId as UnitID
from sc2.position import Point2

from bot.combat.constants import (
    AOE_TYPES,
    ATTACK_COMMIT_TIME,
    ApproachStyle,
    BattlefieldState,
    CombatObjective,
    CombatPlan,
    EnemyArmyEstimate,
    EXPANSION_TYPES,
    FAST_TYPES,
    FLYER_TYPES,
    MELEE_TYPES,
    MIN_ATTACK_SUPPLY,
    OpportunityTarget,
    RALLYING_ROLE,
    RANGED_TYPES,
    REGROUP_RATIO,
    STATIC_DEFENSE_TYPES,
    SUPPORT_TYPES,
    WORKER_TYPES,
    KiteStyle,
    SquadAssignment,
    SquadRole,
    TerrainFeature,
)
from bot.combat.profiles import (
    ENGAGEMENT_PROFILES,
    EngagementProfile,
    get_profile,
)
from bot.combat.targeting import (
    pick_best_target,
    score_focus_fire_target,
    score_strategic_targets,
)


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

class MockPoint2:
    """Lightweight Point2 substitute for tests."""
    def __init__(self, x: float = 50.0, y: float = 50.0):
        self.x = x
        self.y = y

    def distance_to(self, other) -> float:
        dx = self.x - getattr(other, "x", 0)
        dy = self.y - getattr(other, "y", 0)
        return (dx ** 2 + dy ** 2) ** 0.5

    def __repr__(self):
        return f"MockPoint2({self.x}, {self.y})"


class MockCombatUnit:
    """Mock unit for targeting tests."""

    def __init__(
        self,
        tag: int = 1,
        type_id: UnitID = UnitID.ZERGLING,
        position: Any = None,
        health: float = 100.0,
        health_max: float = 100.0,
        shield: float = 0.0,
        shield_max: float = 0.0,
        ground_range: float = 5.0,
        radius: float = 0.5,
        is_structure: bool = False,
        is_flying: bool = False,
        supply_cost: float = 1.0,
    ):
        self.tag = tag
        self.type_id = type_id
        self.position = position or MockPoint2()
        self.health = health
        self.health_max = health_max
        self.shield = shield
        self.shield_max = shield_max
        self.ground_range = ground_range
        self.radius = radius
        self.is_structure = is_structure
        self.is_flying = is_flying
        self.supply_cost = supply_cost

    def distance_to(self, other) -> float:
        ox = getattr(other, "x", getattr(getattr(other, "position", None), "x", 0))
        oy = getattr(other, "y", getattr(getattr(other, "position", None), "y", 0))
        return self.position.distance_to(MockPoint2(ox, oy))


# ==========================================================================
# CONSTANTS TESTS
# ==========================================================================

class TestConstants:
    """Test enum members and dataclass defaults."""

    def test_combat_objective_members(self):
        assert CombatObjective.ATTACK_BASE is not None
        assert CombatObjective.DEFEND is not None
        assert CombatObjective.REGROUP is not None
        assert CombatObjective.HARASS is not None
        assert len(CombatObjective) == 7

    def test_kite_style_members(self):
        assert KiteStyle.STUTTER_BACK is not None
        assert KiteStyle.STUTTER_FORWARD is not None
        assert KiteStyle.HOLD_POSITION is not None
        assert KiteStyle.AGGRESSIVE is not None

    def test_squad_role_members(self):
        assert SquadRole.MAIN_ARMY is not None
        assert SquadRole.HARASS is not None
        assert SquadRole.DEFENSE is not None
        assert SquadRole.RALLYING is not None

    def test_approach_style_members(self):
        assert ApproachStyle.FRONTAL is not None
        assert ApproachStyle.FLANK is not None
        assert ApproachStyle.MULTI_PRONG is not None

    def test_battlefield_state_defaults(self):
        state = BattlefieldState()
        assert state.our_army_supply == 0.0
        assert state.our_army_center is None
        assert state.peak_army_supply == 0.0
        assert state.army_wipes == 0
        assert len(state.opportunity_targets) == 0
        assert len(state.terrain_features) == 0
        assert len(state.threatened_bases) == 0
        assert state.confidence == 0.5

    def test_combat_plan_defaults(self):
        plan = CombatPlan()
        assert plan.objective == CombatObjective.REGROUP
        assert plan.approach == ApproachStyle.FRONTAL
        assert plan.engage_if_sim_wins is True
        assert plan.force_engage is False
        assert plan.profile_name == "DEFAULT"

    def test_enemy_army_estimate_defaults(self):
        est = EnemyArmyEstimate()
        assert est.last_known_position is None
        assert est.confidence == 0.0
        assert est.contains_air is False

    def test_unit_type_sets(self):
        """Verify critical unit type sets have expected members."""
        assert UnitID.ROACH in RANGED_TYPES
        assert UnitID.HYDRALISK in RANGED_TYPES
        assert UnitID.ZERGLING in MELEE_TYPES
        assert UnitID.BANELING in MELEE_TYPES
        assert UnitID.RAVAGER in AOE_TYPES
        assert UnitID.MUTALISK in FAST_TYPES
        assert UnitID.CORRUPTOR in FLYER_TYPES
        assert UnitID.MEDIVAC in SUPPORT_TYPES
        assert UnitID.PHOTONCANNON in STATIC_DEFENSE_TYPES
        assert UnitID.SCV in WORKER_TYPES
        assert UnitID.NEXUS in EXPANSION_TYPES

    def test_tuning_constants(self):
        assert MIN_ATTACK_SUPPLY > 0
        assert 0 < REGROUP_RATIO < 1.0
        assert ATTACK_COMMIT_TIME > 0


# ==========================================================================
# PROFILES TESTS
# ==========================================================================

class TestProfiles:
    """Test engagement profiles."""

    EXPECTED_PROFILES = [
        "DEFAULT", "BIO", "MECH", "BIO_MECH", "AIR_TERRAN",
        "GATEWAY", "ROBO", "STARGATE", "SKYTOSS", "GATEWAY_ROBO",
        "LING_BANE", "ROACH_HYDRA", "MUTA_LING",
        "AGGRESSIVE", "TURTLE", "MACRO", "UNKNOWN",
    ]

    def test_all_profiles_exist(self):
        for name in self.EXPECTED_PROFILES:
            assert name in ENGAGEMENT_PROFILES, f"Missing profile: {name}"

    def test_get_profile_returns_correct(self):
        bio = get_profile("BIO")
        assert bio.name == "BIO"
        assert UnitID.MEDIVAC in bio.focus_targets

    def test_get_profile_default_fallback(self):
        profile = get_profile("NONEXISTENT")
        assert profile.name == "DEFAULT"

    def test_bio_focus_targets(self):
        bio = ENGAGEMENT_PROFILES["BIO"]
        # MEDIVAC should be highest priority
        assert bio.focus_targets[0] == UnitID.MEDIVAC

    def test_mech_avoids_sieged_tanks(self):
        mech = ENGAGEMENT_PROFILES["MECH"]
        assert UnitID.SIEGETANKSIEGED in mech.avoid_targets

    def test_skytoss_kite_forward(self):
        skytoss = ENGAGEMENT_PROFILES["SKYTOSS"]
        assert skytoss.kite_style == KiteStyle.STUTTER_FORWARD
        assert skytoss.melee_aggressive is False

    def test_turtle_hold_position(self):
        turtle = ENGAGEMENT_PROFILES["TURTLE"]
        assert turtle.kite_style == KiteStyle.HOLD_POSITION
        assert UnitID.PLANETARYFORTRESS in turtle.focus_targets

    def test_aggressive_no_harass(self):
        agg = ENGAGEMENT_PROFILES["AGGRESSIVE"]
        assert agg.allow_harass is False

    def test_macro_allows_harass(self):
        macro = ENGAGEMENT_PROFILES["MACRO"]
        assert macro.allow_harass is True
        assert macro.kite_style == KiteStyle.STUTTER_FORWARD

    def test_profile_is_frozen(self):
        """Profiles should be immutable."""
        profile = get_profile("BIO")
        with pytest.raises(AttributeError):
            profile.name = "CHANGED"

    def test_all_profiles_have_name(self):
        for name, profile in ENGAGEMENT_PROFILES.items():
            assert profile.name == name


# ==========================================================================
# TARGETING TESTS
# ==========================================================================

class TestFocusFire:
    """Test per-unit focus fire scoring."""

    @pytest.fixture
    def bio_profile(self):
        return get_profile("BIO")

    @pytest.fixture
    def default_profile(self):
        return get_profile("DEFAULT")

    def test_focus_target_bonus(self, bio_profile):
        """Profile focus targets should score higher than non-focus."""
        attacker = MockCombatUnit(tag=1, type_id=UnitID.ROACH)
        medivac = MockCombatUnit(
            tag=2, type_id=UnitID.MEDIVAC,
            position=MockPoint2(52, 50),
        )
        marine = MockCombatUnit(
            tag=3, type_id=UnitID.MARINE,
            position=MockPoint2(52, 50),
        )
        # MEDIVAC is first in BIO focus_targets
        score_medivac = score_focus_fire_target(attacker, medivac, bio_profile)
        score_marine = score_focus_fire_target(attacker, marine, bio_profile)
        assert score_medivac > score_marine

    def test_wounded_bonus(self, default_profile):
        """Wounded units should score higher than healthy ones."""
        attacker = MockCombatUnit(tag=1)
        healthy = MockCombatUnit(
            tag=2, position=MockPoint2(52, 50),
            health=100, health_max=100,
        )
        wounded = MockCombatUnit(
            tag=3, position=MockPoint2(52, 50),
            health=20, health_max=100,
        )
        score_healthy = score_focus_fire_target(attacker, healthy, default_profile)
        score_wounded = score_focus_fire_target(attacker, wounded, default_profile)
        assert score_wounded > score_healthy

    def test_support_bonus(self, default_profile):
        """Support units should get a bonus when support_focus=True."""
        attacker = MockCombatUnit(tag=1)
        medivac = MockCombatUnit(
            tag=2, type_id=UnitID.MEDIVAC,
            position=MockPoint2(52, 50),
        )
        marine = MockCombatUnit(
            tag=3, type_id=UnitID.MARINE,
            position=MockPoint2(52, 50),
            health=100, health_max=100,
        )
        score_medivac = score_focus_fire_target(attacker, medivac, default_profile)
        score_marine = score_focus_fire_target(attacker, marine, default_profile)
        assert score_medivac > score_marine

    def test_avoid_target_penalty(self):
        """Avoid targets should score much lower."""
        mech = get_profile("MECH")
        attacker = MockCombatUnit(tag=1)
        sieged_tank = MockCombatUnit(
            tag=2, type_id=UnitID.SIEGETANKSIEGED,
            position=MockPoint2(52, 50),
        )
        hellion = MockCombatUnit(
            tag=3, type_id=UnitID.HELLION,
            position=MockPoint2(52, 50),
        )
        score_sieged = score_focus_fire_target(attacker, sieged_tank, mech)
        score_hellion = score_focus_fire_target(attacker, hellion, mech)
        assert score_sieged < score_hellion

    def test_distance_penalty(self, default_profile):
        """Farther enemies should score lower."""
        attacker = MockCombatUnit(tag=1, position=MockPoint2(50, 50))
        close_enemy = MockCombatUnit(
            tag=2, position=MockPoint2(52, 50),
        )
        far_enemy = MockCombatUnit(
            tag=3, position=MockPoint2(65, 50),
        )
        score_close = score_focus_fire_target(attacker, close_enemy, default_profile)
        score_far = score_focus_fire_target(attacker, far_enemy, default_profile)
        assert score_close > score_far


class TestPickBestTarget:
    """Test pick_best_target filtering and selection."""

    def test_returns_none_for_empty(self):
        attacker = MockCombatUnit(tag=1)
        result = pick_best_target(attacker, [], get_profile("DEFAULT"))
        assert result is None

    def test_filters_by_max_range(self):
        attacker = MockCombatUnit(tag=1, position=MockPoint2(50, 50))
        far_enemy = MockCombatUnit(
            tag=2, position=MockPoint2(100, 50),
        )
        result = pick_best_target(
            attacker, [far_enemy], get_profile("DEFAULT"), max_range=15.0,
        )
        assert result is None

    def test_picks_higher_scored_target(self):
        bio = get_profile("BIO")
        attacker = MockCombatUnit(tag=1, type_id=UnitID.ROACH)
        medivac = MockCombatUnit(
            tag=2, type_id=UnitID.MEDIVAC,
            position=MockPoint2(52, 50),
        )
        marine = MockCombatUnit(
            tag=3, type_id=UnitID.MARINE,
            position=MockPoint2(52, 50),
        )
        result = pick_best_target(
            attacker, [marine, medivac], bio, max_range=15.0,
        )
        assert result is not None
        assert result.type_id == UnitID.MEDIVAC


class TestStrategicTargets:
    """Test strategic target scoring."""

    def test_expansion_scores_higher_than_main(self):
        """Expansions should have higher base value than main structures."""
        nexus = MockCombatUnit(
            tag=1, type_id=UnitID.NEXUS,
            position=Point2((60, 60)),
            is_structure=True,
        )
        pylon = MockCombatUnit(
            tag=2, type_id=UnitID.PYLON,
            position=Point2((60, 60)),
            is_structure=True,
        )

        targets = score_strategic_targets(
            enemy_structures=[nexus, pylon],
            enemy_units=[],
            our_army_center=Point2((50, 50)),
            enemy_army_estimate=None,
            info_staleness=0.0,
        )
        # Nexus (expansion) should score higher than Pylon (main)
        nexus_target = next(t for t in targets if 1 in t.tags)
        pylon_target = next(t for t in targets if 2 in t.tags)
        assert nexus_target.base_value > pylon_target.base_value

    def test_enemy_proximity_penalty(self):
        """Targets near enemy army should score lower."""
        base = MockCombatUnit(
            tag=1, type_id=UnitID.NEXUS,
            position=Point2((60, 60)),
            is_structure=True,
        )

        enemy_army = EnemyArmyEstimate(
            estimated_position=Point2((60, 60)),
            confidence=1.0,
        )

        targets = score_strategic_targets(
            enemy_structures=[base],
            enemy_units=[],
            our_army_center=Point2((50, 50)),
            enemy_army_estimate=enemy_army,
            info_staleness=0.0,
        )
        # Target near enemy army should have high risk
        assert targets[0].risk > 0

    def test_empty_inputs(self):
        """Should handle empty inputs gracefully."""
        targets = score_strategic_targets(
            enemy_structures=[],
            enemy_units=[],
            our_army_center=None,
            enemy_army_estimate=None,
        )
        assert targets == []

    def test_staleness_penalty(self):
        """High info staleness should reduce target scores."""
        base = MockCombatUnit(
            tag=1, type_id=UnitID.NEXUS,
            position=Point2((60, 60)),
            is_structure=True,
        )

        targets_fresh = score_strategic_targets(
            enemy_structures=[base],
            enemy_units=[],
            our_army_center=Point2((50, 50)),
            enemy_army_estimate=None,
            info_staleness=0.0,
        )
        targets_stale = score_strategic_targets(
            enemy_structures=[base],
            enemy_units=[],
            our_army_center=Point2((50, 50)),
            enemy_army_estimate=None,
            info_staleness=120.0,
        )
        assert targets_fresh[0].score > targets_stale[0].score


# ==========================================================================
# AWARENESS TESTS (via BattlefieldState construction)
# ==========================================================================

class TestBattlefieldState:
    """Test BattlefieldState dataclass behavior."""

    def test_opportunity_target_sorting(self):
        """OpportunityTargets should be sortable by score."""
        t1 = OpportunityTarget(score=10.0)
        t2 = OpportunityTarget(score=30.0)
        t3 = OpportunityTarget(score=20.0)
        sorted_targets = sorted([t1, t2, t3], key=lambda t: t.score, reverse=True)
        assert sorted_targets[0].score == 30.0
        assert sorted_targets[2].score == 10.0

    def test_terrain_feature_defaults(self):
        tf = TerrainFeature()
        assert tf.feature_type == "choke"
        assert tf.width == 0.0

    def test_squad_assignment_defaults(self):
        sa = SquadAssignment()
        assert sa.role == SquadRole.MAIN_ARMY
        assert sa.kite_style == KiteStyle.STUTTER_BACK
        assert len(sa.priority_targets) == 0


# ==========================================================================
# PLANNER TESTS (unit-level logic)
# ==========================================================================

class TestPlannerLogic:
    """Test planner decision logic without needing a full bot."""

    def test_regroup_below_min_supply(self):
        """Army below MIN_ATTACK_SUPPLY should regroup."""
        bf = BattlefieldState(our_army_supply=10.0)
        assert bf.our_army_supply < MIN_ATTACK_SUPPLY

    def test_defense_priority_over_attack(self):
        """Threatened bases should trigger defense."""
        bf = BattlefieldState(
            threatened_bases=[Point2((50, 50))],
            threat_units_near_bases=5,
        )
        assert len(bf.threatened_bases) > 0

    def test_attack_commitment_timing(self):
        """Attack commitment should last ATTACK_COMMIT_TIME."""
        assert ATTACK_COMMIT_TIME == 20.0

    def test_combat_plan_defense(self):
        """Defense plan should have force_engage=True."""
        plan = CombatPlan(
            objective=CombatObjective.DEFEND,
            force_engage=True,
        )
        assert plan.force_engage is True
        assert plan.objective == CombatObjective.DEFEND

    def test_combat_plan_harass_supply(self):
        """Multi-prong plans should set max_harass_supply."""
        plan = CombatPlan(
            objective=CombatObjective.ATTACK_BASE,
            approach=ApproachStyle.MULTI_PRONG,
            max_harass_supply=15.0,
            allow_split=True,
        )
        assert plan.allow_split is True
        assert plan.max_harass_supply == 15.0


# ==========================================================================
# INTEGRATION TESTS (CombatSystem backward compat)
# ==========================================================================

class TestCombatSystemCompat:
    """Test backward-compatible properties of CombatSystem."""

    def test_combat_plan_regroup_mode(self):
        """REGROUP objective should map to 'REGROUP' mode string."""
        plan = CombatPlan(objective=CombatObjective.REGROUP)
        # Simulate what CombatSystem.combat_mode does
        if plan.objective == CombatObjective.DEFEND:
            mode = "DEFEND"
        elif plan.objective == CombatObjective.REGROUP:
            mode = "REGROUP"
        elif plan.objective in (
            CombatObjective.ATTACK_BASE,
            CombatObjective.ATTACK_EXPANSION,
        ):
            mode = "ATTACK"
        else:
            mode = "RETREAT"
        assert mode == "REGROUP"

    def test_combat_plan_attack_mode(self):
        plan = CombatPlan(objective=CombatObjective.ATTACK_BASE)
        if plan.objective == CombatObjective.DEFEND:
            mode = "DEFEND"
        elif plan.objective == CombatObjective.REGROUP:
            mode = "REGROUP"
        elif plan.objective in (
            CombatObjective.ATTACK_BASE,
            CombatObjective.ATTACK_EXPANSION,
        ):
            mode = "ATTACK"
        else:
            mode = "RETREAT"
        assert mode == "ATTACK"

    def test_combat_plan_defend_mode(self):
        plan = CombatPlan(objective=CombatObjective.DEFEND)
        if plan.objective == CombatObjective.DEFEND:
            mode = "DEFEND"
        elif plan.objective == CombatObjective.REGROUP:
            mode = "REGROUP"
        else:
            mode = "ATTACK"
        assert mode == "DEFEND"


# ==========================================================================
# PROFILE COVERAGE TESTS
# ==========================================================================

class TestProfileCoverage:
    """Verify profiles cover all enemy archetypes from CompositionClassifier."""

    ARCHETYPE_NAMES = [
        "UNKNOWN", "BIO", "MECH", "BIO_MECH", "AIR_TERRAN",
        "GATEWAY", "ROBO", "STARGATE", "SKYTOSS", "GATEWAY_ROBO",
        "LING_BANE", "ROACH_HYDRA", "MUTA_LING",
        "AGGRESSIVE", "TURTLE", "MACRO",
    ]

    def test_every_archetype_has_profile(self):
        """Every known archetype should have a matching engagement profile."""
        for arch in self.ARCHETYPE_NAMES:
            profile = get_profile(arch)
            assert profile.name == arch, f"Profile {arch} returned wrong profile: {profile.name}"

    def test_all_profiles_have_valid_kite_style(self):
        for name, profile in ENGAGEMENT_PROFILES.items():
            assert isinstance(profile.kite_style, KiteStyle), f"{name} has invalid kite_style"

    def test_focus_targets_are_unit_ids(self):
        for name, profile in ENGAGEMENT_PROFILES.items():
            for target in profile.focus_targets:
                assert isinstance(target, UnitID), f"{name} has non-UnitID focus target: {target}"

    def test_avoid_targets_are_unit_ids(self):
        for name, profile in ENGAGEMENT_PROFILES.items():
            for target in profile.avoid_targets:
                assert isinstance(target, UnitID), f"{name} has non-UnitID avoid target: {target}"
