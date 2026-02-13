"""Tests for CompositionClassifier and CompositionAnalysis.

Covers:
  - Race-specific classification (Terran, Protoss, Zerg)
  - Cross-race overlays (turtle, aggressive, macro)
  - Anti-air floor scaling
  - Confidence computation
  - Derived metrics (mech flag, anti-armor, detection)
"""

from __future__ import annotations

import pytest
from sc2.ids.unit_typeid import UnitTypeId as UnitID

from bot.strategy.composition import (
    CompositionAnalysis,
    CompositionClassifier,
    EnemyArchetype,
)


@pytest.fixture
def classifier():
    return CompositionClassifier()


# ---------------------------------------------------------------------------
# Terran classification
# ---------------------------------------------------------------------------

class TestTerranClassification:

    def test_terran_bio(self, classifier):
        comp = {UnitID.MARINE: 15, UnitID.MARAUDER: 5, UnitID.MEDIVAC: 2}
        result = classifier.classify(
            comp, set(), "Terran", 300.0, 2, 22,
        )
        assert result.archetype == EnemyArchetype.BIO

    def test_terran_mech(self, classifier):
        comp = {
            UnitID.SIEGETANK: 4, UnitID.HELLION: 6,
            UnitID.THOR: 2, UnitID.CYCLONE: 2,
        }
        result = classifier.classify(
            comp, set(), "Terran", 360.0, 2, 14,
        )
        assert result.archetype == EnemyArchetype.MECH

    def test_terran_bio_mech(self, classifier):
        comp = {
            UnitID.MARINE: 10, UnitID.MARAUDER: 4,
            UnitID.SIEGETANK: 3, UnitID.HELLION: 3,
        }
        result = classifier.classify(
            comp, set(), "Terran", 360.0, 2, 20,
        )
        assert result.archetype == EnemyArchetype.BIO_MECH

    def test_terran_air(self, classifier):
        comp = {
            UnitID.LIBERATOR: 4, UnitID.BANSHEE: 3,
            UnitID.BATTLECRUISER: 2, UnitID.MARINE: 2,
        }
        result = classifier.classify(
            comp, set(), "Terran", 480.0, 2, 11,
        )
        assert result.archetype == EnemyArchetype.AIR_TERRAN


# ---------------------------------------------------------------------------
# Protoss classification
# ---------------------------------------------------------------------------

class TestProtossClassification:

    def test_protoss_gateway(self, classifier):
        comp = {UnitID.ZEALOT: 8, UnitID.STALKER: 6, UnitID.SENTRY: 2}
        result = classifier.classify(
            comp, set(), "Protoss", 300.0, 2, 16,
        )
        assert result.archetype == EnemyArchetype.GATEWAY

    def test_protoss_robo(self, classifier):
        comp = {UnitID.IMMORTAL: 4, UnitID.COLOSSUS: 2, UnitID.ZEALOT: 3}
        result = classifier.classify(
            comp, set(), "Protoss", 420.0, 2, 9,
        )
        assert result.archetype == EnemyArchetype.ROBO

    def test_protoss_stargate(self, classifier):
        comp = {UnitID.PHOENIX: 5, UnitID.VOIDRAY: 3, UnitID.STALKER: 2}
        result = classifier.classify(
            comp, set(), "Protoss", 360.0, 2, 10,
        )
        assert result.archetype == EnemyArchetype.STARGATE

    def test_protoss_skytoss_carriers(self, classifier):
        comp = {UnitID.CARRIER: 4, UnitID.VOIDRAY: 2, UnitID.STALKER: 3}
        result = classifier.classify(
            comp, set(), "Protoss", 600.0, 3, 9,
        )
        assert result.archetype == EnemyArchetype.SKYTOSS

    def test_protoss_skytoss_tempests(self, classifier):
        comp = {UnitID.TEMPEST: 4, UnitID.STALKER: 3}
        result = classifier.classify(
            comp, set(), "Protoss", 600.0, 3, 7,
        )
        assert result.archetype == EnemyArchetype.SKYTOSS

    def test_protoss_skytoss_mixed(self, classifier):
        comp = {UnitID.CARRIER: 3, UnitID.VOIDRAY: 3, UnitID.ZEALOT: 2}
        result = classifier.classify(
            comp, set(), "Protoss", 600.0, 3, 8,
        )
        assert result.archetype == EnemyArchetype.SKYTOSS

    def test_protoss_gateway_robo(self, classifier):
        comp = {
            UnitID.ZEALOT: 6, UnitID.STALKER: 5,
            UnitID.IMMORTAL: 1, UnitID.WARPPRISM: 1,
        }
        result = classifier.classify(
            comp, set(), "Protoss", 420.0, 2, 13,
        )
        assert result.archetype == EnemyArchetype.GATEWAY_ROBO


# ---------------------------------------------------------------------------
# Zerg classification (mirror)
# ---------------------------------------------------------------------------

