"""Spatial intelligence from the 9-channel observation tensor.

Decodes the base64-encoded float32 spatial map into NumPy arrays and
provides tactical queries: terrain analysis, threat heatmaps,
resource locations, fog coverage, and movement corridors.

Channel layout (from OpenRA ObservationSerializer.cs):
  0 = terrain type index
  1 = height
  2 = resource density (ore/gems)
  3 = passability (1.0=passable, 0.0=impassable)
  4 = fog of war (0=hidden, 0.5=explored, 1.0=visible)
  5 = own buildings (1.0 where present)
  6 = own unit density (count per cell)
  7 = enemy buildings (1.0 where present)
  8 = enemy unit density (count per cell)

Usage:
    from milsim.spatial import SpatialIntel
    si = SpatialIntel()
    si.update(obs)  # OpenRAObservation
    threat_map = si.get_threat_heatmap()
    corridors = si.find_approach_corridors(target_x, target_y)
"""

import base64
import struct
from dataclasses import dataclass, field
from typing import Optional

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
class TerrainAnalysis:
    """Summary of terrain features in a region."""
    passable_ratio: float = 0.0
    avg_height: float = 0.0
    resource_cells: int = 0
    total_resources: float = 0.0
    chokepoints: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class ThreatAssessment:
    """Spatial threat analysis."""
    enemy_centroid: tuple[int, int] = (0, 0)
    enemy_spread: float = 0.0
    threat_direction: tuple[int, int] = (0, 0)
    high_threat_cells: list[tuple[int, int]] = field(default_factory=list)
    total_visible_enemies: int = 0


@dataclass
class ExplorationStatus:
    """Map exploration coverage."""
    explored_pct: float = 0.0
    visible_pct: float = 0.0
    hidden_pct: float = 0.0
    unexplored_regions: list[tuple[int, int, int, int]] = field(default_factory=list)


