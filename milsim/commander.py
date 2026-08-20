"""Commander interface for LLM-driven military decision-making.

Bridges the WEGO turn system with structured commander decisions.
Translates high-level tactical orders (assault, defend, reconnoiter)
into OpenRA-RL action sequences.

The commander sees:
  - Situation report (SITREP) with friendly/enemy disposition
  - Battle damage assessment (BDA) from previous turn
  - Available units with capabilities
  - Available production options

The commander issues:
  - Movement orders (advance, withdraw, flank)
  - Engagement orders (assault, suppress, ambush)
  - Production orders (build, train, reinforce)
  - Stance directives (aggressive, defensive, hold)
"""

import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from openra_env.models import (
    ActionType,
    CommandModel,
    OpenRAObservation,
    UnitInfoModel,
    BuildingInfoModel,
)

from milsim.wego import WEGOTurnManager, Situation, TurnResult


class OrderType(str, Enum):
    ADVANCE = "advance"
    ASSAULT = "assault"
    DEFEND = "defend"
    WITHDRAW = "withdraw"
    RECONNOITER = "reconnoiter"
    HOLD_POSITION = "hold"
    BUILD = "build"
    TRAIN = "train"
    DEPLOY = "deploy"
    SET_STANCE = "set_stance"


class Stance(str, Enum):
    AGGRESSIVE = "aggressive"
    DEFENSIVE = "defensive"
    HOLD_FIRE = "hold_fire"
    FREE_FIRE = "free_fire"


STANCE_MAP = {
    Stance.HOLD_FIRE: 0,
    Stance.DEFENSIVE: 1,
    Stance.AGGRESSIVE: 2,
    Stance.FREE_FIRE: 3,
}


@dataclass
class TacticalOrder:
    """A high-level tactical order from the commander."""

    order_type: OrderType
    unit_ids: list[int] = field(default_factory=list)
    target_x: int = 0
    target_y: int = 0
    target_actor_id: int = 0
    item_type: str = ""
    stance: Stance = Stance.DEFENSIVE
    count: int = 1
    queued: bool = False


@dataclass
class UnitSummary:
    """Simplified unit info for commander briefing."""

    actor_id: int
    type: str
    cell_x: int
    cell_y: int
    hp_percent: float
    is_idle: bool
    stance: str
    can_attack: bool
    speed: int
    attack_range: int

    @classmethod
    def from_unit(cls, u: UnitInfoModel) -> "UnitSummary":
        stance_names = {0: "hold_fire", 1: "return_fire", 2: "defend", 3: "attack_anything"}
        return cls(
            actor_id=u.actor_id,
            type=u.type,
            cell_x=u.cell_x,
            cell_y=u.cell_y,
            hp_percent=u.hp_percent,
            is_idle=u.is_idle,
            stance=stance_names.get(u.stance, "unknown"),
            can_attack=u.can_attack,
            speed=u.speed,
            attack_range=u.attack_range,
        )


@dataclass
class CommanderBriefing:
    """Structured briefing for the commander's decision."""

    turn_number: int
    game_tick: int
    friendly_units: list[UnitSummary]
    friendly_buildings: list[dict]
    known_enemies: list[UnitSummary]
    known_enemy_buildings: list[dict]
    economy: dict
    military_stats: dict
    available_production: list[str]
    active_production: list[dict] = field(default_factory=list)
    previous_bda: Optional[dict] = None
    sitrep: str = ""


