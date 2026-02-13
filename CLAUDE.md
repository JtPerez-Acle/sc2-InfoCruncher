# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

SC2 Information-Gain Bot — a StarCraft II Zerg bot that treats attention as a finite resource. It uses Bayesian surprise to skip ~50% of game state observations with zero critical misses, and a three-layer combat system that translates strategic intelligence into archetype-specific micro.

---

## Commands

```bash
# Run a game (launches SC2, plays vs CheatVision AI, logs to data/logs/)
poetry run python run.py

# Run all tests (269 tests, no SC2 needed)
poetry run pytest

# Run a single test file
poetry run pytest tests/test_logging.py

# Run a single test by name
poetry run pytest tests/test_logging.py -k "test_delta_tracker_surprise"

# Validate game logs (picks most recent JSONL in data/logs/)
poetry run python scripts/validate_logs.py

# Install deps (after any pyproject.toml change)
poetry lock && poetry install --with test
```

Poetry executable is at `C:/Users/joset/.local/bin/poetry.exe`. If bare `poetry` doesn't work in bash, use the full path.

---

## Critical Gotchas

### ares-sc2 is a git submodule, not a pip package
Lives at `ares-sc2/` and is referenced as `path = "ares-sc2"` in pyproject.toml. After cloning: `git submodule update --init --recursive`. Never `pip install ares-sc2`.

### sys.path manipulation is required
`run.py` prepends `ares-sc2/src/ares`, `ares-sc2/src`, `ares-sc2` to `sys.path` before any ares import. Any new script that imports from `bot.main` needs the same, or must be run via `poetry run` from the project root.

### Always run from project root
The bot reads `config.yml` and `zerg_builds.yml` from cwd. Running from a subdirectory breaks config loading.

### Python 3.13 does NOT work
ares-sc2 requires `<3.13`. Poetry is configured to use uv-managed Python 3.12. Don't change this.

### Don't format ares-sc2/
Both black and isort are configured to skip `ares-sc2/*`. It's upstream code.

### No commits yet
Everything is untracked. The repo has `git init` and a staged submodule but zero commits.

---

## Architecture

### Game loop (bot/main.py)

```
MyBot(AresBot)
  on_start()    -> init MacroManager, CombatSystem, QueenManager,
                   GameLogger, StrategicAnalyzer, ScoutingManager
  on_step()     -> Mining -> assign roles -> [build order | strategy-driven] -> log
  on_unit_*     -> log events
  on_building_* -> log events
  on_end        -> write game summary, flush logs
```

### Two-phase game loop
1. **Build order phase** (`build_order_runner.build_completed == False`): Framework handles production from `zerg_builds.yml`. Bot only does queen inject + emergency defense + emergency production if floating.
2. **Post-build-order** (`build_completed == True`): `StrategicAnalyzer.evaluate()` produces a `Strategy` object that drives all macro, combat, and scouting decisions.

### Strategy system (bot/strategy/)

The `StrategicAnalyzer` reads game state every 2 seconds and outputs a `Strategy` dataclass:
- `ThreatAssessment` — enemy archetype, air fraction, counter comp, AA floor, threat level
- `EconomyPlan` — drone targets, gas timing, expansion triggers
- `army_comp` — counter-composition with dynamic proportions
- `tech_targets` — what to build next
- `attack_ready` — whether army is strong enough to attack

Pipeline: `memory.update()` -> `classifier.classify()` -> `threat_model.evaluate()` -> `economy_model.evaluate()` -> `Strategy`

Sub-modules:
- `composition.py` — `CompositionClassifier` classifies enemy into 16 archetypes (BIO, MECH, SKYTOSS, TURTLE, etc.), outputs `CompositionAnalysis` with dynamic AA floor (0-55%), mech flags, anti-armor priority
- `threat_model.py` — `ThreatModel` reads enemy data + composition -> `ThreatAssessment`
- `economy_model.py` — `EconomyModel` reads saturation/supply + composition -> `EconomyPlan`
- `memory.py` — `StrategicMemory` accumulates scouting data, `SpendingDiagnosis`, `get_info_staleness()`
- `unit_counter_db.py` — Unit profiles, counter maps (including static defense), tech requirements

