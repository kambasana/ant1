"""LLM commander interface — three ways to plug an LLM into milsim C2.

Approach 1: MCP Server — tools for get_briefing, issue_order, advance_turn
Approach 2: Structured Text — format briefings as text, parse text orders
Approach 3: Function-Calling — OpenAI-compatible tool calling loop

All three share the same CommanderBridge that wraps the existing Commander
and integrates spatial, ISR, game knowledge, memory, and assessment.

Usage (structured text — simplest):
    bridge = CommanderBridge(commander, isr, scenario)
    await bridge.start()
    while not bridge.game_over:
        briefing_text = bridge.format_briefing()
        # send briefing_text to LLM, get response
        orders = bridge.parse_orders(llm_response)
        result_text = await bridge.execute(orders)

Usage (function-calling):
    fc = FunctionCallingCommander(bridge, llm_config)
    aar = await fc.play_game()

Usage (MCP server):
    python -m milsim.llm_commander --mode mcp --port 8000
"""

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from milsim.commander import (
    Commander,
    CommanderBriefing,
    TacticalOrder,
    OrderType,
    Stance,
    UnitSummary,
)
from milsim.isr import ISRManager, IntelLevel
from milsim.scenario import ScenarioRunner
from milsim.spatial import SpatialIntel
from milsim.game_knowledge import GameKnowledge
from milsim.memory import TacticalMemory
from milsim.assessment import PerformanceAssessor
from milsim.cnn import TacticalCNN

try:
    from openra_env.bench_export import build_bench_export
    _HAS_BENCH = True
except ImportError:
    _HAS_BENCH = False


# ── Shared Bridge ─────────────────────────────────────────────────

class CommanderBridge:
    """Unified bridge between milsim systems and any LLM interface.

    Integrates Commander, ISR, Spatial, GameKnowledge, Memory, and
    Assessment into a single object that any of the three LLM
    interfaces can use.
    """

    def __init__(
        self,
        commander: Commander,
        isr: ISRManager,
        scenario: Optional[ScenarioRunner] = None,
        verbose: bool = True,
    ):
        self._commander = commander
        self._isr = isr
        self._scenario = scenario
        self._verbose = verbose

        self._spatial = SpatialIntel()
        self._cnn = TacticalCNN(use_torch=False)
        self._knowledge = GameKnowledge()
        self._memory = TacticalMemory()
        self._assessor = PerformanceAssessor(vector_enabled=True)

        self._turn = 0
        self._started = False
        self._decision_log: list[str] = []

    @property
    def game_over(self) -> bool:
        return self._commander.is_game_over

    @property
    def turn(self) -> int:
        return self._turn

    async def start(self) -> CommanderBriefing:
        briefing = await self._commander.start_game()
        self._started = True
        self._memory.reset_events()
        self._assessor.reset()
        self._memory.record_milestone("game_start", 0)
        self._update_intel(briefing)
        return briefing

    def _update_intel(self, briefing: CommanderBriefing) -> None:
        try:
            sit = self._commander._wego.get_situation()
            self._spatial.update(sit.observation)
            self._cnn.update(sit.observation)
            self._isr.process_observation(sit.observation, self._turn)
            self._assessor.evaluate({
                "economy": briefing.economy,
                "military": briefing.military_stats,
                "done": self._commander.is_game_over,
                "result": self._commander.game_result or "",
                "tick": briefing.game_tick,
            })
        except Exception:
            pass

    async def execute(self, orders: list[TacticalOrder]) -> str:
        self._turn += 1

        if self._scenario:
            events, _ = self._scenario.advance_turn()
            for ev in events:
                self._memory.record_event(
                    "msel_event", self._turn, f"[{ev.category}] {ev.event}"
                )

        if orders:
            result, briefing = await self._commander.issue_orders(orders)
        else:
            result, briefing = await self._commander.advance_time()

        self._update_intel(briefing)
        self._log_orders(orders)
        return self._format_result(result, briefing)

    async def advance(self) -> str:
        return await self.execute([])

    def get_briefing(self) -> CommanderBriefing:
        return self._commander.get_briefing()

    def _log_orders(self, orders: list[TacticalOrder]) -> None:
        for o in orders:
            desc = f"T{self._turn}: {o.order_type.value}"
            if o.unit_ids:
                desc += f" units={o.unit_ids}"
            if o.item_type:
                desc += f" item={o.item_type}"
            if o.target_x or o.target_y:
                desc += f" target=({o.target_x},{o.target_y})"
            self._decision_log.append(desc)
            self._memory.record_event("order", self._turn, desc)

    def _format_result(self, result, briefing: CommanderBriefing) -> str:
        bda = result.bda
        lines = [f"TURN {self._turn} RESULT"]

        if bda.units_killed_this_turn:
            lines.append(f"  Killed: {bda.units_killed_this_turn} enemy units")
            self._memory.record_event(
                "engagement", self._turn,
                f"+{bda.units_killed_this_turn} kills"
            )
        if bda.units_lost_this_turn:
            lines.append(f"  Lost: {bda.units_lost_this_turn} own units")
            self._memory.record_event(
                "casualty", self._turn,
                f"-{bda.units_lost_this_turn} losses"
            )
        if bda.new_enemy_contacts:
            lines.append(f"  New contacts: {bda.new_enemy_contacts}")
            self._memory.record_milestone(
                "first_contact", self._turn,
                f"{bda.new_enemy_contacts} contacts"
            )
        if bda.cash_delta:
            lines.append(f"  Cash delta: {bda.cash_delta:+d}")

        lines.append(f"  Units: {len(briefing.friendly_units)}")
        lines.append(f"  Cash: ${briefing.economy['cash']}")
        return "\n".join(lines)

    def get_aar(self) -> str:
        lines = [
            "=== LLM COMMANDER — AFTER ACTION REVIEW ===",
            f"Turns: {self._turn}",
            f"Result: {self._commander.game_result or 'ongoing'}",
            "",
            "DECISION LOG:",
        ]
        lines.extend(self._decision_log)
        lines.extend(["", self._commander.get_aar()])
        lines.extend(["", self._isr.format_intel_report()])

        if self._spatial.has_data:
            lines.extend(["", self._spatial.format_spatial_sitrep()])

        perf = self._assessor.format_summary()
        if perf and "No performance" not in perf:
            lines.extend(["", perf])

        timeline = self._memory.get_timeline()
        if timeline and "No events" not in timeline:
            lines.extend(["", timeline])

        if self._scenario:
            lines.extend(["", self._scenario.get_exercise_aar()])

        return "\n".join(lines)


