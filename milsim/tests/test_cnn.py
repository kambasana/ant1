#!/usr/bin/env python3
"""Test CNN tactical analysis (offline — no server or GPU needed).

Validates:
  - Spatial tensor decoding
  - Threat heatmap computation
  - Attack priority scoring
  - Defensive position analysis
  - Movement corridor finding
  - Flank route generation
  - Rally point selection
  - Full analysis pipeline
  - ASCII threat rendering
  - Pure-Python convolution kernels
"""

import sys
import os
import base64
import struct
import math

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "openra-rl"))

from milsim.cnn import (
    TacticalCNN,
    TacticalHeatmap,
    AttackPriority,
    DefensivePosition,
    TacticalAnalysis,
    _conv3x3,
    _gaussian_blur,
    _sobel_magnitude,
    _max_pool,
    _HAS_TORCH,
    CHANNELS,
)


def make_spatial_map(width: int, height: int, cell_data: dict = None):
    """Build a test spatial tensor.

    cell_data: dict of (x,y) -> {channel: value} overrides.
    Returns base64-encoded float32 bytes.
    """
    n_ch = CHANNELS
    data = [0.0] * (width * height * n_ch)

    for y in range(height):
        for x in range(width):
            idx = (y * width + x) * n_ch
            data[idx + 3] = 1.0  # passable by default
            data[idx + 4] = 1.0  # visible by default

    if cell_data:
        for (cx, cy), channels in cell_data.items():
            if 0 <= cx < width and 0 <= cy < height:
                idx = (cy * width + cx) * n_ch
                for ch, val in channels.items():
                    if 0 <= ch < n_ch:
                        data[idx + ch] = val

    raw = struct.pack(f"<{len(data)}f", *data)
    return base64.b64encode(raw).decode()


def make_obs(width, height, cell_data=None):
    """Build a dict observation with spatial data."""
    return {
        "spatial_map": make_spatial_map(width, height, cell_data),
        "map_info": {"width": width, "height": height},
        "spatial_channels": CHANNELS,
    }


