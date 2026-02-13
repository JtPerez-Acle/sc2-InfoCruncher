# SC2 Information-Gain Bot

A StarCraft II Zerg bot that treats **attention as a finite resource**. It selectively observes game state using Bayesian surprise, skipping ~50% of frames with zero critical misses, while a three-layer combat system translates strategic intelligence into archetype-specific micro.

**371 tests** | Beats VeryHard AI | Full JSONL game logging

---

## Status

| Phase | Description | Status |
|-------|------------|--------|
| 1 | Playable bot (macro, queens, build order) | Complete |
| 2 | Instrumentation (JSONL logging, delta tracking) | Complete |
| 3 | Info-gain scheduler (Bayesian observation skipping) | Complete |
| 4 | Three-layer combat (perception → planning → micro) | Complete |
| 4.5 | Game intelligence (composition classifier, scouting, late-game) | Complete |
| 5 | Learned scheduler (replace heuristics with ML) | Planned |
| 6 | Evaluation / Ladder (SC2 AI Arena) | Planned |

### Game Results

| # | Opponent | Difficulty | Result | Time | Notes |
|---|----------|-----------|--------|------|-------|
| 1 | Terran | Easy | Defeat | — | Build order hung, 10k minerals |
| 2 | Zerg | Easy | **Victory** | 7:15 | Build order at 4:52 |
| 3 | Terran | Medium | Defeat | 8:52 | Gas hoarding, pure lings |
| 4 | Zerg | Medium | **Victory** | 7:56 | Peak 200 supply, roach+ling |
| 6 | Protoss | Medium | **Victory** | — | Info-gain scheduler validation |
| 7 | Terran | Medium | Defeat | 8:40 | REGROUP deadlock, 0 attacks |
| 8 | Zerg | Medium | Defeat | 8:05 | Fragmentation yo-yo |
| 9 | Protoss | Medium | **Victory** | 8:04 | 153s attack/chase, roach+hydra |
| 10 | Terran | VeryHard | Defeat | 20:33 | Trickle attack, no multi-army |
| 11 | Zerg | VeryHard | **Victory** | 11:09 | 0 wipes, LATE_ZERG detected |

---

## Architecture

```mermaid
graph TD
    subgraph "Game Loop"
        A[on_step] --> B[Mining]
        A --> C{Build Order<br/>Complete?}
        C -->|No| D[Queens + Early Defense]
        C -->|Yes| E[StrategicAnalyzer.evaluate]
        E --> F[Strategy]
        F --> G[MacroManager]
        F --> H[CombatSystem]
        F --> I[ScoutingManager]
        A --> J[GameLogger]
    end
```

### Strategy Pipeline

```mermaid
graph LR
    A[Visible Enemy] --> B[StrategicMemory]
    B --> C[CompositionClassifier<br/>17 archetypes]
    C --> D[ThreatModel<br/>counter comp, AA floor]
    C --> E[EconomyModel<br/>gas scaling, drone targets]
    D --> F[Strategy]
    E --> F
```

The `StrategicAnalyzer` reads game state every 2 seconds and outputs a `Strategy` dataclass containing threat assessment, economy plan, army composition, tech targets, and attack readiness.

**Composition Classifier** identifies 17 enemy archetypes across all three races plus cross-race overlays:
- **Terran**: BIO, MECH, BIO_MECH, AIR_TERRAN
- **Protoss**: GATEWAY, ROBO, STARGATE, SKYTOSS, GATEWAY_ROBO
- **Zerg**: LING_BANE, ROACH_HYDRA, MUTA_LING, LATE_ZERG
- **Cross-race**: AGGRESSIVE, TURTLE, MACRO, UNKNOWN

Each archetype drives: counter composition, engagement profile, AA floor (0-55%), tech targets, and gas scaling.

### Three-Layer Combat System

```mermaid
graph TD
    S[Strategy + Game State] --> L1

    subgraph "bot/combat/"
        L1["Layer 1: Awareness<br/><i>awareness.py</i>"]
        L2["Layer 2: Planner<br/><i>planner.py</i>"]
        L3["Layer 3: Executor<br/><i>executor.py</i>"]
        P["Engagement Profiles<br/><i>profiles.py</i>"]
        T["Target Scoring<br/><i>targeting.py</i>"]

        L1 -->|BattlefieldState| L2
        L2 -->|CombatPlan| L3
        P -.-> L2
        P -.-> L3
        T -.-> L1
        T -.-> L3
    end

    L3 -->|ares behaviors| R[Unit Commands]
```

| Layer | Module | Input | Output |
|-------|--------|-------|--------|
| **Perception** | `awareness.py` | Game state, strategy | `BattlefieldState` (enemy estimate, targets, staging point, confidence) |
| **Decisions** | `planner.py` | Battlefield + strategy | `CombatPlan` (objective, approach, squads, profile) |
| **Execution** | `executor.py` | Combat plan | ares-sc2 behaviors (kiting, focus fire, AOE, pathing) |

**Engagement profiles** (`profiles.py`) map each of 18 enemy archetypes to micro rules: focus targets, kite style, spread distance, and ability priorities. Example: vs BIO, prioritize Medivacs, spread 3.0, bile clumps; vs SKYTOSS, kite forward, prioritize Carriers; vs LATE_ZERG, prioritize Infestors/Vipers, spread 4.0.

**Target scoring** (`targeting.py`) operates at two levels:
- *Strategic*: Where to attack (base value, distance, enemy army proximity, info staleness)
- *Tactical*: What to kill (profile focus bonus, wounded bonus, support bonus, range/distance)