# ── Approach 2: Structured Text Protocol ──────────────────────────

ORDER_SCHEMA = """ORDER FORMAT (one per line):
  MOVE <unit_ids> TO <x>,<y>
  ATTACK <unit_ids> TO <x>,<y>
  DEFEND <unit_ids> AT <x>,<y>
  SCOUT <unit_ids> TO <x>,<y>
  HOLD <unit_ids>
  BUILD <item_type>
  TRAIN <count>x <item_type>
  DEPLOY <unit_ids>
  STANCE <unit_ids> <aggressive|defensive|hold_fire|free_fire>
  WAIT

Unit IDs: comma-separated integers (e.g., 42,43,44)
Coordinates: cell x,y integers

Example:
  BUILD powr
  TRAIN 3x e1
  ATTACK 42,43,44 TO 80,30
  SCOUT 45 TO 50,25
"""


class StructuredTextCommander:
    """Approach 2: Text-in, text-out LLM interface.

    Formats the full game state as structured text and parses
    natural-language-ish orders back into TacticalOrders.
    """

    def __init__(self, bridge: CommanderBridge):
        self._bridge = bridge

    def format_briefing(self, briefing: Optional[CommanderBriefing] = None) -> str:
        b = briefing or self._bridge.get_briefing()
        lines = [
            f"COMMANDER BRIEFING — Turn {b.turn_number} (tick {b.game_tick})",
            "=" * 60,
        ]

        # Economy
        eco = b.economy
        lines.append(
            f"ECONOMY: ${eco['cash']} cash | "
            f"Power {eco['power_surplus']:+d} "
            f"({eco['power_provided']}/{eco['power_drained']}) | "
            f"Harvesters: {eco['harvesters']}"
        )

        # Military stats
        mil = b.military_stats
        lines.append(
            f"MILITARY: {mil['units_killed']} kills / "
            f"{mil['units_lost']} lost | "
            f"Army value: ${mil['army_value']} | "
            f"K/D: {mil['kill_ratio']:.1f}"
        )

        # Production queue
        if b.active_production:
            queue_parts = []
            for p in b.active_production:
                pct = f"{p['progress']:.0%}" if p["progress"] < 1.0 else "READY"
                queue_parts.append(f"{p['item']}({pct})")
            lines.append(f"QUEUE: {', '.join(queue_parts)}")
        else:
            lines.append("QUEUE: idle")

        # BDA from previous turn
        if b.previous_bda:
            bda = b.previous_bda
            bda_parts = []
            if bda.get("units_killed"):
                bda_parts.append(f"+{bda['units_killed']} kills")
            if bda.get("units_lost"):
                bda_parts.append(f"-{bda['units_lost']} losses")
            if bda.get("new_contacts"):
                bda_parts.append(f"{bda['new_contacts']} new contacts")
            if bda_parts:
                lines.append(f"LAST TURN: {', '.join(bda_parts)}")

        # Friendly units
        lines.append("")
        lines.append("FRIENDLY FORCES:")
        if b.friendly_units:
            by_type: dict[str, list[UnitSummary]] = {}
            for u in b.friendly_units:
                by_type.setdefault(u.type, []).append(u)
            for utype, units in sorted(by_type.items()):
                idle = [u for u in units if u.is_idle]
                active = [u for u in units if not u.is_idle]
                ids_str = ",".join(str(u.actor_id) for u in units)
                status_parts = []
                if idle:
                    status_parts.append(f"{len(idle)} idle")
                if active:
                    status_parts.append(f"{len(active)} active")
                pos = units[0]
                lines.append(
                    f"  {len(units)}x {utype} [{ids_str}] "
                    f"at ({pos.cell_x},{pos.cell_y}) "
                    f"{' / '.join(status_parts)}"
                )
        else:
            lines.append("  (none)")

        # Friendly buildings
        lines.append("")
        lines.append("BUILDINGS:")
        if b.friendly_buildings:
            for bldg in b.friendly_buildings:
                prod_str = ""
                if bldg.get("is_producing"):
                    prod_str = f" producing {bldg.get('producing_item', '?')}"
                pwr = "" if bldg.get("is_powered", True) else " UNPOWERED"
                lines.append(
                    f"  {bldg['type']} ({bldg['actor_id']}){prod_str}{pwr}"
                )
        else:
            lines.append("  (none)")

        # Enemy contacts
        lines.append("")
        if b.known_enemies or b.known_enemy_buildings:
            lines.append("ENEMY CONTACTS:")
            for e in b.known_enemies:
                lines.append(
                    f"  {e.type} [{e.actor_id}] at ({e.cell_x},{e.cell_y}) "
                    f"HP:{e.hp_percent:.0%}"
                )
            for eb in b.known_enemy_buildings:
                lines.append(
                    f"  {eb['type']} [{eb['actor_id']}] at "
                    f"({eb.get('cell_x', '?')},{eb.get('cell_y', '?')}) "
                    f"HP:{eb.get('hp_percent', 1):.0%}"
                )
        else:
            lines.append("ENEMY: no contacts")

        # Spatial intel
        if self._bridge._spatial.has_data:
            sp = self._bridge._spatial
            exploration = sp.get_exploration()
            threat = sp.get_threat_assessment()
            lines.append("")
            lines.append("SPATIAL INTEL:")
            lines.append(
                f"  Map: {sp.width}x{sp.height} | "
                f"Explored: {exploration.explored_pct}%"
            )
            if threat.total_visible_enemies > 0:
                lines.append(
                    f"  Threat: {threat.total_visible_enemies} enemies "
                    f"centered at ({threat.enemy_centroid[0]},{threat.enemy_centroid[1]})"
                )
            if exploration.unexplored_regions:
                regions = exploration.unexplored_regions[:3]
                lines.append(
                    f"  Unexplored: {len(exploration.unexplored_regions)} regions "
                    f"(nearest: {regions[0]})"
                )

        # ISR summary
        intel = self._bridge._isr.get_summary()
        if intel.active_contacts > 0:
            lines.append("")
            lines.append(
                f"INTELLIGENCE: {intel.active_contacts} active / "
                f"{intel.total_contacts} total contacts"
            )

        # Performance
        perf = self._bridge._assessor.get_latest()
        if perf:
            lines.append("")
            lines.append(
                f"PERFORMANCE: score={perf.scalar:.1f} "
                f"cumulative={perf.cumulative_scalar:.1f}"
            )

        # Available production
        lines.append("")
        lines.append(f"CAN BUILD: {', '.join(b.available_production[:15])}")

        # Order schema
        lines.append("")
        lines.append(ORDER_SCHEMA)

        return "\n".join(lines)

    @staticmethod
    def parse_orders(text: str) -> list[TacticalOrder]:
        """Parse structured text orders into TacticalOrder objects."""
        orders = []
        for line in text.strip().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("//"):
                continue

            order = _parse_order_line(line.upper())
            if order:
                orders.append(order)

        return orders


