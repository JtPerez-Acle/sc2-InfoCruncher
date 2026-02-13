"""Threat assessment from observed game state.

Reads the current game state and computes:
- What the enemy is doing (composition, tech level, aggression)
- What we should build to counter it
- How safe we are (threat level, army advantage)

All calculations are from observable data — no magic numbers, no time gates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sc2.ids.unit_typeid import UnitTypeId as UnitID

from bot.strategy.unit_counter_db import (
    COUNTER_MAP,
    LAIR_EQUIVALENT,
    HIVE_EQUIVALENT,
    TECH_REQUIREMENTS,
    UNIT_PROFILES,
    can_build_unit,
    get_counter_units,
    get_ideal_counter_units,
    get_unit_value,
)

# Morph chain: structures that need a predecessor morph to exist first
_MORPH_PREREQS: dict[UnitID, UnitID] = {
    UnitID.HIVE: UnitID.LAIR,
    UnitID.GREATERSPIRE: UnitID.SPIRE,
}

if TYPE_CHECKING:
    from bot.strategy.composition import CompositionAnalysis


# Structures that reveal enemy tech path
_PROTOSS_AIR_TECH = {UnitID.STARGATE, UnitID.FLEETBEACON}
_PROTOSS_ROBO_TECH = {UnitID.ROBOTICSFACILITY, UnitID.ROBOTICSBAY}
_PROTOSS_TEMPLAR_TECH = {UnitID.TEMPLARARCHIVE, UnitID.DARKSHRINE}
_TERRAN_AIR_TECH = {UnitID.STARPORT, UnitID.STARPORTFLYING, UnitID.FUSIONCORE}
_TERRAN_FACTORY_TECH = {UnitID.FACTORY, UnitID.FACTORYFLYING}
_ZERG_AIR_TECH = {UnitID.SPIRE, UnitID.GREATERSPIRE}
_ZERG_LAIR_TECH = {UnitID.LAIR, UnitID.HIVE}

# Worker types
_WORKER_TYPES = {UnitID.SCV, UnitID.PROBE, UnitID.DRONE}

# Expansion structures by race
_EXPANSION_TYPES = {
    UnitID.NEXUS, UnitID.COMMANDCENTER, UnitID.COMMANDCENTERFLYING,
    UnitID.ORBITALCOMMAND, UnitID.ORBITALCOMMANDFLYING,
    UnitID.PLANETARYFORTRESS,
    UnitID.HATCHERY, UnitID.LAIR, UnitID.HIVE,
}


@dataclass
class ThreatAssessment:
    """Output of threat analysis — drives all strategic decisions."""

    # --- What we SEE ---
    visible_army_value: float = 0.0
    visible_army_comp: dict[UnitID, int] = field(default_factory=dict)
    dominant_enemy_type: UnitID | None = None

    # --- What we INFER ---
    enemy_race: str = "Unknown"
    enemy_tech_level: str = "early"  # "early" | "mid" | "late"
    enemy_has_air: bool = False
    enemy_has_detection: bool = False
    enemy_is_aggressive: bool = False
    enemy_is_expanding: bool = False
    enemy_is_turtling: bool = False
    enemy_base_count: int = 1

    # --- COMPOSITION analysis ---
    enemy_archetype: str = "UNKNOWN"
    air_fraction: float = 0.0
    is_mech_heavy: bool = False

    # --- COUNTER analysis ---
    recommended_comp: dict[UnitID, float] = field(default_factory=dict)
    recommended_tech: list[UnitID] = field(default_factory=list)

    # --- THREAT level ---
    threat_level: float = 0.0       # 0.0 (safe) to 1.0 (all-in incoming)
    army_advantage: float = 1.0     # own_value / enemy_value ratio
    safe_to_drone: bool = True


class ThreatModel:
    """Analyzes enemy state and computes threat assessment + counter composition."""

    def evaluate(
        self,
        enemy_comp: dict[UnitID, int],
        enemy_structures: set[UnitID],
        own_structures: dict,
        own_army_value: float,
        enemy_army_value: float,
        game_time: float,
        ground_threats_near_base: int = 0,
        air_threats_near_base: int = 0,
        enemy_expanded: bool = False,
        estimated_enemy_value: float = 0.0,
        cumulative_enemy_comp: dict[UnitID, int] | None = None,
        composition: CompositionAnalysis | None = None,
    ) -> ThreatAssessment:
        """Run full threat analysis from current observations + memory."""
        assessment = ThreatAssessment()

        # --- Basic observations ---
        assessment.enemy_race = self._infer_race(enemy_comp, enemy_structures)
        assessment.visible_army_comp = dict(enemy_comp)
        assessment.visible_army_value = enemy_army_value

        # Dominant enemy type (most supply invested)
        if enemy_comp:
            assessment.dominant_enemy_type = max(
                enemy_comp,
                key=lambda uid: enemy_comp[uid] * get_unit_value(uid),
            )

        # --- Infer enemy state ---
        assessment.enemy_tech_level = self._infer_tech_level(
            enemy_structures, game_time, enemy_comp=enemy_comp
        )
        assessment.enemy_has_air = self._has_air_capability(
            enemy_comp, enemy_structures
        )
        assessment.enemy_has_detection = self._has_detection(
            enemy_comp, enemy_structures
        )
        assessment.enemy_base_count = self._count_enemy_bases(enemy_structures)
        assessment.enemy_is_expanding = (
            enemy_expanded or assessment.enemy_base_count >= 2
        )
        assessment.enemy_is_aggressive = (
            ground_threats_near_base > 0 or air_threats_near_base > 0
        )
        assessment.enemy_is_turtling = self._is_turtling(
            enemy_structures, enemy_comp, game_time
        )

        # --- Composition analysis (if available) ---
        if composition is not None:
            assessment.enemy_archetype = composition.archetype.name
            assessment.air_fraction = composition.air_fraction
            assessment.is_mech_heavy = composition.is_mech_heavy

        # --- Army advantage ---
        # Use the higher of visible value or memory-estimated value
        effective_enemy_value = max(enemy_army_value, estimated_enemy_value)
        if effective_enemy_value > 0:
            assessment.army_advantage = own_army_value / effective_enemy_value
        elif own_army_value > 0:
            assessment.army_advantage = 5.0  # We have army, they don't
        else:
            assessment.army_advantage = 1.0  # Both low

        # --- Threat level ---
        assessment.threat_level = self._compute_threat_level(
            assessment, ground_threats_near_base, air_threats_near_base,
            game_time,
        )
        assessment.safe_to_drone = assessment.threat_level < 0.5

        # --- Counter composition (merge current + cumulative for better data) ---
        merged_enemy = dict(cumulative_enemy_comp or {})
        for uid, count in enemy_comp.items():
            merged_enemy[uid] = max(merged_enemy.get(uid, 0), count)

        assessment.recommended_comp = self._compute_recommended_comp(
            merged_enemy, own_structures, assessment, composition
        )

        # --- Recommended tech (from IDEAL comp to break circular dependency) ---
        ideal_scores = get_ideal_counter_units(merged_enemy)
        ideal_total = sum(ideal_scores.values()) if ideal_scores else 0
        ideal_comp = (
            {uid: s / ideal_total for uid, s in ideal_scores.items()}
            if ideal_total > 0
            else None
        )
        assessment.recommended_tech = self._compute_recommended_tech(
            assessment.recommended_comp, own_structures, ideal_comp=ideal_comp
        )

        return assessment

    # ------------------------------------------------------------------
    # Race detection
    # ------------------------------------------------------------------

    def _infer_race(
        self, enemy_comp: dict[UnitID, int], enemy_structures: set[UnitID]
    ) -> str:
        """Determine enemy race from observed units/structures."""
        for uid in enemy_comp:
            profile = UNIT_PROFILES.get(uid)
            if profile:
                return profile.race
        # Check structures
        protoss_structs = {
            UnitID.NEXUS, UnitID.PYLON, UnitID.GATEWAY, UnitID.WARPGATE,
            UnitID.FORGE, UnitID.CYBERNETICSCORE, UnitID.ASSIMILATOR,
        }
        terran_structs = {
            UnitID.COMMANDCENTER, UnitID.BARRACKS, UnitID.FACTORY,
            UnitID.STARPORT, UnitID.SUPPLYDEPOT, UnitID.REFINERY,
            UnitID.ORBITALCOMMAND, UnitID.PLANETARYFORTRESS,
        }
        zerg_structs = {
            UnitID.HATCHERY, UnitID.SPAWNINGPOOL, UnitID.ROACHWARREN,
            UnitID.EXTRACTOR, UnitID.LAIR, UnitID.HIVE,
        }
        if enemy_structures & protoss_structs:
            return "Protoss"
        if enemy_structures & terran_structs:
            return "Terran"
        if enemy_structures & zerg_structs:
            return "Zerg"
        return "Unknown"

    # ------------------------------------------------------------------
    # Tech level inference
    # ------------------------------------------------------------------

    def _infer_tech_level(
        self,
        enemy_structures: set[UnitID],
        game_time: float,
        enemy_comp: dict[UnitID, int] | None = None,
    ) -> str:
        """Infer enemy tech level from visible structures, units, + game time.

        We use structures, unit sightings, AND time as a fallback.
        If we haven't scouted, time gives a conservative estimate.
        """
        # Unit-based inference — strongest signal (e.g., Ultralisk → HIVE)
        if enemy_comp:
            late_units = {
                UnitID.ULTRALISK, UnitID.BROODLORD, UnitID.VIPER,
                UnitID.MOTHERSHIP, UnitID.CARRIER, UnitID.TEMPEST,
                UnitID.BATTLECRUISER, UnitID.GHOST,
                UnitID.INFESTOR, UnitID.LURKERMP,
            }
            if any(uid in enemy_comp for uid in late_units):
                return "late"

        # Late game indicators
        late_indicators = {
            UnitID.FLEETBEACON, UnitID.TEMPLARARCHIVE, UnitID.DARKSHRINE,
            UnitID.FUSIONCORE, UnitID.GHOSTACADEMY,
            UnitID.HIVE, UnitID.ULTRALISKCAVERN, UnitID.GREATERSPIRE,
        }
        if enemy_structures & late_indicators:
            return "late"

        # Mid game indicators
        mid_indicators = {
            UnitID.ROBOTICSFACILITY, UnitID.ROBOTICSBAY, UnitID.STARGATE,
            UnitID.FACTORY, UnitID.STARPORT, UnitID.ARMORY,
            UnitID.LAIR, UnitID.HYDRALISKDEN, UnitID.SPIRE,
            UnitID.INFESTATIONPIT,
        }
        if enemy_structures & mid_indicators:
            return "mid"

        # Time-based fallback (if we haven't scouted enough)
        if game_time > 480:  # 8 minutes
            return "late"
        if game_time > 300:  # 5 minutes
            return "mid"

        return "early"

    # ------------------------------------------------------------------
    # Capability inference
    # ------------------------------------------------------------------

    def _has_air_capability(
        self, enemy_comp: dict[UnitID, int], enemy_structures: set[UnitID]
    ) -> bool:
        """Can the enemy produce air units?"""
        # Direct observation of air units
        for uid in enemy_comp:
            profile = UNIT_PROFILES.get(uid)
            if profile and profile.can_attack_air and not profile.can_attack_ground:
                return True  # They have dedicated air units
            # Check for flying combat units
            if uid in {
                UnitID.MUTALISK, UnitID.CORRUPTOR, UnitID.BROODLORD,
                UnitID.PHOENIX, UnitID.VOIDRAY, UnitID.CARRIER, UnitID.TEMPEST,
                UnitID.ORACLE, UnitID.VIKING, UnitID.VIKINGFIGHTER,
                UnitID.BANSHEE, UnitID.LIBERATOR, UnitID.BATTLECRUISER,
                UnitID.MEDIVAC, UnitID.RAVEN,
            }:
                return True

        # Structure indicators
        air_structures = _PROTOSS_AIR_TECH | _TERRAN_AIR_TECH | _ZERG_AIR_TECH
        return bool(enemy_structures & air_structures)

    def _has_detection(
        self, enemy_comp: dict[UnitID, int], enemy_structures: set[UnitID]
    ) -> bool:
        """Does the enemy have mobile detection?"""
        detection_units = {
            UnitID.OBSERVER, UnitID.ORACLE,
            UnitID.RAVEN,
            UnitID.OVERSEER,
        }
        return bool(set(enemy_comp.keys()) & detection_units)

    def _count_enemy_bases(self, enemy_structures: set[UnitID]) -> int:
        """Count observed enemy expansion structures."""
        # We only know about visible ones
        count = 0
        for s in enemy_structures:
            if s in _EXPANSION_TYPES:
                count += 1
        return max(count, 1)  # At least 1 (main)

    def _is_turtling(
        self,
        enemy_structures: set[UnitID],
        enemy_comp: dict[UnitID, int],
        game_time: float,
    ) -> bool:
        """Is the enemy playing defensively?"""
        static_defense = {
            UnitID.PHOTONCANNON, UnitID.SHIELDBATTERY,
            UnitID.BUNKER, UnitID.MISSILETURRET, UnitID.PLANETARYFORTRESS,
            UnitID.SPINECRAWLER, UnitID.SPORECRAWLER,
        }
        defense_count = len(enemy_structures & static_defense)
        army_count = sum(enemy_comp.values())

        # Heavy static defense relative to army = turtling
        if defense_count >= 3 and army_count < 10:
            return True
        # Late game with low army movement
        if game_time > 360 and defense_count >= 2 and army_count < 5:
            return True

        # Mech turtle: Factory + Armory + siege tanks + few bases
        has_factory = bool(enemy_structures & _TERRAN_FACTORY_TECH)
        has_armory = UnitID.ARMORY in enemy_structures
        tank_count = enemy_comp.get(UnitID.SIEGETANK, 0) + enemy_comp.get(
            UnitID.SIEGETANKSIEGED, 0
        )
        base_count = self._count_enemy_bases(enemy_structures)
        if has_factory and has_armory and tank_count >= 2 and base_count <= 2:
            return True

        return False

    # ------------------------------------------------------------------
    # Threat level computation
    # ------------------------------------------------------------------

    def _compute_threat_level(
        self,
        assessment: ThreatAssessment,
        ground_threats: int,
        air_threats: int,
        game_time: float,
    ) -> float:
        """Compute 0.0-1.0 threat level from multiple signals."""
        threat = 0.0

        # Active threats at our bases — highest priority signal
        if ground_threats > 0 or air_threats > 0:
            threat += 0.4 + min(0.3, (ground_threats + air_threats) * 0.05)

        # Army disadvantage
        if assessment.army_advantage < 0.5:
            threat += 0.3
        elif assessment.army_advantage < 0.8:
            threat += 0.15

        # Enemy is aggressive (moving toward us)
        if assessment.enemy_is_aggressive:
            threat += 0.1

        # Early game with visible army = potential rush
        if game_time < 240 and assessment.visible_army_value > 500:
            threat += 0.2

        # Unknown race at 3+ minutes = we haven't scouted = danger
        if assessment.enemy_race == "Unknown" and game_time > 180:
            threat += 0.15

        return min(1.0, threat)

    # ------------------------------------------------------------------
    # Counter composition
    # ------------------------------------------------------------------

    def _compute_recommended_comp(
        self,
        enemy_comp: dict[UnitID, int],
        own_structures: dict,
        assessment: ThreatAssessment,
        composition: CompositionAnalysis | None = None,
    ) -> dict[UnitID, float]:
        """Compute recommended army composition based on enemy state.

        Returns {UnitTypeId: proportion} summing to ~1.0.
        Uses CompositionAnalysis for dynamic AA floor and anti-armor boosts.
        """
        if not enemy_comp:
            return self._default_comp(own_structures, assessment)

        # Get raw counter scores
        raw_scores = get_counter_units(enemy_comp, own_structures)

        if not raw_scores:
            return self._default_comp(own_structures, assessment)

        # Normalize to proportions
        total = sum(raw_scores.values())
        if total <= 0:
            return self._default_comp(own_structures, assessment)

        comp = {uid: score / total for uid, score in raw_scores.items()}

        # Anti-armor boost: when enemy is armor-heavy (mech, robo), boost
        # Lurker and Ravager (bonus vs Armored)
        if composition and composition.anti_armor_priority:
            for uid in [UnitID.LURKERMP, UnitID.RAVAGER]:
                if uid in comp:
                    comp[uid] *= 1.5

        # SKYTOSS override: force minimum Corruptor + Hydra proportions
        if composition and composition.archetype.name == "SKYTOSS":
            if can_build_unit(UnitID.CORRUPTOR, own_structures):
                comp[UnitID.CORRUPTOR] = max(
                    comp.get(UnitID.CORRUPTOR, 0), 0.30
                )
            if can_build_unit(UnitID.HYDRALISK, own_structures):
                comp[UnitID.HYDRALISK] = max(
                    comp.get(UnitID.HYDRALISK, 0), 0.25
                )

        # Re-normalize after boosts
        total = sum(comp.values())
        if total > 0:
            comp = {uid: score / total for uid, score in comp.items()}

        # Enforce minimum diversity — no single unit > 70%
        max_proportion = 0.70
        for uid in list(comp):
            if comp[uid] > max_proportion:
                excess = comp[uid] - max_proportion
                comp[uid] = max_proportion
                # Redistribute excess to other units
                others = [u for u in comp if u != uid]
                if others:
                    per_other = excess / len(others)
                    for other in others:
                        comp[other] += per_other

        # Dynamic anti-air floor from composition analysis
        aa_floor = 0.25  # default
        if composition is not None:
            aa_floor = composition.recommended_aa_floor

        if assessment.enemy_has_air or (composition and aa_floor > 0):
            anti_air_in_comp = sum(
                v for k, v in comp.items()
                if UNIT_PROFILES.get(k) and UNIT_PROFILES[k].can_attack_air
            )
            if anti_air_in_comp < aa_floor:
                # Need more anti-air — try hydra, then corruptor
                for aa_unit in [UnitID.HYDRALISK, UnitID.CORRUPTOR, UnitID.QUEEN]:
                    if can_build_unit(aa_unit, own_structures):
                        needed = aa_floor - anti_air_in_comp
                        comp[aa_unit] = comp.get(aa_unit, 0) + needed
                        # Re-normalize
                        total = sum(comp.values())
                        comp = {k: v / total for k, v in comp.items()}
                        break

        return comp

    def _default_comp(
        self,
        own_structures: dict,
        assessment: ThreatAssessment,
    ) -> dict[UnitID, float]:
        """Fallback composition when enemy is unknown or not visible.

        Based on what tech we have — build the best we can.
        """
        if can_build_unit(UnitID.HYDRALISK, own_structures):
            if can_build_unit(UnitID.LURKERMP, own_structures):
                return {
                    UnitID.ROACH: 0.25,
                    UnitID.HYDRALISK: 0.40,
                    UnitID.LURKERMP: 0.25,
                    UnitID.ZERGLING: 0.10,
                }
            return {
                UnitID.ROACH: 0.40,
                UnitID.HYDRALISK: 0.45,
                UnitID.ZERGLING: 0.15,
            }
        if can_build_unit(UnitID.ROACH, own_structures):
            return {
                UnitID.ROACH: 0.65,
                UnitID.ZERGLING: 0.35,
            }
        return {
            UnitID.ZERGLING: 1.0,
        }

    # ------------------------------------------------------------------
    # Recommended tech
    # ------------------------------------------------------------------

    @staticmethod
    def _has_structure(structure_id: UnitID, own_structures: dict) -> bool:
        """Check if we have a structure, handling LAIR/HIVE equivalences."""
        if structure_id == UnitID.LAIR:
            return any(
                len(own_structures.get(t, [])) > 0 for t in LAIR_EQUIVALENT
            )
        if structure_id == UnitID.HIVE:
            return any(
                len(own_structures.get(t, [])) > 0 for t in HIVE_EQUIVALENT
            )
        return len(own_structures.get(structure_id, [])) > 0

    def _compute_recommended_tech(
        self,
        recommended_comp: dict[UnitID, float],
        own_structures: dict,
        ideal_comp: dict[UnitID, float] | None = None,
    ) -> list[UnitID]:
        """What structures should we build next to enable recommended units?

        Uses ideal_comp (without tech filter) for planning so we can
        request tech we don't have yet. Falls back to recommended_comp
        if ideal is not provided.
        """
        # Use ideal comp for tech planning — breaks circular dependency
        tech_source = ideal_comp if ideal_comp else recommended_comp

        needed: list[UnitID] = []
        seen: set[UnitID] = set()

        # Sort by proportion — prioritize structures for our most-wanted units
        sorted_comp = sorted(
            tech_source.items(), key=lambda x: x[1], reverse=True
        )

        for unit_type, proportion in sorted_comp:
            if proportion < 0.10:
                continue  # Higher threshold for ideal comp (avoid noise)

            reqs = TECH_REQUIREMENTS.get(unit_type, [])
            for req in reqs:
                if req in seen:
                    continue
                if not self._has_structure(req, own_structures):
                    # Add morph prerequisite first (e.g., LAIR before HIVE)
                    morph_source = _MORPH_PREREQS.get(req)
                    if morph_source and morph_source not in seen:
                        if not self._has_structure(morph_source, own_structures):
                            needed.append(morph_source)
                            seen.add(morph_source)
                    needed.append(req)
                    seen.add(req)

        return needed
