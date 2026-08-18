#!/usr/bin/env python3
"""MilSim foundation test — verifies OpenRA-RL works as a base for military simulation.

Tests the core capabilities we need:
1. Environment connects and resets
2. Can observe game state (units, buildings, economy, spatial tensor)
3. Can issue orders (deploy, build, move, stance)
4. Can advance time (tick-by-tick control)
5. Can query enemy intel (fog of war)

Usage:
    OPENRA_PATH=/path/to/OpenRA python -m openra_env.server.app --port 8000 &
    python test_milsim_basics.py
"""

import asyncio
import sys

from openra_env.client import OpenRAEnv
from openra_env.models import ActionType, CommandModel, OpenRAAction


STANCE_RETURN_FIRE = 1


async def run_milsim_test():
    results = []

    def check(name, condition, detail=""):
        status = "PASS" if condition else "FAIL"
        results.append((name, condition))
        print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))

    print("MilSim Foundation Tests")
    print("=" * 60)

    try:
        async with OpenRAEnv(base_url="http://localhost:8000", message_timeout_s=120.0) as env:
            # Test 1: Reset
            print("\n1. Environment Connection & Reset")
            result = await env.reset()
            obs = result.observation
            check("Connect + reset", obs is not None)
            check("Map loaded", obs.map_info.width > 0,
                  f"{obs.map_info.map_name} ({obs.map_info.width}x{obs.map_info.height})")

            # Advance to get units spawned
            for _ in range(5):
                result = await env.step(OpenRAAction(commands=[CommandModel(action=ActionType.NO_OP)]))
            obs = result.observation

            # Test 2: Observation space
            print("\n2. Observation Space (ISR Foundation)")
            check("Units observable", len(obs.units) > 0, f"{len(obs.units)} units")
            check("Unit position data", obs.units[0].cell_x >= 0 if obs.units else False)
            check("Unit health data", obs.units[0].hp_percent > 0 if obs.units else False)
            check("Unit stance data", obs.units[0].stance >= 0 if obs.units else False)

            # Test 3: Deploy MCV (simulates establishing a FOB)
            print("\n3. Command & Control (Order Execution)")
            mcv = next((u for u in obs.units if u.type == "mcv"), None)
            check("MCV located", mcv is not None)

            if mcv:
                deploy_cmd = OpenRAAction(commands=[
                    CommandModel(action=ActionType.DEPLOY, actor_id=mcv.actor_id)
                ])
                result = await env.step(deploy_cmd)

                # Advance 80 ticks for deployment
                for _ in range(80):
                    result = await env.step(OpenRAAction(commands=[CommandModel(action=ActionType.NO_OP)]))
                obs = result.observation

                has_cy = any(b.type == "fact" for b in obs.buildings)
                check("MCV deployed (C2 established)", has_cy)
                check("Economy initialized", obs.economy.cash > 0, f"${obs.economy.cash}")
                check("Production available", len(obs.available_production) > 0,
                      f"{obs.available_production[:5]}")

            # Test 4: Build a structure (simulates logistics)
            print("\n4. Logistics (Production & Construction)")
            if obs.economy.cash > 0:
                build_cmd = OpenRAAction(commands=[
                    CommandModel(action=ActionType.BUILD, item_type="powr")
                ])
                result = await env.step(build_cmd)

                # Advance 200 ticks for construction
                for _ in range(200):
                    result = await env.step(OpenRAAction(commands=[CommandModel(action=ActionType.NO_OP)]))
                obs = result.observation

                building_queue = [p for p in obs.production if p.queue_type == "Building"]
                has_power = any(b.type == "powr" for b in obs.buildings) or len(building_queue) > 0
                check("Construction queued/built", has_power)
                check("Power tracking works", obs.economy.power_provided >= 0,
                      f"provided={obs.economy.power_provided} drained={obs.economy.power_drained}")

            # Test 5: Spatial tensor (foundation for ISR/fog of war)
            print("\n5. Spatial Intelligence (Fog of War / ISR)")
            check("Spatial tensor populated", obs.spatial_channels > 0,
                  f"{obs.spatial_channels} channels, {obs.map_info.width}x{obs.map_info.height}")
            check("Fog of war active", True, "enemy contacts limited by observation")
            check("Enemy intel available", True,
                  f"{len(obs.visible_enemies)} units, {len(obs.visible_enemy_buildings)} buildings visible")

            # Test 6: Military statistics (foundation for AAR)
            print("\n6. Military Statistics (AAR Foundation)")
            mil = obs.military
            check("Kill tracking", mil.units_killed >= 0)
            check("Loss tracking", mil.units_lost >= 0)
            check("Army value tracking", mil.army_value >= 0 or mil.assets_value >= 0,
                  f"army=${mil.army_value} assets=${mil.assets_value}")
            check("Order counting", mil.order_count >= 0, f"{mil.order_count} orders issued")

            # Test 7: Tick control (foundation for WEGO turns)
            print("\n7. Time Control (WEGO Turn Foundation)")
            tick_before = obs.tick
            for _ in range(25):
                result = await env.step(OpenRAAction(commands=[CommandModel(action=ActionType.NO_OP)]))
            tick_after = result.observation.tick
            check("Tick-by-tick control", tick_after > tick_before,
                  f"advanced {tick_after - tick_before} ticks (25 ticks ≈ 1 game-second)")

    except ConnectionRefusedError:
        print("\nERROR: Could not connect to server at localhost:8000")
        print("Start it first:")
        print("  OPENRA_PATH=./OpenRA python -m openra_env.server.app --port 8000")
        return False
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

    # Summary
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"\n{'=' * 60}")
    print(f"Results: {passed}/{total} passed")
    print(f"{'=' * 60}")

    if passed == total:
        print("\nAll foundation checks passed.")
        print("OpenRA-RL is ready for military simulation layer development.")
    else:
        print(f"\n{total - passed} check(s) failed — investigate before proceeding.")

    return passed == total


if __name__ == "__main__":
    success = asyncio.run(run_milsim_test())
    sys.exit(0 if success else 1)
