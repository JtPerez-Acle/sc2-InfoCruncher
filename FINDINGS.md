# Combat System Findings — Game 10 (Defeat 20:33 vs VeryHard Terran)

Log: `data/logs/20260211_234910_Persephone_AIE.jsonl`
Analysis: `data/analysis/20260211_234910_Persephone_AIE_analysis.txt`

---

## Problem 1: Trickle Attack (Critical)

**Symptom**: Units sent into enemy army 1-2 at a time instead of massing.

**Evidence** (rallying promotions from log):
```
5:37 | 17 units -> ATTACK_EXPANSION   (good — full army)
7:31 |  2 units -> ATTACK_EXPANSION   (BAD — trickle)
9:26 |  1 units -> ATTACK_BASE        (BAD — trickle)
10:53 | 21 units -> ATTACK_BASE       (good)
14:42 |  1 units -> ATTACK_BASE       (BAD — trickle)
```

**Evidence** (mode transitions showing instant REGROUP→ATTACK):
```
7:28 -> REGROUP
7:31 -> ATTACK    ← 3 seconds of regroup, army=2

9:26 -> REGROUP
9:27 -> ATTACK    ← 1 second of regroup, army=1

14:40 -> REGROUP
14:42 -> ATTACK   ← 2 seconds of regroup, army=1
```

**Root cause**: `CombatPlanner._should_regroup()` in `bot/combat/planner.py:145-176`.

The ONLY regroup gate is:
```python
if supply < MIN_ATTACK_SUPPLY:  # 20.0
    return True
```

What happens:
1. Army fights, supply drops below 20 → REGROUP triggers
2. Eggs hatch / new units spawn → supply spikes above 20 within 1-3 seconds
3. `_should_regroup()` returns False (supply >= 20)
4. Planner calls `_plan_attack()` which sets `_attack_committed = True`
5. `_manage_roles()` in `__init__.py:207-218` promotes ALL rallying units to ATTACKING
6. Those 1-2 units march alone into the enemy and die
7. Loop repeats

Missing:
- **No minimum regroup DURATION** — once REGROUP starts, there's no timer forcing the bot to stay in REGROUP for at least N seconds
- **No minimum army size for ATTACK promotion** — even 1 rallying unit gets promoted
- **The 20s attack commitment timer only prevents ATTACK→REGROUP, not REGROUP→ATTACK** — it resets when the planner enters `_plan_attack()`

The peak_army_supply and REGROUP_RATIO (0.55) path at line 167 only triggers after a wipe (WIPE_COOLDOWN=15s), which means partial losses don't trigger it.

**Relevant constants** in `bot/combat/constants.py`:
```python
MIN_ATTACK_SUPPLY = 20.0
REGROUP_RATIO = 0.55
WIPE_COOLDOWN = 15.0
ATTACK_COMMIT_TIME = 20.0
```

---

## Problem 2: No Multi-Army / No Real Harassment

**Symptom**: Bot never splits army. Log shows `squads:1` in every single cycle summary. All units go to the same target.

**Evidence**:
```
Every cycle_summary shows: squads:1
approach: always FRONTAL (never MULTI_PRONG)
micro summary: always {'main': N} or {'rally': N} or {'defend': N}
```

**Root cause**: Multi-prong has three gates in `bot/combat/planner.py:344-355`:

```python
if (battlefield.our_army_supply >= HARASS_MIN_ARMY_SUPPLY  # 60.0
        and profile.allow_harass):
    army = ai.mediator.get_units_from_role(role=UnitRole.ATTACKING)
    if army:
        fast_count = sum(1 for u in army if u.type_id in FAST_TYPES)
        if fast_count >= 6:
            return ApproachStyle.MULTI_PRONG
```

Issues:
1. **HARASS_MIN_ARMY_SUPPLY = 60.0** — very high threshold, rarely met given trickle losses
2. **Even when MULTI_PRONG is chosen**, the harass squad in `_assign_squads()` (line 383-394) creates a `SquadAssignment(role=SquadRole.HARASS)` with empty `unit_tags` — no units are actually assigned to it
3. **The executor** (`bot/combat/executor.py`) only processes `SquadRole.MAIN_ARMY` and `SquadRole.DEFENSE` squads. There is no code path that separates fast units into a harass group and gives them independent commands
4. The `FAST_TYPES` set is `{ZERGLING, BANELING, MUTALISK}` — mutas are the natural harass unit but they're lumped with the main army

**Result**: The architecture PLANS for multi-prong but never EXECUTES it. The executor treats all units as one blob.

---

## Problem 3: LAIR Tech Spam (Still Present)

**Symptom**: LAIR requested 12 times between 3:25 and 6:10 despite our recent fixes.

**Evidence** from tech decisions:
```
3:25 | LAIR trigger=strategy_tech_target [SPAM]
3:40 | LAIR trigger=strategy_tech_target [SPAM]
...repeated 12 times through 6:10
```

