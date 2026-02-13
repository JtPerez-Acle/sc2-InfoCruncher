"""Engagement profiles per enemy archetype.

Each profile defines HOW to fight a specific enemy composition:
  - focus_targets: Priority kill order (highest priority first)
  - avoid_targets: Don't chase these units
  - kite_style: Default kite behavior for ranged units
  - spread_distance: How far apart units should be (anti-splash)
  - ability_priorities: Hint for AOE ability targeting
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sc2.ids.unit_typeid import UnitTypeId as UnitID

from bot.combat.constants import KiteStyle


@dataclass(frozen=True)
class EngagementProfile:
    """How to fight a specific enemy archetype."""

    name: str
    focus_targets: tuple[UnitID, ...] = ()
    avoid_targets: tuple[UnitID, ...] = ()
    kite_style: KiteStyle = KiteStyle.STUTTER_BACK
    spread_distance: float = 2.0
    melee_aggressive: bool = True
    support_focus: bool = True
    ability_priorities: tuple[str, ...] = ()
    allow_harass: bool = True
    preferred_harass_unit: UnitID | None = None


# ---------------------------------------------------------------------------
# Profile definitions — one per archetype from CompositionClassifier
# ---------------------------------------------------------------------------

ENGAGEMENT_PROFILES: dict[str, EngagementProfile] = {
    "DEFAULT": EngagementProfile(
        name="DEFAULT",
        kite_style=KiteStyle.STUTTER_BACK,
        spread_distance=2.0,
    ),

    # ======= TERRAN =======

    "BIO": EngagementProfile(
        name="BIO",
        focus_targets=(
            UnitID.MEDIVAC, UnitID.MARINE, UnitID.MARAUDER, UnitID.GHOST,
        ),
        kite_style=KiteStyle.STUTTER_BACK,
        spread_distance=3.0,
        ability_priorities=("bile_clumps",),
        preferred_harass_unit=UnitID.ZERGLING,
    ),

    "MECH": EngagementProfile(
        name="MECH",
        focus_targets=(
            UnitID.SIEGETANK, UnitID.THOR, UnitID.CYCLONE, UnitID.HELLION,
        ),
        avoid_targets=(UnitID.SIEGETANKSIEGED,),
        kite_style=KiteStyle.STUTTER_BACK,
        spread_distance=4.0,
        ability_priorities=("bile_siege_tanks",),
        preferred_harass_unit=UnitID.MUTALISK,
    ),

    "BIO_MECH": EngagementProfile(
        name="BIO_MECH",
        focus_targets=(
            UnitID.MEDIVAC, UnitID.SIEGETANK, UnitID.MARINE, UnitID.MARAUDER,
        ),
        kite_style=KiteStyle.STUTTER_BACK,
        spread_distance=3.5,
        ability_priorities=("bile_siege_tanks", "bile_clumps"),
        preferred_harass_unit=UnitID.ZERGLING,
    ),

    "AIR_TERRAN": EngagementProfile(
        name="AIR_TERRAN",
        focus_targets=(
            UnitID.LIBERATOR, UnitID.BANSHEE, UnitID.BATTLECRUISER,
            UnitID.VIKING, UnitID.RAVEN,
        ),
        kite_style=KiteStyle.STUTTER_FORWARD,
        spread_distance=2.5,
        preferred_harass_unit=UnitID.MUTALISK,
    ),

    # ======= PROTOSS =======

    "GATEWAY": EngagementProfile(
        name="GATEWAY",
        focus_targets=(
            UnitID.SENTRY, UnitID.HIGHTEMPLAR, UnitID.STALKER,
            UnitID.ZEALOT, UnitID.ADEPT,
        ),
        kite_style=KiteStyle.STUTTER_BACK,
        spread_distance=3.5,
        ability_priorities=("bile_forcefields",),
    ),

    "ROBO": EngagementProfile(
        name="ROBO",
        focus_targets=(
            UnitID.IMMORTAL, UnitID.COLOSSUS, UnitID.DISRUPTOR,
            UnitID.WARPPRISM,
        ),
        kite_style=KiteStyle.STUTTER_BACK,
        spread_distance=4.0,
        ability_priorities=("bile_colossus",),
    ),

    "STARGATE": EngagementProfile(
        name="STARGATE",
        focus_targets=(
            UnitID.ORACLE, UnitID.PHOENIX, UnitID.VOIDRAY,
        ),
        kite_style=KiteStyle.STUTTER_BACK,
        spread_distance=2.0,
    ),

    "SKYTOSS": EngagementProfile(
        name="SKYTOSS",
        focus_targets=(
            UnitID.CARRIER, UnitID.TEMPEST, UnitID.VOIDRAY,
        ),
        kite_style=KiteStyle.STUTTER_FORWARD,
        spread_distance=2.0,
        melee_aggressive=False,
        ability_priorities=("abduct_carriers",),
        preferred_harass_unit=UnitID.MUTALISK,
    ),

    "GATEWAY_ROBO": EngagementProfile(
        name="GATEWAY_ROBO",
        focus_targets=(
            UnitID.IMMORTAL, UnitID.ARCHON, UnitID.SENTRY,
            UnitID.HIGHTEMPLAR, UnitID.STALKER,
        ),
        kite_style=KiteStyle.STUTTER_BACK,
        spread_distance=3.5,
        ability_priorities=("bile_forcefields",),
    ),

    # ======= ZERG (mirror) =======

    "LING_BANE": EngagementProfile(
        name="LING_BANE",
        focus_targets=(UnitID.BANELING, UnitID.ZERGLING),
        avoid_targets=(UnitID.BANELING,),
        kite_style=KiteStyle.STUTTER_BACK,
        spread_distance=3.0,
    ),

    "ROACH_HYDRA": EngagementProfile(
        name="ROACH_HYDRA",
        focus_targets=(
            UnitID.HYDRALISK, UnitID.RAVAGER, UnitID.ROACH,
        ),
        kite_style=KiteStyle.STUTTER_BACK,
        spread_distance=2.5,
    ),

    "MUTA_LING": EngagementProfile(
        name="MUTA_LING",
        focus_targets=(UnitID.MUTALISK, UnitID.ZERGLING, UnitID.BANELING),
        kite_style=KiteStyle.STUTTER_BACK,
        spread_distance=1.5,
    ),

    "LATE_ZERG": EngagementProfile(
        name="LATE_ZERG",
        focus_targets=(
            UnitID.INFESTOR, UnitID.VIPER, UnitID.BROODLORD, UnitID.ULTRALISK,
        ),
        kite_style=KiteStyle.STUTTER_BACK,
        spread_distance=4.0,
        ability_priorities=("fungal_dodge", "bile_clumps"),
    ),

    # ======= CROSS-RACE OVERLAYS =======

    "AGGRESSIVE": EngagementProfile(
        name="AGGRESSIVE",
        kite_style=KiteStyle.STUTTER_BACK,
        melee_aggressive=True,
        allow_harass=False,
        spread_distance=2.0,
    ),

    "TURTLE": EngagementProfile(
        name="TURTLE",
        focus_targets=(
            UnitID.PLANETARYFORTRESS, UnitID.BUNKER,
            UnitID.PHOTONCANNON, UnitID.SIEGETANKSIEGED,
        ),
        kite_style=KiteStyle.HOLD_POSITION,
        ability_priorities=("bile_structures",),
        allow_harass=True,
        preferred_harass_unit=UnitID.ZERGLING,
    ),

    "MACRO": EngagementProfile(
        name="MACRO",
        kite_style=KiteStyle.STUTTER_FORWARD,
        melee_aggressive=True,
        allow_harass=True,
        spread_distance=2.0,
        preferred_harass_unit=UnitID.ZERGLING,
    ),

    "UNKNOWN": EngagementProfile(
        name="UNKNOWN",
        kite_style=KiteStyle.STUTTER_BACK,
        spread_distance=2.0,
    ),
}


def get_profile(archetype_name: str) -> EngagementProfile:
    """Look up engagement profile by archetype name, fallback to DEFAULT."""
    return ENGAGEMENT_PROFILES.get(archetype_name, ENGAGEMENT_PROFILES["DEFAULT"])