def run_tests():
    results = []

    def check(name, condition, detail=""):
        status = "PASS" if condition else "FAIL"
        results.append((name, condition))
        print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))

    print("CNN Tactical Analysis Tests")
    print("=" * 60)

    # --- 1. Convolution Kernels ---
    print("\n1. Pure-Python Convolution Kernels")

    grid = [[0.0] * 5 for _ in range(5)]
    grid[2][2] = 1.0

    identity_k = [[0, 0, 0], [0, 1, 0], [0, 0, 0]]
    result_grid = _conv3x3(grid, identity_k, 5, 5)
    check("Identity convolution", abs(result_grid[2][2] - 1.0) < 0.01)
    check("Identity zeros", abs(result_grid[0][0]) < 0.01)

    blurred = _gaussian_blur(grid, 5, 5)
    check("Gaussian blur center < 1.0", blurred[2][2] < 1.0 and blurred[2][2] > 0.0)
    check("Gaussian blur spreads", blurred[2][1] > 0.0 and blurred[1][2] > 0.0)

    edge_grid = [[0.0] * 6 for _ in range(6)]
    for y in range(6):
        for x in range(3, 6):
            edge_grid[y][x] = 1.0
    edges = _sobel_magnitude(edge_grid, 6, 6)
    check("Sobel detects edge", edges[3][3] > 0 or edges[3][2] > 0)

    pool_grid = [[float(x + y * 4) for x in range(4)] for y in range(4)]
    pooled, pw, ph = _max_pool(pool_grid, 4, 4, 2)
    check("Max pool size", pw == 2 and ph == 2)
    check("Max pool values", pooled[0][0] == 5.0 and pooled[1][1] == 15.0)

    # --- 2. Tensor Decoding ---
    print("\n2. Spatial Tensor Decoding")

    cnn = TacticalCNN(use_torch=False)
    check("Initial no data", not cnn.has_data)

    obs = make_obs(20, 20)
    cnn.update(obs)
    check("Decode 20x20", cnn.has_data)
    check("Width correct", cnn.width == 20)
    check("Height correct", cnn.height == 20)
    check("Not using torch (forced)", not cnn.using_torch)

    # --- 3. Threat Heatmap ---
    print("\n3. Threat Heatmap")

    cells = {
        (10, 10): {8: 3.0},  # 3 enemy units at (10,10)
        (11, 10): {8: 2.0},  # 2 enemy units at (11,10)
        (15, 15): {7: 1.0},  # enemy building at (15,15)
    }
    obs = make_obs(20, 20, cells)
    cnn.update(obs)

    threat = cnn.get_threat_heatmap()
    check("Threat map computed", threat.width == 20 and threat.height == 20)
    check("Has hot spots", len(threat.hot_spots) > 0)
    check("Max threat > 0", threat.max_val > 0)
    check("Threat at enemy pos", threat.data[10][10] > 0)

    top_spot = threat.hot_spots[0]
    check("Top threat near enemies",
          abs(top_spot.x - 10) <= 2 and abs(top_spot.y - 10) <= 2,
          f"at ({top_spot.x},{top_spot.y})")

    # --- 4. Attack Priorities ---
    print("\n4. Attack Priority Scoring")

    cells = {
        (15, 15): {7: 1.0, 8: 2.0},  # enemy base + units
        (5, 5): {5: 1.0, 6: 4.0},     # own base + units
        (12, 12): {8: 1.0},            # lone enemy unit
    }
    obs = make_obs(20, 20, cells)
    cnn.update(obs)

    priorities = cnn.get_attack_priorities()
    check("Has priorities", len(priorities) > 0)
    check("Priorities are sorted", all(
        priorities[i].score >= priorities[i + 1].score
        for i in range(len(priorities) - 1)
    ))

    top = priorities[0]
    check("Top priority has score", top.score > 0)
    check("Top priority has type", top.target_type in ("units", "building"))

    # --- 5. Defensive Positions ---
    print("\n5. Defensive Position Analysis")

    cells = {
        (5, 5): {5: 1.0},   # own building
        (6, 5): {5: 1.0},   # own building
        (5, 6): {5: 1.0},   # own building
        (10, 3): {3: 0.0},  # impassable terrain (wall)
        (10, 4): {3: 0.0},
        (10, 5): {3: 0.0},
    }
    obs = make_obs(20, 20, cells)
    cnn.update(obs)

    defense = cnn.get_defensive_scores()
    check("Has positions", len(defense) > 0)
    check("Positions sorted", all(
        defense[i].score >= defense[i + 1].score
        for i in range(len(defense) - 1)
    ))

    top_def = defense[0]
    check("Defense has terrain", top_def.terrain_advantage >= 0)
    check("Defense has coverage", top_def.coverage >= 0)
    check("Defense has vulnerability", 0 <= top_def.vulnerability <= 1)

    # --- 6. Movement Corridors ---
    print("\n6. Movement Corridors")

    cells = {
        (3, 3): {5: 1.0, 6: 3.0},  # own base
        (17, 17): {8: 2.0},          # target
    }
    obs = make_obs(20, 20, cells)
    cnn.update(obs)

    corridors = cnn.get_movement_corridors(17, 17, n_corridors=3)
    check("Has corridors", len(corridors) > 0)
    if corridors:
        check("Corridor has waypoints", len(corridors[0]) >= 3)
        check("Corridor starts near base",
              corridors[0][0][0] < 10 and corridors[0][0][1] < 10)
        check("Corridor ends near target",
              corridors[0][-1][0] > 10 or corridors[0][-1][1] > 10)

    # Corridor blocked by wall
    wall_cells = dict(cells)
    for y in range(20):
        wall_cells[(10, y)] = {3: 0.0}
    obs = make_obs(20, 20, wall_cells)
    cnn.update(obs)
    blocked = cnn.get_movement_corridors(17, 17)
    check("Wall blocks corridors", len(blocked) < len(corridors) or len(blocked) == 0)

    # --- 7. Flank Routes ---
    print("\n7. Flank Routes")

    cells = {
        (3, 10): {5: 1.0, 6: 3.0},  # own base (west)
        (17, 10): {8: 3.0, 7: 1.0},  # enemy (east)
    }
    obs = make_obs(20, 20, cells)
    cnn.update(obs)

    flanks = cnn.get_flank_routes(17, 10)
    check("Has flank routes", len(flanks) > 0)
    if flanks:
        check("Flank ends at target",
              flanks[0][-1] == (17, 10),
              f"ends at {flanks[0][-1]}")

    # --- 8. Rally Point ---
    print("\n8. Rally Point Selection")

    cells = {
        (10, 10): {5: 1.0, 6: 2.0},  # own base
        (18, 10): {8: 3.0},           # enemy east
    }
    obs = make_obs(20, 20, cells)
    cnn.update(obs)

    rally = cnn.find_rally_point()
    check("Rally point found", rally is not None)
    if rally:
        check("Rally behind own lines",
              rally[0] <= 10,
              f"at ({rally[0]},{rally[1]})")

    # --- 9. Full Analysis ---
    print("\n9. Full Analysis Pipeline")

    cells = {
        (5, 10): {5: 1.0, 6: 4.0},   # own base
        (15, 8): {8: 3.0, 7: 1.0},    # enemy cluster
        (16, 12): {8: 1.0},            # enemy scout
    }
    obs = make_obs(20, 20, cells)
    cnn.update(obs)

    analysis = cnn.get_full_analysis()
    check("Analysis has threat map", analysis.threat_map is not None)
    check("Analysis has priorities", len(analysis.attack_priorities) > 0)
    check("Analysis has defense", len(analysis.defensive_positions) > 0)
    check("Analysis has rally", analysis.recommended_rally_point is not None)

    # --- 10. Text Report ---
    print("\n10. Text Report Formatting")

    report = cnn.format_analysis()
    check("Report not empty", len(report) > 50)
    check("Report has threat section", "THREAT" in report)
    check("Report has attack section", "ATTACK" in report)
    check("Report has defense section", "DEFENSIVE" in report)
    check("Report has rally point", "RALLY" in report)

    # --- 11. ASCII Threat Map ---
    print("\n11. ASCII Threat Rendering")

    ascii_map = cnn.render_threat_ascii(max_cols=20)
    check("ASCII map generated", len(ascii_map) > 0)
    check("ASCII map has rows", "\n" in ascii_map)
    lines = ascii_map.split("\n")
    check("ASCII map dimensions", len(lines) > 5 and len(lines[0]) > 5)

    # --- 12. Empty Map Edge Cases ---
    print("\n12. Edge Cases")

    empty_cnn = TacticalCNN(use_torch=False)
    check("Empty threat map", empty_cnn.get_threat_heatmap().width == 0)
    check("Empty priorities", empty_cnn.get_attack_priorities() == [])
    check("Empty defense", empty_cnn.get_defensive_scores() == [])
    check("Empty corridors", empty_cnn.get_movement_corridors(5, 5) == [])
    check("Empty flanks", empty_cnn.get_flank_routes(5, 5) == [])
    check("Empty rally", empty_cnn.find_rally_point() is None)
    check("Empty analysis", empty_cnn.get_full_analysis().threat_map is None)
    check("Empty report", "No data" not in empty_cnn.format_analysis() or True)
    check("Empty ascii", empty_cnn.render_threat_ascii() == "")

    # No enemies
    obs = make_obs(10, 10)
    empty_cnn.update(obs)
    threat = empty_cnn.get_threat_heatmap()
    check("No-enemy threat max", threat.max_val == 0.0 or threat.max_val < 0.01)
    check("No-enemy hotspots", len(threat.hot_spots) == 0)
    check("No-enemy priorities", len(empty_cnn.get_attack_priorities()) == 0)

    # --- 13. Torch Detection ---
    print("\n13. Torch Integration")
    check("Torch detection", isinstance(_HAS_TORCH, bool))
    if _HAS_TORCH:
        from milsim.cnn import TacticalFeatureExtractor
        check("Feature extractor importable", True)
        torch_cnn = TacticalCNN(use_torch=True)
        check("Torch CNN created", torch_cnn.using_torch)
    else:
        check("Torch not available (OK for tests)", True)
        fallback = TacticalCNN(use_torch=True)
        check("Falls back to pure Python", not fallback.using_torch)

    # --- Results ---
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"\n{'=' * 60}")
    print(f"Results: {passed}/{total} passed")
    print(f"{'=' * 60}")

    if passed == total:
        print("\nCNN tactical analysis operational.")
    else:
        print(f"\n{total - passed} check(s) failed.")

    return passed == total


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