LAIR never appears in building_started/completed events. HYDRALISKDEN starts at 6:20, meaning LAIR must have completed by then (prerequisite gate works), but the morph was never logged.

**Root cause analysis**:
1. `TechUp(LAIR)` silently fails every frame (busy hatchery) — ares framework behavior
2. Our `_force_morph()` escalation fires after 10s, but may fail if `can_afford(LAIR)` returns False during heavy droning (LAIR costs 150m/100g)
3. Even when force_morph succeeds, LAIR morphing is not logged because `on_building_construction_started` doesn't fire for morphs — only for placed structures
4. The 15s `STRUCT_RETRY_COOLDOWN` causes re-requests even while morph is in progress, because `structure_present_or_pending(LAIR)` may return False during the morph (depends on python-sc2 implementation)

**Possible fix**: Track LAIR morph explicitly. After `_force_morph()` succeeds, mark the structure as pending in our own tracking (not just relying on `structure_present_or_pending`).

---

## Problem 4: Gas Hoarding (Late Game)

**Symptom**: 1540g banked at 19:00 despite gas banking feedback fix.

**Evidence** (gas timeline from state snapshots):
```
14:00 |   15m/ 350g
15:00 |   10m/ 332g
16:00 |   40m/ 126g   ← gas spending temporarily works
17:00 |   24m/ 432g   ← army dies, gas banks again
18:00 |   86m/ 964g   ← severe banking
19:00 |  111m/1540g   ← critical
```

**Root cause**: The gas banking feedback (economy_model.py) reduces gas INCOME but doesn't help when units die faster than replacements. The comp switched to Corruptor/Muta/Hydra (all gas-heavy), but these units die instantly to mass Vikings (16!) and Battlecruisers. Gas income continues but nothing to spend it on because:
1. Production is larva-limited (units produced already at max rate)
2. Lost 63/65 mutas, 43/45 corruptors, 121/123 hydras — almost total losses
3. The trickle attack means units die before enough can accumulate

This is a SECONDARY problem — fixing the trickle (Problem 1) would naturally reduce gas banking by keeping units alive longer.

---

## Problem 5: Enemy Kill Tracking Broken

**Symptom**: All 165 enemy kills logged as "UNKNOWN".

**Evidence**: `Enemy Units Killed: UNKNOWN: 165`

**Root cause**: The `game_logger.py` `_tag_type_map` only maps tags for OWN units (events `unit_created` and `building_started`). When an enemy unit dies, `get_unit_type_for_tag()` returns "UNKNOWN" because enemy tags were never registered.

**Fix location**: `bot/logging/game_logger.py:229-231` — the `_tag_type_map` population only happens for `unit_created` and `building_started` events. Enemy unit deaths come through `on_unit_destroyed` in `bot/main.py` which passes the unit_type directly. The issue is likely that main.py passes tag but not type for enemy deaths, or the type resolution fails.

---

## Casualty Summary

| Unit | Produced | Lost | Survival Rate |
|------|----------|------|--------------|
| Zergling | 224 | 224 | 0% |
| Hydralisk | 123 | 121 | 1.6% |
| Mutalisk | 65 | 63 | 3.1% |
| Corruptor | 45 | 43 | 4.4% |
| Roach | 44 | 44 | 0% |

634s in ATTACK mode but army supply fluctuated wildly (50→23→43→29→32→26→50→37).
3841 engagement evaluations (W:2230 / L:1611) — combat sim approved fights that were suicidal trickling.

---

## Fix Priority

1. **Trickle attack (Problem 1)** — Most impactful. Needs minimum regroup duration + minimum army size for ATTACK transition. This alone would improve survival rates and reduce gas banking.
2. **Multi-army execution (Problem 2)** — Important for harass but requires executor changes to physically split units. Architecture exists but execution is empty.
3. **Enemy kill tracking (Problem 5)** — Logging-only fix, easy.
4. **LAIR morph tracking (Problem 3)** — Mostly cosmetic (morph works, just not logged). Could add explicit pending tracking.
5. **Gas hoarding (Problem 4)** — Secondary effect of trickle. Fixing #1 should mostly resolve this.

---

## Key Files

| File | What to examine |
|------|----------------|
| `bot/combat/planner.py:145-176` | `_should_regroup()` — the trickle root cause |
| `bot/combat/planner.py:344-355` | `_choose_approach()` — multi-prong gate |
| `bot/combat/planner.py:359-414` | `_assign_squads()` — harass squad creation (empty) |
| `bot/combat/executor.py` | `_execute_main_army()` — no harass path |
| `bot/combat/__init__.py:190-225` | `_manage_roles()` — instant promotion |
| `bot/combat/constants.py:225-234` | Tuning constants (MIN_ATTACK_SUPPLY, etc.) |
| `bot/main.py` | `on_unit_destroyed` — enemy kill logging |
| `bot/logging/game_logger.py:229` | Tag-type mapping for kills |
| `analyze_game_detailed.py` | Log analysis tool |