class SpatialIntel:
    """Tactical intelligence from the spatial observation tensor.

    Maintains a persistent map picture that updates each observation.
    Provides spatial queries for tactical decision-making.
    """

    def __init__(self):
        self._width = 0
        self._height = 0
        self._data: Optional[list[float]] = None
        self._history: list[list[float]] = []
        self._max_history = 5

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    @property
    def has_data(self) -> bool:
        return self._data is not None

    def update(self, obs) -> None:
        """Update from an OpenRAObservation or dict with spatial_map field."""
        if hasattr(obs, "spatial_map"):
            spatial = obs.spatial_map
            w = obs.map_info.width
            h = obs.map_info.height
            channels = obs.spatial_channels
        else:
            spatial = obs.get("spatial_map", "")
            mi = obs.get("map_info", {})
            w = mi.get("width", 0)
            h = mi.get("height", 0)
            channels = obs.get("spatial_channels", 0)

        if not spatial or w == 0 or h == 0 or channels < CHANNELS:
            return

        raw = base64.b64decode(spatial)
        expected = w * h * channels * 4
        if len(raw) < expected:
            return

        self._width = w
        self._height = h
        n_floats = w * h * channels
        self._data = list(struct.unpack(f"<{n_floats}f", raw[:n_floats * 4]))

        if len(self._history) >= self._max_history:
            self._history.pop(0)
        self._history.append(self._data[:])

    def _val(self, x: int, y: int, channel: int) -> float:
        if not self._data or x < 0 or y < 0 or x >= self._width or y >= self._height:
            return 0.0
        return self._data[(y * self._width + x) * CHANNELS + channel]

    def get_channel_grid(self, channel: int) -> list[list[float]]:
        """Extract a full 2D grid for a channel."""
        if not self._data:
            return []
        grid = []
        for y in range(self._height):
            row = []
            for x in range(self._width):
                row.append(self._val(x, y, channel))
            grid.append(row)
        return grid

    def get_exploration(self) -> ExplorationStatus:
        """Compute map exploration coverage."""
        if not self._data:
            return ExplorationStatus()

        total = self._width * self._height
        visible = 0
        explored = 0
        hidden = 0

        for i in range(total):
            fog = self._data[i * CHANNELS + CH_FOG]
            if fog >= 0.9:
                visible += 1
            elif fog >= 0.4:
                explored += 1
            else:
                hidden += 1

        # Find unexplored region centroids (simple grid sampling)
        regions = []
        step = max(self._width, self._height) // 4
        if step < 2:
            step = 2
        for gy in range(0, self._height, step):
            for gx in range(0, self._width, step):
                hidden_count = 0
                sample_count = 0
                for dy in range(min(step, self._height - gy)):
                    for dx in range(min(step, self._width - gx)):
                        if self._val(gx + dx, gy + dy, CH_FOG) < 0.1:
                            hidden_count += 1
                        sample_count += 1
                if sample_count > 0 and hidden_count / sample_count > 0.6:
                    regions.append((gx, gy, min(gx + step, self._width),
                                    min(gy + step, self._height)))

        return ExplorationStatus(
            explored_pct=round(100 * (explored + visible) / max(total, 1), 1),
            visible_pct=round(100 * visible / max(total, 1), 1),
            hidden_pct=round(100 * hidden / max(total, 1), 1),
            unexplored_regions=regions,
        )

    def get_threat_assessment(self) -> ThreatAssessment:
        """Analyze enemy spatial distribution."""
        if not self._data:
            return ThreatAssessment()

        enemy_cells = []
        total_enemies = 0

        for y in range(self._height):
            for x in range(self._width):
                enemy_units = self._val(x, y, CH_ENEMY_UNITS)
                enemy_bldgs = self._val(x, y, CH_ENEMY_BUILDINGS)
                if enemy_units > 0 or enemy_bldgs > 0:
                    weight = enemy_units + enemy_bldgs * 3
                    enemy_cells.append((x, y, weight))
                    total_enemies += int(enemy_units)

        if not enemy_cells:
            return ThreatAssessment(total_visible_enemies=0)

        total_weight = sum(w for _, _, w in enemy_cells)
        cx = sum(x * w for x, _, w in enemy_cells) / total_weight
        cy = sum(y * w for _, y, w in enemy_cells) / total_weight

        # Spread = average distance from centroid
        spread = sum(
            ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 * w
            for x, y, w in enemy_cells
        ) / total_weight

        # Threat direction from own base centroid
        own_cx, own_cy = self._own_centroid()
        dx = int(cx - own_cx)
        dy = int(cy - own_cy)

        # High-threat cells (enemy density >= 2)
        high_threat = [(x, y) for x, y, w in enemy_cells if w >= 2]

        return ThreatAssessment(
            enemy_centroid=(int(cx), int(cy)),
            enemy_spread=round(spread, 1),
            threat_direction=(dx, dy),
            high_threat_cells=high_threat,
            total_visible_enemies=total_enemies,
        )

    def get_terrain_analysis(self, x1: int, y1: int, x2: int, y2: int) -> TerrainAnalysis:
        """Analyze terrain in a rectangular region."""
        if not self._data:
            return TerrainAnalysis()

        x1, x2 = max(0, x1), min(self._width, x2)
        y1, y2 = max(0, y1), min(self._height, y2)

        total = 0
        passable = 0
        height_sum = 0.0
        resource_cells = 0
        resource_total = 0.0

        for y in range(y1, y2):
            for x in range(x1, x2):
                total += 1
                if self._val(x, y, CH_PASSABLE) > 0.5:
                    passable += 1
                height_sum += self._val(x, y, CH_HEIGHT)
                res = self._val(x, y, CH_RESOURCE)
                if res > 0:
                    resource_cells += 1
                    resource_total += res

        if total == 0:
            return TerrainAnalysis()

        # Find chokepoints: passable cells with mostly impassable neighbors
        chokepoints = []
        for y in range(y1, y2):
            for x in range(x1, x2):
                if self._val(x, y, CH_PASSABLE) < 0.5:
                    continue
                impassable_neighbors = 0
                for nx, ny in [(x-1, y), (x+1, y), (x, y-1), (x, y+1)]:
                    if (nx < 0 or nx >= self._width or ny < 0 or ny >= self._height
                            or self._val(nx, ny, CH_PASSABLE) < 0.5):
                        impassable_neighbors += 1
                if impassable_neighbors >= 3:
                    chokepoints.append((x, y))

        return TerrainAnalysis(
            passable_ratio=round(passable / total, 3),
            avg_height=round(height_sum / total, 1),
            resource_cells=resource_cells,
            total_resources=round(resource_total, 1),
            chokepoints=chokepoints[:20],
        )

    def find_resources(self, min_density: float = 0.5) -> list[tuple[int, int, float]]:
        """Find resource patches above a density threshold."""
        if not self._data:
            return []

        patches = []
        for y in range(self._height):
            for x in range(self._width):
                d = self._val(x, y, CH_RESOURCE)
                if d >= min_density:
                    patches.append((x, y, d))

        return sorted(patches, key=lambda p: -p[2])[:50]

    def find_approach_corridors(
        self, target_x: int, target_y: int, corridor_width: int = 3
    ) -> list[list[tuple[int, int]]]:
        """Find passable corridors from own base toward a target.

        Uses a simple directional sweep from base centroid to target,
        checking passability along the path. Returns up to 3 corridors.
        """
        if not self._data:
            return []

        own_x, own_y = self._own_centroid()
        if own_x == 0 and own_y == 0:
            return []

        corridors = []
        offsets = [0, -corridor_width, corridor_width]

        for offset in offsets:
            path = []
            steps = max(abs(target_x - own_x), abs(target_y - own_y))
            if steps == 0:
                continue

            blocked = False
            for i in range(0, steps + 1, max(1, steps // 30)):
                t = i / max(steps, 1)
                px = int(own_x + (target_x - own_x) * t)
                py = int(own_y + (target_y - own_y) * t)

                # Apply perpendicular offset
                dx = target_x - own_x
                dy = target_y - own_y
                length = max(1, (dx * dx + dy * dy) ** 0.5)
                perp_x = int(-dy / length * offset)
                perp_y = int(dx / length * offset)
                px += perp_x
                py += perp_y

                px = max(0, min(self._width - 1, px))
                py = max(0, min(self._height - 1, py))

                if self._val(px, py, CH_PASSABLE) < 0.5:
                    blocked = True
                    break
                path.append((px, py))

            if not blocked and path:
                corridors.append(path)

        return corridors

    def get_own_base_bounds(self) -> tuple[int, int, int, int]:
        """Get bounding box of own buildings."""
        if not self._data:
            return (0, 0, 0, 0)

        min_x, min_y = self._width, self._height
        max_x, max_y = 0, 0
        found = False

        for y in range(self._height):
            for x in range(self._width):
                if self._val(x, y, CH_OWN_BUILDINGS) > 0:
                    min_x = min(min_x, x)
                    min_y = min(min_y, y)
                    max_x = max(max_x, x)
                    max_y = max(max_y, y)
                    found = True

        if not found:
            return (0, 0, 0, 0)
        return (min_x, min_y, max_x + 1, max_y + 1)

    def get_enemy_positions(self) -> list[tuple[int, int, float]]:
        """Get all visible enemy positions with density."""
        if not self._data:
            return []

        positions = []
        for y in range(self._height):
            for x in range(self._width):
                units = self._val(x, y, CH_ENEMY_UNITS)
                bldgs = self._val(x, y, CH_ENEMY_BUILDINGS)
                if units > 0 or bldgs > 0:
                    positions.append((x, y, units + bldgs * 3))
        return positions

    def _own_centroid(self) -> tuple[int, int]:
        """Compute centroid of own buildings and units."""
        if not self._data:
            return (0, 0)

        wx, wy, total_w = 0.0, 0.0, 0.0
        for y in range(self._height):
            for x in range(self._width):
                bldg = self._val(x, y, CH_OWN_BUILDINGS)
                units = self._val(x, y, CH_OWN_UNITS)
                w = bldg * 5 + units
                if w > 0:
                    wx += x * w
                    wy += y * w
                    total_w += w

        if total_w == 0:
            return (0, 0)
        return (int(wx / total_w), int(wy / total_w))

    def format_spatial_sitrep(self) -> str:
        """Generate a spatial intelligence summary."""
        if not self._data:
            return "SPATIAL INTEL: No data available."

        exploration = self.get_exploration()
        threat = self.get_threat_assessment()
        base = self.get_own_base_bounds()
        resources = self.find_resources()

        lines = [
            "SPATIAL INTELLIGENCE SUMMARY",
            "=" * 40,
            "",
            f"Map: {self._width}x{self._height} cells",
            f"Explored: {exploration.explored_pct}% "
            f"(visible: {exploration.visible_pct}%, hidden: {exploration.hidden_pct}%)",
            f"Own base: ({base[0]},{base[1]}) to ({base[2]},{base[3]})",
        ]

        if threat.total_visible_enemies > 0:
            lines.extend([
                "",
                f"THREAT: {threat.total_visible_enemies} visible enemies",
                f"  Centroid: ({threat.enemy_centroid[0]},{threat.enemy_centroid[1]})",
                f"  Spread: {threat.enemy_spread} cells",
                f"  Direction: ({threat.threat_direction[0]:+d},{threat.threat_direction[1]:+d})",
                f"  High-threat cells: {len(threat.high_threat_cells)}",
            ])
        else:
            lines.append("\nTHREAT: No visible enemy forces.")

        if resources:
            lines.extend([
                "",
                f"RESOURCES: {len(resources)} patches found",
                f"  Nearest: ({resources[0][0]},{resources[0][1]}) density={resources[0][2]:.1f}",
            ])

        if exploration.unexplored_regions:
            lines.extend([
                "",
                f"UNEXPLORED: {len(exploration.unexplored_regions)} regions",
            ])
            for r in exploration.unexplored_regions[:4]:
                lines.append(f"  ({r[0]},{r[1]}) to ({r[2]},{r[3]})")

        return "\n".join(lines)

    def render_minimap(self, max_cols: int = 60) -> str:
        """Render an ASCII minimap from the spatial tensor."""
        if not self._data:
            return ""

        from math import ceil
        scale = max(1, ceil(self._width / max_cols))
        gw = ceil(self._width / scale)
        gh = ceil(self._height / scale)

        rows = []
        for gy in range(gh):
            row = []
            for gx in range(gw):
                sx = min(gx * scale + scale // 2, self._width - 1)
                sy = min(gy * scale + scale // 2, self._height - 1)

                fog = self._val(sx, sy, CH_FOG)
                if fog < 0.1:
                    row.append("#")
                    continue

                if self._val(sx, sy, CH_ENEMY_UNITS) > 0:
                    row.append("!")
                elif self._val(sx, sy, CH_ENEMY_BUILDINGS) > 0:
                    row.append("X")
                elif self._val(sx, sy, CH_OWN_UNITS) > 0:
                    row.append("@")
                elif self._val(sx, sy, CH_OWN_BUILDINGS) > 0:
                    row.append("B")
                elif self._val(sx, sy, CH_RESOURCE) > 0:
                    row.append("$")
                elif self._val(sx, sy, CH_PASSABLE) < 0.5:
                    row.append("~")
                elif fog >= 0.9:
                    row.append(".")
                else:
                    row.append(",")
            rows.append("".join(row))

        return "\n".join(rows)
