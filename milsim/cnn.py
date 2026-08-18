"""CNN feature extraction from the 9-channel spatial observation tensor.

Processes the spatial map (H x W x 9 float32) through convolutional
layers to produce tactical predictions: threat heatmaps, attack
priority maps, defensive positioning scores, and movement corridors.

Works with or without PyTorch — falls back to pure-Python convolution
kernels for basic threat/terrain analysis when torch is unavailable.

Channel layout (from spatial.py):
  0 = terrain type, 1 = height, 2 = resources, 3 = passability,
  4 = fog of war, 5 = own buildings, 6 = own units,
  7 = enemy buildings, 8 = enemy units

Usage:
    from milsim.cnn import TacticalCNN
    cnn = TacticalCNN()
    cnn.update(obs)
    threat = cnn.get_threat_heatmap()
    priorities = cnn.get_attack_priorities()
    defense = cnn.get_defensive_scores()
"""

import base64
import struct
import math
from dataclasses import dataclass, field
from typing import Optional

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False

CHANNELS = 9
CH_TERRAIN = 0
CH_HEIGHT = 1
CH_RESOURCE = 2
CH_PASSABLE = 3
CH_FOG = 4
CH_OWN_BUILDINGS = 5
CH_OWN_UNITS = 6
CH_ENEMY_BUILDINGS = 7
CH_ENEMY_UNITS = 8


@dataclass
class ThreatCell:
    """A map cell with computed threat score."""
    x: int
    y: int
    score: float
    contributing_factors: list[str] = field(default_factory=list)


@dataclass
class TacticalHeatmap:
    """A 2D heatmap with metadata."""
    width: int
    height: int
    data: list[list[float]]
    min_val: float = 0.0
    max_val: float = 0.0
    hot_spots: list[ThreatCell] = field(default_factory=list)


@dataclass
class AttackPriority:
    """A target with computed priority score."""
    x: int
    y: int
    score: float
    target_type: str = ""
    approach_quality: float = 0.0
    risk: float = 0.0


@dataclass
class DefensivePosition:
    """A position scored for defensive value."""
    x: int
    y: int
    score: float
    terrain_advantage: float = 0.0
    coverage: float = 0.0
    vulnerability: float = 0.0


@dataclass
class TacticalAnalysis:
    """Complete CNN tactical analysis output."""
    threat_map: Optional[TacticalHeatmap] = None
    attack_priorities: list[AttackPriority] = field(default_factory=list)
    defensive_positions: list[DefensivePosition] = field(default_factory=list)
    movement_corridors: list[list[tuple[int, int]]] = field(default_factory=list)
    recommended_rally_point: Optional[tuple[int, int]] = None
    flank_routes: list[list[tuple[int, int]]] = field(default_factory=list)