**Anti-trickle regroup**: Minimum 15s regroup duration (unconditional), peak-scaled army minimum (40% of peak supply), 20s attack commitment timer.

### Macro System

The `MacroManager` executes economy and production from the Strategy — no hardcoded recipes.

Key features:
- **Tiered gas spending override**: Boosts gas-heavy units when vespene banks. Normal ≤15%, severe (5x ratio) ≤30%, extreme (10x) ≤40%
- **Tech execution tracker**: Detects stalled TechUp/BuildStructure (10s timeout), escalates with force-morph
- **Prerequisite gating**: Blocks tech requests until prerequisites exist (LAIR/HIVE equivalences)
- **Gas extractor feedback**: Reduces extractors when vespene banks (-1 at 400g, -2 at 800g, -3 at 2000g, -5 at 4000g)

### Info-Gain Scheduler

```mermaid
graph LR
    D[Frame Delta] --> P[Priority Score]
    P -->|above threshold| O[Observe]
    P -->|below threshold| S[Skip]
    O --> T[Adaptive Threshold]
    S --> T
    T --> P
```

Per-frame priority = surprise + phase weight + combat bonus + enemy bonus - production discount. Budget 0.4 achieves ~50% skip rate with 0 critical misses.

---

## Quick Start

**Prerequisites:** StarCraft II (free Starter Edition), Python 3.11-3.12, Poetry

```bash
git clone --recurse-submodules <repo-url>
cd SC2-BOT
poetry install --with test

# Play a game
poetry run python run.py

# Run tests (no SC2 needed)
poetry run pytest

# Validate game logs
poetry run python scripts/validate_logs.py
```

---

## Project Structure

```
bot/
  main.py                MyBot(AresBot) -- lifecycle, role assignment
  macro.py               MacroManager -- drones, tech, army production
  queens.py              QueenManager -- inject, creep, transfuse
  scouting.py            ScoutingManager -- overlord positioning
  combat/
    __init__.py           CombatSystem orchestrator (public entry point)
    constants.py          Enums, dataclasses, unit type sets, tuning
    profiles.py           18 engagement profiles per enemy archetype
    targeting.py          Strategic + focus fire target scoring
    awareness.py          Layer 1: enemy tracking, staging, terrain
    planner.py            Layer 2: objective, approach, squad assignment
    executor.py           Layer 3: profile-driven per-unit micro
  strategy/
    analyzer.py           StrategicAnalyzer -- orchestrates pipeline
    composition.py        CompositionClassifier -- 17 archetypes
    threat_model.py       ThreatModel -- counter comp, AA floor, tech inference
    economy_model.py      EconomyModel -- drone/gas/base targets
    memory.py             StrategicMemory -- persistent scouting data
    unit_counter_db.py    Unit profiles, counter maps, tech reqs
  logging/
    game_logger.py        Buffered JSONL writer with async flush
    snapshot.py           State snapshot extraction
    delta.py              Frame diffs, surprise scoring
    schemas.py            TypedDict definitions for all record types
    engagement_tracker.py Combat encounter recording for ML data
  info_gain/
    scheduler.py          ObservationScheduler -- skip/observe

tests/                   371 tests (no SC2 needed)
config.yml               Bot + logging + info-gain configuration
zerg_builds.yml          Build order (ares-sc2 format)
ares-sc2/                Framework (git submodule)
data/logs/               JSONL game logs (generated at runtime)
```

---

## Logging

Every game writes `data/logs/YYYYMMDD_HHMMSS_MapName.jsonl`:

| Record | Frequency | Content |
|--------|-----------|---------|
| `state` | ~2.2/sec | Economy, units, positions, delta, surprise score |
| `skip` | Per skipped frame | Priority, threshold, reason (info-gain scheduler) |
| `decision` | Per action | Module, action, reasoning context |
| `event` | Per lifecycle | Unit/building created, destroyed, sighted, first observed |
| `engagement` | Per combat encounter | Own/enemy comp, sim results, micro style, economy state |
| `summary` | Once | Result, cumulative stats, combat time distribution, discovery timeline |

---

## Key Design Decisions

1. **No hardcoded recipes.** Every macro/combat decision flows from observable game state through the strategy pipeline. The bot adapts to what it sees, not what it expects.

2. **Attention as resource.** The info-gain scheduler proves that a bot can skip 50%+ of observations with zero decision quality loss — a foundation for learned attention in Phase 5.

3. **Profile-driven micro.** Instead of per-matchup code, engagement profiles parameterize combat behavior. Adding a new enemy archetype means adding one data entry, not rewriting micro logic.

4. **Persistent memory.** `StrategicMemory` accumulates scouting data across the entire game. Enemies that leave vision don't disappear from the model — their estimated value grows conservatively until re-observed.

5. **Separation of concerns.** Each manager receives the bot instance and accesses game state through `self.ai.mediator`. Managers don't talk to each other — they all receive the same `Strategy` object from the analyzer.

---

## Tech Stack

- **Framework**: [ares-sc2](https://github.com/AresSC2/ares-sc2) (git submodule)
- **Language**: Python 3.12
- **Dependencies**: Poetry
- **Testing**: pytest (MockAresBot — no SC2 needed)
- **SC2 API**: python-sc2 (via ares-sc2)

---

## Research

> *Can a bot skip 50%+ of game state observations without meaningful decision quality loss?*

**Result:** Yes. The analytical scheduler (Phase 3) skips 50.7% of frames with zero critical misses across 4 validated game logs. The bot won its validation game with the scheduler active. Phase 5 will replace heuristics with a learned model trained on the engagement data collected by the logging system.
