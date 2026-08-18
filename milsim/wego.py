"""WEGO turn system for military simulation.

Transforms OpenRA-RL's real-time tick loop into discrete
planning/execution/review turns used in military wargaming.

Each WEGO turn:
  1. PLANNING  — Commander observes situation, queues orders (no game time)
  2. EXECUTION — Orders execute for N ticks (both sides act simultaneously)
  3. REVIEW    — Results assessed, BDA computed, situation updated

Usage:
    async with OpenRAEnv(base_url="http://localhost:8000") as env:
        wego = WEGOTurnManager(env, ticks_per_turn=50)
        await wego.start()

        while not wego.is_game_over:
            situation = wego.get_situation()
            orders = decide_orders(situation)
            result = await wego.execute_turn(orders)
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from openra_env.client import OpenRAEnv
from openra_env.models import (
    ActionType,
    BuildingInfoModel,
    CommandModel,
    MilitaryInfo,
    OpenRAAction,
    OpenRAObservation,
    UnitInfoModel,
)


class TurnPhase(str, Enum):
    PLANNING = "planning"
    EXECUTION = "execution"
    REVIEW = "review"


@dataclass
class BattleDamageAssessment:
    """Post-execution damage assessment for a single turn."""

    units_killed_this_turn: int = 0
    units_lost_this_turn: int = 0
    buildings_killed_this_turn: int = 0
    buildings_lost_this_turn: int = 0
    cash_delta: int = 0
    army_value_delta: int = 0
    new_enemy_contacts: int = 0
    lost_contacts: int = 0


@dataclass
class Situation:
    """Commander's view of the battlefield at planning time."""

    turn_number: int
    game_tick: int
    phase: TurnPhase
    observation: OpenRAObservation
    bda: Optional[BattleDamageAssessment] = None

    @property
    def friendly_units(self) -> list[UnitInfoModel]:
        return self.observation.units

    @property
    def friendly_buildings(self) -> list[BuildingInfoModel]:
        return self.observation.buildings

    @property
    def known_enemies(self) -> list[UnitInfoModel]:
        return self.observation.visible_enemies

    @property
    def known_enemy_buildings(self) -> list[BuildingInfoModel]:
        return self.observation.visible_enemy_buildings

    @property
    def cash(self) -> int:
        return self.observation.economy.cash

    @property
    def power_surplus(self) -> int:
        return self.observation.economy.power_provided - self.observation.economy.power_drained


@dataclass
class TurnResult:
    """Result of executing a WEGO turn."""

    turn_number: int
    ticks_executed: int
    situation_before: Situation
    situation_after: Situation
    bda: BattleDamageAssessment
    orders_issued: list[CommandModel]
    game_over: bool = False
    game_result: str = ""


@dataclass
class TurnLog:
    """Record of a single turn for AAR."""

    turn_number: int
    tick_start: int
    tick_end: int
    orders: list[CommandModel]
    bda: BattleDamageAssessment
    unit_count_start: int
    unit_count_end: int
    enemy_contacts_start: int
    enemy_contacts_end: int
    cash_start: int
    cash_end: int


