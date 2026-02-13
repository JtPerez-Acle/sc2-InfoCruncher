"""Quick debug trace for game analysis."""
import json, sys

path = sys.argv[1] if len(sys.argv) > 1 else "data/logs/20260211_183705_Ley_Lines_AIE.jsonl"

print("=== Strategy Analysis ===")
with open(path, "r", encoding="utf-8") as f:
    for line in f:
        r = json.loads(line)
        if r.get("type") == "decision" and r.get("action") == "analysis":
            gt = r["game_time"]
            d = r["details"]
            print(f"t={gt:5.0f}: adv={d['army_advantage']:5.2f}  atk={str(d['attack_ready']):5s}  "
                  f"ratio={d['army_ratio']:.2f}  bases={d['target_bases']}  drones={d['target_drones']}  "
                  f"gas={d['target_gas']}  threat={d['threat_level']:.2f}  "
                  f"tech={d.get('tech_targets',[])}  comp={list(d.get('recommended_comp',{}).keys())[:3]}")

print("\n=== Memory States ===")
with open(path, "r", encoding="utf-8") as f:
    for line in f:
        r = json.loads(line)
        if r.get("type") == "decision" and r.get("action") == "memory_state":
            gt = r["game_time"]
            d = r["details"]
            print(f"t={gt:5.0f}: est_val={d['estimated_enemy_value']:6.0f}  "
                  f"types={d['enemy_types_seen']}  "
                  f"larva={str(d['is_larva_bound']):5s}  tech={str(d['is_tech_bound']):5s}  "
                  f"gas={str(d['is_gas_bound']):5s}  supply={str(d['is_supply_bound']):5s}  "
                  f"float={d['float_duration']:5.1f}s  min={d['mineral_float']}")
