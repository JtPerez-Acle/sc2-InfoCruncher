"""Static SC2 unit data and counter relationships.

Pure data module — no game state, no side effects.
All counter relationships come from established SC2 knowledge:
  - Unit attributes (Light, Armored, Biological, Mechanical, etc.)
  - Bonus damage types (+vs Armored, +vs Light, etc.)
  - Empirical counter charts (Liquipedia, Osiris SC2 guide)

This is the "knowledge" layer — SC2 facts, not decisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sc2.ids.unit_typeid import UnitTypeId as UnitID


@dataclass(frozen=True)
class UnitProfile:
    """Static combat profile for a single unit type."""

    unit_id: UnitID
    race: str  # "Protoss" | "Terran" | "Zerg"
    attributes: frozenset[str]  # {"Light", "Armored", "Biological", etc.}
    hp: int
    shields: int = 0  # Protoss only
    ground_dps: float = 0.0
    air_dps: float = 0.0
    ground_range: float = 0.0
    speed: float = 0.0
    supply: float = 0
    mineral_cost: int = 0
    gas_cost: int = 0
    can_attack_air: bool = False
    can_attack_ground: bool = True
    bonus_vs: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Unit profiles — core combat stats
# ---------------------------------------------------------------------------

UNIT_PROFILES: dict[UnitID, UnitProfile] = {
    # ========================= ZERG =========================
    UnitID.ZERGLING: UnitProfile(
        unit_id=UnitID.ZERGLING, race="Zerg",
        attributes=frozenset({"Biological", "Light"}),
        hp=35, ground_dps=10.0, ground_range=0.1, speed=6.58,
        supply=0.5, mineral_cost=25, gas_cost=0,
    ),
    UnitID.BANELING: UnitProfile(
        unit_id=UnitID.BANELING, race="Zerg",
        attributes=frozenset({"Biological"}),
        hp=30, ground_dps=16.0, ground_range=0.25, speed=3.5,
        supply=0.5, mineral_cost=25, gas_cost=25,
        bonus_vs={"Light": 19.0},
    ),
    UnitID.ROACH: UnitProfile(
        unit_id=UnitID.ROACH, race="Zerg",
        attributes=frozenset({"Armored", "Biological"}),
        hp=145, ground_dps=11.2, ground_range=4.0, speed=3.15,
        supply=2, mineral_cost=75, gas_cost=25,
    ),
    UnitID.RAVAGER: UnitProfile(
        unit_id=UnitID.RAVAGER, race="Zerg",
        attributes=frozenset({"Biological"}),
        hp=120, ground_dps=11.2, ground_range=6.0, speed=3.85,
        supply=3, mineral_cost=25, gas_cost=75,
    ),
    UnitID.HYDRALISK: UnitProfile(
        unit_id=UnitID.HYDRALISK, race="Zerg",
        attributes=frozenset({"Biological", "Light"}),
        hp=90, ground_dps=16.8, air_dps=16.8, ground_range=5.0,
        speed=3.15, supply=2, mineral_cost=100, gas_cost=50,
        can_attack_air=True,
    ),
    UnitID.LURKERMP: UnitProfile(
        unit_id=UnitID.LURKERMP, race="Zerg",
        attributes=frozenset({"Biological", "Armored"}),
        hp=200, ground_dps=14.3, ground_range=8.0, speed=4.13,
        supply=3, mineral_cost=50, gas_cost=100,
        bonus_vs={"Armored": 10.0},
    ),
    UnitID.MUTALISK: UnitProfile(
        unit_id=UnitID.MUTALISK, race="Zerg",
        attributes=frozenset({"Biological", "Light"}),
        hp=120, ground_dps=7.6, air_dps=7.6, ground_range=3.0,
        speed=5.6, supply=2, mineral_cost=100, gas_cost=100,
        can_attack_air=True,
    ),
    UnitID.CORRUPTOR: UnitProfile(
        unit_id=UnitID.CORRUPTOR, race="Zerg",
        attributes=frozenset({"Armored", "Biological"}),
        hp=200, air_dps=10.3, ground_range=6.0, speed=4.725,
        supply=2, mineral_cost=150, gas_cost=100,
        can_attack_air=True, can_attack_ground=False,
        bonus_vs={"Massive": 6.0},
    ),
    UnitID.INFESTOR: UnitProfile(
        unit_id=UnitID.INFESTOR, race="Zerg",
        attributes=frozenset({"Armored", "Biological", "Psionic"}),
        hp=90, ground_range=0.0, speed=3.15,
        supply=2, mineral_cost=100, gas_cost=150,
    ),
    UnitID.SWARMHOSTMP: UnitProfile(
        unit_id=UnitID.SWARMHOSTMP, race="Zerg",
        attributes=frozenset({"Armored", "Biological"}),
        hp=160, ground_range=0.0, speed=3.15,
        supply=3, mineral_cost=100, gas_cost=75,
    ),
    UnitID.VIPER: UnitProfile(
        unit_id=UnitID.VIPER, race="Zerg",
        attributes=frozenset({"Armored", "Biological", "Psionic"}),
        hp=150, ground_range=0.0, speed=4.13,
        supply=3, mineral_cost=100, gas_cost=200,
        can_attack_air=False, can_attack_ground=False,
    ),
    UnitID.ULTRALISK: UnitProfile(
        unit_id=UnitID.ULTRALISK, race="Zerg",
        attributes=frozenset({"Armored", "Biological", "Massive"}),
        hp=500, ground_dps=23.3, ground_range=1.0, speed=4.13,
        supply=6, mineral_cost=275, gas_cost=200,
    ),
    UnitID.BROODLORD: UnitProfile(
        unit_id=UnitID.BROODLORD, race="Zerg",
        attributes=frozenset({"Armored", "Biological", "Massive"}),
        hp=225, ground_dps=11.2, ground_range=10.0, speed=2.24,
        supply=4, mineral_cost=150, gas_cost=150,
    ),
    # ========================= PROTOSS =========================
    UnitID.ZEALOT: UnitProfile(
        unit_id=UnitID.ZEALOT, race="Protoss",
        attributes=frozenset({"Light", "Biological"}),
        hp=100, shields=50, ground_dps=18.6, ground_range=0.1,
        speed=3.15, supply=2, mineral_cost=100, gas_cost=0,
    ),
    UnitID.ADEPT: UnitProfile(
        unit_id=UnitID.ADEPT, race="Protoss",
        attributes=frozenset({"Light", "Biological"}),
        hp=70, shields=70, ground_dps=7.0, ground_range=4.0,
        speed=3.5, supply=2, mineral_cost=100, gas_cost=25,
        bonus_vs={"Light": 12.0},
    ),
    UnitID.STALKER: UnitProfile(
        unit_id=UnitID.STALKER, race="Protoss",
        attributes=frozenset({"Armored", "Mechanical"}),
        hp=80, shields=80, ground_dps=9.7, air_dps=9.7,
        ground_range=6.0, speed=4.13, supply=2,
        mineral_cost=125, gas_cost=50,
        can_attack_air=True, bonus_vs={"Armored": 5.0},
    ),
    UnitID.SENTRY: UnitProfile(
        unit_id=UnitID.SENTRY, race="Protoss",
        attributes=frozenset({"Mechanical", "Psionic"}),
        hp=40, shields=40, ground_dps=6.0, air_dps=6.0,
        ground_range=5.0, speed=3.5, supply=2,
        mineral_cost=50, gas_cost=100,
        can_attack_air=True,
    ),
    UnitID.HIGHTEMPLAR: UnitProfile(
        unit_id=UnitID.HIGHTEMPLAR, race="Protoss",
        attributes=frozenset({"Biological", "Light", "Psionic"}),
        hp=40, shields=40, ground_dps=0.0, ground_range=6.0,
        speed=2.82, supply=2, mineral_cost=50, gas_cost=150,
    ),
    UnitID.DARKTEMPLAR: UnitProfile(
        unit_id=UnitID.DARKTEMPLAR, race="Protoss",
        attributes=frozenset({"Biological", "Light", "Psionic"}),
        hp=40, shields=80, ground_dps=37.2, ground_range=0.1,
        speed=3.94, supply=2, mineral_cost=125, gas_cost=125,
    ),
    UnitID.ARCHON: UnitProfile(
        unit_id=UnitID.ARCHON, race="Protoss",
        attributes=frozenset({"Psionic", "Massive"}),
        hp=350, shields=0, ground_dps=15.6, air_dps=15.6,
        ground_range=3.0, speed=3.94, supply=4,
        mineral_cost=0, gas_cost=0,
        can_attack_air=True, bonus_vs={"Biological": 10.0},
    ),
    UnitID.IMMORTAL: UnitProfile(
        unit_id=UnitID.IMMORTAL, race="Protoss",
        attributes=frozenset({"Armored", "Mechanical"}),
        hp=200, shields=100, ground_dps=15.2, ground_range=6.0,
        speed=3.15, supply=4, mineral_cost=250, gas_cost=100,
        bonus_vs={"Armored": 30.0},
    ),
    UnitID.COLOSSUS: UnitProfile(
        unit_id=UnitID.COLOSSUS, race="Protoss",
        attributes=frozenset({"Armored", "Massive", "Mechanical"}),
        hp=200, shields=150, ground_dps=18.7, ground_range=7.0,
        speed=3.15, supply=6, mineral_cost=300, gas_cost=200,
        bonus_vs={"Light": 5.0},
    ),
    UnitID.DISRUPTOR: UnitProfile(
        unit_id=UnitID.DISRUPTOR, race="Protoss",
        attributes=frozenset({"Armored", "Mechanical"}),
        hp=100, shields=100, ground_dps=0.0, ground_range=0.0,
        speed=3.15, supply=3, mineral_cost=150, gas_cost=150,
    ),
    UnitID.PHOENIX: UnitProfile(
        unit_id=UnitID.PHOENIX, race="Protoss",
        attributes=frozenset({"Light", "Mechanical"}),
        hp=120, shields=60, air_dps=12.7, ground_range=5.0,
        speed=5.95, supply=2, mineral_cost=150, gas_cost=100,
        can_attack_air=True, can_attack_ground=False,
        bonus_vs={"Light": 5.0},
    ),
    UnitID.VOIDRAY: UnitProfile(
        unit_id=UnitID.VOIDRAY, race="Protoss",
        attributes=frozenset({"Armored", "Mechanical"}),
        hp=150, shields=100, ground_dps=11.2, air_dps=11.2,
        ground_range=6.0, speed=3.85, supply=4,
        mineral_cost=250, gas_cost=150,
        can_attack_air=True, bonus_vs={"Armored": 4.0},
    ),
    UnitID.ORACLE: UnitProfile(
        unit_id=UnitID.ORACLE, race="Protoss",
        attributes=frozenset({"Mechanical", "Armored", "Psionic"}),
        hp=100, shields=60, ground_dps=15.0, ground_range=4.0,
        speed=5.6, supply=3, mineral_cost=150, gas_cost=150,
        bonus_vs={"Light": 7.0},
    ),
    UnitID.CARRIER: UnitProfile(
        unit_id=UnitID.CARRIER, race="Protoss",
        attributes=frozenset({"Armored", "Massive", "Mechanical"}),
        hp=300, shields=150, ground_dps=30.0, air_dps=30.0,
        ground_range=8.0, speed=2.62, supply=6,
        mineral_cost=350, gas_cost=250,
        can_attack_air=True,
    ),
    UnitID.TEMPEST: UnitProfile(
        unit_id=UnitID.TEMPEST, race="Protoss",
        attributes=frozenset({"Armored", "Massive", "Mechanical"}),
        hp=200, shields=100, ground_dps=4.6, air_dps=6.2,
        ground_range=10.0, speed=3.15, supply=4,
        mineral_cost=250, gas_cost=175,
        can_attack_air=True, bonus_vs={"Massive": 22.0},
    ),
    # ========================= TERRAN =========================
    UnitID.MARINE: UnitProfile(
        unit_id=UnitID.MARINE, race="Terran",
        attributes=frozenset({"Biological", "Light"}),
        hp=45, ground_dps=9.8, air_dps=9.8, ground_range=5.0,
        speed=3.15, supply=1, mineral_cost=50, gas_cost=0,
        can_attack_air=True,
    ),
    UnitID.MARAUDER: UnitProfile(
        unit_id=UnitID.MARAUDER, race="Terran",
        attributes=frozenset({"Armored", "Biological"}),
        hp=125, ground_dps=9.3, ground_range=6.0, speed=3.15,
        supply=2, mineral_cost=100, gas_cost=25,
        bonus_vs={"Armored": 10.0},
    ),
    UnitID.REAPER: UnitProfile(
        unit_id=UnitID.REAPER, race="Terran",
        attributes=frozenset({"Biological", "Light"}),
        hp=60, ground_dps=10.1, ground_range=5.0, speed=5.25,
        supply=1, mineral_cost=50, gas_cost=50,
    ),
    UnitID.GHOST: UnitProfile(
        unit_id=UnitID.GHOST, race="Terran",
        attributes=frozenset({"Biological", "Light", "Psionic"}),
        hp=100, ground_dps=9.3, air_dps=9.3, ground_range=6.0,
        speed=3.94, supply=2, mineral_cost=150, gas_cost=125,
        can_attack_air=True, bonus_vs={"Light": 10.0},
    ),
    UnitID.HELLION: UnitProfile(
        unit_id=UnitID.HELLION, race="Terran",
        attributes=frozenset({"Light", "Mechanical"}),
        hp=90, ground_dps=4.5, ground_range=5.0, speed=5.95,
        supply=2, mineral_cost=100, gas_cost=0,
        bonus_vs={"Light": 6.0},
    ),
    UnitID.HELLIONTANK: UnitProfile(
        unit_id=UnitID.HELLIONTANK, race="Terran",
        attributes=frozenset({"Biological", "Light", "Mechanical"}),
        hp=135, ground_dps=12.6, ground_range=2.0, speed=3.15,
        supply=2, mineral_cost=100, gas_cost=0,
        bonus_vs={"Light": 12.0},
    ),
    UnitID.WIDOWMINE: UnitProfile(
        unit_id=UnitID.WIDOWMINE, race="Terran",
        attributes=frozenset({"Mechanical", "Light"}),
        hp=90, ground_dps=0.0, ground_range=5.0, speed=3.94,
        supply=2, mineral_cost=75, gas_cost=25,
        can_attack_air=True,
    ),
    UnitID.SIEGETANK: UnitProfile(
        unit_id=UnitID.SIEGETANK, race="Terran",
        attributes=frozenset({"Armored", "Mechanical"}),
        hp=175, ground_dps=20.3, ground_range=13.0, speed=3.15,
        supply=3, mineral_cost=150, gas_cost=125,
        bonus_vs={"Armored": 0.0},  # Siege mode splash is the counter
    ),
    UnitID.SIEGETANKSIEGED: UnitProfile(
        unit_id=UnitID.SIEGETANKSIEGED, race="Terran",
        attributes=frozenset({"Armored", "Mechanical"}),
        hp=175, ground_dps=18.7, ground_range=13.0, speed=0.0,
        supply=3, mineral_cost=150, gas_cost=125,
    ),
    UnitID.CYCLONE: UnitProfile(
        unit_id=UnitID.CYCLONE, race="Terran",
        attributes=frozenset({"Armored", "Mechanical"}),
        hp=120, ground_dps=15.0, air_dps=15.0, ground_range=5.0,
        speed=4.725, supply=3, mineral_cost=150, gas_cost=100,
        can_attack_air=True,
    ),
    UnitID.THOR: UnitProfile(
        unit_id=UnitID.THOR, race="Terran",
        attributes=frozenset({"Armored", "Massive", "Mechanical"}),
        hp=400, ground_dps=17.6, air_dps=6.0, ground_range=7.0,
        speed=2.62, supply=6, mineral_cost=300, gas_cost=200,
        can_attack_air=True, bonus_vs={"Light": 6.0},
    ),
    UnitID.VIKING: UnitProfile(
        unit_id=UnitID.VIKING, race="Terran",
        attributes=frozenset({"Armored", "Mechanical"}),
        hp=135, air_dps=14.0, ground_range=9.0, speed=3.85,
        supply=2, mineral_cost=150, gas_cost=75,
        can_attack_air=True, bonus_vs={"Armored": 4.0},
    ),
    UnitID.VIKINGFIGHTER: UnitProfile(
        unit_id=UnitID.VIKINGFIGHTER, race="Terran",
        attributes=frozenset({"Armored", "Mechanical"}),
        hp=135, air_dps=14.0, ground_range=9.0, speed=3.85,
        supply=2, mineral_cost=150, gas_cost=75,
        can_attack_air=True, bonus_vs={"Armored": 4.0},
    ),
    UnitID.MEDIVAC: UnitProfile(
        unit_id=UnitID.MEDIVAC, race="Terran",
        attributes=frozenset({"Armored", "Mechanical"}),
        hp=150, speed=3.5, supply=2,
        mineral_cost=100, gas_cost=100,
        can_attack_ground=False,
    ),
    UnitID.LIBERATOR: UnitProfile(
        unit_id=UnitID.LIBERATOR, race="Terran",
        attributes=frozenset({"Armored", "Mechanical"}),
        hp=180, ground_dps=75.0, air_dps=7.0, ground_range=10.0,
        speed=4.72, supply=3, mineral_cost=150, gas_cost=125,
        can_attack_air=True,
    ),
    UnitID.BANSHEE: UnitProfile(
        unit_id=UnitID.BANSHEE, race="Terran",
        attributes=frozenset({"Light", "Mechanical"}),
        hp=140, ground_dps=16.8, ground_range=6.0, speed=3.85,
        supply=3, mineral_cost=150, gas_cost=100,
    ),
    UnitID.BATTLECRUISER: UnitProfile(
        unit_id=UnitID.BATTLECRUISER, race="Terran",
        attributes=frozenset({"Armored", "Massive", "Mechanical"}),
        hp=550, ground_dps=31.2, air_dps=22.4, ground_range=6.0,
        speed=2.62, supply=6, mineral_cost=400, gas_cost=300,
        can_attack_air=True,
    ),
    UnitID.RAVEN: UnitProfile(
        unit_id=UnitID.RAVEN, race="Terran",
        attributes=frozenset({"Light", "Mechanical"}),
        hp=140, speed=4.13, supply=2,
        mineral_cost=100, gas_cost=150,
        can_attack_ground=False,
    ),
}

# ---------------------------------------------------------------------------
# Counter map: enemy UnitTypeId → list of Zerg UnitTypeIds that counter it
# Ordered by effectiveness (first = best counter)
# ---------------------------------------------------------------------------

COUNTER_MAP: dict[UnitID, list[UnitID]] = {
    # === PROTOSS ===
    UnitID.ZEALOT: [
        UnitID.ROACH,          # Armored, range > melee, good HP
        UnitID.BANELING,       # Splash devastates groups of zealots
        UnitID.LURKERMP,       # Area denial, bonus vs armored... wait zealots are Light
    ],
    UnitID.ADEPT: [
        UnitID.ROACH,          # Armored survives shade harass
        UnitID.HYDRALISK,      # Outranges, good DPS
        UnitID.LURKERMP,       # Area denial
    ],
    UnitID.STALKER: [
        UnitID.ZERGLING,       # Cheap, surround, stalkers waste overkill
        UnitID.HYDRALISK,      # High DPS, trades well
        UnitID.ROACH,          # Tanks stalker damage
    ],
    UnitID.SENTRY: [
        UnitID.ZERGLING,       # Cheap, overwhelm force fields
        UnitID.BANELING,       # Blow through force field wall
        UnitID.RAVAGER,        # Bile destroys force fields
    ],
    UnitID.HIGHTEMPLAR: [
        UnitID.ZERGLING,       # Surround before storm, cheap to trade
        UnitID.MUTALISK,       # Fly over storms, harass
        UnitID.ULTRALISK,      # High HP tanks storms
    ],
    UnitID.DARKTEMPLAR: [
        UnitID.OVERSEER,       # Detection (not a combat unit, but essential)
        UnitID.ROACH,          # Fights well once detected
        UnitID.HYDRALISK,      # Range advantage once detected
    ],
    UnitID.ARCHON: [
        UnitID.ROACH,          # Armored trades OK, no bio bonus applies
        UnitID.HYDRALISK,      # Range + DPS
        UnitID.LURKERMP,       # Outranges, area denial
    ],
    UnitID.IMMORTAL: [
        UnitID.ZERGLING,       # Cheap swarm, immortal wastes overkill on lings
        UnitID.HYDRALISK,      # Light, avoids +armored bonus
        UnitID.MUTALISK,       # Can't shoot air
    ],
    UnitID.COLOSSUS: [
        UnitID.CORRUPTOR,      # Air vs ground, colossus counts as air target
        UnitID.MUTALISK,       # Fast, can target colossus
        UnitID.RAVAGER,        # Bile hits, outranges splash
    ],
    UnitID.DISRUPTOR: [
        UnitID.ZERGLING,       # Cheap, spread out to minimize purification
        UnitID.MUTALISK,       # Can't hit air
        UnitID.LURKERMP,       # Burrowed avoids purification nova
    ],
    UnitID.PHOENIX: [
        UnitID.HYDRALISK,      # Shoots air, high DPS
        UnitID.CORRUPTOR,      # Air-to-air specialist
        UnitID.QUEEN,          # Anti-air at base
    ],
    UnitID.VOIDRAY: [
        UnitID.HYDRALISK,      # Anti-air DPS, Light avoids bonus
        UnitID.CORRUPTOR,      # Air-to-air, tanky
        UnitID.QUEEN,          # Anti-air at base
    ],
    UnitID.ORACLE: [
        UnitID.QUEEN,          # Anti-air defense at home
        UnitID.HYDRALISK,      # Shoots air
        UnitID.MUTALISK,       # Air-to-air
    ],
    UnitID.CARRIER: [
        UnitID.CORRUPTOR,      # Direct counter, bonus vs Massive
        UnitID.HYDRALISK,      # Mass hydra DPS
        UnitID.VIPER,          # Abduct carriers
    ],
    UnitID.TEMPEST: [
        UnitID.CORRUPTOR,      # Close air-to-air
        UnitID.VIPER,          # Abduct into army
        UnitID.MUTALISK,       # Speed to close distance
    ],
    UnitID.WARPPRISM: [
        UnitID.HYDRALISK,      # Shoots air
        UnitID.MUTALISK,       # Fast, shoots air
        UnitID.QUEEN,          # Anti-air at home
    ],
    # === TERRAN ===
    UnitID.MARINE: [
        UnitID.BANELING,       # Splash melts bio, +19 vs Light
        UnitID.ROACH,          # Tanky, range
        UnitID.LURKERMP,       # Area denial vs clumped bio
    ],
    UnitID.MARAUDER: [
        UnitID.ZERGLING,       # Surround, marauder overkills
        UnitID.HYDRALISK,      # Light, high DPS
        UnitID.MUTALISK,       # Can't shoot air
    ],
    UnitID.REAPER: [
        UnitID.ZERGLING,       # Speed lings catch reapers
        UnitID.QUEEN,          # Anti-reaper defense
        UnitID.ROACH,          # Tanks reaper damage
    ],
    UnitID.GHOST: [
        UnitID.ZERGLING,       # Cheap swarm
        UnitID.ROACH,          # Armored, not light
        UnitID.ULTRALISK,      # Massive HP
    ],
    UnitID.HELLION: [
        UnitID.ROACH,          # Armored, tanks splash
        UnitID.MUTALISK,       # Can't shoot air
        UnitID.RAVAGER,        # Range + bile
    ],
    UnitID.HELLIONTANK: [
        UnitID.ROACH,          # Armored, outranges hellbat
        UnitID.MUTALISK,       # Can't shoot air
        UnitID.RAVAGER,        # Bile
    ],
    UnitID.WIDOWMINE: [
        UnitID.ROACH,          # Armored, tanks splash
        UnitID.ZERGLING,       # Cheap bait
        UnitID.OVERSEER,       # Detection (reveals burrowed mines)
    ],
    UnitID.SIEGETANK: [
        UnitID.MUTALISK,       # Can't shoot air, fast flanking
        UnitID.ZERGLING,       # Surround, get under min range
        UnitID.RAVAGER,        # Bile hits sieged tanks
    ],
    UnitID.SIEGETANKSIEGED: [
        UnitID.MUTALISK,
        UnitID.ZERGLING,
        UnitID.RAVAGER,
    ],
    UnitID.CYCLONE: [
        UnitID.ZERGLING,       # Swarm, cyclone slow to kite
        UnitID.ROACH,          # Armored trades well
        UnitID.HYDRALISK,      # Range + DPS
    ],
    UnitID.THOR: [
        UnitID.ZERGLING,       # Surround, overwhelm
        UnitID.ROACH,          # Armored, trades decently
        UnitID.BROODLORD,      # Range, free broodlings
    ],
    UnitID.VIKING: [
        UnitID.HYDRALISK,      # Shoots air, high DPS
        UnitID.CORRUPTOR,      # Air-to-air
        UnitID.MUTALISK,       # Speed advantage
    ],
    UnitID.VIKINGFIGHTER: [
        UnitID.HYDRALISK,
        UnitID.CORRUPTOR,
        UnitID.MUTALISK,
    ],
    UnitID.MEDIVAC: [
        UnitID.HYDRALISK,      # Shoots air
        UnitID.MUTALISK,       # Fast, harasses drops
        UnitID.CORRUPTOR,      # Air-to-air
    ],
    UnitID.LIBERATOR: [
        UnitID.CORRUPTOR,      # Air-to-air counter
        UnitID.MUTALISK,       # Flanking from angles
        UnitID.HYDRALISK,      # Anti-air DPS
    ],
    UnitID.BANSHEE: [
        UnitID.HYDRALISK,      # Anti-air DPS
        UnitID.MUTALISK,       # Air-to-air
        UnitID.QUEEN,          # Anti-air at base
    ],
    UnitID.BATTLECRUISER: [
        UnitID.CORRUPTOR,      # Best counter, bonus vs Massive
        UnitID.HYDRALISK,      # Mass DPS
        UnitID.VIPER,          # Abduct into army
    ],
    UnitID.RAVEN: [
        UnitID.MUTALISK,       # Fast, kills ravens
        UnitID.CORRUPTOR,      # Air-to-air
        UnitID.HYDRALISK,      # Anti-air
    ],
    # === ZERG (mirror) ===
    UnitID.ZERGLING: [
        UnitID.ROACH,          # Armored, range
        UnitID.BANELING,       # Splash vs light
    ],
    UnitID.BANELING: [
        UnitID.ROACH,          # Armored, avoids splash bonus
        UnitID.MUTALISK,       # Can't hit air
    ],
    UnitID.ROACH: [
        UnitID.HYDRALISK,      # DPS, range advantage
        UnitID.MUTALISK,       # Can't shoot air
        UnitID.BROODLORD,      # Range + free units
    ],
    UnitID.HYDRALISK: [
        UnitID.BANELING,       # Splash vs Light hydras
        UnitID.LURKERMP,       # Area denial
        UnitID.INFESTOR,       # Fungal growth
    ],
    UnitID.MUTALISK: [
        UnitID.HYDRALISK,      # Anti-air DPS
        UnitID.INFESTOR,       # Fungal locks mutas in place
    ],
    # === STRUCTURES (static defense) ===
    UnitID.PLANETARYFORTRESS: [
        UnitID.BROODLORD,      # Siege from range, free broodlings
        UnitID.SWARMHOSTMP,    # Free locusts, safe siege
        UnitID.RAVAGER,        # Corrosive Bile outranges
    ],
    UnitID.BUNKER: [
        UnitID.RAVAGER,        # Corrosive Bile destroys bunkers
        UnitID.ROACH,          # Armored, sustain damage
        UnitID.SWARMHOSTMP,    # Free locusts siege safely
    ],
}


# ---------------------------------------------------------------------------
# Tech requirements: what structures are needed to build each Zerg unit
# ---------------------------------------------------------------------------

TECH_REQUIREMENTS: dict[UnitID, list[UnitID]] = {
    UnitID.ZERGLING: [UnitID.SPAWNINGPOOL],
    UnitID.BANELING: [UnitID.SPAWNINGPOOL, UnitID.BANELINGNEST],
    UnitID.ROACH: [UnitID.SPAWNINGPOOL, UnitID.ROACHWARREN],
    UnitID.RAVAGER: [UnitID.SPAWNINGPOOL, UnitID.ROACHWARREN],
    UnitID.HYDRALISK: [UnitID.LAIR, UnitID.HYDRALISKDEN],
    UnitID.LURKERMP: [UnitID.LAIR, UnitID.HYDRALISKDEN, UnitID.LURKERDENMP],
    UnitID.MUTALISK: [UnitID.LAIR, UnitID.SPIRE],
    UnitID.CORRUPTOR: [UnitID.LAIR, UnitID.SPIRE],
    UnitID.INFESTOR: [UnitID.LAIR, UnitID.INFESTATIONPIT],
    UnitID.SWARMHOSTMP: [UnitID.LAIR, UnitID.INFESTATIONPIT],
    UnitID.VIPER: [UnitID.HIVE, UnitID.INFESTATIONPIT],
    UnitID.ULTRALISK: [UnitID.HIVE, UnitID.ULTRALISKCAVERN],
    UnitID.BROODLORD: [UnitID.HIVE, UnitID.GREATERSPIRE],
}


# ---------------------------------------------------------------------------
# Helper: structures we consider as "lair-tier" for detecting lair presence
# (LAIR morphs from HATCHERY, HIVE morphs from LAIR — either counts)
# ---------------------------------------------------------------------------

LAIR_EQUIVALENT = {UnitID.LAIR, UnitID.HIVE}
HIVE_EQUIVALENT = {UnitID.HIVE}


def can_build_unit(unit_id: UnitID, own_structures: dict) -> bool:
    """Check if we have the tech to build a given Zerg unit.

    own_structures: dict from mediator.get_own_structures_dict
    """
    reqs = TECH_REQUIREMENTS.get(unit_id)
    if reqs is None:
        return False

    for req in reqs:
        # Lair/Hive equivalences
        if req == UnitID.LAIR:
            has_lair = any(
                len(own_structures.get(t, [])) > 0 for t in LAIR_EQUIVALENT
            )
            if not has_lair:
                return False
        elif req == UnitID.HIVE:
            has_hive = any(
                len(own_structures.get(t, [])) > 0 for t in HIVE_EQUIVALENT
            )
            if not has_hive:
                return False
        else:
            if len(own_structures.get(req, [])) == 0:
                return False
    return True


def get_unit_value(unit_id: UnitID) -> float:
    """Get mineral + gas cost for a unit type."""
    profile = UNIT_PROFILES.get(unit_id)
    if profile:
        return float(profile.mineral_cost + profile.gas_cost)
    return 100.0  # fallback


# Worker types to exclude from army value calculations
_WORKER_TYPES = {UnitID.DRONE, UnitID.SCV, UnitID.PROBE}
# Non-combat types to exclude
_NON_COMBAT_TYPES = _WORKER_TYPES | {
    UnitID.LARVA, UnitID.EGG, UnitID.OVERLORD, UnitID.OVERSEER,
    UnitID.OBSERVER, UnitID.WARPPRISM, UnitID.MEDIVAC,
    UnitID.MULE,
}


def compute_army_value(army_dict: dict) -> float:
    """Compute total army value from a {UnitTypeId: [Unit, ...]} dict.

    Skips workers, larvae, eggs, and non-combat units.
    Uses mineral + gas cost as value (same as get_unit_value).

    Works with both own and enemy army dicts from the ares mediator:
        ai.mediator.get_own_army_dict
        ai.mediator.get_enemy_army_dict
    """
    total = 0.0
    for unit_type, units in army_dict.items():
        if unit_type in _NON_COMBAT_TYPES:
            continue
        count = len(units) if hasattr(units, "__len__") else 0
        if count > 0:
            total += count * get_unit_value(unit_type)
    return total


def get_counter_units(
    enemy_comp: dict[UnitID, int],
    own_structures: dict,
) -> dict[UnitID, float]:
    """Compute recommended Zerg comp to counter observed enemy composition.

    Returns {UnitTypeId: weight} — higher weight = more important.
    Filters to units we can actually build with current tech.
    """
    scores: dict[UnitID, float] = {}

    for enemy_type, count in enemy_comp.items():
        counters = COUNTER_MAP.get(enemy_type, [])
        for rank, counter_unit in enumerate(counters):
            if not can_build_unit(counter_unit, own_structures):
                continue
            # First counter gets highest weight, diminishing
            enemy_value = get_unit_value(enemy_type)
            weight = count * enemy_value * (1.0 / (rank + 1))
            scores[counter_unit] = scores.get(counter_unit, 0.0) + weight

    return scores


def get_ideal_counter_units(
    enemy_comp: dict[UnitID, int],
) -> dict[UnitID, float]:
    """Compute ideal Zerg counter comp WITHOUT tech filtering.

    Same logic as get_counter_units() but ignores whether we have
    the tech to build each unit. Used for tech planning — tells us
    what we SHOULD build if we had the right structures.

    Skips non-producible units (QUEEN, OVERSEER) that appear in
    COUNTER_MAP but aren't in TECH_REQUIREMENTS.
    """
    scores: dict[UnitID, float] = {}

    for enemy_type, count in enemy_comp.items():
        counters = COUNTER_MAP.get(enemy_type, [])
        for rank, counter_unit in enumerate(counters):
            # Skip units not in tech requirements (QUEEN, OVERSEER, etc.)
            if counter_unit not in TECH_REQUIREMENTS:
                continue
            # First counter gets highest weight, diminishing
            enemy_value = get_unit_value(enemy_type)
            weight = count * enemy_value * (1.0 / (rank + 1))
            scores[counter_unit] = scores.get(counter_unit, 0.0) + weight

    return scores