class TestZergClassification:

    def test_zerg_ling_bane(self, classifier):
        comp = {UnitID.ZERGLING: 30, UnitID.BANELING: 10}
        result = classifier.classify(
            comp, set(), "Zerg", 240.0, 2, 40,
        )
        assert result.archetype == EnemyArchetype.LING_BANE

    def test_zerg_roach_hydra(self, classifier):
        comp = {UnitID.ROACH: 8, UnitID.HYDRALISK: 5, UnitID.ZERGLING: 4}
        result = classifier.classify(
            comp, set(), "Zerg", 360.0, 3, 17,
        )
        assert result.archetype == EnemyArchetype.ROACH_HYDRA

    def test_zerg_muta_ling(self, classifier):
        comp = {UnitID.MUTALISK: 5, UnitID.ZERGLING: 15}
        result = classifier.classify(
            comp, set(), "Zerg", 360.0, 3, 20,
        )
        assert result.archetype == EnemyArchetype.MUTA_LING


# ---------------------------------------------------------------------------
# Cross-race overlays
# ---------------------------------------------------------------------------

class TestOverlays:

    def test_turtle_detection(self, classifier):
        """Heavy static defense + low army -> TURTLE."""
        comp = {UnitID.MARINE: 3}
        structures = {
            UnitID.BUNKER, UnitID.MISSILETURRET,
            UnitID.PLANETARYFORTRESS,
        }
        result = classifier.classify(
            comp, structures, "Terran", 300.0, 2, 3,
        )
        assert result.archetype == EnemyArchetype.TURTLE

    def test_mech_turtle_detection(self, classifier):
        """Factory + Armory + tanks + few bases + defense -> TURTLE."""
        comp = {UnitID.SIEGETANK: 2, UnitID.SIEGETANKSIEGED: 1, UnitID.MARINE: 2}
        structures = {UnitID.FACTORY, UnitID.ARMORY, UnitID.BUNKER}
        result = classifier.classify(
            comp, structures, "Terran", 400.0, 2, 5,
        )
        assert result.archetype == EnemyArchetype.TURTLE

    def test_aggressive_early(self, classifier):
        """Army before expansion in early game -> AGGRESSIVE."""
        comp = {UnitID.ZEALOT: 4, UnitID.STALKER: 3}
        result = classifier.classify(
            comp, set(), "Protoss", 150.0, 1, 7,
        )
        assert result.archetype == EnemyArchetype.AGGRESSIVE

    def test_macro_play(self, classifier):
        """Fast expand + low visible army -> MACRO."""
        comp = {UnitID.MARINE: 2}
        result = classifier.classify(
            comp, set(), "Terran", 240.0, 3, 2,
        )
        assert result.archetype == EnemyArchetype.MACRO

    def test_unknown_no_data(self, classifier):
        """Empty composition -> UNKNOWN."""
        result = classifier.classify(
            {}, set(), "Unknown", 60.0, 1, 0,
        )
        assert result.archetype == EnemyArchetype.UNKNOWN

    def test_unknown_insufficient_data(self, classifier):
        """Very little data, no overlay trigger -> UNKNOWN."""
        comp = {UnitID.MARINE: 1}
        result = classifier.classify(
            comp, set(), "Terran", 120.0, 1, 1,
        )
        assert result.archetype == EnemyArchetype.UNKNOWN


# ---------------------------------------------------------------------------
# Anti-air floor scaling
# ---------------------------------------------------------------------------

class TestAntiAirFloor:

    def test_no_air_zero_floor(self, classifier):
        """No air units, no air structures -> 0.0."""
        comp = {UnitID.MARINE: 10, UnitID.MARAUDER: 5}
        result = classifier.classify(
            comp, set(), "Terran", 300.0, 2, 15,
        )
        assert result.recommended_aa_floor == 0.0

    def test_air_structures_only(self, classifier):
        """Air structures but no air units -> precautionary 0.10."""
        comp = {UnitID.MARINE: 10, UnitID.MARAUDER: 5}
        structures = {UnitID.STARPORT}
        result = classifier.classify(
            comp, structures, "Terran", 300.0, 2, 15,
        )
        assert result.recommended_aa_floor == 0.10

    def test_light_air(self, classifier):
        """Small air fraction (1 phoenix among gateway) -> 0.15."""
        comp = {UnitID.ZEALOT: 6, UnitID.STALKER: 5, UnitID.PHOENIX: 1}
        result = classifier.classify(
            comp, set(), "Protoss", 300.0, 2, 12,
        )
        assert result.recommended_aa_floor == 0.15

    def test_moderate_air(self, classifier):
        """Air 25-50% -> 0.25."""
        # Need air_fraction strictly > 0.25
        # Phoenix=150 each, Zealot=100 each, Stalker=125 each
        # 3 phoenix = 450, 5 zealot=500, 3 stalker=375 → air=450/1325=0.34
        comp = {UnitID.ZEALOT: 5, UnitID.STALKER: 3, UnitID.PHOENIX: 3}
        result = classifier.classify(
            comp, set(), "Protoss", 360.0, 2, 11,
        )
        assert result.recommended_aa_floor == 0.25

    def test_heavy_air(self, classifier):
        """Air 50-75% -> 0.40."""
        # Marine=50, Medivac=200 (in _ALL_AIR_UNITS but not _TERRAN_AIR)
        # 6 marines=300, 3 medivacs=600 → air=600/900=0.67 → 0.40
        # Classified as BIO (no SKYTOSS/AIR_TERRAN override)
        comp = {UnitID.MARINE: 6, UnitID.MEDIVAC: 3}
        result = classifier.classify(
            comp, set(), "Terran", 360.0, 2, 9,
        )
        assert result.recommended_aa_floor == 0.40

    def test_mass_air_skytoss(self, classifier):
        """Mass air / SKYTOSS -> 0.55."""
        comp = {UnitID.CARRIER: 4, UnitID.TEMPEST: 3}
        result = classifier.classify(
            comp, set(), "Protoss", 600.0, 3, 7,
        )
        assert result.archetype == EnemyArchetype.SKYTOSS
        assert result.recommended_aa_floor == 0.55