class WEGOTurnManager:
    """Manages WEGO (We-Go) turn-based execution over OpenRA-RL.

    Injects planning/execution/review phases into the real-time
    tick loop. Both sides issue orders during planning, then
    all orders execute simultaneously during the execution phase.

    Args:
        env: Connected OpenRAEnv instance.
        ticks_per_turn: Game ticks per execution phase (25 ticks ~ 1 second).
            Default 50 (2 game-seconds per turn).
        execution_substeps: Break execution into this many observation
            checkpoints. Allows mid-turn interrupts for critical events.
    """

    def __init__(
        self,
        env: OpenRAEnv,
        ticks_per_turn: int = 50,
        execution_substeps: int = 5,
    ):
        self._env = env
        self._ticks_per_turn = ticks_per_turn
        self._substeps = execution_substeps
        self._turn = 0
        self._phase = TurnPhase.PLANNING
        self._current_obs: Optional[OpenRAObservation] = None
        self._prev_military: Optional[MilitaryInfo] = None
        self._prev_enemy_ids: set[int] = set()
        self._turn_log: list[TurnLog] = []
        self._game_over = False
        self._game_result = ""

    @property
    def turn_number(self) -> int:
        return self._turn

    @property
    def phase(self) -> TurnPhase:
        return self._phase

    @property
    def is_game_over(self) -> bool:
        return self._game_over

    @property
    def game_result(self) -> str:
        return self._game_result

    @property
    def turn_history(self) -> list[TurnLog]:
        return list(self._turn_log)

    async def start(self) -> Situation:
        """Reset the game and enter the first planning phase.

        Returns the initial situation for the commander.
        """
        result = await self._env.reset()
        self._current_obs = result.observation

        # Advance a few ticks to let initial units spawn
        for _ in range(5):
            result = await self._env.step(
                OpenRAAction(commands=[CommandModel(action=ActionType.NO_OP)])
            )
        self._current_obs = result.observation

        self._turn = 1
        self._phase = TurnPhase.PLANNING
        self._prev_military = MilitaryInfo()
        self._prev_enemy_ids = {u.actor_id for u in self._current_obs.visible_enemies}

        return self.get_situation()

    def get_situation(self) -> Situation:
        """Get the current battlefield situation."""
        return Situation(
            turn_number=self._turn,
            game_tick=self._current_obs.tick if self._current_obs else 0,
            phase=self._phase,
            observation=self._current_obs,
        )

    async def execute_turn(
        self,
        orders: list[CommandModel],
        interrupt_on_contact: bool = False,
    ) -> TurnResult:
        """Execute a complete WEGO turn.

        Args:
            orders: Commands to issue at the start of execution.
            interrupt_on_contact: If True, halt execution early when
                new enemy contacts are detected.

        Returns:
            TurnResult with before/after situation and BDA.
        """
        if self._game_over:
            raise RuntimeError("Game is over")

        situation_before = self.get_situation()
        pre_military = _snapshot_military(self._current_obs.military)
        pre_enemy_ids = {u.actor_id for u in self._current_obs.visible_enemies}
        pre_cash = self._current_obs.economy.cash
        pre_unit_count = len(self._current_obs.units)

        # Phase 1: Issue all orders in a single step (no time advance)
        self._phase = TurnPhase.EXECUTION
        if orders:
            action = OpenRAAction(commands=orders)
            result = await self._env.step(action)
            self._current_obs = result.observation

        # Phase 2: Execute — advance ticks in substeps
        ticks_per_sub = self._ticks_per_turn // self._substeps
        ticks_executed = 0

        for _ in range(self._substeps):
            for _ in range(ticks_per_sub):
                result = await self._env.step(
                    OpenRAAction(commands=[CommandModel(action=ActionType.NO_OP)])
                )
                ticks_executed += 1

            self._current_obs = result.observation

            if self._current_obs.done:
                self._game_over = True
                self._game_result = self._current_obs.result
                break

            if interrupt_on_contact:
                current_enemy_ids = {u.actor_id for u in self._current_obs.visible_enemies}
                new_contacts = current_enemy_ids - pre_enemy_ids
                if new_contacts:
                    break

        # Phase 3: Review — compute BDA
        self._phase = TurnPhase.REVIEW
        post_military = self._current_obs.military
        post_enemy_ids = {u.actor_id for u in self._current_obs.visible_enemies}

        bda = BattleDamageAssessment(
            units_killed_this_turn=post_military.units_killed - pre_military.units_killed,
            units_lost_this_turn=post_military.units_lost - pre_military.units_lost,
            buildings_killed_this_turn=post_military.buildings_killed - pre_military.buildings_killed,
            buildings_lost_this_turn=post_military.buildings_lost - pre_military.buildings_lost,
            cash_delta=self._current_obs.economy.cash - pre_cash,
            army_value_delta=post_military.army_value - pre_military.army_value,
            new_enemy_contacts=len(post_enemy_ids - pre_enemy_ids),
            lost_contacts=len(pre_enemy_ids - post_enemy_ids),
        )

        situation_after = Situation(
            turn_number=self._turn,
            game_tick=self._current_obs.tick,
            phase=TurnPhase.REVIEW,
            observation=self._current_obs,
            bda=bda,
        )

        # Log for AAR
        self._turn_log.append(TurnLog(
            turn_number=self._turn,
            tick_start=situation_before.game_tick,
            tick_end=self._current_obs.tick,
            orders=orders,
            bda=bda,
            unit_count_start=pre_unit_count,
            unit_count_end=len(self._current_obs.units),
            enemy_contacts_start=len(pre_enemy_ids),
            enemy_contacts_end=len(post_enemy_ids),
            cash_start=pre_cash,
            cash_end=self._current_obs.economy.cash,
        ))

        turn_result = TurnResult(
            turn_number=self._turn,
            ticks_executed=ticks_executed,
            situation_before=situation_before,
            situation_after=situation_after,
            bda=bda,
            orders_issued=orders,
            game_over=self._game_over,
            game_result=self._game_result,
        )

        # Advance to next turn
        self._turn += 1
        self._phase = TurnPhase.PLANNING
        self._prev_military = _snapshot_military(post_military)
        self._prev_enemy_ids = post_enemy_ids

        return turn_result

    def format_sitrep(self, situation: Optional[Situation] = None) -> str:
        """Format a military-style situation report."""
        sit = situation or self.get_situation()
        obs = sit.observation
        eco = obs.economy
        mil = obs.military

        lines = [
            f"=== SITREP — Turn {sit.turn_number} (Tick {sit.game_tick}) ===",
            f"Phase: {sit.phase.value.upper()}",
            "",
            "BLUE FORCES:",
            f"  Units: {len(obs.units)} | Buildings: {len(obs.buildings)}",
            f"  Army value: ${mil.army_value} | Assets: ${mil.assets_value}",
            "",
            "ECONOMY:",
            f"  Cash: ${eco.cash} | Ore: {eco.ore}",
            f"  Power: {eco.power_provided}/{eco.power_drained} "
            f"({'OK' if eco.power_provided >= eco.power_drained else 'LOW'})",
            f"  Harvesters: {eco.harvester_count}",
            "",
            "RED FORCES (known):",
            f"  Units: {len(obs.visible_enemies)} | Buildings: {len(obs.visible_enemy_buildings)}",
            "",
            "MILITARY:",
            f"  Kills: {mil.units_killed}u/{mil.buildings_killed}b",
            f"  Losses: {mil.units_lost}u/{mil.buildings_lost}b",
            f"  Orders issued: {mil.order_count}",
        ]

        if sit.bda:
            lines.extend([
                "",
                "LAST TURN BDA:",
                f"  Kills: +{sit.bda.units_killed_this_turn}u/+{sit.bda.buildings_killed_this_turn}b",
                f"  Losses: -{sit.bda.units_lost_this_turn}u/-{sit.bda.buildings_lost_this_turn}b",
                f"  Cash delta: {'+' if sit.bda.cash_delta >= 0 else ''}{sit.bda.cash_delta}",
                f"  New contacts: {sit.bda.new_enemy_contacts} | Lost: {sit.bda.lost_contacts}",
            ])

        if obs.production:
            lines.extend([
                "",
                "PRODUCTION:",
            ])
            for p in obs.production:
                lines.append(f"  {p.queue_type}: {p.item} ({p.progress:.0%})")

        if obs.available_production:
            lines.append(f"\nCan build: {', '.join(obs.available_production[:10])}")

        return "\n".join(lines)

    def generate_aar_summary(self) -> str:
        """Generate After Action Review summary from turn history."""
        if not self._turn_log:
            return "No turns executed yet."

        total_kills_u = sum(t.bda.units_killed_this_turn for t in self._turn_log)
        total_kills_b = sum(t.bda.buildings_killed_this_turn for t in self._turn_log)
        total_losses_u = sum(t.bda.units_lost_this_turn for t in self._turn_log)
        total_losses_b = sum(t.bda.buildings_lost_this_turn for t in self._turn_log)
        total_orders = sum(len(t.orders) for t in self._turn_log)

        lines = [
            "=== AFTER ACTION REVIEW ===",
            f"Turns played: {len(self._turn_log)}",
            f"Game ticks: {self._turn_log[0].tick_start} → {self._turn_log[-1].tick_end}",
            f"Result: {self._game_result or 'ongoing'}",
            "",
            "AGGREGATE:",
            f"  Total kills: {total_kills_u} units, {total_kills_b} buildings",
            f"  Total losses: {total_losses_u} units, {total_losses_b} buildings",
            f"  Kill ratio: {total_kills_u / max(total_losses_u, 1):.1f}:1",
            f"  Total orders: {total_orders}",
            f"  Cash flow: ${self._turn_log[0].cash_start} → ${self._turn_log[-1].cash_end}",
            "",
            "TURN-BY-TURN:",
        ]

        for t in self._turn_log:
            lines.append(
                f"  T{t.turn_number:3d} | "
                f"Orders: {len(t.orders):2d} | "
                f"Kills: +{t.bda.units_killed_this_turn}u | "
                f"Lost: -{t.bda.units_lost_this_turn}u | "
                f"Cash: {'+' if t.bda.cash_delta >= 0 else ''}{t.bda.cash_delta:>6d} | "
                f"Units: {t.unit_count_end}"
            )

        return "\n".join(lines)


def _snapshot_military(mil: MilitaryInfo) -> MilitaryInfo:
    """Take a snapshot of military stats for delta computation."""
    return MilitaryInfo(
        units_killed=mil.units_killed,
        units_lost=mil.units_lost,
        buildings_killed=mil.buildings_killed,
        buildings_lost=mil.buildings_lost,
        army_value=mil.army_value,
        active_unit_count=mil.active_unit_count,
        kills_cost=mil.kills_cost,
        deaths_cost=mil.deaths_cost,
        assets_value=mil.assets_value,
        experience=mil.experience,
        order_count=mil.order_count,
    )