def _parse_unit_ids(s: str) -> list[int]:
    return [int(x.strip()) for x in s.split(",") if x.strip().isdigit()]


def _parse_coords(s: str) -> tuple[int, int]:
    parts = s.split(",")
    if len(parts) >= 2:
        return int(parts[0].strip()), int(parts[1].strip())
    return 0, 0


def _parse_order_line(line: str) -> Optional[TacticalOrder]:
    """Parse a single order line. Returns None if unrecognized."""

    if line == "WAIT" or line == "NO-OP":
        return None

    # MOVE <ids> TO <x>,<y>
    m = re.match(r"MOVE\s+([\d,]+)\s+TO\s+(\d+)\s*,\s*(\d+)", line)
    if m:
        return TacticalOrder(
            order_type=OrderType.ADVANCE,
            unit_ids=_parse_unit_ids(m.group(1)),
            target_x=int(m.group(2)),
            target_y=int(m.group(3)),
        )

    # ATTACK <ids> TO <x>,<y>
    m = re.match(r"ATTACK\s+([\d,]+)\s+TO\s+(\d+)\s*,\s*(\d+)", line)
    if m:
        return TacticalOrder(
            order_type=OrderType.ASSAULT,
            unit_ids=_parse_unit_ids(m.group(1)),
            target_x=int(m.group(2)),
            target_y=int(m.group(3)),
        )

    # DEFEND <ids> AT <x>,<y>
    m = re.match(r"DEFEND\s+([\d,]+)\s+AT\s+(\d+)\s*,\s*(\d+)", line)
    if m:
        return TacticalOrder(
            order_type=OrderType.DEFEND,
            unit_ids=_parse_unit_ids(m.group(1)),
            target_x=int(m.group(2)),
            target_y=int(m.group(3)),
        )

    # SCOUT <ids> TO <x>,<y>
    m = re.match(r"SCOUT\s+([\d,]+)\s+TO\s+(\d+)\s*,\s*(\d+)", line)
    if m:
        return TacticalOrder(
            order_type=OrderType.RECONNOITER,
            unit_ids=_parse_unit_ids(m.group(1)),
            target_x=int(m.group(2)),
            target_y=int(m.group(3)),
        )

    # HOLD <ids>
    m = re.match(r"HOLD\s+([\d,]+)", line)
    if m:
        return TacticalOrder(
            order_type=OrderType.HOLD_POSITION,
            unit_ids=_parse_unit_ids(m.group(1)),
        )

    # BUILD <item>
    m = re.match(r"BUILD\s+(\w+)", line)
    if m:
        return TacticalOrder(
            order_type=OrderType.BUILD,
            item_type=m.group(1).lower(),
        )

    # TRAIN <count>x <item>
    m = re.match(r"TRAIN\s+(\d+)\s*[Xx]\s*(\w+)", line)
    if m:
        return TacticalOrder(
            order_type=OrderType.TRAIN,
            item_type=m.group(2).lower(),
            count=int(m.group(1)),
        )

    # DEPLOY <ids>
    m = re.match(r"DEPLOY\s+([\d,]+)", line)
    if m:
        return TacticalOrder(
            order_type=OrderType.DEPLOY,
            unit_ids=_parse_unit_ids(m.group(1)),
        )

    # STANCE <ids> <stance>
    m = re.match(r"STANCE\s+([\d,]+)\s+(\w+)", line)
    if m:
        stance_map = {
            "AGGRESSIVE": Stance.AGGRESSIVE,
            "DEFENSIVE": Stance.DEFENSIVE,
            "HOLD_FIRE": Stance.HOLD_FIRE,
            "FREE_FIRE": Stance.FREE_FIRE,
        }
        stance = stance_map.get(m.group(2), Stance.DEFENSIVE)
        return TacticalOrder(
            order_type=OrderType.SET_STANCE,
            unit_ids=_parse_unit_ids(m.group(1)),
            stance=stance,
        )

    return None


