"""Strategic target scoring and focus fire priority.

Two levels of target selection:
  1. Strategic targets — WHERE to attack (bases, expansions, mineral lines)
  2. Focus fire targets — WHAT to kill (per-unit target priority in combat)

Both are driven by data: the engagement profile, battlefield state, and
unit counter knowledge.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sc2.ids.unit_typeid import UnitTypeId as UnitID
from sc2.position import Point2
from sc2.unit import Unit

from bot.combat.constants import (
    EXPANSION_TYPES,
    SUPPORT_TYPES,
    WORKER_TYPES,
    OpportunityTarget,
)
from bot.combat.profiles import EngagementProfile

if TYPE_CHECKING:
    from bot.combat.constants import BattlefieldState, EnemyArmyEstimate


# ---------------------------------------------------------------------------
# Layer 1 helper — score WHERE to attack
# ---------------------------------------------------------------------------

def score_strategic_targets(
    enemy_structures: list[Unit],
    enemy_units: list[Unit],
    our_army_center: Point2 | None,
    enemy_army_estimate: EnemyArmyEstimate | None,
    info_staleness: float = 0.0,
) -> list[OpportunityTarget]:
    """Score all potential attack targets on the map.

    Returns targets sorted by score descending — highest priority first.
    Considers: base value, distance from our army, enemy army proximity,
    information staleness.
    """
    targets: list[OpportunityTarget] = []

    for struct in enemy_structures:
        target = _score_structure_target(
            struct, our_army_center, enemy_army_estimate, info_staleness,
        )
        if target is not None:
            targets.append(target)

    # Mineral line targets — clusters of enemy workers
    worker_clusters = _find_worker_clusters(enemy_units)
    for pos, tags in worker_clusters:
        target = _score_mineral_line_target(
            pos, tags, our_army_center, enemy_army_estimate, info_staleness,
        )
        targets.append(target)

    targets.sort(key=lambda t: t.score, reverse=True)
    return targets


def _score_structure_target(
    struct: Unit,
    our_army_center: Point2 | None,
    enemy_army: EnemyArmyEstimate | None,
    info_staleness: float,
) -> OpportunityTarget | None:
    """Score a single enemy structure as an attack target."""
    pos = struct.position

    # Classify target type and base value
    if struct.type_id in EXPANSION_TYPES:
        target_type = "expansion"
        base_value = 50.0
    else:
        target_type = "main"
        base_value = 30.0

    # Distance penalty from our army
    if our_army_center is not None:
        dist = our_army_center.distance_to(pos)
        distance_penalty = dist * 0.5
    else:
        distance_penalty = 0.0

    # Enemy army proximity modifier
    enemy_army_dist = 999.0
    enemy_proximity_modifier = 0.0
    if enemy_army is not None and enemy_army.estimated_position is not None:
        enemy_army_dist = enemy_army.estimated_position.distance_to(pos)
        if enemy_army_dist < 20:
            enemy_proximity_modifier = -80.0  # heavily defended
        elif enemy_army_dist > 40:
            enemy_proximity_modifier = 30.0  # undefended

    # Info staleness penalty — less confident about old info
    staleness_penalty = 20.0 * min(info_staleness / 120.0, 1.0)

    score = base_value - distance_penalty + enemy_proximity_modifier - staleness_penalty

    return OpportunityTarget(
        position=pos,
        target_type=target_type,
        base_value=base_value,
        score=score,
        risk=max(0.0, -enemy_proximity_modifier),
        enemy_army_distance=enemy_army_dist,
        tags={struct.tag},
    )


def _find_worker_clusters(
    enemy_units: list[Unit],
    cluster_radius: float = 8.0,
) -> list[tuple[Point2, set[int]]]:
    """Find clusters of enemy workers (mineral line targets).

    Returns list of (center_position, worker_tags).
    """
    workers = [u for u in enemy_units if u.type_id in WORKER_TYPES]
    if len(workers) < 3:
        return []

    # Simple greedy clustering
    used: set[int] = set()
    clusters: list[tuple[Point2, set[int]]] = []

    for seed in workers:
        if seed.tag in used:
            continue
        cluster_tags: set[int] = {seed.tag}
        used.add(seed.tag)
        for other in workers:
            if other.tag in used:
                continue
            if seed.distance_to(other) < cluster_radius:
                cluster_tags.add(other.tag)
                used.add(other.tag)
        if len(cluster_tags) >= 3:
            # Compute center
            cx = sum(
                w.position.x for w in workers if w.tag in cluster_tags
            ) / len(cluster_tags)
            cy = sum(
                w.position.y for w in workers if w.tag in cluster_tags
            ) / len(cluster_tags)
            clusters.append((Point2((cx, cy)), cluster_tags))

    return clusters


def _score_mineral_line_target(
    pos: Point2,
    tags: set[int],
    our_army_center: Point2 | None,
    enemy_army: EnemyArmyEstimate | None,
    info_staleness: float,
) -> OpportunityTarget:
    """Score a mineral line cluster as a harassment target."""
    base_value = 20.0 + len(tags) * 5.0  # more workers = higher value

    distance_penalty = 0.0
    if our_army_center is not None:
        distance_penalty = our_army_center.distance_to(pos) * 0.5

    enemy_army_dist = 999.0
    enemy_proximity_modifier = 0.0
    if enemy_army is not None and enemy_army.estimated_position is not None:
        enemy_army_dist = enemy_army.estimated_position.distance_to(pos)
        if enemy_army_dist < 20:
            enemy_proximity_modifier = -60.0
        elif enemy_army_dist > 40:
            enemy_proximity_modifier = 20.0

    staleness_penalty = 10.0 * min(info_staleness / 120.0, 1.0)

    score = base_value - distance_penalty + enemy_proximity_modifier - staleness_penalty

    return OpportunityTarget(
        position=pos,
        target_type="mineral_line",
        base_value=base_value,
        score=score,
        risk=max(0.0, -enemy_proximity_modifier),
        enemy_army_distance=enemy_army_dist,
        tags=tags,
    )


# ---------------------------------------------------------------------------
# Layer 3 helper — score WHAT to kill (per-unit focus fire)
# ---------------------------------------------------------------------------

def score_focus_fire_target(
    unit: Unit,
    enemy: Unit,
    profile: EngagementProfile,
) -> float:
    """Score a single enemy unit as a focus fire target for `unit`.

    Higher score = more desirable to attack.

    Scoring factors:
      - Profile focus targets: +100, +90, +80... by position in priority list
      - Wounded: +(1 - hp%) * 30  — finish off damaged units
      - In weapon range: +20  — can attack immediately
      - Distance: -3 per unit distance  — closer is better
      - Support unit: +50  — kill medivacs, warp prisms first
      - Avoid target: -200  — strongly discouraged
    """
    score = 0.0

    # Profile focus target bonus (100, 90, 80, ...)
    if enemy.type_id in profile.focus_targets:
        idx = profile.focus_targets.index(enemy.type_id)
        score += max(100.0 - idx * 10.0, 10.0)

    # Support unit bonus
    if profile.support_focus and enemy.type_id in SUPPORT_TYPES:
        score += 50.0

    # Wounded bonus — finish off damaged units
    hp_fraction = (enemy.health + enemy.shield) / max(
        enemy.health_max + enemy.shield_max, 1.0
    )
    score += (1.0 - hp_fraction) * 30.0

    # In weapon range bonus
    dist = unit.distance_to(enemy)
    if dist <= unit.ground_range + unit.radius + enemy.radius:
        score += 20.0

    # Distance penalty
    score -= dist * 3.0

    # Avoid target penalty
    if enemy.type_id in profile.avoid_targets:
        score -= 200.0

    return score


def pick_best_target(
    unit: Unit,
    enemies: list[Unit],
    profile: EngagementProfile,
    max_range: float = 15.0,
) -> Unit | None:
    """Pick the best target for `unit` to attack, driven by the profile.

    Filters enemies within `max_range`, scores each using the engagement
    profile, and returns the highest-scoring target.

    Returns None if no valid targets in range.
    """
    if not enemies:
        return None

    best_score = float("-inf")
    best_target: Unit | None = None

    for enemy in enemies:
        dist = unit.distance_to(enemy)
        if dist > max_range:
            continue

        score = score_focus_fire_target(unit, enemy, profile)
        if score > best_score:
            best_score = score
            best_target = enemy

    return best_target
