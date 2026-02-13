"""Layer 1: Battlefield Awareness — perception layer.

Observes the full game state and produces a BattlefieldState snapshot
that downstream layers consume. Tracks enemy army position (even when
invisible), our army strength, base threats, strategic targets, staging
points, and terrain features.

Consumes data from: ares mediator, StrategicAnalyzer memory, scouting.
Produces: BattlefieldState (defined in constants.py).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sc2.ids.unit_typeid import UnitTypeId as UnitID
from sc2.position import Point2
from sc2.units import Units

from ares.consts import UnitRole

from bot.combat.constants import (
    BattlefieldState,
    EnemyArmyEstimate,
    EXPANSION_TYPES,
    MIN_ATTACK_SUPPLY,
    PEAK_DECAY_RATE,
    RALLYING_ROLE,
    STATIC_DEFENSE_TYPES,
    WORKER_TYPES,
    WIPE_COOLDOWN,
    OpportunityTarget,
    TerrainFeature,
)
from bot.combat.targeting import score_strategic_targets

if TYPE_CHECKING:
    from ares import AresBot
    from bot.strategy.analyzer import Strategy


class BattlefieldAwareness:
    """Layer 1 — reads game state, produces BattlefieldState.

    Called every combat cycle. Tracks persistent state (enemy army
    estimate, peak supply, wipe history) and produces a fresh
    BattlefieldState for the planner.
    """

    # Minimum visible enemy combat units to consider it "an army"
    ARMY_SIGHTING_MIN: int = 3
    # Confidence decay: 1.0 at 0s stale → 0.0 at 120s stale
    CONFIDENCE_DECAY_TIME: float = 120.0

    def __init__(self, ai: AresBot) -> None:
        self.ai = ai

        # Persistent enemy army tracking
        self._enemy_last_position: Point2 | None = None
        self._enemy_last_seen: float = 0.0
        self._enemy_heading: Point2 | None = None
        self._enemy_prev_position: Point2 | None = None

        # Our army supply tracking
        self._peak_army_supply: float = 0.0
        self._last_peak_decay_time: float = 0.0
        self._army_wipe_time: float = 0.0
        self._army_wipes: int = 0

        # Cached terrain data (computed once)
        self._choke_centers: list[Point2] | None = None

    @property
    def peak_army_supply(self) -> float:
        return self._peak_army_supply

    @property
    def army_wipes(self) -> int:
        return self._army_wipes

    @property
    def last_wipe_time(self) -> float:
        return self._army_wipe_time

    def update(self, strategy: Strategy | None = None) -> BattlefieldState:
        """Build full battlefield picture from current game state."""
        ai = self.ai
        now = ai.time
        state = BattlefieldState(game_time=now)

        # 1. Our army state
        self._update_our_army(state)

        # 2. Enemy army estimate
        self._update_enemy_army(state, now)

        # 3. Base threats
        self._update_threats(state)

        # 4. Opportunity targets
        self._update_targets(state)

        # 5. Staging point
        state.staging_point = self._compute_staging_point()

        # 6. Terrain features
        self._update_terrain(state)

        # 7. Confidence from info staleness + composition analysis
        self._update_confidence(state, strategy, now)

        return state

    # ------------------------------------------------------------------
    # 1. Our army
    # ------------------------------------------------------------------

    def _update_our_army(self, state: BattlefieldState) -> None:
        ai = self.ai
        now = ai.time

        army: Units = ai.mediator.get_units_from_role(role=UnitRole.ATTACKING)
        rallying: Units = ai.mediator.get_units_from_role(role=RALLYING_ROLE)

        # Combine for total supply calculation
        combined = army | rallying if rallying and len(rallying) > 0 else army
        if combined and len(combined) > 0:
            state.our_army_supply = ai.get_total_supply(combined)
            state.our_army_center = combined.center
        else:
            state.our_army_supply = 0.0
            state.our_army_center = None

        # Peak supply tracking with decay
        self._update_peak_supply(state.our_army_supply, now)
        state.peak_army_supply = self._peak_army_supply
        state.army_wipes = self._army_wipes
        state.last_wipe_time = self._army_wipe_time

    def _update_peak_supply(self, current_supply: float, now: float) -> None:
        """Track rolling peak army supply with time decay."""
        # Decay peak over time so it doesn't stay at historical high forever
        if now - self._last_peak_decay_time > 30.0:
            self._peak_army_supply *= (1.0 - PEAK_DECAY_RATE)
            self._last_peak_decay_time = now

        if current_supply > self._peak_army_supply:
            self._peak_army_supply = current_supply

        # Detect army wipe: supply drops below 30% of peak
        if (self._peak_army_supply > 20
                and current_supply < self._peak_army_supply * 0.3
                and now - self._army_wipe_time > 30.0):
            self._army_wipe_time = now
            self._army_wipes += 1
            logger = getattr(self.ai, "game_logger", None)
            if logger:
                logger.log_decision("awareness", "army_wipe_detected", {
                    "current_supply": current_supply,
                    "peak_supply": round(self._peak_army_supply, 1),
                    "game_time": round(now, 1),
                    "wipe_number": self._army_wipes,
                })

    # ------------------------------------------------------------------
    # 2. Enemy army estimation
    # ------------------------------------------------------------------

    def _update_enemy_army(self, state: BattlefieldState, now: float) -> None:
        ai = self.ai

        # Gather visible enemy combat units (exclude workers, structures)
        enemy_combat = [
            u for u in (ai.enemy_units or [])
            if not u.is_structure and u.type_id not in WORKER_TYPES
        ]

        estimate = EnemyArmyEstimate()

        if len(enemy_combat) >= self.ARMY_SIGHTING_MIN:
            # Sighting — update tracking
            positions = [u.position for u in enemy_combat]
            center = Point2((
                sum(p.x for p in positions) / len(positions),
                sum(p.y for p in positions) / len(positions),
            ))

            # Compute heading from previous sighting
            if self._enemy_last_position is not None:
                dx = center.x - self._enemy_last_position.x
                dy = center.y - self._enemy_last_position.y
                dist = (dx ** 2 + dy ** 2) ** 0.5
                if dist > 2.0:  # only update heading if moved significantly
                    self._enemy_heading = Point2((dx / dist, dy / dist))

            self._enemy_prev_position = self._enemy_last_position
            self._enemy_last_position = center
            self._enemy_last_seen = now

            estimate.last_known_position = center
            estimate.last_seen_time = now
            estimate.estimated_position = center
            estimate.heading = self._enemy_heading
            estimate.confidence = 1.0
            estimate.estimated_supply = sum(
                u._type_data._proto.food_required
                for u in enemy_combat
            )
            estimate.contains_air = any(u.is_flying for u in enemy_combat)

        elif self._enemy_last_position is not None:
            # Not visible — predict from last known
            staleness = now - self._enemy_last_seen
            confidence = max(0.0, 1.0 - staleness / self.CONFIDENCE_DECAY_TIME)

            # Predict position from heading (if known)
            predicted = self._enemy_last_position
            if self._enemy_heading is not None and staleness < 60.0:
                # Assume speed ~3.5 game units/sec average
                move_dist = min(staleness * 3.5, 50.0)
                predicted = Point2((
                    self._enemy_last_position.x + self._enemy_heading.x * move_dist,
                    self._enemy_last_position.y + self._enemy_heading.y * move_dist,
                ))

            estimate.last_known_position = self._enemy_last_position
            estimate.last_seen_time = self._enemy_last_seen
            estimate.estimated_position = predicted
            estimate.heading = self._enemy_heading
            estimate.confidence = confidence

        state.enemy_army = estimate

    # ------------------------------------------------------------------
    # 3. Base threats (worker filter applied)
    # ------------------------------------------------------------------

    def _update_threats(self, state: BattlefieldState) -> None:
        ai = self.ai

        ground_threats: Units = ai.mediator.get_main_ground_threats_near_townhall
        air_threats: Units = ai.mediator.get_main_air_threats_near_townhall

        # Filter trivial threats: lone workers scouting are not real attacks
        has_ground_threat = False
        threat_count = 0
        if ground_threats:
            real_ground = ground_threats.filter(
                lambda u: u.type_id not in WORKER_TYPES
            )
            has_ground_threat = (
                (real_ground and len(real_ground) > 0)
                or len(ground_threats) >= 3
            )
            if has_ground_threat:
                threat_count += len(ground_threats)

        has_air_threat = air_threats and len(air_threats) > 0
        if has_air_threat:
            threat_count += len(air_threats)

        if has_ground_threat or has_air_threat:
            state.threat_units_near_bases = threat_count

            # Find which bases are threatened
            threatened: list[Point2] = []
            for th in ai.townhalls:
                nearby = False
                if ground_threats:
                    for u in ground_threats:
                        if u.distance_to(th.position) < 20:
                            nearby = True
                            break
                if not nearby and air_threats:
                    for u in air_threats:
                        if u.distance_to(th.position) < 20:
                            nearby = True
                            break
                if nearby:
                    threatened.append(th.position)
            state.threatened_bases = threatened

    # ------------------------------------------------------------------
    # 4. Strategic targets
    # ------------------------------------------------------------------

    def _update_targets(self, state: BattlefieldState) -> None:
        ai = self.ai

        enemy_structures = list(ai.enemy_structures) if ai.enemy_structures else []
        enemy_units = list(ai.enemy_units) if ai.enemy_units else []

        # Get info staleness for target scoring
        info_staleness = 0.0
        try:
            analyzer = ai.strategic_analyzer
            info_staleness = analyzer.memory.get_info_staleness(ai.time)
        except AttributeError:
            pass

        state.opportunity_targets = score_strategic_targets(
            enemy_structures=enemy_structures,
            enemy_units=enemy_units,
            our_army_center=state.our_army_center,
            enemy_army_estimate=state.enemy_army,
            info_staleness=info_staleness,
        )

    # ------------------------------------------------------------------
    # 5. Staging point — dynamic rally position
    # ------------------------------------------------------------------

    def _compute_staging_point(self) -> Point2 | None:
        """Compute best staging point for the army.

        Priority:
        1. Creep edge toward enemy (gives regen + speed advantage)
        2. Own natural position (safe fallback)
        3. Start location (last resort)
        """
        ai = self.ai

        # Get own natural as base reference
        own_nat: Point2 | None = None
        try:
            own_nat = ai.mediator.get_own_nat
        except Exception:
            pass

        # Try creep edge toward enemy
        if own_nat is not None:
            try:
                creep_edge = ai.mediator.find_nearby_creep_edge_position(
                    position=own_nat, search_radius=30,
                )
                if creep_edge is not None:
                    return creep_edge
            except Exception:
                pass

        # Fallback: natural position
        if own_nat is not None:
            return own_nat

        # Last resort: start location
        return ai.start_location

    # ------------------------------------------------------------------
    # 6. Terrain features (chokes between staging and target)
    # ------------------------------------------------------------------

    def _update_terrain(self, state: BattlefieldState) -> None:
        """Find choke points relevant to current attack path."""
        ai = self.ai

        # Cache choke centers (computed once, expensive)
        if self._choke_centers is None:
            self._choke_centers = self._cluster_choke_points()

        if not self._choke_centers:
            return

        # Find chokes between staging point and primary target
        staging = state.staging_point
        if staging is None:
            return

        target = None
        if state.opportunity_targets:
            target = state.opportunity_targets[0].position
        elif ai.enemy_start_locations:
            target = ai.enemy_start_locations[0]

        if target is None:
            return

        # Filter chokes that are roughly between us and the target
        features: list[TerrainFeature] = []
        path_dist = staging.distance_to(target)
        for choke in self._choke_centers:
            d_from_staging = choke.distance_to(staging)
            d_from_target = choke.distance_to(target)
            # Choke is "between" if it's closer to both than the path length
            # and not too far to the side
            if d_from_staging < path_dist and d_from_target < path_dist:
                features.append(TerrainFeature(
                    position=choke,
                    feature_type="choke",
                    width=0.0,  # we don't have width data from raw tiles
                ))

        # Sort by distance from staging (encounter order)
        features.sort(key=lambda f: f.position.distance_to(staging))
        state.terrain_features = features[:5]  # top 5 relevant chokes

    def _cluster_choke_points(self, cluster_radius: float = 4.0) -> list[Point2]:
        """Cluster raw choke tile positions into choke centers.

        get_map_choke_points returns individual tiles — we cluster them
        into meaningful choke positions.
        """
        ai = self.ai
        try:
            raw_tiles = ai.mediator.get_map_choke_points
            if not raw_tiles:
                return []
        except Exception:
            return []

        tiles = list(raw_tiles)
        used: set[int] = set()
        centers: list[Point2] = []

        for i, seed in enumerate(tiles):
            if i in used:
                continue
            cluster = [seed]
            used.add(i)
            for j, other in enumerate(tiles):
                if j in used:
                    continue
                if seed.distance_to(other) < cluster_radius:
                    cluster.append(other)
                    used.add(j)

            if len(cluster) >= 2:
                cx = sum(p.x for p in cluster) / len(cluster)
                cy = sum(p.y for p in cluster) / len(cluster)
                centers.append(Point2((cx, cy)))

        return centers

    # ------------------------------------------------------------------
    # 7. Confidence score
    # ------------------------------------------------------------------

    def _update_confidence(
        self,
        state: BattlefieldState,
        strategy: Strategy | None,
        now: float,
    ) -> None:
        """Overall confidence level based on info quality.

        0.0 = flying blind, 1.0 = full information.
        """
        # Base: enemy army tracking confidence
        confidence = state.enemy_army.confidence

        # Factor in info staleness from strategic memory
        try:
            analyzer = self.ai.strategic_analyzer
            staleness = analyzer.memory.get_info_staleness(now)
            staleness_factor = max(0.0, 1.0 - staleness / 120.0)
            confidence = min(confidence, staleness_factor)
        except AttributeError:
            pass

        # Factor in composition classifier confidence if available
        if strategy is not None:
            try:
                classifier = self.ai.strategic_analyzer.classifier
                if hasattr(classifier, "_last_analysis") and classifier._last_analysis:
                    comp_confidence = classifier._last_analysis.confidence
                    # Weight: 70% info, 30% classifier
                    confidence = 0.7 * confidence + 0.3 * comp_confidence
            except AttributeError:
                pass

        state.confidence = max(0.0, min(1.0, confidence))