### Combat system (bot/combat/)

Three-layer architecture replacing the old flat `CombatManager`:

```
Strategy + Game State
        |
  Layer 1: BattlefieldAwareness (awareness.py)
        |  -> BattlefieldState: enemy estimate, targets, staging, confidence
        |
  Layer 2: CombatPlanner (planner.py)
        |  -> CombatPlan: objective, approach, squads, profile
        |
  Layer 3: TacticalExecutor (executor.py)
        |  -> ares behaviors: kiting, focus fire, AOE, group pathing
        v
  Unit Commands
```

**Key files:**
- `__init__.py` — `CombatSystem` orchestrator (public entry point, backward compat properties)
- `constants.py` — Enums (`CombatObjective`, `ApproachStyle`, `KiteStyle`, `SquadRole`), dataclasses (`BattlefieldState`, `CombatPlan`, `SquadAssignment`), unit type sets, tuning constants
- `profiles.py` — 17 `EngagementProfile` entries keyed by archetype name. Each defines focus targets, avoid targets, kite style, spread distance, ability priorities, harass preferences
- `targeting.py` — `score_strategic_targets()` for WHERE to attack (expansion=50, main=30, distance/proximity/staleness adjustments); `pick_best_target()` for WHAT to kill (profile focus +100/90/80, wounded bonus, support bonus)
- `awareness.py` — Layer 1: tracks enemy army position (even invisible, with heading prediction), peak supply, wipe detection, base threats (worker filter), staging point (creep edge > natural > start), terrain chokes, confidence from info staleness
- `planner.py` — Layer 2: defense (highest priority, force engage), regroup (below MIN_ATTACK_SUPPLY or post-wipe), attack (objective from targets, approach from archetype, multi-prong if 60+ supply with fast units)
- `executor.py` — Layer 3: per-unit micro driven by profile kite style (STUTTER_BACK/FORWARD/HOLD/AGGRESSIVE), AOE abilities, focus fire from `pick_best_target()`, harass squad with retreat at 40% HP

**Combat entry point in main.py:**
```python
self.combat_system = CombatSystem(self)
# Build order phase:
self.combat_system.handle_early_defense()
# Post-build-order:
self.combat_system.handle_combat(strategy)
```

**Backward compat:** `combat_system.combat_mode` returns string ("ATTACK"/"DEFEND"/"REGROUP"), `combat_system.is_attacking` returns bool, `combat_system.combat_context` returns dict for logging. `_assign_army_roles` checks `combat_system._plan.objective == CombatObjective.REGROUP`.

### Manager pattern
Each manager receives the `AresBot` instance and accesses game state through `self.ai.mediator`. Managers don't talk to each other — they all go through the bot. Post-build-order, managers receive the `Strategy` object from the analyzer.

### Scouting system (bot/scouting.py)
`ScoutingManager` handles active intelligence gathering after build order completes:
- Assigns overlords to prioritized vision positions (enemy nat spot, third, map center, OL spots)
- Checks info staleness via `StrategicMemory.get_info_staleness()` — sends scouts when >90s stale
- Retreats overlords from anti-air threats (distance < 15) using `PathUnitToTarget` + air grid
- Uses `UnitRole.SCOUTING` for assigned overlords; never picks first overlord (supply)

### Info-gain scheduler (bot/info_gain/)
`ObservationScheduler` in `scheduler.py` decides per-frame whether to observe or skip:
- Composite priority = adjusted surprise + phase weighting + combat bonuses + enemy bonuses
- Combat guard force-observes mode transitions and new threats
- Production noise discount (EGG/LARVA/DRONE/OVERLORD at 0.7x)
- Adaptive threshold from sliding window of recent priorities
- Integrated into `GameLogger.on_step()` — controlled by `InfoGain:` section in `config.yml`

