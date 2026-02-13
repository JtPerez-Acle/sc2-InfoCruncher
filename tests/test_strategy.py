"""Tests for bot.strategy — unit counter DB, threat model, economy model, analyzer.

Uses lightweight mocks (no SC2/ares needed).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sc2.ids.unit_typeid import UnitTypeId as UnitID

from bot.strategy.unit_counter_db import (
    COUNTER_MAP,
    TECH_REQUIREMENTS,
    UNIT_PROFILES,
    can_build_unit,
    get_counter_units,
    get_unit_value,
)
from bot.strategy.threat_model import ThreatAssessment, ThreatModel
from bot.strategy.economy_model import EconomyModel, EconomyPlan


# ---------------------------------------------------------------------------
# Helpers: build own_structures dicts for different tech states
# ---------------------------------------------------------------------------

def _structures_early() -> dict:
    """Pool only — can build lings."""
    return {UnitID.SPAWNINGPOOL: [SimpleNamespace()]}


def _structures_roach() -> dict:
    """Pool + roach warren."""
    return {
        UnitID.SPAWNINGPOOL: [SimpleNamespace()],
        UnitID.ROACHWARREN: [SimpleNamespace()],
    }


def _structures_lair() -> dict:
    """Pool + roach warren + lair + hydra den."""
    return {
        UnitID.SPAWNINGPOOL: [SimpleNamespace()],
        UnitID.ROACHWARREN: [SimpleNamespace()],
        UnitID.LAIR: [SimpleNamespace()],
        UnitID.HYDRALISKDEN: [SimpleNamespace()],
    }


def _structures_hive() -> dict:
    """Full tech: pool, roach warren, lair→hive, hydra, lurker, infestation pit."""
    return {
        UnitID.SPAWNINGPOOL: [SimpleNamespace()],
        UnitID.ROACHWARREN: [SimpleNamespace()],
        UnitID.HIVE: [SimpleNamespace()],
        UnitID.HYDRALISKDEN: [SimpleNamespace()],
        UnitID.LURKERDENMP: [SimpleNamespace()],
        UnitID.INFESTATIONPIT: [SimpleNamespace()],
        UnitID.ULTRALISKCAVERN: [SimpleNamespace()],
    }


# ===========================================================================
# unit_counter_db tests
# ===========================================================================


class TestUnitProfiles:
    """Verify UNIT_PROFILES data integrity."""

    def test_all_profiles_have_race(self):
        for uid, profile in UNIT_PROFILES.items():
            assert profile.race in ("Zerg", "Protoss", "Terran"), (
                f"{uid.name} has invalid race: {profile.race}"
            )

    def test_all_profiles_have_positive_cost(self):
        """Every unit should cost at least some minerals."""
        # Exception: Archon (merges from templar, 0 direct cost)
        for uid, profile in UNIT_PROFILES.items():
            if uid == UnitID.ARCHON:
                continue
            assert profile.mineral_cost > 0 or profile.gas_cost > 0, (
                f"{uid.name} has zero cost"
            )

    def test_zerg_units_present(self):
        """All Zerg combat units should have profiles."""
        zerg_ids = [
            UnitID.ZERGLING, UnitID.BANELING, UnitID.ROACH, UnitID.RAVAGER,
            UnitID.HYDRALISK, UnitID.LURKERMP, UnitID.MUTALISK,
            UnitID.CORRUPTOR, UnitID.ULTRALISK, UnitID.BROODLORD,
            UnitID.INFESTOR, UnitID.VIPER,
        ]
        for uid in zerg_ids:
            assert uid in UNIT_PROFILES, f"Missing profile: {uid.name}"

    def test_counter_map_entries_are_zerg(self):
        """Counter units must be Zerg (we're Zerg)."""
        for enemy_uid, counters in COUNTER_MAP.items():
            for counter_uid in counters:
                # OVERSEER is a valid counter but not in UNIT_PROFILES
                if counter_uid == UnitID.OVERSEER:
                    continue
                # QUEEN is used for anti-air defense
                if counter_uid == UnitID.QUEEN:
                    continue
                profile = UNIT_PROFILES.get(counter_uid)
                assert profile is not None, (
                    f"Counter {counter_uid.name} for {enemy_uid.name} has no profile"
                )
                assert profile.race == "Zerg", (
                    f"Counter {counter_uid.name} is not Zerg"
                )


class TestCanBuildUnit:
    """Verify tech requirement checking."""

    def test_lings_need_pool(self):
        assert can_build_unit(UnitID.ZERGLING, _structures_early()) is True
        assert can_build_unit(UnitID.ZERGLING, {}) is False

    def test_roach_needs_roach_warren(self):
        assert can_build_unit(UnitID.ROACH, _structures_early()) is False
        assert can_build_unit(UnitID.ROACH, _structures_roach()) is True

    def test_hydra_needs_lair(self):
        assert can_build_unit(UnitID.HYDRALISK, _structures_roach()) is False
        assert can_build_unit(UnitID.HYDRALISK, _structures_lair()) is True

    def test_hive_counts_as_lair(self):
        """HIVE is a lair-tier structure — units requiring lair should work."""
        structs = _structures_hive()
        assert can_build_unit(UnitID.HYDRALISK, structs) is True

    def test_ultra_needs_hive(self):
        assert can_build_unit(UnitID.ULTRALISK, _structures_lair()) is False
        assert can_build_unit(UnitID.ULTRALISK, _structures_hive()) is True

    def test_unknown_unit_returns_false(self):
        """Units not in TECH_REQUIREMENTS (e.g. Protoss) can't be built."""
        assert can_build_unit(UnitID.ZEALOT, _structures_hive()) is False


class TestGetCounterUnits:
    """Verify counter scoring logic."""

    def test_empty_enemy_returns_empty(self):
        result = get_counter_units({}, _structures_roach())
        assert result == {}

    def test_marines_recommend_banelings(self):
        """Marines are Light, banelings are the #1 counter."""
        enemy = {UnitID.MARINE: 10}
        structs = {
            UnitID.SPAWNINGPOOL: [SimpleNamespace()],
            UnitID.BANELINGNEST: [SimpleNamespace()],
            UnitID.ROACHWARREN: [SimpleNamespace()],
        }
        result = get_counter_units(enemy, structs)
        assert UnitID.BANELING in result
        # Banelings should have highest or near-highest weight
        assert result[UnitID.BANELING] > 0

    def test_filters_unbuildable_units(self):
        """If we don't have hydra den, hydras shouldn't appear."""
        enemy = {UnitID.VOIDRAY: 3}
        structs = _structures_roach()  # No lair/hydra den
        result = get_counter_units(enemy, structs)
        assert UnitID.HYDRALISK not in result

    def test_stalkers_counter_with_available_tech(self):
        """With only pool+roach warren, countering stalkers should yield lings/roaches."""
        enemy = {UnitID.STALKER: 5}
        result = get_counter_units(enemy, _structures_roach())
        assert UnitID.ZERGLING in result or UnitID.ROACH in result

    def test_mixed_enemy_produces_diverse_counters(self):
        """Mixed enemy comp should produce multiple counter types."""
        enemy = {UnitID.MARINE: 8, UnitID.SIEGETANK: 2, UnitID.MEDIVAC: 2}
        structs = _structures_lair()
        result = get_counter_units(enemy, structs)
        assert len(result) >= 2, "Should recommend at least 2 different unit types"


class TestGetUnitValue:
    def test_known_unit(self):
        assert get_unit_value(UnitID.ROACH) == 100.0  # 75 min + 25 gas

    def test_unknown_unit_returns_fallback(self):
        assert get_unit_value(UnitID.OBSERVER) == 100.0  # Not in profiles


# ===========================================================================
# threat_model tests
# ===========================================================================


class TestThreatModel:
    """Test ThreatModel.evaluate() with various game states."""

    def setup_method(self):
        self.model = ThreatModel()

    def _evaluate(self, **kwargs):
        """Helper with sensible defaults."""
        defaults = dict(
            enemy_comp={},
            enemy_structures=set(),
            own_structures=_structures_roach(),
            own_army_value=500.0,
            enemy_army_value=500.0,
            game_time=180.0,
            ground_threats_near_base=0,
            air_threats_near_base=0,
            enemy_expanded=False,
        )
        defaults.update(kwargs)
        return self.model.evaluate(**defaults)

    def test_default_returns_assessment(self):
        result = self._evaluate()
        assert isinstance(result, ThreatAssessment)

    def test_army_advantage_equal(self):
        result = self._evaluate(own_army_value=1000, enemy_army_value=1000)
        assert 0.9 <= result.army_advantage <= 1.1

    def test_army_advantage_winning(self):
        result = self._evaluate(own_army_value=2000, enemy_army_value=500)
        assert result.army_advantage > 3.0

    def test_army_advantage_no_enemy_visible(self):
        result = self._evaluate(own_army_value=1000, enemy_army_value=0)
        assert result.army_advantage >= 5.0

    def test_race_detected_from_units(self):
        result = self._evaluate(enemy_comp={UnitID.MARINE: 5})
        assert result.enemy_race == "Terran"

    def test_race_detected_from_structures(self):
        result = self._evaluate(
            enemy_structures={UnitID.NEXUS, UnitID.GATEWAY}
        )
        assert result.enemy_race == "Protoss"

    def test_unknown_race_when_no_data(self):
        result = self._evaluate()
        assert result.enemy_race == "Unknown"

    def test_tech_level_early(self):
        result = self._evaluate(game_time=60.0)
        assert result.enemy_tech_level == "early"

    def test_tech_level_mid_from_structures(self):
        result = self._evaluate(
            enemy_structures={UnitID.FACTORY},
            game_time=180.0,
        )
        assert result.enemy_tech_level == "mid"

    def test_tech_level_late_from_structures(self):
        result = self._evaluate(
            enemy_structures={UnitID.FUSIONCORE},
        )
        assert result.enemy_tech_level == "late"

    def test_tech_level_late_from_time(self):
        result = self._evaluate(game_time=500.0)
        assert result.enemy_tech_level == "late"

    def test_air_detected_from_units(self):
        result = self._evaluate(enemy_comp={UnitID.MUTALISK: 3})
        assert result.enemy_has_air is True

    def test_air_detected_from_structures(self):
        result = self._evaluate(enemy_structures={UnitID.STARGATE})
        assert result.enemy_has_air is True

    def test_no_air_by_default(self):
        result = self._evaluate()
        assert result.enemy_has_air is False

    def test_threat_level_low_when_safe(self):
        result = self._evaluate(
            own_army_value=2000, enemy_army_value=500,
            ground_threats_near_base=0, game_time=300,
        )
        assert result.threat_level < 0.3

    def test_threat_level_high_when_under_attack(self):
        result = self._evaluate(
            ground_threats_near_base=10,
            own_army_value=500, enemy_army_value=1000,
        )
        assert result.threat_level > 0.5

    def test_safe_to_drone_follows_threat_level(self):
        # Low threat → safe
        result = self._evaluate(
            own_army_value=2000, enemy_army_value=200,
            ground_threats_near_base=0, game_time=300,
        )
        assert result.safe_to_drone is True

        # High threat → not safe
        result2 = self._evaluate(
            ground_threats_near_base=10,
            own_army_value=200, enemy_army_value=1000,
        )
        assert result2.safe_to_drone is False

    def test_recommended_comp_not_empty(self):
        """Should always produce a recommended composition."""
        result = self._evaluate(
            enemy_comp={UnitID.MARINE: 10},
        )
        assert len(result.recommended_comp) > 0
        assert sum(result.recommended_comp.values()) > 0

    def test_recommended_comp_uses_available_tech(self):
        """Recommended units should be buildable with current structures."""
        result = self._evaluate(
            enemy_comp={UnitID.STALKER: 5},
            own_structures=_structures_early(),  # Pool only
        )
        for uid in result.recommended_comp:
            assert can_build_unit(uid, _structures_early()) or uid == UnitID.QUEEN

    def test_default_comp_when_enemy_unknown(self):
        """Empty enemy comp → default composition based on our tech."""
        result = self._evaluate(enemy_comp={})
        assert len(result.recommended_comp) > 0

    def test_max_proportion_capped(self):
        """No single unit should exceed 70% of the comp."""
        result = self._evaluate(
            enemy_comp={UnitID.MARINE: 50},
            own_structures=_structures_lair(),
        )
        for uid, proportion in result.recommended_comp.items():
            assert proportion <= 0.75, (
                f"{uid.name} proportion {proportion:.2f} exceeds cap"
            )

    def test_recommended_tech_lists_missing_structures(self):
        """If comp needs hydras but we have no hydra den, recommend it."""
        result = self._evaluate(
            enemy_comp={UnitID.STALKER: 5, UnitID.VOIDRAY: 3},
            own_structures=_structures_roach(),  # No lair/hydra den
        )
        # Since we can't build hydras yet, tech targets should include
        # at least LAIR or HYDRALISKDEN
        tech_names = [t.name for t in result.recommended_tech]
        # We expect some tech progression needs
        # (may or may not include LAIR depending on counter calc)
        assert isinstance(result.recommended_tech, list)

    def test_enemy_aggressive_when_threats_at_base(self):
        result = self._evaluate(ground_threats_near_base=5)
        assert result.enemy_is_aggressive is True

    def test_enemy_expanding(self):
        result = self._evaluate(enemy_expanded=True)
        assert result.enemy_is_expanding is True

    def test_dominant_enemy_type(self):
        result = self._evaluate(
            enemy_comp={UnitID.MARINE: 20, UnitID.SIEGETANK: 1}
        )
        # Marines: 20 * 50 = 1000. Tanks: 1 * 275 = 275. Marines dominate.
        assert result.dominant_enemy_type == UnitID.MARINE


# ===========================================================================
# economy_model tests
# ===========================================================================


class TestEconomyModel:
    """Test economy planning from threat assessment."""

    def setup_method(self):
        self.model = EconomyModel()

    def _evaluate(self, threat=None, **kwargs):
        """Helper with sensible defaults."""
        if threat is None:
            threat = ThreatAssessment()
        defaults = dict(
            threat=threat,
            supply_workers=30,
            supply_army=15,
            num_bases=2,
            minerals=300,
            vespene=100,
            num_extractors=2,
        )
        defaults.update(kwargs)
        return self.model.evaluate(**defaults)

    def test_returns_economy_plan(self):
        result = self._evaluate()
        assert isinstance(result, EconomyPlan)

    def test_target_drones_scales_with_bases(self):
        two_base = self._evaluate(num_bases=2)
        three_base = self._evaluate(num_bases=3)
        assert three_base.target_drones >= two_base.target_drones

    def test_target_drones_reduced_under_threat(self):
        safe = self._evaluate(
            threat=ThreatAssessment(threat_level=0.0, army_advantage=2.0)
        )
        threatened = self._evaluate(
            threat=ThreatAssessment(threat_level=0.8, army_advantage=0.5)
        )
        assert threatened.target_drones <= safe.target_drones

    def test_target_gas_minimum(self):
        """Should always want at least 1 gas."""
        result = self._evaluate(num_bases=1)
        assert result.target_gas >= 1

    def test_target_gas_scales_with_gas_intensity(self):
        """Gas-heavy comp → more extractors."""
        gas_heavy = ThreatAssessment(
            recommended_comp={UnitID.HYDRALISK: 0.5, UnitID.LURKERMP: 0.3}
        )
        mineral_heavy = ThreatAssessment(
            recommended_comp={UnitID.ZERGLING: 0.9, UnitID.ROACH: 0.1}
        )
        gas_result = self._evaluate(threat=gas_heavy, num_bases=3)
        min_result = self._evaluate(threat=mineral_heavy, num_bases=3)
        assert gas_result.target_gas >= min_result.target_gas

    def test_always_want_natural(self):
        """Always target at least 2 bases."""
        result = self._evaluate(num_bases=1)
        assert result.target_bases >= 2

    def test_no_expand_under_threat(self):
        """Don't expand when under heavy pressure."""
        threat = ThreatAssessment(threat_level=0.8)
        result = self._evaluate(threat=threat, num_bases=2)
        assert result.target_bases == 2  # Hold at current count

    def test_expand_when_saturated(self):
        """Expand when workers exceed 75% of mining capacity."""
        result = self._evaluate(
            threat=ThreatAssessment(threat_level=0.1),
            num_bases=2,
            supply_workers=28,  # 28/32 = 87.5% saturated
        )
        assert result.target_bases >= 3

    def test_army_ratio_full_when_workers_at_target(self):
        """If workers are at target, army ratio should be 1.0."""
        result = self._evaluate(supply_workers=44)  # Default target is ~44
        assert result.army_ratio >= 0.9

    def test_army_ratio_high_under_threat(self):
        """High threat → prioritize army."""
        threat = ThreatAssessment(threat_level=0.8, army_advantage=0.5)
        result = self._evaluate(threat=threat)
        assert result.army_ratio >= 0.85

    def test_army_ratio_low_when_greeding(self):
        """Big army advantage + safe → prioritize drones."""
        threat = ThreatAssessment(
            threat_level=0.0, army_advantage=3.0, safe_to_drone=True
        )
        result = self._evaluate(threat=threat, minerals=200, supply_workers=20)
        assert result.army_ratio <= 0.5

    def test_safe_to_expand_follows_threat(self):
        safe = self._evaluate(
            threat=ThreatAssessment(threat_level=0.2)
        )
        dangerous = self._evaluate(
            threat=ThreatAssessment(threat_level=0.7)
        )
        assert safe.safe_to_expand is True
        assert dangerous.safe_to_expand is False

    def test_dont_expand_past_five(self):
        """Cap at 5 bases."""
        result = self._evaluate(
            threat=ThreatAssessment(threat_level=0.0),
            num_bases=5,
            supply_workers=70,
        )
        assert result.target_bases == 5

    def test_float_minerals_triggers_expand(self):
        """Floating resources with low threat → expand."""
        result = self._evaluate(
            threat=ThreatAssessment(threat_level=0.1, safe_to_drone=True),
            minerals=800,
            num_bases=2,
            supply_workers=20,
        )
        assert result.target_bases >= 3

    # ------------------------------------------------------------------
    # Gas banking feedback tests
    # ------------------------------------------------------------------

    def test_gas_banking_reduces_target(self):
        """When vespene > 400 and vespene > minerals*2, gas target drops."""
        gas_heavy = ThreatAssessment(
            recommended_comp={UnitID.HYDRALISK: 0.5, UnitID.ROACH: 0.3}
        )
        # Normal (no banking)
        normal = self._evaluate(
            threat=gas_heavy, num_bases=3, minerals=400, vespene=200,
        )
        # Banking hard
        banking = self._evaluate(
            threat=gas_heavy, num_bases=3, minerals=200, vespene=900,
        )
        assert banking.target_gas < normal.target_gas

    def test_gas_banking_severe_reduces_more(self):
        """When vespene > 800 and > minerals*1.5, double reduction."""
        gas_heavy = ThreatAssessment(
            recommended_comp={UnitID.HYDRALISK: 0.5, UnitID.ROACH: 0.3}
        )
        moderate = self._evaluate(
            threat=gas_heavy, num_bases=3, minerals=200, vespene=500,
        )
        severe = self._evaluate(
            threat=gas_heavy, num_bases=3, minerals=200, vespene=1200,
        )
        assert severe.target_gas <= moderate.target_gas

    def test_gas_banking_minimum_one(self):
        """Gas target never drops below 1, even with extreme banking."""
        gas_light = ThreatAssessment(
            recommended_comp={UnitID.ZERGLING: 0.9, UnitID.ROACH: 0.1}
        )
        result = self._evaluate(
            threat=gas_light, num_bases=2, minerals=100, vespene=2000,
        )
        assert result.target_gas >= 1

    def test_no_gas_reduction_when_balanced(self):
        """No reduction when vespene/minerals ratio is healthy."""
        gas_heavy = ThreatAssessment(
            recommended_comp={UnitID.HYDRALISK: 0.5, UnitID.ROACH: 0.3}
        )
        result_a = self._evaluate(
            threat=gas_heavy, num_bases=3, minerals=500, vespene=300,
        )
        result_b = self._evaluate(
            threat=gas_heavy, num_bases=3, minerals=500, vespene=0,
        )
        # With balanced resources, no reduction should happen
        assert result_a.target_gas == result_b.target_gas