# ---------------------------------------------------------------------------
# CompositionAnalysis derived fields
# ---------------------------------------------------------------------------

class TestCompositionAnalysis:

    def test_mech_sets_anti_armor(self, classifier):
        """MECH archetype -> anti_armor_priority=True."""
        comp = {
            UnitID.SIEGETANK: 4, UnitID.HELLION: 6,
            UnitID.THOR: 2, UnitID.CYCLONE: 2,
        }
        result = classifier.classify(
            comp, set(), "Terran", 360.0, 2, 14,
        )
        assert result.archetype == EnemyArchetype.MECH
        assert result.is_mech_heavy is True
        assert result.anti_armor_priority is True

    def test_skytoss_dominant_damage_air(self, classifier):
        """SKYTOSS -> dominant_damage_type='air'."""
        comp = {UnitID.CARRIER: 5, UnitID.TEMPEST: 2}
        result = classifier.classify(
            comp, set(), "Protoss", 600.0, 3, 7,
        )
        assert result.dominant_damage_type == "air"

    def test_confidence_high_with_data(self, classifier):
        """Many units observed late game -> high confidence."""
        # Marine=50, Marauder=125, Medivac=200. Total ~50*30+125*15+200*6=4575
        # expected at 600s = (600-120)*50 = 24000, raw = 4575/24000 ~0.19
        # Need more army value: 50 marines=2500, 20 marauders=2500, 8 medivac=1600 = 6600
        # Or use later time with smaller expected
        comp = {UnitID.MARINE: 50, UnitID.MARAUDER: 20, UnitID.MEDIVAC: 8}
        result = classifier.classify(
            comp, set(), "Terran", 300.0, 3, 78,
        )
        # expected at 300s = (300-120)*50 = 9000, value ~50*50+20*125+8*200=2500+2500+1600=6600
        # raw = 6600/9000 = 0.73 -> capped at 0.73
        assert result.confidence > 0.5

    def test_confidence_low_sparse_data(self, classifier):
        """Few units in early game -> low confidence."""
        comp = {UnitID.MARINE: 3}
        result = classifier.classify(
            comp, set(), "Terran", 180.0, 1, 3,
        )
        # Below MIN_DATA_VALUE, so archetype=UNKNOWN, confidence=0.0
        assert result.confidence <= 0.2

    def test_needs_detection_dark_templar(self, classifier):
        """DT in comp -> needs_detection=True."""
        comp = {UnitID.DARKTEMPLAR: 2, UnitID.ZEALOT: 5, UnitID.STALKER: 3}
        result = classifier.classify(
            comp, set(), "Protoss", 300.0, 2, 10,
        )
        assert result.needs_detection is True

    def test_needs_detection_dark_shrine(self, classifier):
        """Dark Shrine structure -> needs_detection=True."""
        comp = {UnitID.ZEALOT: 5, UnitID.STALKER: 3}
        structures = {UnitID.DARKSHRINE}
        result = classifier.classify(
            comp, structures, "Protoss", 300.0, 2, 8,
        )
        assert result.needs_detection is True

    def test_air_fraction_pure_ground(self, classifier):
        """Pure ground -> air_fraction=0.0."""
        comp = {UnitID.MARINE: 10, UnitID.MARAUDER: 5}
        result = classifier.classify(
            comp, set(), "Terran", 300.0, 2, 15,
        )
        assert result.air_fraction == 0.0

    def test_bio_not_armor_heavy(self, classifier):
        """BIO comp -> anti_armor_priority=False."""
        comp = {UnitID.MARINE: 15, UnitID.MARAUDER: 3, UnitID.MEDIVAC: 2}
        result = classifier.classify(
            comp, set(), "Terran", 300.0, 2, 20,
        )
        assert result.anti_armor_priority is False