### Logging system (bot/logging/)
Every game writes JSONL to `data/logs/YYYYMMDD_HHMMSS_MapName.jsonl`. Five record types (schemas in `bot/logging/schemas.py`):
- `"state"` — Full game state snapshot every 5 steps (~2.2/sec) with delta + surprise_score
- `"skip"` — Lightweight marker when info-gain scheduler skips a frame (priority, threshold, reason)
- `"decision"` — Decision traces from managers with "why" context
- `"event"` — Unit created/destroyed, building started/completed
- `"summary"` — Once at game end: result, cumulative stats, surprise statistics

Adding logging to new code:
```python
logger = getattr(ai, "game_logger", None)
if logger:
    logger.log_decision("module_name", "action_name", {"reason": "why"})
```

### Key ares-sc2 APIs used
- `self.mediator.get_own_army_dict` / `get_own_structures_dict` — `{UnitTypeId: [Unit, ...]}`
- `self.mediator.can_win_fight(own, enemy)` — returns `EngagementResult`
- `self.mediator.assign_role(tag, role)` / `get_units_from_role(role)` / `switch_roles(from, to)` — unit role tagging
- `self.register_behavior(behavior)` — queue behavior for execution in `_after_step`
- `MacroPlan`, `SpawnController`, `AutoSupply`, `BuildWorkers` — macro behaviors
- `CombatManeuver` — priority-ordered behavior chain (first success wins)
- Individual behaviors: `StutterUnitBack`, `StutterUnitForward`, `AMove`, `PathUnitToTarget`, `KeepUnitSafe`, `ShootTargetInRange`, `AttackTarget`
- `AutoUseAOEAbility` — import from `ares.behaviors.combat.individual.auto_use_aoe_ability` (NOT from `individual/__init__.py`)
- `self.mediator.get_ol_spots` / `get_ol_spot_near_enemy_natural` / `get_enemy_nat` — scouting positions
- `self.mediator.get_ground_grid` / `get_air_grid` — pathing grids
- `self.mediator.get_map_choke_points` — returns `set[Point2]` of individual tiles (need clustering)
- `self.mediator.find_nearby_creep_edge_position(position, search_radius)` — creep edge position

---

## Config Reference

```yaml
# config.yml — key sections
GameStep: 2                          # Decide every 2 game frames (~11 decisions/sec)

Logging:
    Enabled: True
    StateSnapshotInterval: 5         # Snapshot every N on_step calls
    DecisionTracing: True
    EventLogging: True
    FlushInterval: 50                # Async flush every N steps
    DeltaTracking: True
    LogDirectory: "data/logs"

InfoGain:
    Enabled: False                   # Toggle info-gain scheduler
    ObservationBudget: 0.4           # Target skip rate (0.4 -> ~50% skip)
    CombatGuard: True                # Force-observe combat transitions
    ProductionDiscount: 0.7          # Discount noise from eggs/larva/drones
    MinObserveInterval: 3            # Min steps between observations
```

---

## Testing

Tests use `MockAresBot` (defined in `tests/conftest.py`) — a lightweight mock with just enough attributes for the logging and strategy systems. No SC2 installation needed.

Test files:
- `tests/test_combat_system.py` — Constants, profiles, targeting, planner logic, backward compat (47 tests)
- `tests/test_logging.py` — DeltaTracker, GameLogger, JSONL output, surprise scoring, info-gain scheduler
- `tests/test_strategy.py` — Unit counter DB, threat model, economy model
- `tests/test_memory.py` — Strategic memory, spending diagnosis, info staleness
- `tests/test_composition.py` — CompositionClassifier, archetype detection, AA floor scaling
- `tests/test_scouting.py` — ScoutingManager, overlord assignment, retreat, staleness triggers

---

## Environment

- **Python**: 3.12.12 (managed by uv, required: >=3.11, <3.13)
- **Virtualenv**: `C:\Users\joset\AppData\Local\pypoetry\Cache\virtualenvs\sc2-infogain-bot-7ZPckq5v-py3.12`
- **Dependency manager**: Poetry (NOT pip, NOT uv for deps)
- **SC2 path**: `C:\Program Files (x86)\StarCraft II\`
- **Maps path**: `C:\Program Files (x86)\StarCraft II\Maps\`
- **OS**: Windows (WSL not used)
