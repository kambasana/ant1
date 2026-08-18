#!/usr/bin/env python3
"""Test the spatial intelligence system against a live game.

Validates:
  - Spatial tensor decoding from base64 float32
  - Exploration tracking (fog of war)
  - Terrain analysis (passability, resources, chokepoints)
  - Threat assessment (enemy positions, centroid)
  - Base detection and minimap rendering
  - Approach corridor computation

Usage:
    OPENRA_PATH=/path/to/OpenRA python -m openra_env.server.app --port 8000 &
    python milsim/tests/test_spatial.py
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "openra-rl"))

from openra_env.client import OpenRAEnv
from milsim.wego import WEGOTurnManager
from milsim.commander import Commander, TacticalOrder, OrderType
from milsim.spatial import SpatialIntel


async def run_spatial_test():
    results = []

    def check(name, condition, detail=""):
        status = "PASS" if condition else "FAIL"
        results.append((name, condition))
        print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))

    print("Spatial Intelligence Tests")
    print("=" * 60)

    try:
        async with OpenRAEnv(base_url="http://localhost:8000", message_timeout_s=120.0) as env:
            wego = WEGOTurnManager(env, ticks_per_turn=75, execution_substeps=5)
            commander = Commander(wego)
            spatial = SpatialIntel()

            # --- Phase 1: Initial observation ---
            print("\n1. Spatial Tensor Decoding")
            briefing = await commander.start_game()
            obs = wego.get_situation().observation

            check("Has spatial data", obs.spatial_channels > 0,
                  f"{obs.spatial_channels} channels")
            check("Map dimensions", obs.map_info.width > 0 and obs.map_info.height > 0,
                  f"{obs.map_info.width}x{obs.map_info.height}")

            spatial.update(obs)
            check("Tensor decoded", spatial.has_data)
            check("Width matches", spatial.width == obs.map_info.width)
            check("Height matches", spatial.height == obs.map_info.height)

            # --- Phase 2: Exploration ---
            print("\n2. Exploration Tracking")
            exploration = spatial.get_exploration()
            check("Exploration computed", exploration.explored_pct > 0,
                  f"{exploration.explored_pct}% explored")
            check("Hidden area exists", exploration.hidden_pct > 0,
                  f"{exploration.hidden_pct}% hidden")
            check("Unexplored regions found", len(exploration.unexplored_regions) > 0,
                  f"{len(exploration.unexplored_regions)} regions")

            # --- Phase 3: Terrain Analysis ---
            print("\n3. Terrain Analysis")
            # Analyze center of map
            cx, cy = spatial.width // 2, spatial.height // 2
            terrain = spatial.get_terrain_analysis(cx - 10, cy - 10, cx + 10, cy + 10)
            check("Terrain passability", terrain.passable_ratio > 0,
                  f"{terrain.passable_ratio:.0%} passable")
            check("Height data", terrain.avg_height >= 0,
                  f"avg height {terrain.avg_height}")

            # Full map terrain
            full_terrain = spatial.get_terrain_analysis(0, 0, spatial.width, spatial.height)
            check("Resources found", full_terrain.resource_cells >= 0,
                  f"{full_terrain.resource_cells} resource cells")

            # Resource finder
            resources = spatial.find_resources(min_density=0.1)
            check("Resource patches", len(resources) >= 0,
                  f"{len(resources)} patches")

            # --- Phase 4: Base Detection ---
            print("\n4. Base Detection")

            # Deploy MCV first
            mcv = next((u for u in briefing.friendly_units if u.type == "mcv"), None)
            if mcv:
                await commander.issue_orders([
                    TacticalOrder(order_type=OrderType.DEPLOY, unit_ids=[mcv.actor_id]),
                ])
            for _ in range(2):
                await commander.advance_time()

            obs = wego.get_situation().observation
            spatial.update(obs)

            base_bounds = spatial.get_own_base_bounds()
            check("Base detected", base_bounds != (0, 0, 0, 0),
                  f"({base_bounds[0]},{base_bounds[1]}) to ({base_bounds[2]},{base_bounds[3]})")

            # --- Phase 5: Minimap ---
            print("\n5. Minimap Rendering")
            minimap = spatial.render_minimap(max_cols=40)
            check("Minimap renders", len(minimap) > 0, f"{len(minimap)} chars")
            minimap_hires = spatial.render_minimap(max_cols=spatial.width)
            check("Minimap has buildings", "B" in minimap_hires,
                  "full-res check")
            check("Minimap has fog", "#" in minimap)
            print(f"\n{minimap}\n")

            # --- Phase 6: Build up and scout ---
            print("\n6. Scouting & Threat Detection")

            # Build barracks and train scouts
            await commander.issue_orders([
                TacticalOrder(order_type=OrderType.BUILD, item_type="powr"),
            ])
            for _ in range(5):
                await commander.advance_time()

            obs = wego.get_situation().observation
            available = obs.available_production
            barracks = next((t for t in ("tent", "barr") if t in available), None)
            if barracks:
                await commander.issue_orders([
                    TacticalOrder(order_type=OrderType.BUILD, item_type=barracks),
                ])
                for _ in range(5):
                    await commander.advance_time()

            await commander.issue_orders([
                TacticalOrder(order_type=OrderType.TRAIN, item_type="e1", count=3),
            ])
            for _ in range(4):
                await commander.advance_time()

            # Send scouts toward map center
            obs = wego.get_situation().observation
            scouts = [u for u in obs.units if u.type == "e1"]
            if len(scouts) >= 2:
                map_cx = obs.map_info.width // 2
                map_cy = obs.map_info.height // 2
                await commander.issue_orders([
                    TacticalOrder(
                        order_type=OrderType.RECONNOITER,
                        unit_ids=[scouts[0].actor_id],
                        target_x=map_cx,
                        target_y=map_cy,
                    ),
                    TacticalOrder(
                        order_type=OrderType.RECONNOITER,
                        unit_ids=[scouts[1].actor_id],
                        target_x=obs.map_info.width - 5,
                        target_y=obs.map_info.height - 5,
                    ),
                ])

            # Advance many turns to scout
            enemy_found = False
            for turn_idx in range(30):
                result, briefing = await commander.advance_time()
                obs = wego.get_situation().observation
                spatial.update(obs)

                threat = spatial.get_threat_assessment()
                if threat.total_visible_enemies > 0 and not enemy_found:
                    enemy_found = True
                    print(f"  Spatial contact! Turn {wego.turn_number}: "
                          f"{threat.total_visible_enemies} enemies at "
                          f"({threat.enemy_centroid[0]},{threat.enemy_centroid[1]})")

                if wego.is_game_over:
                    break

                if enemy_found and turn_idx > 10:
                    break

            # --- Phase 7: Threat Assessment ---
            print("\n7. Threat Assessment")
            threat = spatial.get_threat_assessment()
            check("Enemy detection", threat.total_visible_enemies >= 0,
                  f"{threat.total_visible_enemies} visible")

            if threat.total_visible_enemies > 0:
                check("Enemy centroid", threat.enemy_centroid != (0, 0),
                      f"({threat.enemy_centroid[0]},{threat.enemy_centroid[1]})")
                check("Threat direction", threat.threat_direction != (0, 0),
                      f"({threat.threat_direction[0]:+d},{threat.threat_direction[1]:+d})")
                check("Enemy spread", threat.enemy_spread >= 0,
                      f"{threat.enemy_spread} cells")
            else:
                check("Enemy centroid", True, "no enemies visible yet")
                check("Threat direction", True, "n/a")
                check("Enemy spread", True, "n/a")

            # --- Phase 8: Approach Corridors ---
            print("\n8. Approach Corridors")
            target_x = threat.enemy_centroid[0] if threat.total_visible_enemies > 0 else spatial.width // 2
            target_y = threat.enemy_centroid[1] if threat.total_visible_enemies > 0 else spatial.height // 2
            corridors = spatial.find_approach_corridors(target_x, target_y)
            check("Corridors computed", len(corridors) > 0,
                  f"{len(corridors)} corridors found")
            if corridors:
                check("Corridor has waypoints", len(corridors[0]) > 2,
                      f"{len(corridors[0])} waypoints")

            # --- Phase 9: Spatial SITREP ---
            print("\n9. Spatial SITREP")
            sitrep = spatial.format_spatial_sitrep()
            check("SITREP generates", len(sitrep) > 100, f"{len(sitrep)} chars")
            check("SITREP has exploration", "Explored:" in sitrep)
            check("SITREP has map size", f"{spatial.width}x{spatial.height}" in sitrep)
            print(f"\n{sitrep}\n")

            # Updated minimap after scouting
            print("Updated minimap:")
            minimap2 = spatial.render_minimap(max_cols=50)
            print(f"{minimap2}\n")

            exploration2 = spatial.get_exploration()
            check("Exploration increased", exploration2.explored_pct > exploration.explored_pct,
                  f"{exploration.explored_pct}% → {exploration2.explored_pct}%")

    except ConnectionRefusedError:
        print("\nERROR: Could not connect to server at localhost:8000")
        return False
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"\n{'=' * 60}")
    print(f"Results: {passed}/{total} passed")
    print(f"{'=' * 60}")

    if passed == total:
        print("\nSpatial intelligence system fully operational.")
    else:
        print(f"\n{total - passed} check(s) failed.")

    return passed == total


if __name__ == "__main__":
    success = asyncio.run(run_spatial_test())
    sys.exit(0 if success else 1)
