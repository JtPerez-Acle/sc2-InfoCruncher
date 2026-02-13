"""Enemy composition classifier.

Reads cumulative enemy data from StrategicMemory and classifies the
enemy strategy into actionable archetypes. This drives:
  - Dynamic anti-air floor (0-55% instead of static 25%)
  - Composition-aware counter recommendations (mech vs bio vs air)
  - Gas scaling in economy model
  - Attack timing adjustments

Classification is rule-based and race-specific, using weighted unit
counts + structure signatures from accumulated scouting data.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from sc2.ids.unit_typeid import UnitTypeId as UnitID

from bot.strategy.unit_counter_db import UNIT_PROFILES, get_unit_value


# ---------------------------------------------------------------------------
# Archetypes
# ---------------------------------------------------------------------------

class EnemyArchetype(Enum):
    """Recognized enemy strategy patterns."""

    UNKNOWN = auto()         # Haven't scouted enough
    # Terran
    BIO = auto()             # Marine/Marauder/Medivac
    MECH = auto()            # Hellion/Tank/Thor
    BIO_MECH = auto()        # Mixed Terran
    AIR_TERRAN = auto()      # Liberator/Banshee/BC heavy
    # Protoss
    GATEWAY = auto()         # Zealot/Stalker/Sentry
    ROBO = auto()            # Immortal/Colossus/Disruptor
    STARGATE = auto()        # Phoenix/Void Ray/Oracle
    SKYTOSS = auto()         # Carrier/Tempest/Void Ray (mass air)
    GATEWAY_ROBO = auto()    # Mixed Protoss ground
    # Zerg (mirror)
    LING_BANE = auto()       # Zergling/Baneling aggro
    ROACH_HYDRA = auto()     # Roach/Hydra mid-game
    MUTA_LING = auto()       # Mutalisk harass
    LATE_ZERG = auto()       # Infestor/Viper/Broodlord/Ultra late-game
    # Cross-race overlays
    AGGRESSIVE = auto()      # Early pressure / all-in
    TURTLE = auto()          # Heavy static defense, low army
    MACRO = auto()           # Fast expand, economy-focused


# ---------------------------------------------------------------------------
# Unit groupings for classification
# ---------------------------------------------------------------------------

_TERRAN_BIO = {
    UnitID.MARINE, UnitID.MARAUDER, UnitID.GHOST, UnitID.REAPER,
}
_TERRAN_BIO_SUPPORT = {UnitID.MEDIVAC}
_TERRAN_MECH = {
    UnitID.HELLION, UnitID.HELLIONTANK, UnitID.SIEGETANK,
    UnitID.SIEGETANKSIEGED, UnitID.CYCLONE, UnitID.THOR, UnitID.WIDOWMINE,
}
_TERRAN_AIR = {
    UnitID.VIKING, UnitID.VIKINGFIGHTER, UnitID.LIBERATOR,
    UnitID.BANSHEE, UnitID.BATTLECRUISER, UnitID.RAVEN,
}

_PROTOSS_GATEWAY = {
    UnitID.ZEALOT, UnitID.STALKER, UnitID.ADEPT, UnitID.SENTRY,
    UnitID.HIGHTEMPLAR, UnitID.DARKTEMPLAR, UnitID.ARCHON,
}
_PROTOSS_ROBO = {
    UnitID.IMMORTAL, UnitID.COLOSSUS, UnitID.DISRUPTOR, UnitID.WARPPRISM,
}
_PROTOSS_STARGATE = {
    UnitID.PHOENIX, UnitID.VOIDRAY, UnitID.ORACLE,
    UnitID.CARRIER, UnitID.TEMPEST,
}
_PROTOSS_SKYTOSS_CORE = {UnitID.CARRIER, UnitID.TEMPEST, UnitID.VOIDRAY}

_ZERG_GROUND_AGGRO = {UnitID.ZERGLING, UnitID.BANELING}
_ZERG_ROACH_COMP = {UnitID.ROACH, UnitID.RAVAGER, UnitID.HYDRALISK, UnitID.LURKERMP}
_ZERG_AIR = {UnitID.MUTALISK, UnitID.CORRUPTOR, UnitID.BROODLORD, UnitID.VIPER}
_ZERG_LATE_SUPPORT = {UnitID.INFESTOR, UnitID.VIPER, UnitID.SWARMHOSTMP, UnitID.ULTRALISK}

# All air units across all races (for air_fraction calculation)
_ALL_AIR_UNITS = {
    # Terran
    UnitID.VIKING, UnitID.VIKINGFIGHTER, UnitID.LIBERATOR,
    UnitID.BANSHEE, UnitID.BATTLECRUISER, UnitID.RAVEN, UnitID.MEDIVAC,
    # Protoss
    UnitID.PHOENIX, UnitID.VOIDRAY, UnitID.ORACLE,
    UnitID.CARRIER, UnitID.TEMPEST, UnitID.WARPPRISM,
    # Zerg
    UnitID.MUTALISK, UnitID.CORRUPTOR, UnitID.BROODLORD,
    UnitID.VIPER, UnitID.OVERSEER,
}

# Static defense structures (for turtle detection)
_STATIC_DEFENSE = {
    UnitID.PHOTONCANNON, UnitID.SHIELDBATTERY,
    UnitID.BUNKER, UnitID.MISSILETURRET, UnitID.PLANETARYFORTRESS,
    UnitID.SPINECRAWLER, UnitID.SPORECRAWLER,
}

# Cloaked / burrowed units requiring detection
_CLOAKED_UNITS = {
    UnitID.DARKTEMPLAR, UnitID.BANSHEE, UnitID.GHOST, UnitID.WIDOWMINE,
    UnitID.LURKERMP, UnitID.INFESTOR, UnitID.ROACH,  # roach with burrow
}

# Air-producing structures (for precautionary AA floor)
_AIR_STRUCTURES = {
    UnitID.STARGATE, UnitID.FLEETBEACON,
    UnitID.STARPORT, UnitID.STARPORTFLYING, UnitID.FUSIONCORE,
    UnitID.SPIRE, UnitID.GREATERSPIRE,
}

# Armored composition indicators
_ARMORED_HEAVY = {
    UnitID.SIEGETANK, UnitID.SIEGETANKSIEGED, UnitID.THOR, UnitID.CYCLONE,
    UnitID.IMMORTAL, UnitID.COLOSSUS, UnitID.STALKER,
    UnitID.ROACH, UnitID.RAVAGER, UnitID.ULTRALISK,
}

# Expansion structures
_EXPANSION_TYPES = {
    UnitID.NEXUS, UnitID.COMMANDCENTER, UnitID.COMMANDCENTERFLYING,
    UnitID.ORBITALCOMMAND, UnitID.ORBITALCOMMANDFLYING,
    UnitID.PLANETARYFORTRESS,
    UnitID.HATCHERY, UnitID.LAIR, UnitID.HIVE,
}


# ---------------------------------------------------------------------------
# Output dataclass
# ---------------------------------------------------------------------------

@dataclass
class CompositionAnalysis:
    """Result of enemy composition classification."""

    archetype: EnemyArchetype
    confidence: float            # 0.0-1.0: how much data backs this classification
    air_fraction: float          # 0.0-1.0: what fraction of enemy army is air
    ground_fraction: float       # complement of air_fraction
    is_mech_heavy: bool          # Terran mech flag
    dominant_damage_type: str    # "ground" | "air" | "mixed"
    recommended_aa_floor: float  # dynamic anti-air minimum (0.0-0.55)
    needs_detection: bool        # enemy has cloaked/burrowed units
    anti_armor_priority: bool    # enemy is armor-heavy (mech, robo)


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

class CompositionClassifier:
    """Classifies enemy strategy from accumulated scouting data.

    Produces a CompositionAnalysis that drives composition recommendations,
    anti-air scaling, gas adjustments, and attack timing.
    """

    # Minimum total unit value to attempt classification
    MIN_DATA_VALUE: float = 200.0

    def classify(
        self,
        cumulative_comp: dict[UnitID, int],
        structures_ever: set[UnitID],
        enemy_race: str,
        game_time: float,
        enemy_base_count: int,
        visible_army_count: int,
    ) -> CompositionAnalysis:
        """Classify enemy composition from accumulated data.

        Args:
            cumulative_comp: Max-ever-seen count per enemy unit type (from memory)
            structures_ever: All enemy structures ever observed
            enemy_race: "Protoss" | "Terran" | "Zerg" | "Unknown"
            game_time: Current game time in seconds
            enemy_base_count: Number of known enemy bases
            visible_army_count: Current visible enemy army unit count
        """
        # --- Compute derived metrics ---
        air_fraction = self._compute_air_fraction(cumulative_comp)
        ground_fraction = 1.0 - air_fraction
        total_value = self._total_army_value(cumulative_comp)
        needs_detection = self._needs_detection(cumulative_comp, structures_ever)
        anti_armor = self._is_armor_heavy(cumulative_comp)

        # --- Check cross-race overlays first ---
        archetype = self._check_overlays(
            cumulative_comp, structures_ever, game_time,
            enemy_base_count, visible_army_count, total_value,
        )

        # --- Race-specific classification ---
        if archetype == EnemyArchetype.UNKNOWN and total_value >= self.MIN_DATA_VALUE:
            if enemy_race == "Terran":
                archetype = self._classify_terran(
                    cumulative_comp, structures_ever
                )
            elif enemy_race == "Protoss":
                archetype = self._classify_protoss(
                    cumulative_comp, structures_ever
                )
            elif enemy_race == "Zerg":
                archetype = self._classify_zerg(cumulative_comp)

        # --- Compute confidence ---
        confidence = self._compute_confidence(
            total_value, game_time, archetype
        )

        # --- Dynamic anti-air floor ---
        aa_floor = self._compute_aa_floor(
            air_fraction, structures_ever, archetype
        )

        # --- Dominant damage type ---
        is_mech = archetype in (EnemyArchetype.MECH, EnemyArchetype.BIO_MECH)
        if air_fraction > 0.6:
            dominant_damage = "air"
        elif air_fraction > 0.3:
            dominant_damage = "mixed"
        else:
            dominant_damage = "ground"

        return CompositionAnalysis(
            archetype=archetype,
            confidence=confidence,
            air_fraction=air_fraction,
            ground_fraction=ground_fraction,
            is_mech_heavy=is_mech,
            dominant_damage_type=dominant_damage,
            recommended_aa_floor=aa_floor,
            needs_detection=needs_detection,
            anti_armor_priority=anti_armor,
        )

    # ------------------------------------------------------------------
    # Cross-race overlay checks (run first, override race-specific)
    # ------------------------------------------------------------------

    def _check_overlays(
        self,
        comp: dict[UnitID, int],
        structures: set[UnitID],
        game_time: float,
        enemy_base_count: int,
        visible_army_count: int,
        total_value: float,
    ) -> EnemyArchetype:
        """Check for cross-race strategy patterns."""

        # Turtle: heavy static defense relative to army
        static_count = sum(1 for s in structures if s in _STATIC_DEFENSE)
        army_count = sum(
            count for uid, count in comp.items()
            if uid not in _STATIC_DEFENSE and uid not in _EXPANSION_TYPES
        )
        if static_count >= 3 and army_count < 10:
            return EnemyArchetype.TURTLE
        if game_time > 360 and static_count >= 2 and army_count < 5:
            return EnemyArchetype.TURTLE

        # Mech turtle variant: Factory+Armory+Tanks but few bases
        if (UnitID.FACTORY in structures or UnitID.FACTORYFLYING in structures):
            if UnitID.ARMORY in structures:
                tank_count = comp.get(UnitID.SIEGETANK, 0) + comp.get(
                    UnitID.SIEGETANKSIEGED, 0
                )
                if tank_count >= 2 and enemy_base_count <= 2 and static_count >= 1:
                    return EnemyArchetype.TURTLE

        # Aggressive: army before expansion, early game
        if game_time < 180 and total_value > 300 and enemy_base_count <= 1:
            return EnemyArchetype.AGGRESSIVE
        # Also aggressive if significant army without natural at 3 minutes
        if game_time < 240 and total_value > 500 and enemy_base_count <= 1:
            return EnemyArchetype.AGGRESSIVE

        # Macro: fast expand + low visible army (greedy play)
        if (enemy_base_count >= 3 and visible_army_count < 5
                and game_time < 300 and total_value < 400):
            return EnemyArchetype.MACRO

        return EnemyArchetype.UNKNOWN

    # ------------------------------------------------------------------
    # Terran classification
    # ------------------------------------------------------------------

    def _classify_terran(
        self,
        comp: dict[UnitID, int],
        structures: set[UnitID],
    ) -> EnemyArchetype:
        """Classify Terran composition."""
        bio_value = self._group_value(comp, _TERRAN_BIO | _TERRAN_BIO_SUPPORT)
        mech_value = self._group_value(comp, _TERRAN_MECH)
        air_value = self._group_value(comp, _TERRAN_AIR)
        total = bio_value + mech_value + air_value

        if total <= 0:
            return EnemyArchetype.UNKNOWN

        air_ratio = air_value / total

        # Air-heavy Terran (>40% air by value)
        if air_ratio > 0.4:
            return EnemyArchetype.AIR_TERRAN

        ground_total = bio_value + mech_value
        if ground_total <= 0:
            return EnemyArchetype.UNKNOWN

        mech_ratio = mech_value / ground_total

        # Mech: >66% mech by value
        if mech_ratio > 0.66:
            return EnemyArchetype.MECH

        # Bio: <33% mech by value
        if mech_ratio < 0.33:
            return EnemyArchetype.BIO

        # Mixed
        return EnemyArchetype.BIO_MECH

    # ------------------------------------------------------------------
    # Protoss classification
    # ------------------------------------------------------------------

    def _classify_protoss(
        self,
        comp: dict[UnitID, int],
        structures: set[UnitID],
    ) -> EnemyArchetype:
        """Classify Protoss composition."""
        gateway_value = self._group_value(comp, _PROTOSS_GATEWAY)
        robo_value = self._group_value(comp, _PROTOSS_ROBO)
        stargate_value = self._group_value(comp, _PROTOSS_STARGATE)
        total = gateway_value + robo_value + stargate_value

        if total <= 0:
            return EnemyArchetype.UNKNOWN

        # Skytoss check: mass Carriers/Tempests/VoidRays
        skytoss_count = sum(comp.get(uid, 0) for uid in _PROTOSS_SKYTOSS_CORE)
        carrier_count = comp.get(UnitID.CARRIER, 0)
        tempest_count = comp.get(UnitID.TEMPEST, 0)
        voidray_count = comp.get(UnitID.VOIDRAY, 0)

        if (carrier_count >= 3
                or tempest_count >= 3
                or (carrier_count + voidray_count) >= 5
                or skytoss_count >= 6):
            return EnemyArchetype.SKYTOSS

        stargate_ratio = stargate_value / total
        robo_ratio = robo_value / total
        gateway_ratio = gateway_value / total

        # Stargate-heavy (>40%)
        if stargate_ratio > 0.4:
            return EnemyArchetype.STARGATE

        # Robo-heavy (>40%)
        if robo_ratio > 0.4:
            return EnemyArchetype.ROBO

        # Mixed gateway + robo
        if gateway_ratio > 0.3 and robo_ratio > 0.2:
            return EnemyArchetype.GATEWAY_ROBO

        # Default gateway
        return EnemyArchetype.GATEWAY

    # ------------------------------------------------------------------
    # Zerg classification (mirror)
    # ------------------------------------------------------------------

    def _classify_zerg(self, comp: dict[UnitID, int]) -> EnemyArchetype:
        """Classify Zerg composition."""
        aggro_value = self._group_value(comp, _ZERG_GROUND_AGGRO)
        roach_value = self._group_value(comp, _ZERG_ROACH_COMP)
        air_value = self._group_value(comp, _ZERG_AIR)
        late_value = self._group_value(comp, _ZERG_LATE_SUPPORT)
        total = aggro_value + roach_value + air_value + late_value

        if total <= 0:
            return EnemyArchetype.UNKNOWN

        # Late-game detection — Infestor/Viper/Ultra/SwarmHost presence
        if late_value / total > 0.15 or (late_value > 0 and air_value / total > 0.2):
            return EnemyArchetype.LATE_ZERG

        # Mutalisk presence is distinctive
        muta_count = comp.get(UnitID.MUTALISK, 0)
        if muta_count >= 3 or (air_value / total > 0.3):
            return EnemyArchetype.MUTA_LING

        if roach_value / total > 0.4:
            return EnemyArchetype.ROACH_HYDRA

        if aggro_value / total > 0.5:
            return EnemyArchetype.LING_BANE

        return EnemyArchetype.ROACH_HYDRA  # safe default for ZvZ

    # ------------------------------------------------------------------
    # Derived metrics
    # ------------------------------------------------------------------

    def _compute_air_fraction(self, comp: dict[UnitID, int]) -> float:
        """Fraction of enemy army value that is air units."""
        air_value = 0.0
        total_value = 0.0
        for uid, count in comp.items():
            if count <= 0:
                continue
            value = count * get_unit_value(uid)
            total_value += value
            if uid in _ALL_AIR_UNITS:
                air_value += value
        if total_value <= 0:
            return 0.0
        return air_value / total_value

    def _total_army_value(self, comp: dict[UnitID, int]) -> float:
        """Total cost of all units in composition."""
        return sum(
            count * get_unit_value(uid)
            for uid, count in comp.items()
            if count > 0
        )

    def _group_value(
        self, comp: dict[UnitID, int], group: set[UnitID]
    ) -> float:
        """Sum value of units in a group."""
        return sum(
            comp.get(uid, 0) * get_unit_value(uid)
            for uid in group
            if comp.get(uid, 0) > 0
        )

    def _needs_detection(
        self, comp: dict[UnitID, int], structures: set[UnitID]
    ) -> bool:
        """Does the enemy have cloaked/burrowed units?"""
        for uid in _CLOAKED_UNITS:
            if comp.get(uid, 0) > 0:
                return True
        # Structure indicators
        if UnitID.DARKSHRINE in structures:
            return True  # Dark Templar incoming
        return False

    def _is_armor_heavy(self, comp: dict[UnitID, int]) -> bool:
        """Is the enemy composition armor-heavy (mech, robo)?"""
        armored_value = self._group_value(comp, _ARMORED_HEAVY)
        total = self._total_army_value(comp)
        if total <= 0:
            return False
        return (armored_value / total) > 0.5

    # ------------------------------------------------------------------
    # Confidence
    # ------------------------------------------------------------------

    def _compute_confidence(
        self,
        total_value: float,
        game_time: float,
        archetype: EnemyArchetype,
    ) -> float:
        """How confident are we in the classification?

        Based on amount of data (army value seen) relative to expected
        game progression. More data = higher confidence.
        """
        if archetype == EnemyArchetype.UNKNOWN:
            return 0.0

        # Expected enemy army value at this game time (rough estimate)
        # ~1 marine/sec starting at 2 minutes = ~50 value/sec
        expected = max(200.0, (game_time - 120) * 50) if game_time > 120 else 200.0

        # Confidence = data / expected, capped at 1.0
        raw = total_value / expected
        return min(1.0, max(0.1, raw))

    # ------------------------------------------------------------------
    # Dynamic anti-air floor
    # ------------------------------------------------------------------

    def _compute_aa_floor(
        self,
        air_fraction: float,
        structures: set[UnitID],
        archetype: EnemyArchetype,
    ) -> float:
        """Compute dynamic anti-air composition floor.

        Scales from 0.0 (no air threat) to 0.55 (mass air).
        """
        # Skytoss gets maximum priority
        if archetype == EnemyArchetype.SKYTOSS:
            return 0.55

        # Air Terran is serious
        if archetype == EnemyArchetype.AIR_TERRAN:
            return 0.45

        # Scale by observed air fraction
        if air_fraction > 0.75:
            return 0.55
        if air_fraction > 0.50:
            return 0.40
        if air_fraction > 0.25:
            return 0.25
        if air_fraction > 0.10:
            return 0.15

        # Precautionary: air-producing structures seen but no air units yet
        if structures & _AIR_STRUCTURES:
            return 0.10

        return 0.0