# ── Approach 3: Function-Calling Interface ────────────────────────

MILSIM_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_briefing",
            "description": "Get the current tactical briefing with all unit positions, economy, and intel.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_units",
            "description": "Move units to a map position.",
            "parameters": {
                "type": "object",
                "properties": {
                    "unit_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Actor IDs of units to move",
                    },
                    "x": {"type": "integer", "description": "Target cell X"},
                    "y": {"type": "integer", "description": "Target cell Y"},
                },
                "required": ["unit_ids", "x", "y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "attack_move",
            "description": "Attack-move units toward a position, engaging enemies on the way.",
            "parameters": {
                "type": "object",
                "properties": {
                    "unit_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Actor IDs of units",
                    },
                    "x": {"type": "integer", "description": "Target cell X"},
                    "y": {"type": "integer", "description": "Target cell Y"},
                },
                "required": ["unit_ids", "x", "y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "defend_position",
            "description": "Set units to defend a position with defensive stance.",
            "parameters": {
                "type": "object",
                "properties": {
                    "unit_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Actor IDs of units",
                    },
                    "x": {"type": "integer", "description": "Position X"},
                    "y": {"type": "integer", "description": "Position Y"},
                },
                "required": ["unit_ids", "x", "y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scout_area",
            "description": "Send units to reconnoiter an area.",
            "parameters": {
                "type": "object",
                "properties": {
                    "unit_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Actor IDs of scouts",
                    },
                    "x": {"type": "integer", "description": "Target cell X"},
                    "y": {"type": "integer", "description": "Target cell Y"},
                },
                "required": ["unit_ids", "x", "y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hold_position",
            "description": "Stop units and hold current position.",
            "parameters": {
                "type": "object",
                "properties": {
                    "unit_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Actor IDs of units",
                    },
                },
                "required": ["unit_ids"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "build_structure",
            "description": "Start building a structure. Use 'powr' for power plant, 'tent'/'barr' for barracks, 'proc' for refinery, 'weap' for war factory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "item_type": {
                        "type": "string",
                        "description": "Building type code (e.g., powr, tent, barr, proc, weap)",
                    },
                },
                "required": ["item_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "train_units",
            "description": "Queue unit production. Use 'e1' for rifle infantry, 'e3' for rocket soldier, 'e2' for grenadier.",
            "parameters": {
                "type": "object",
                "properties": {
                    "item_type": {
                        "type": "string",
                        "description": "Unit type code (e.g., e1, e3, e2)",
                    },
                    "count": {
                        "type": "integer",
                        "description": "How many to queue",
                        "default": 1,
                    },
                },
                "required": ["item_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "deploy_unit",
            "description": "Deploy a unit (e.g., deploy MCV to build construction yard).",
            "parameters": {
                "type": "object",
                "properties": {
                    "unit_id": {
                        "type": "integer",
                        "description": "Actor ID of unit to deploy",
                    },
                },
                "required": ["unit_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_stance",
            "description": "Set combat stance for units.",
            "parameters": {
                "type": "object",
                "properties": {
                    "unit_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Actor IDs of units",
                    },
                    "stance": {
                        "type": "string",
                        "enum": ["aggressive", "defensive", "hold_fire", "free_fire"],
                    },
                },
                "required": ["unit_ids", "stance"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "advance_turn",
            "description": "End your turn and advance the game. Call this after issuing all orders for this turn.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_spatial_intel",
            "description": "Get spatial intelligence: exploration %, threat assessment, unexplored regions.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_force_assessment",
            "description": "Assess combat strength of specified unit types.",
            "parameters": {
                "type": "object",
                "properties": {
                    "unit_types": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Unit type codes to assess (e.g., ['e1', 'e3', '3tnk'])",
                    },
                },
                "required": ["unit_types"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_counter_units",
            "description": "Get recommended counter-units for enemy composition.",
            "parameters": {
                "type": "object",
                "properties": {
                    "enemy_types": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Enemy unit type codes",
                    },
                },
                "required": ["enemy_types"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_minimap",
            "description": "Get ASCII minimap showing terrain, units, and fog of war.",
            "parameters": {
                "type": "object",
                "properties": {
                    "width": {
                        "type": "integer",
                        "description": "Minimap width in characters",
                        "default": 40,
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_cnn_analysis",
            "description": "Get CNN tactical analysis: threat heatmap hotspots, attack priorities with approach/risk scores, defensive positions, rally point, movement corridors, and flank routes.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_threat_map",
            "description": "Get ASCII threat heatmap showing where enemy danger is concentrated.",
            "parameters": {
                "type": "object",
                "properties": {
                    "width": {
                        "type": "integer",
                        "description": "Map width in characters",
                        "default": 40,
                    },
                },
            },
        },
    },
]


class FunctionCallingCommander:
    """Approach 3: OpenAI function-calling loop for milsim C2.

    Manages a tool-calling conversation where the LLM issues commands
    through structured function calls. Each turn the LLM receives a
    briefing, calls tools to issue orders, then calls advance_turn.
    """

    def __init__(self, bridge: CommanderBridge):
        self._bridge = bridge
        self._pending_orders: list[TacticalOrder] = []

    def get_tools(self) -> list[dict]:
        return MILSIM_TOOLS

    def get_system_prompt(self) -> str:
        opfor = ""
        if self._bridge._knowledge.available:
            profile = self._bridge._knowledge.get_opfor_profile("normal")
            if profile.summary:
                opfor = f"\nOPFOR Intelligence:\n{profile.summary}\n"

        context = self._bridge._memory.get_mission_context()
        context_section = f"\nPrevious Engagements:\n{context}\n" if context and "No previous" not in context else ""

        return f"""You are a military commander playing Command & Conquer: Red Alert.

Your mission: Establish a base, build forces, locate the enemy, and destroy their base.

Doctrine:
1. ESTABLISH: Deploy MCV immediately to create construction yard
2. BUILD: Power plant → Barracks → Refinery → War Factory → more power
3. SCOUT: Send infantry to explore and find the enemy base
4. MASS: Build a mixed force (infantry + anti-armor) before attacking
5. ATTACK: Attack-move your full force to the enemy position
6. ADAPT: Counter enemy composition, replace losses, exploit success

Rules:
- Always call get_briefing first each turn to see the situation
- Issue all orders for a turn, then call advance_turn
- Don't queue duplicate production — check active queue first
- Maintain power surplus (build power plants as needed)
- Scout with expendable infantry, don't send your whole army
- When attacking, commit your full combat force to one objective
{opfor}{context_section}
Available unit types: e1 (rifle), e2 (grenadier), e3 (rocket), e1 scouts best.
Building codes: powr (power), tent/barr (barracks), proc (refinery), weap (war factory).
"""

    async def handle_tool_call(
        self, name: str, args: dict
    ) -> str:
        """Execute a tool call and return the result as text."""

        if name == "get_briefing":
            tc = StructuredTextCommander(self._bridge)
            return tc.format_briefing()

        elif name == "move_units":
            self._pending_orders.append(TacticalOrder(
                order_type=OrderType.ADVANCE,
                unit_ids=args["unit_ids"],
                target_x=args["x"],
                target_y=args["y"],
            ))
            return f"Queued: move {args['unit_ids']} to ({args['x']},{args['y']})"

        elif name == "attack_move":
            self._pending_orders.append(TacticalOrder(
                order_type=OrderType.ASSAULT,
                unit_ids=args["unit_ids"],
                target_x=args["x"],
                target_y=args["y"],
            ))
            return f"Queued: attack-move {args['unit_ids']} to ({args['x']},{args['y']})"

        elif name == "defend_position":
            self._pending_orders.append(TacticalOrder(
                order_type=OrderType.DEFEND,
                unit_ids=args["unit_ids"],
                target_x=args["x"],
                target_y=args["y"],
            ))
            return f"Queued: defend {args['unit_ids']} at ({args['x']},{args['y']})"

        elif name == "scout_area":
            self._pending_orders.append(TacticalOrder(
                order_type=OrderType.RECONNOITER,
                unit_ids=args["unit_ids"],
                target_x=args["x"],
                target_y=args["y"],
            ))
            return f"Queued: scout {args['unit_ids']} to ({args['x']},{args['y']})"

        elif name == "hold_position":
            self._pending_orders.append(TacticalOrder(
                order_type=OrderType.HOLD_POSITION,
                unit_ids=args["unit_ids"],
            ))
            return f"Queued: hold {args['unit_ids']}"

        elif name == "build_structure":
            self._pending_orders.append(TacticalOrder(
                order_type=OrderType.BUILD,
                item_type=args["item_type"],
            ))
            return f"Queued: build {args['item_type']}"

        elif name == "train_units":
            self._pending_orders.append(TacticalOrder(
                order_type=OrderType.TRAIN,
                item_type=args["item_type"],
                count=args.get("count", 1),
            ))
            return f"Queued: train {args.get('count', 1)}x {args['item_type']}"

        elif name == "deploy_unit":
            self._pending_orders.append(TacticalOrder(
                order_type=OrderType.DEPLOY,
                unit_ids=[args["unit_id"]],
            ))
            return f"Queued: deploy unit {args['unit_id']}"

        elif name == "set_stance":
            stance_map = {
                "aggressive": Stance.AGGRESSIVE,
                "defensive": Stance.DEFENSIVE,
                "hold_fire": Stance.HOLD_FIRE,
                "free_fire": Stance.FREE_FIRE,
            }
            self._pending_orders.append(TacticalOrder(
                order_type=OrderType.SET_STANCE,
                unit_ids=args["unit_ids"],
                stance=stance_map.get(args["stance"], Stance.DEFENSIVE),
            ))
            return f"Queued: stance {args['unit_ids']} → {args['stance']}"

        elif name == "advance_turn":
            orders = self._pending_orders[:]
            self._pending_orders.clear()
            result = await self._bridge.execute(orders)
            return result

        elif name == "get_spatial_intel":
            if not self._bridge._spatial.has_data:
                return "No spatial data available yet."
            return self._bridge._spatial.format_spatial_sitrep()

        elif name == "get_force_assessment":
            return self._bridge._knowledge.format_force_intel(args["unit_types"])

        elif name == "get_counter_units":
            counters = self._bridge._knowledge.get_counter_units(args["enemy_types"])
            return f"Recommended counters: {', '.join(counters)}"

        elif name == "get_minimap":
            if not self._bridge._spatial.has_data:
                return "No spatial data available yet."
            width = args.get("width", 40)
            return self._bridge._spatial.render_minimap(max_cols=width)

        elif name == "get_cnn_analysis":
            if not self._bridge._cnn.has_data:
                return "No spatial data for CNN analysis yet."
            return self._bridge._cnn.format_analysis()

        elif name == "get_threat_map":
            if not self._bridge._cnn.has_data:
                return "No spatial data for threat map yet."
            width = args.get("width", 40)
            return self._bridge._cnn.render_threat_ascii(max_cols=width)

        return f"Unknown tool: {name}"


# ── Approach 1: MCP Server ────────────────────────────────────────

def create_mcp_server(bridge: CommanderBridge):
    """Create a FastMCP server exposing milsim C2 tools.

    Returns the FastMCP app object. Run with:
        server = create_mcp_server(bridge)
        server.run()  # stdio transport
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        raise ImportError(
            "MCP server requires 'mcp' package: pip install mcp"
        )

    mcp = FastMCP(
        "milsim-commander",
        instructions="Military simulation C2 interface for LLM commanders",
    )

    fc = FunctionCallingCommander(bridge)

    @mcp.tool()
    async def get_briefing() -> str:
        """Get tactical briefing: economy, forces, enemies, intel, available orders."""
        tc = StructuredTextCommander(bridge)
        return tc.format_briefing()

    @mcp.tool()
    async def move_units(unit_ids: list[int], x: int, y: int) -> str:
        """Move units to a map cell."""
        return await fc.handle_tool_call("move_units", {"unit_ids": unit_ids, "x": x, "y": y})

    @mcp.tool()
    async def attack_move(unit_ids: list[int], x: int, y: int) -> str:
        """Attack-move units toward a position, engaging enemies en route."""
        return await fc.handle_tool_call("attack_move", {"unit_ids": unit_ids, "x": x, "y": y})

    @mcp.tool()
    async def defend_position(unit_ids: list[int], x: int, y: int) -> str:
        """Set units to defend a position with defensive stance."""
        return await fc.handle_tool_call("defend_position", {"unit_ids": unit_ids, "x": x, "y": y})

    @mcp.tool()
    async def scout_area(unit_ids: list[int], x: int, y: int) -> str:
        """Send units to reconnoiter an area."""
        return await fc.handle_tool_call("scout_area", {"unit_ids": unit_ids, "x": x, "y": y})

    @mcp.tool()
    async def hold_position(unit_ids: list[int]) -> str:
        """Stop units and hold current position."""
        return await fc.handle_tool_call("hold_position", {"unit_ids": unit_ids})

    @mcp.tool()
    async def build_structure(item_type: str) -> str:
        """Build a structure: powr, tent/barr, proc, weap, etc."""
        return await fc.handle_tool_call("build_structure", {"item_type": item_type})

    @mcp.tool()
    async def train_units(item_type: str, count: int = 1) -> str:
        """Queue unit production: e1 (rifle), e3 (rocket), e2 (grenadier)."""
        return await fc.handle_tool_call("train_units", {"item_type": item_type, "count": count})

    @mcp.tool()
    async def deploy_unit(unit_id: int) -> str:
        """Deploy a unit (e.g., MCV → construction yard)."""
        return await fc.handle_tool_call("deploy_unit", {"unit_id": unit_id})

    @mcp.tool()
    async def set_stance(unit_ids: list[int], stance: str) -> str:
        """Set combat stance: aggressive, defensive, hold_fire, free_fire."""
        return await fc.handle_tool_call("set_stance", {"unit_ids": unit_ids, "stance": stance})

    @mcp.tool()
    async def advance_turn() -> str:
        """Execute all queued orders and advance the game by one WEGO turn."""
        return await fc.handle_tool_call("advance_turn", {})

    @mcp.tool()
    async def get_spatial_intel() -> str:
        """Get spatial intelligence: exploration %, threats, unexplored regions, minimap."""
        return await fc.handle_tool_call("get_spatial_intel", {})

    @mcp.tool()
    async def get_force_assessment(unit_types: list[str]) -> str:
        """Assess combat strength of unit types."""
        return await fc.handle_tool_call("get_force_assessment", {"unit_types": unit_types})

    @mcp.tool()
    async def get_counter_units(enemy_types: list[str]) -> str:
        """Get recommended counter-units for enemy composition."""
        return await fc.handle_tool_call("get_counter_units", {"enemy_types": enemy_types})

    @mcp.tool()
    async def get_minimap(width: int = 40) -> str:
        """Get ASCII minimap showing terrain, units, and fog of war."""
        return await fc.handle_tool_call("get_minimap", {"width": width})

    @mcp.tool()
    async def get_cnn_analysis() -> str:
        """Get CNN tactical analysis: threat hotspots, attack priorities, defensive positions, rally point, movement corridors, flank routes."""
        return await fc.handle_tool_call("get_cnn_analysis", {})

    @mcp.tool()
    async def get_threat_map(width: int = 40) -> str:
        """Get ASCII threat heatmap showing where enemy danger is concentrated."""
        return await fc.handle_tool_call("get_threat_map", {"width": width})

    @mcp.tool()
    async def get_aar() -> str:
        """Get the After Action Review summary."""
        return bridge.get_aar()

    return mcp
