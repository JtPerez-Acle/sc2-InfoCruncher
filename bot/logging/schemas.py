"""TypedDict definitions for all log record types.

These are zero-overhead at runtime — they exist only for type checking
and documentation. At write time we just json.dumps plain dicts.
"""

from __future__ import annotations

from typing import TypedDict


class DeltaRecord(TypedDict, total=False):
    """Frame-over-frame changes (attached to StateSnapshot)."""

    economy_delta: dict[str, float]  # field -> change
    own_units_delta: dict[str, int]  # unit_type -> count change
    enemy_units_delta: dict[str, int]
    own_structures_delta: dict[str, int]
    enemy_structures_delta: dict[str, int]
    own_army_moved: float  # distance in map units
    enemy_army_moved: float
    new_upgrades: list[str]
    surprise_score: float  # weighted L1 norm of all deltas


class StateSnapshot(TypedDict, total=False):
    """Full game state captured every N steps."""

    type: str  # "state"
    game_time: float  # in-game seconds
    step: int  # iteration number
    real_time: float  # wall-clock seconds since epoch

    # Economy
    minerals: int
    vespene: int
    supply_used: int
    supply_cap: int
    supply_army: int
    supply_workers: int
    larva_count: int

    # Units / structures — dict[unit_type_name, count]
    own_units: dict[str, int]
    enemy_units: dict[str, int]
    own_structures: dict[str, int]
    enemy_structures: dict[str, int]

    # Positions — [x, y] or None
    own_army_center: list[float] | None
    enemy_army_center: list[float] | None

    # Zerg-specific
    creep_coverage: float  # 0.0 – 1.0
    queen_count: int
    inject_queens: int
    creep_queens: int

    # Tech / upgrades
    upgrades: list[str]

    # Build order status
    build_order_completed: bool
    build_order_step: int

    # Threat assessment (Phase 3 input)
    ground_threat_count: int
    air_threat_count: int
    enemy_army_value: int
    did_enemy_rush: bool

    # Combat state (Phase 3 — correlate micro mode with outcomes)
    combat_mode: str  # "CHASE" | "ATTACK" | "RETREAT" | "REGROUP" | "DEFEND" | "IDLE"
    last_engagement_sim: str | None  # EngagementResult.name or None
    last_engagement_value: int | None  # EngagementResult.value (0-10 scale)

    # Economy rates (from SC2 score API)
    income_minerals: float  # current mineral collection rate
    income_vespene: float  # current gas collection rate
    saturation_ratio: float  # supply_workers / (num_bases * 16)

    # Spending diagnosis
    is_larva_bound: bool
    is_tech_bound: bool
    is_supply_bound: bool
    is_gas_bound: bool
    mineral_float: int  # current mineral bank when floating
    float_duration: float  # seconds floating >400 minerals

    # Strategic intelligence
    enemy_archetype: str  # from CompositionClassifier
    enemy_archetype_confidence: float
    threat_level: float  # 0.0–1.0
    army_advantage: float  # own_value / enemy_value
    info_staleness: float  # seconds since last enemy observation
    enemy_tech_level: str  # "early" | "mid" | "late"

    # Delta from previous snapshot
    delta: DeltaRecord | None


class DecisionTrace(TypedDict, total=False):
    """A single decision made by a bot module."""

    type: str  # "decision"
    game_time: float
    step: int
    module: str  # "macro" | "combat" | "queens"
    action: str  # e.g. "evaluate_engagement", "build_tech_structure"
    details: dict  # free-form "why" context


class EventRecord(TypedDict, total=False):
    """Discrete game event (unit created, destroyed, building done)."""

    type: str  # "event"
    game_time: float
    step: int
    event: str  # "unit_created" | "unit_destroyed" | "building_started" | "building_completed"
    unit_type: str
    unit_tag: int
    is_own: bool
    extra: dict


class GameSummary(TypedDict, total=False):
    """End-of-game summary written once."""

    type: str  # "summary"
    game_time: float
    real_duration: float  # wall-clock seconds
    result: str  # "Victory" | "Defeat" | "Tie" | "Undecided"
    map_name: str
    enemy_race: str
    enemy_name: str

    # Cumulative stats
    total_minerals_collected: int
    total_vespene_collected: int
    units_produced: dict[str, int]  # unit_type -> count
    units_lost: dict[str, int]
    enemy_units_killed: dict[str, int]
    structures_built: dict[str, int]
    structures_lost: dict[str, int]

    # Key timestamps
    build_order_completed_time: float | None
    first_attack_time: float | None
    first_expansion_time: float | None

    # Peak values
    peak_supply: int
    peak_army_supply: int
    peak_worker_count: int

    # Combat stats
    total_engagements: int
    engagements_won: int
    engagements_lost: int
    time_in_chase: float  # seconds spent in CHASE mode
    time_in_attack: float  # seconds spent in ATTACK mode
    time_in_retreat: float  # seconds spent in RETREAT mode
    time_in_regroup: float  # seconds spent in REGROUP mode
    army_wipes: int  # number of detected army wipes

    # Logging meta
    total_snapshots: int
    total_decisions: int
    total_events: int
    avg_surprise_score: float
    max_surprise_score: float

    # Info-gain scheduler stats (Phase 3)
    info_gain: dict  # ObservationScheduler.get_stats() output


class SkipRecord(TypedDict, total=False):
    """Lightweight marker for a skipped observation."""

    type: str  # "skip"
    game_time: float
    step: int
    surprise_score: float
    priority: float
    reason: str
    threshold: float


class ArmySnapshot(TypedDict, total=False):
    """Snapshot of an army at a point in time."""

    units: dict[str, int]  # unit_type_name -> count
    supply: float
    value: float  # from compute_army_value


class EngagementRecord(TypedDict, total=False):
    """One discrete combat engagement from start to end."""

    type: str  # "engagement"
    engagement_id: int
    game_time_start: float
    game_time_end: float
    duration: float  # seconds
    step_start: int
    step_end: int

    # Position context
    position: list[float]  # [x, y] center of engagement
    distance_from_home: float

    # Army snapshots
    own_army_before: ArmySnapshot
    own_army_after: ArmySnapshot
    enemy_army_before: ArmySnapshot
    enemy_army_after: ArmySnapshot

    # Casualties
    own_losses: dict[str, int]  # unit_type -> count lost
    enemy_losses: dict[str, int]
    own_supply_lost: float
    enemy_supply_lost: float

    # Prediction vs. outcome
    sim_prediction: str  # EngagementResult.name
    actual_outcome: str  # "won" | "lost" | "draw" | "disengaged"
    prediction_correct: bool

    # Context
    combat_objective: str
    engagement_profile: str
    approach_style: str
    micro_style: str
    was_committed: bool
    confidence: float

    # Economy context at engagement start
    own_minerals: int
    own_vespene: int
    income_minerals: float
    income_vespene: float
    own_supply_used: int
    own_worker_count: int