def _conv3x3(grid: list[list[float]], kernel: list[list[float]],
             w: int, h: int) -> list[list[float]]:
    """Pure-Python 3x3 convolution with zero padding."""
    out = [[0.0] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            total = 0.0
            for ky in range(-1, 2):
                for kx in range(-1, 2):
                    ny, nx = y + ky, x + kx
                    if 0 <= ny < h and 0 <= nx < w:
                        total += grid[ny][nx] * kernel[ky + 1][kx + 1]
            out[y][x] = total
    return out


def _gaussian_blur(grid: list[list[float]], w: int, h: int) -> list[list[float]]:
    """3x3 Gaussian blur."""
    k = [[1/16, 2/16, 1/16],
         [2/16, 4/16, 2/16],
         [1/16, 2/16, 1/16]]
    return _conv3x3(grid, k, w, h)


def _sobel_magnitude(grid: list[list[float]], w: int, h: int) -> list[list[float]]:
    """Sobel edge detection — returns gradient magnitude."""
    sx = [[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]]
    sy = [[-1, -2, -1], [0, 0, 0], [1, 2, 1]]
    gx = _conv3x3(grid, sx, w, h)
    gy = _conv3x3(grid, sy, w, h)
    out = [[0.0] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            out[y][x] = math.sqrt(gx[y][x] ** 2 + gy[y][x] ** 2)
    return out


def _max_pool(grid: list[list[float]], w: int, h: int,
              pool: int = 2) -> tuple[list[list[float]], int, int]:
    """Downsample via max pooling."""
    ow = w // pool
    oh = h // pool
    out = [[0.0] * ow for _ in range(oh)]
    for y in range(oh):
        for x in range(ow):
            vals = []
            for dy in range(pool):
                for dx in range(pool):
                    sy, sx = y * pool + dy, x * pool + dx
                    if sy < h and sx < w:
                        vals.append(grid[sy][sx])
            out[y][x] = max(vals) if vals else 0.0
    return out, ow, oh


if _HAS_TORCH:
    class TacticalFeatureExtractor(nn.Module):
        """Lightweight CNN for extracting tactical features from the spatial tensor.

        Architecture: 3 conv blocks with batch norm, followed by
        task-specific heads for threat, attack priority, and defense scoring.
        """

        def __init__(self, in_channels: int = CHANNELS):
            super().__init__()
            self.encoder = nn.Sequential(
                nn.Conv2d(in_channels, 32, 3, padding=1),
                nn.BatchNorm2d(32),
                nn.ReLU(inplace=True),
                nn.Conv2d(32, 64, 3, padding=1),
                nn.BatchNorm2d(64),
                nn.ReLU(inplace=True),
                nn.MaxPool2d(2),
                nn.Conv2d(64, 64, 3, padding=1),
                nn.BatchNorm2d(64),
                nn.ReLU(inplace=True),
            )
            self.threat_head = nn.Sequential(
                nn.Conv2d(64, 32, 1),
                nn.ReLU(inplace=True),
                nn.Conv2d(32, 1, 1),
                nn.Sigmoid(),
            )
            self.priority_head = nn.Sequential(
                nn.Conv2d(64, 32, 1),
                nn.ReLU(inplace=True),
                nn.Conv2d(32, 1, 1),
            )
            self.defense_head = nn.Sequential(
                nn.Conv2d(64, 32, 1),
                nn.ReLU(inplace=True),
                nn.Conv2d(32, 1, 1),
                nn.Sigmoid(),
            )

        def forward(self, x: torch.Tensor) -> dict[str, torch.Tensor]:
            features = self.encoder(x)
            return {
                "threat": self.threat_head(features),
                "priority": self.priority_head(features),
                "defense": self.defense_head(features),
                "features": features,
            }


class TacticalCNN:
    """CNN-based tactical analysis of the spatial observation tensor.

    Extracts multi-scale features to identify:
    - Threat heatmaps (where danger is concentrated)
    - Attack priorities (high-value targets considering approach routes)
    - Defensive positions (terrain advantage + coverage)
    - Movement corridors (passable routes avoiding threats)
    - Flanking routes (paths around enemy concentrations)
    """

    def __init__(self, use_torch: bool = True):
        self._width = 0
        self._height = 0
        self._channels: list[list[list[float]]] = []
        self._use_torch = use_torch and _HAS_TORCH
        self._model: Optional[object] = None
        self._history: list[list[list[list[float]]]] = []
        self._max_history = 3

        if self._use_torch:
            self._model = TacticalFeatureExtractor()
            self._model.eval()

    @property
    def has_data(self) -> bool:
        return len(self._channels) > 0

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    @property
    def using_torch(self) -> bool:
        return self._use_torch

    def update(self, obs) -> None:
        """Decode the spatial tensor from an observation."""
        if hasattr(obs, "spatial_map"):
            spatial = obs.spatial_map
            w = obs.map_info.width
            h = obs.map_info.height
            n_ch = obs.spatial_channels
        elif isinstance(obs, dict):
            spatial = obs.get("spatial_map", "")
            mi = obs.get("map_info", {})
            w = mi.get("width", 0)
            h = mi.get("height", 0)
            n_ch = obs.get("spatial_channels", 0)
        else:
            return

        if not spatial or w == 0 or h == 0 or n_ch < CHANNELS:
            return

        raw = base64.b64decode(spatial)
        expected = w * h * n_ch * 4
        if len(raw) < expected:
            return

        self._width = w
        self._height = h
        n_floats = w * h * n_ch
        flat = struct.unpack(f"<{n_floats}f", raw[:n_floats * 4])

        self._channels = []
        for ch in range(CHANNELS):
            grid = [[0.0] * w for _ in range(h)]
            for y in range(h):
                for x in range(w):
                    grid[y][x] = flat[(y * w + x) * n_ch + ch]
            self._channels.append(grid)

        if len(self._history) >= self._max_history:
            self._history.pop(0)
        self._history.append([row[:] for ch in self._channels for row in ch])

    def _ch(self, channel: int) -> list[list[float]]:
        """Get a channel grid."""
        if channel < len(self._channels):
            return self._channels[channel]
        return []

    def get_threat_heatmap(self) -> TacticalHeatmap:
        """Compute a threat heatmap by convolving enemy positions with
        terrain and fog-of-war data.

        Threat = enemy_density * passability_toward_us * (1 - fog_advantage)
        Then Gaussian-blurred for spatial smoothing.
        """
        if not self.has_data:
            return TacticalHeatmap(0, 0, [])

        w, h = self._width, self._height
        enemy_units = self._ch(CH_ENEMY_UNITS)
        enemy_bldgs = self._ch(CH_ENEMY_BUILDINGS)
        passable = self._ch(CH_PASSABLE)
        fog = self._ch(CH_FOG)

        raw_threat = [[0.0] * w for _ in range(h)]
        for y in range(h):
            for x in range(w):
                eu = enemy_units[y][x]
                eb = enemy_bldgs[y][x]
                if eu <= 0 and eb <= 0:
                    continue
                threat = eu * 2.0 + eb * 4.0
                threat *= max(0.3, passable[y][x])
                fog_val = fog[y][x]
                if fog_val < 0.5:
                    threat *= 1.5
                raw_threat[y][x] = threat

        smoothed = _gaussian_blur(raw_threat, w, h)
        smoothed = _gaussian_blur(smoothed, w, h)

        min_v = float("inf")
        max_v = float("-inf")
        hot_spots = []

        for y in range(h):
            for x in range(w):
                v = smoothed[y][x]
                if v < min_v:
                    min_v = v
                if v > max_v:
                    max_v = v
                if v > 1.0:
                    factors = []
                    if enemy_units[y][x] > 0:
                        factors.append(f"units:{enemy_units[y][x]:.0f}")
                    if enemy_bldgs[y][x] > 0:
                        factors.append("building")
                    if fog[y][x] < 0.5:
                        factors.append("fog_masked")
                    hot_spots.append(ThreatCell(x, y, v, factors))

        hot_spots.sort(key=lambda c: -c.score)

        return TacticalHeatmap(
            width=w, height=h, data=smoothed,
            min_val=min_v if min_v != float("inf") else 0.0,
            max_val=max_v if max_v != float("-inf") else 0.0,
            hot_spots=hot_spots[:20],
        )

    def get_attack_priorities(self) -> list[AttackPriority]:
        """Score potential attack targets by value, approachability, and risk.

        Priority = target_value * approach_quality / (1 + risk)
        """
        if not self.has_data:
            return []

        w, h = self._width, self._height
        enemy_units = self._ch(CH_ENEMY_UNITS)
        enemy_bldgs = self._ch(CH_ENEMY_BUILDINGS)
        passable = self._ch(CH_PASSABLE)
        own_units = self._ch(CH_OWN_UNITS)
        own_bldgs = self._ch(CH_OWN_BUILDINGS)

        own_cx, own_cy = self._own_centroid()

        targets = []
        for y in range(h):
            for x in range(w):
                eu = enemy_units[y][x]
                eb = enemy_bldgs[y][x]
                if eu <= 0 and eb <= 0:
                    continue

                value = eu * 1.0 + eb * 3.0
                ttype = "units" if eu > eb else "building"

                approach = self._approach_score(x, y, own_cx, own_cy, passable)
                risk = self._local_enemy_strength(x, y, enemy_units, enemy_bldgs, radius=5)
                own_nearby = self._local_strength(x, y, own_units, radius=8)

                priority = value * approach * max(0.1, own_nearby) / (1.0 + risk * 0.5)

                targets.append(AttackPriority(
                    x=x, y=y, score=round(priority, 2),
                    target_type=ttype,
                    approach_quality=round(approach, 2),
                    risk=round(risk, 2),
                ))

        targets.sort(key=lambda t: -t.score)
        return self._cluster_targets(targets, radius=4)[:15]

    def get_defensive_scores(self) -> list[DefensivePosition]:
        """Score map positions for defensive value.

        Defense = terrain_advantage * coverage * (1 - vulnerability)
        Terrain advantage: height, chokepoints, impassable neighbors.
        Coverage: line of sight to likely approach routes.
        Vulnerability: exposure to multiple directions.
        """
        if not self.has_data:
            return []

        w, h = self._width, self._height
        passable = self._ch(CH_PASSABLE)
        height_map = self._ch(CH_HEIGHT)
        own_bldgs = self._ch(CH_OWN_BUILDINGS)

        terrain_edges = _sobel_magnitude(passable, w, h)
        height_advantage = _gaussian_blur(height_map, w, h)

        positions = []
        step = max(1, min(w, h) // 30)

        for y in range(0, h, step):
            for x in range(0, w, step):
                if passable[y][x] < 0.5:
                    continue

                terrain_adv = height_advantage[y][x]
                edge_proximity = terrain_edges[y][x]
                terrain_score = terrain_adv * 0.6 + edge_proximity * 0.4

                n_passable_dirs = 0
                for dx, dy in [(-3, 0), (3, 0), (0, -3), (0, 3),
                                (-2, -2), (2, -2), (-2, 2), (2, 2)]:
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < w and 0 <= ny < h and passable[ny][nx] > 0.5:
                        n_passable_dirs += 1

                coverage = min(1.0, n_passable_dirs / 5.0)
                vulnerability = n_passable_dirs / 8.0

                near_base = 0.0
                for dy in range(-3, 4):
                    for dx in range(-3, 4):
                        ny, nx = y + dy, x + dx
                        if 0 <= ny < h and 0 <= nx < w and own_bldgs[ny][nx] > 0:
                            near_base = 1.0
                            break
                    if near_base > 0:
                        break

                score = (terrain_score * 0.3 + coverage * 0.3 +
                         (1 - vulnerability) * 0.2 + near_base * 0.2)

                positions.append(DefensivePosition(
                    x=x, y=y, score=round(score, 3),
                    terrain_advantage=round(terrain_score, 3),
                    coverage=round(coverage, 3),
                    vulnerability=round(vulnerability, 3),
                ))

        positions.sort(key=lambda p: -p.score)
        return positions[:20]

    def get_movement_corridors(self, target_x: int, target_y: int,
                                n_corridors: int = 3) -> list[list[tuple[int, int]]]:
        """Find safe movement corridors avoiding high-threat areas.

        Combines passability with inverse threat to find low-risk paths.
        """
        if not self.has_data:
            return []

        w, h = self._width, self._height
        passable = self._ch(CH_PASSABLE)
        threat_map = self.get_threat_heatmap()

        own_cx, own_cy = self._own_centroid()
        if own_cx == 0 and own_cy == 0:
            return []

        corridors = []
        spread_angles = [0.0]
        for i in range(1, (n_corridors + 1) // 2 + 1):
            spread_angles.append(i * 0.3)
            spread_angles.append(-i * 0.3)

        dx = target_x - own_cx
        dy = target_y - own_cy
        base_angle = math.atan2(dy, dx)
        dist = math.sqrt(dx * dx + dy * dy)

        for angle_offset in spread_angles[:n_corridors]:
            angle = base_angle + angle_offset
            path = []
            steps = int(dist)
            if steps == 0:
                continue

            blocked = False
            for i in range(0, steps + 1, max(1, steps // 40)):
                t = i / max(steps, 1)
                curve = math.sin(t * math.pi) * angle_offset * dist * 0.3
                perp_angle = angle + math.pi / 2

                px = int(own_cx + math.cos(angle) * dist * t +
                         math.cos(perp_angle) * curve)
                py = int(own_cy + math.sin(angle) * dist * t +
                         math.sin(perp_angle) * curve)

                px = max(0, min(w - 1, px))
                py = max(0, min(h - 1, py))

                if passable[py][px] < 0.5:
                    blocked = True
                    break

                threat_val = threat_map.data[py][px] if threat_map.data else 0.0
                if threat_val > 5.0:
                    blocked = True
                    break

                path.append((px, py))

            if not blocked and len(path) >= 3:
                corridors.append(path)

        return corridors

    def get_flank_routes(self, target_x: int, target_y: int) -> list[list[tuple[int, int]]]:
        """Find flanking routes that approach a target from the sides or rear."""
        if not self.has_data:
            return []

        own_cx, own_cy = self._own_centroid()
        dx = target_x - own_cx
        dy = target_y - own_cy
        dist = math.sqrt(dx * dx + dy * dy)
        if dist < 5:
            return []

        flank_targets = []
        for angle_offset in [math.pi / 3, -math.pi / 3, math.pi * 2 / 3, -math.pi * 2 / 3]:
            base_angle = math.atan2(dy, dx)
            flank_angle = base_angle + angle_offset
            flank_dist = dist * 0.7
            fx = int(target_x + math.cos(flank_angle + math.pi) * 8)
            fy = int(target_y + math.sin(flank_angle + math.pi) * 8)
            fx = max(0, min(self._width - 1, fx))
            fy = max(0, min(self._height - 1, fy))
            flank_targets.append((fx, fy))

        routes = []
        for fx, fy in flank_targets:
            corridor = self.get_movement_corridors(fx, fy, n_corridors=1)
            if corridor:
                route = corridor[0]
                route.append((target_x, target_y))
                routes.append(route)

        return routes[:3]

    def find_rally_point(self) -> Optional[tuple[int, int]]:
        """Find an optimal rally point — behind own lines, good terrain, accessible."""
        if not self.has_data:
            return None

        own_cx, own_cy = self._own_centroid()
        threat = self.get_threat_assessment_quick()
        if threat is None:
            return (own_cx, own_cy)

        enemy_cx, enemy_cy = threat
        dx = own_cx - enemy_cx
        dy = own_cy - enemy_cy
        dist = math.sqrt(dx * dx + dy * dy)
        if dist < 1:
            return (own_cx, own_cy)

        ndx = dx / dist
        ndy = dy / dist
        rally_x = int(own_cx + ndx * 5)
        rally_y = int(own_cy + ndy * 5)
        rally_x = max(0, min(self._width - 1, rally_x))
        rally_y = max(0, min(self._height - 1, rally_y))

        passable = self._ch(CH_PASSABLE)
        if passable and passable[rally_y][rally_x] > 0.5:
            return (rally_x, rally_y)

        for r in range(1, 6):
            for dy2 in range(-r, r + 1):
                for dx2 in range(-r, r + 1):
                    ny = rally_y + dy2
                    nx = rally_x + dx2
                    if (0 <= nx < self._width and 0 <= ny < self._height
                            and passable[ny][nx] > 0.5):
                        return (nx, ny)

        return (own_cx, own_cy)

    def get_full_analysis(self) -> TacticalAnalysis:
        """Run all analyses and return a complete tactical picture."""
        if not self.has_data:
            return TacticalAnalysis()

        threat = self.get_threat_heatmap()
        priorities = self.get_attack_priorities()
        defense = self.get_defensive_scores()
        rally = self.find_rally_point()

        corridors = []
        flanks = []
        if priorities:
            top_target = priorities[0]
            corridors = self.get_movement_corridors(top_target.x, top_target.y)
            flanks = self.get_flank_routes(top_target.x, top_target.y)

        return TacticalAnalysis(
            threat_map=threat,
            attack_priorities=priorities,
            defensive_positions=defense,
            movement_corridors=corridors,
            recommended_rally_point=rally,
            flank_routes=flanks,
        )

    def format_analysis(self) -> str:
        """Format the tactical analysis as a text report."""
        analysis = self.get_full_analysis()
        lines = [
            "CNN TACTICAL ANALYSIS",
            "=" * 50,
        ]

        if analysis.threat_map and analysis.threat_map.hot_spots:
            lines.append(f"\nTHREAT HOTSPOTS ({len(analysis.threat_map.hot_spots)}):")
            for spot in analysis.threat_map.hot_spots[:5]:
                factors = ", ".join(spot.contributing_factors) if spot.contributing_factors else "ambient"
                lines.append(f"  ({spot.x},{spot.y}) score={spot.score:.1f} [{factors}]")

        if analysis.attack_priorities:
            lines.append(f"\nATTACK PRIORITIES ({len(analysis.attack_priorities)}):")
            for p in analysis.attack_priorities[:5]:
                lines.append(
                    f"  ({p.x},{p.y}) priority={p.score:.1f} "
                    f"type={p.target_type} approach={p.approach_quality:.1f} risk={p.risk:.1f}"
                )

        if analysis.defensive_positions:
            lines.append(f"\nDEFENSIVE POSITIONS ({len(analysis.defensive_positions)}):")
            for d in analysis.defensive_positions[:5]:
                lines.append(
                    f"  ({d.x},{d.y}) score={d.score:.3f} "
                    f"terrain={d.terrain_advantage:.2f} cover={d.coverage:.2f} vuln={d.vulnerability:.2f}"
                )

        if analysis.recommended_rally_point:
            rx, ry = analysis.recommended_rally_point
            lines.append(f"\nRALLY POINT: ({rx},{ry})")

        if analysis.movement_corridors:
            lines.append(f"\nMOVEMENT CORRIDORS: {len(analysis.movement_corridors)}")
            for i, c in enumerate(analysis.movement_corridors):
                if c:
                    lines.append(f"  Route {i+1}: {c[0]} → {c[-1]} ({len(c)} waypoints)")

        if analysis.flank_routes:
            lines.append(f"\nFLANK ROUTES: {len(analysis.flank_routes)}")
            for i, r in enumerate(analysis.flank_routes):
                if r:
                    lines.append(f"  Flank {i+1}: {r[0]} → {r[-1]} ({len(r)} waypoints)")

        return "\n".join(lines)

    def render_threat_ascii(self, max_cols: int = 60) -> str:
        """Render the threat heatmap as ASCII art."""
        if not self.has_data:
            return ""

        threat = self.get_threat_heatmap()
        if not threat.data:
            return ""

        w, h = threat.width, threat.height
        scale = max(1, math.ceil(w / max_cols))
        gw = math.ceil(w / scale)
        gh = math.ceil(h / scale)

        chars = " .:-=+*#%@"
        max_v = max(threat.max_val, 0.01)

        rows = []
        for gy in range(gh):
            row = []
            for gx in range(gw):
                sx = min(gx * scale + scale // 2, w - 1)
                sy = min(gy * scale + scale // 2, h - 1)
                v = threat.data[sy][sx]
                idx = min(len(chars) - 1, int(v / max_v * (len(chars) - 1)))
                row.append(chars[idx])
            rows.append("".join(row))

        return "\n".join(rows)

    # --- Internal helpers ---

    def _own_centroid(self) -> tuple[int, int]:
        if not self.has_data:
            return (0, 0)
        own_bldgs = self._ch(CH_OWN_BUILDINGS)
        own_units = self._ch(CH_OWN_UNITS)
        wx, wy, total = 0.0, 0.0, 0.0
        for y in range(self._height):
            for x in range(self._width):
                w = own_bldgs[y][x] * 5 + own_units[y][x]
                if w > 0:
                    wx += x * w
                    wy += y * w
                    total += w
        if total == 0:
            return (0, 0)
        return (int(wx / total), int(wy / total))

    def get_threat_assessment_quick(self) -> Optional[tuple[int, int]]:
        """Quick enemy centroid without full heatmap computation."""
        if not self.has_data:
            return None
        eu = self._ch(CH_ENEMY_UNITS)
        eb = self._ch(CH_ENEMY_BUILDINGS)
        wx, wy, total = 0.0, 0.0, 0.0
        for y in range(self._height):
            for x in range(self._width):
                w = eu[y][x] + eb[y][x] * 3
                if w > 0:
                    wx += x * w
                    wy += y * w
                    total += w
        if total == 0:
            return None
        return (int(wx / total), int(wy / total))

    def _approach_score(self, tx: int, ty: int, ox: int, oy: int,
                        passable: list[list[float]]) -> float:
        """Score how approachable a target is from origin."""
        dx = tx - ox
        dy = ty - oy
        dist = math.sqrt(dx * dx + dy * dy)
        if dist < 1:
            return 1.0

        steps = min(20, int(dist))
        passable_count = 0
        for i in range(steps):
            t = i / max(steps - 1, 1)
            px = int(ox + dx * t)
            py = int(oy + dy * t)
            px = max(0, min(self._width - 1, px))
            py = max(0, min(self._height - 1, py))
            if passable[py][px] > 0.5:
                passable_count += 1

        approach = passable_count / max(steps, 1)
        distance_penalty = 1.0 / (1.0 + dist * 0.02)
        return approach * distance_penalty

    def _local_enemy_strength(self, cx: int, cy: int,
                               enemy_units: list[list[float]],
                               enemy_bldgs: list[list[float]],
                               radius: int = 5) -> float:
        """Sum enemy strength in a radius around a point."""
        total = 0.0
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                ny, nx = cy + dy, cx + dx
                if 0 <= ny < self._height and 0 <= nx < self._width:
                    total += enemy_units[ny][nx] + enemy_bldgs[ny][nx] * 3
        return total

    def _local_strength(self, cx: int, cy: int,
                         units: list[list[float]], radius: int = 5) -> float:
        """Sum own unit strength in a radius."""
        total = 0.0
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                ny, nx = cy + dy, cx + dx
                if 0 <= ny < self._height and 0 <= nx < self._width:
                    total += units[ny][nx]
        return total

    def _cluster_targets(self, targets: list[AttackPriority],
                          radius: int = 4) -> list[AttackPriority]:
        """Merge nearby targets into clusters, keeping the highest-scoring."""
        if not targets:
            return []
        result = []
        used = set()
        for i, t in enumerate(targets):
            if i in used:
                continue
            result.append(t)
            for j in range(i + 1, len(targets)):
                if j in used:
                    continue
                dx = targets[j].x - t.x
                dy = targets[j].y - t.y
                if dx * dx + dy * dy <= radius * radius:
                    used.add(j)
        return result
