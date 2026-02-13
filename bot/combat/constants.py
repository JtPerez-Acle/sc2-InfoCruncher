"""Shared enums, dataclasses, and constants for the combat system.

Every other module in bot/combat/ imports from here. No circular deps.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING

from sc2.ids.unit_typeid import UnitTypeId as UnitID
from sc2.position import Point2

from ares.consts import UnitRole

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Combat objective — what the army is trying to accomplish
# ---------------------------------------------------------------------------

class CombatObjective(Enum):
    ATTACK_BASE = auto()
    ATTACK_EXPANSION = auto()
    ATTACK_ARMY = auto()
    DEFEND = auto()
    HARASS = auto()
    CONTAIN = auto()
    REGROUP = auto()


# ---------------------------------------------------------------------------
# Approach style — HOW we approach the objective
# ---------------------------------------------------------------------------

class ApproachStyle(Enum):
    FRONTAL = auto()
    FLANK = auto()
    MULTI_PRONG = auto()
    CONTAIN = auto()


# ---------------------------------------------------------------------------
# Kite style — per-unit micro behavior in engagement
# ---------------------------------------------------------------------------

class KiteStyle(Enum):
    STUTTER_BACK = auto()
    STUTTER_FORWARD = auto()
    HOLD_POSITION = auto()
    AGGRESSIVE = auto()


# ---------------------------------------------------------------------------
# Squad assignment — what each sub-group is doing
# ---------------------------------------------------------------------------

class SquadRole(Enum):
    MAIN_ARMY = auto()
    HARASS = auto()
    DEFENSE = auto()
    RALLYING = auto()


# ---------------------------------------------------------------------------
# Layer 1 output — BattlefieldState
# ---------------------------------------------------------------------------

@dataclass
class EnemyArmyEstimate:
    """Estimated state of the enemy army, even when not visible."""

    last_known_position: Point2 | None = None
    last_seen_time: float = 0.0
    estimated_position: Point2 | None = None
    heading: Point2 | None = None
    confidence: float = 0.0
    estimated_supply: float = 0.0
    contains_air: bool = False


@dataclass
class OpportunityTarget:
    """A potential attack target scored by value and risk."""

    position: Point2 = field(default_factory=lambda: Point2((0, 0)))
    target_type: str = "main"
    base_value: float = 0.0
    score: float = 0.0
    risk: float = 0.0
    enemy_army_distance: float = 999.0
    tags: set[int] = field(default_factory=set)


@dataclass
class TerrainFeature:
    """A notable terrain feature along a path."""

    position: Point2 = field(default_factory=lambda: Point2((0, 0)))
    feature_type: str = "choke"
    width: float = 0.0


@dataclass
class BattlefieldState:
    """Complete battlefield picture — output of Layer 1."""

    # Enemy army
    enemy_army: EnemyArmyEstimate = field(default_factory=EnemyArmyEstimate)

    # Our army state
    our_army_supply: float = 0.0
    our_army_center: Point2 | None = None
    peak_army_supply: float = 0.0
    army_wipes: int = 0
    last_wipe_time: float = 0.0

    # Opportunity targets (sorted by score descending)
    opportunity_targets: list[OpportunityTarget] = field(default_factory=list)

    # Terrain along attack path
    terrain_features: list[TerrainFeature] = field(default_factory=list)

    # Staging point for rally
    staging_point: Point2 | None = None

    # Base threats
    threatened_bases: list[Point2] = field(default_factory=list)
    threat_units_near_bases: int = 0

    # Overall confidence (info-gain driven)
    confidence: float = 0.5

    # Game time
    game_time: float = 0.0


# ---------------------------------------------------------------------------
# Layer 2 output — CombatPlan
# ---------------------------------------------------------------------------

@dataclass
class SquadAssignment:
    """Assignment for a single squad."""

    role: SquadRole = SquadRole.MAIN_ARMY
    target: Point2 = field(default_factory=lambda: Point2((0, 0)))
    unit_tags: set[int] = field(default_factory=set)
    kite_style: KiteStyle = KiteStyle.STUTTER_BACK
    priority_targets: list[UnitID] = field(default_factory=list)
    avoid_targets: list[UnitID] = field(default_factory=list)


@dataclass
class CombatPlan:
    """Complete combat plan — output of Layer 2."""

    objective: CombatObjective = CombatObjective.REGROUP
    approach: ApproachStyle = ApproachStyle.FRONTAL
    primary_target: Point2 | None = None
    retreat_point: Point2 | None = None

    # Squad assignments
    squads: list[SquadAssignment] = field(default_factory=list)

    # Engagement rules
    engage_if_sim_wins: bool = True
    force_engage: bool = False
    allow_split: bool = False
    max_harass_supply: float = 0.0

    # Micro profile name (key into ENGAGEMENT_PROFILES)
    profile_name: str = "DEFAULT"

    # Attack timing
    attack_committed: bool = False
    commit_start_time: float = 0.0


# ---------------------------------------------------------------------------
# Unit type sets (preserved from old combat.py)
# ---------------------------------------------------------------------------

RANGED_TYPES: set[UnitID] = {
    UnitID.ROACH, UnitID.HYDRALISK, UnitID.RAVAGER,
}
MELEE_TYPES: set[UnitID] = {
    UnitID.ZERGLING, UnitID.BANELING, UnitID.ULTRALISK,
}
AOE_TYPES: set[UnitID] = {
    UnitID.RAVAGER, UnitID.INFESTOR, UnitID.VIPER,
}
FAST_TYPES: set[UnitID] = {
    UnitID.ZERGLING, UnitID.BANELING, UnitID.MUTALISK,
}
FLYER_TYPES: set[UnitID] = {
    UnitID.MUTALISK, UnitID.CORRUPTOR, UnitID.BROODLORD, UnitID.VIPER,
}
WORKER_TYPES: set[UnitID] = {
    UnitID.SCV, UnitID.PROBE, UnitID.DRONE,
}
STATIC_DEFENSE_TYPES: set[UnitID] = {
    UnitID.PHOTONCANNON, UnitID.SHIELDBATTERY,
    UnitID.BUNKER, UnitID.MISSILETURRET, UnitID.PLANETARYFORTRESS,
    UnitID.SPINECRAWLER, UnitID.SPORECRAWLER,
}
SUPPORT_TYPES: set[UnitID] = {
    UnitID.MEDIVAC, UnitID.WARPPRISM, UnitID.SENTRY,
    UnitID.RAVEN, UnitID.VIPER, UnitID.INFESTOR,
}
EXPANSION_TYPES: set[UnitID] = {
    UnitID.NEXUS, UnitID.COMMANDCENTER, UnitID.COMMANDCENTERFLYING,
    UnitID.ORBITALCOMMAND, UnitID.ORBITALCOMMANDFLYING,
    UnitID.PLANETARYFORTRESS,
    UnitID.HATCHERY, UnitID.LAIR, UnitID.HIVE,
}

# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------

MIN_ATTACK_SUPPLY: float = 20.0
REGROUP_RATIO: float = 0.55
WIPE_COOLDOWN: float = 15.0
PEAK_DECAY_RATE: float = 0.15
ATTACK_COMMIT_TIME: float = 20.0
HARASS_MIN_ARMY_SUPPLY: float = 60.0
HARASS_MAX_FRACTION: float = 0.25

# Role for units rallying to staging point during REGROUP
RALLYING_ROLE = UnitRole.CONTROL_GROUP_ONE