class Commander:
    """Translates high-level tactical orders into game commands.

    The Commander acts as the interface between a human or LLM
    decision-maker and the WEGO turn system. It:
      1. Provides structured briefings in military format
      2. Accepts tactical orders in domain terms
      3. Translates them into OpenRA-RL command sequences
      4. Executes them through the WEGO system
      5. Returns results in military assessment format
    """

    def __init__(self, wego: WEGOTurnManager):
        self._wego = wego
        self._placement_counter = 0
        self._order_log: list[dict] = []

    async def start_game(self) -> CommanderBriefing:
        """Initialize the game and return first briefing."""
        situation = await self._wego.start()
        return self._build_briefing(situation)

    def get_briefing(self) -> CommanderBriefing:
        """Get the current situation briefing."""
        situation = self._wego.get_situation()
        return self._build_briefing(situation)

    async def issue_orders(
        self,
        orders: list[TacticalOrder],
        interrupt_on_contact: bool = False,
    ) -> tuple[TurnResult, CommanderBriefing]:
        """Execute tactical orders and return results + next briefing.

        Args:
            orders: List of tactical orders from the commander.
            interrupt_on_contact: Stop turn early if new enemies spotted.

        Returns:
            (TurnResult with BDA, next turn's briefing)
        """
        obs = self._wego.get_situation().observation

        # Drop orders aimed at units that do not exist, and say so.
        #
        # Nothing downstream validates actor_id: _translate_order builds a
        # CommandModel for every id it is given, the engine finds no such
        # actor, and the turn passes with no error anywhere. An LLM that
        # invents ids -- numbering units 1, 2, 3 instead of using the actor_ids
        # in the briefing -- therefore produces a run that reports orders
        # issued every turn while nothing moves and cash never changes.
        orders, dropped = self._drop_unknown_units(orders, obs)
        if dropped:
            units = sorted(u.actor_id for u in obs.units)
            buildings = sorted(b.actor_id for b in obs.buildings)
            print(
                f"  WARNING: ignored {len(dropped)} order(s) naming "
                f"{sorted(dropped)} - no such actor. "
                f"Own units: {units or 'none'}  buildings: {buildings or 'none'}",
                file=sys.stderr,
            )

        commands = []
        for order in orders:
            commands.extend(self._translate_order(order))

        # Auto-place any completed buildings
        place_cmds = self._auto_place(obs)
        commands.extend(place_cmds)

        if not commands:
            commands = [CommandModel(action=ActionType.NO_OP)]

        self._order_log.append({
            "turn": self._wego.turn_number,
            "orders": [{"type": o.order_type.value, "units": o.unit_ids} for o in orders],
        })

        result = await self._wego.execute_turn(commands, interrupt_on_contact)
        briefing = self._build_briefing(result.situation_after)
        return result, briefing

    async def advance_time(self) -> tuple[TurnResult, CommanderBriefing]:
        """Advance one turn with no new orders (observe only)."""
        obs = self._wego.get_situation().observation
        place_cmds = self._auto_place(obs)
        commands = place_cmds if place_cmds else [CommandModel(action=ActionType.NO_OP)]
        result = await self._wego.execute_turn(commands)
        briefing = self._build_briefing(result.situation_after)
        return result, briefing

    @property
    def is_game_over(self) -> bool:
        return self._wego.is_game_over

    @property
    def game_result(self) -> str:
        return self._wego.game_result

    def get_aar(self) -> str:
        """Get After Action Review."""
        return self._wego.generate_aar_summary()

    @staticmethod
    def _drop_unknown_units(orders, obs):
        """Filter orders down to units we actually own.

        Returns the surviving orders and the set of ids that were discarded.
        Orders that name no units at all (build, train, global stance) pass
        through untouched; only unit-targeted ones are checked.
        """
        own = {u.actor_id for u in obs.units}
        own.update(b.actor_id for b in obs.buildings)

        kept, dropped = [], set()
        for order in orders:
            if not order.unit_ids:
                kept.append(order)
                continue
            valid = [uid for uid in order.unit_ids if uid in own]
            dropped.update(uid for uid in order.unit_ids if uid not in own)
            if valid:
                order.unit_ids = valid
                kept.append(order)
        return kept, dropped

    def _translate_order(self, order: TacticalOrder) -> list[CommandModel]:
        """Convert a tactical order into game commands."""
        match order.order_type:
            case OrderType.ADVANCE:
                return [CommandModel(
                    action=ActionType.MOVE,
                    actor_id=uid,
                    target_x=order.target_x,
                    target_y=order.target_y,
                    queued=order.queued,
                ) for uid in order.unit_ids]

            case OrderType.ASSAULT:
                return [CommandModel(
                    action=ActionType.ATTACK_MOVE,
                    actor_id=uid,
                    target_x=order.target_x,
                    target_y=order.target_y,
                    queued=order.queued,
                ) for uid in order.unit_ids]

            case OrderType.DEFEND:
                cmds = []
                for uid in order.unit_ids:
                    cmds.append(CommandModel(
                        action=ActionType.SET_STANCE,
                        actor_id=uid,
                        target_x=STANCE_MAP[Stance.DEFENSIVE],
                    ))
                    if order.target_x or order.target_y:
                        cmds.append(CommandModel(
                            action=ActionType.MOVE,
                            actor_id=uid,
                            target_x=order.target_x,
                            target_y=order.target_y,
                        ))
                return cmds

            case OrderType.WITHDRAW:
                return [CommandModel(
                    action=ActionType.MOVE,
                    actor_id=uid,
                    target_x=order.target_x,
                    target_y=order.target_y,
                    queued=order.queued,
                ) for uid in order.unit_ids]

            case OrderType.RECONNOITER:
                return [CommandModel(
                    action=ActionType.ATTACK_MOVE,
                    actor_id=uid,
                    target_x=order.target_x,
                    target_y=order.target_y,
                ) for uid in order.unit_ids]

            case OrderType.HOLD_POSITION:
                return [CommandModel(
                    action=ActionType.STOP,
                    actor_id=uid,
                ) for uid in order.unit_ids]

            case OrderType.BUILD:
                return [CommandModel(
                    action=ActionType.BUILD,
                    item_type=order.item_type,
                )]

            case OrderType.TRAIN:
                return [CommandModel(
                    action=ActionType.TRAIN,
                    item_type=order.item_type,
                ) for _ in range(order.count)]

            case OrderType.DEPLOY:
                return [CommandModel(
                    action=ActionType.DEPLOY,
                    actor_id=uid,
                ) for uid in order.unit_ids]

            case OrderType.SET_STANCE:
                return [CommandModel(
                    action=ActionType.SET_STANCE,
                    actor_id=uid,
                    target_x=STANCE_MAP.get(order.stance, 1),
                ) for uid in order.unit_ids]

            case _:
                return []

    def _auto_place(self, obs: OpenRAObservation) -> list[CommandModel]:
        """Generate placement commands for completed buildings."""
        commands = []
        cy = next((b for b in obs.buildings if b.type == "fact"), None)
        if not cy:
            return commands
        for prod in obs.production:
            if prod.queue_type == "Building" and prod.progress >= 0.99:
                x, y = self._placement_pos(cy)
                commands.append(CommandModel(
                    action=ActionType.PLACE_BUILDING,
                    item_type=prod.item,
                    target_x=x,
                    target_y=y,
                ))
                self._placement_counter += 1
        return commands

    def _placement_pos(self, cy: BuildingInfoModel) -> tuple[int, int]:
        cx = cy.pos_x // 1024
        cy_y = cy.pos_y // 1024
        offsets = [
            (3, 0), (-3, 0), (0, 3), (0, -3),
            (3, 3), (-3, 3), (3, -3), (-3, -3),
            (6, 0), (-6, 0), (0, 6), (0, -6),
            (2, 0), (-2, 0), (0, 2), (0, -2),
        ]
        dx, dy = offsets[self._placement_counter % len(offsets)]
        return cx + dx, cy_y + dy

    def _build_briefing(self, situation: Situation) -> CommanderBriefing:
        """Build structured briefing from situation."""
        obs = situation.observation
        eco = obs.economy
        mil = obs.military

        bda_dict = None
        if situation.bda:
            b = situation.bda
            bda_dict = {
                "units_killed": b.units_killed_this_turn,
                "units_lost": b.units_lost_this_turn,
                "buildings_killed": b.buildings_killed_this_turn,
                "buildings_lost": b.buildings_lost_this_turn,
                "cash_delta": b.cash_delta,
                "new_contacts": b.new_enemy_contacts,
                "lost_contacts": b.lost_contacts,
            }

        return CommanderBriefing(
            turn_number=situation.turn_number,
            game_tick=situation.game_tick,
            friendly_units=[UnitSummary.from_unit(u) for u in obs.units],
            friendly_buildings=[{
                "actor_id": b.actor_id,
                "type": b.type,
                "hp_percent": b.hp_percent,
                "is_producing": b.is_producing,
                "producing_item": b.producing_item,
                "is_powered": b.is_powered,
                "can_produce": b.can_produce,
            } for b in obs.buildings],
            known_enemies=[UnitSummary.from_unit(u) for u in obs.visible_enemies],
            known_enemy_buildings=[{
                "actor_id": b.actor_id,
                "type": b.type,
                "hp_percent": b.hp_percent,
                "cell_x": b.cell_x,
                "cell_y": b.cell_y,
            } for b in obs.visible_enemy_buildings],
            economy={
                "cash": eco.cash,
                "ore": eco.ore,
                "power_provided": eco.power_provided,
                "power_drained": eco.power_drained,
                "power_surplus": eco.power_provided - eco.power_drained,
                "harvesters": eco.harvester_count,
            },
            military_stats={
                "units_killed": mil.units_killed,
                "units_lost": mil.units_lost,
                "buildings_killed": mil.buildings_killed,
                "buildings_lost": mil.buildings_lost,
                "army_value": mil.army_value,
                "kill_ratio": mil.units_killed / max(mil.units_lost, 1),
            },
            available_production=obs.available_production,
            active_production=[
                {
                    "queue_type": p.queue_type,
                    "item": p.item,
                    "progress": p.progress,
                    "paused": p.paused,
                }
                for p in obs.production
            ],
            previous_bda=bda_dict,
            sitrep=self._wego.format_sitrep(situation),
        )

    def format_briefing_text(self, briefing: Optional[CommanderBriefing] = None) -> str:
        """Format briefing as text for LLM consumption."""
        b = briefing or self.get_briefing()
        lines = [
            f"COMMANDER BRIEFING — Turn {b.turn_number}",
            "=" * 50,
            "",
            b.sitrep,
            "",
            "AVAILABLE ORDERS:",
            f"  Can build: {', '.join(b.available_production[:12])}",
            "",
            "UNIT DISPOSITION:",
        ]

        if b.friendly_units:
            for u in b.friendly_units:
                status = f"HP:{u.hp_percent:.0%}"
                activity = "IDLE" if u.is_idle else "ACTIVE"
                lines.append(
                    f"  [{u.actor_id}] {u.type:8s} at ({u.cell_x},{u.cell_y}) "
                    f"{status} {activity} stance={u.stance}"
                )
        else:
            lines.append("  (no units)")

        if b.known_enemies:
            lines.extend(["", "ENEMY CONTACTS:"])
            for e in b.known_enemies:
                lines.append(
                    f"  [{e.actor_id}] {e.type:8s} at ({e.cell_x},{e.cell_y}) HP:{e.hp_percent:.0%}"
                )

        if b.previous_bda:
            lines.extend(["", "PREVIOUS TURN BDA:"])
            for k, v in b.previous_bda.items():
                lines.append(f"  {k}: {v}")

        return "\n".join(lines)
