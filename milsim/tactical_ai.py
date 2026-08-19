"""Tactical AI that plays through the Commander interface.

Implements a doctrine-driven decision loop that:
  1. Reads the Commander briefing (SITREP + ISR)
  2. Assesses the situation against doctrine rules
  3. Issues tactical orders
  4. Reviews results and adapts

This is the slot where an LLM commander plugs in: `LLMTacticalAI`
subclasses `TacticalAI` and replaces the rule-based `decide()` with LLM
inference over the same structured briefing text. Any OpenAI-compatible
provider works, and the default is a local Ollama daemon
($OLLAMA_HOST, default http://localhost:11434) with no API key.

Usage (doctrine / rule-based):
    ai = TacticalAI(commander, isr, scenario_runner)
    await ai.play_game()
    print(ai.get_aar())

Usage (LLM commander, local Ollama by default):
    ai = LLMTacticalAI(commander, isr, scenario_runner)
    await ai.play_game()          # clear error up front if Ollama is down
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from milsim.commander import (
    Commander,
    CommanderBriefing,
    TacticalOrder,
    OrderType,
    Stance,
)
from milsim.isr import ISRManager, IntelLevel, IntelSummary
from milsim.scenario import ScenarioRunner, MSELEvent
from milsim.wego import TurnResult
from milsim.spatial import SpatialIntel
from milsim.game_knowledge import GameKnowledge
from milsim.memory import TacticalMemory
from milsim.assessment import PerformanceAssessor
from milsim.cnn import TacticalCNN
from milsim.llm_provider import LLMClient, LLMError, build_llm

try:
    from openra_env.bench_export import build_bench_export
    _HAS_BENCH = True
except ImportError:
    _HAS_BENCH = False


class Phase(str, Enum):
    ESTABLISH = "establish"
    BUILD = "build"
    SCOUT = "scout"
    MASS = "mass"
    ATTACK = "attack"
    EXPLOIT = "exploit"


# Build order priority
BUILD_ORDER = ["powr", "tent", "barr", "proc", "weap", "powr"]

# Unit production priority by phase
PRODUCTION_PRIORITY = {
    Phase.ESTABLISH: [],
    Phase.BUILD: ["e1", "e1", "e1"],
    Phase.SCOUT: ["e1"],
    Phase.MASS: ["e1", "e1", "e3", "e1", "e1"],
    Phase.ATTACK: ["e1", "e3"],
    Phase.EXPLOIT: ["e1"],
}


@dataclass
class AIState:
    """Internal state for tactical decision-making."""

    phase: Phase = Phase.ESTABLISH
    build_index: int = 0
    scouts_sent: int = 0
    attack_launched: bool = False
    enemy_base_x: int = -1
    enemy_base_y: int = -1
    last_unit_count: int = 0
    consecutive_idle_turns: int = 0
    turn_log: list[str] = field(default_factory=list)


class TacticalAI:
    """Rule-based tactical AI demonstrating the full milsim pipeline.

    Decision loop per turn:
      1. Process ISR (update intelligence picture)
      2. Check MSEL events (if scenario loaded)
      3. Assess phase (establish → build → scout → mass → attack)
      4. Issue orders based on phase + situation
      5. Log decisions for AAR

    Replace `decide()` with LLM inference for AI-powered C2.
    """

    def __init__(
        self,
        commander: Commander,
        isr: ISRManager,
        scenario: Optional[ScenarioRunner] = None,
        max_turns: int = 60,
        verbose: bool = True,
    ):
        self._commander = commander
        self._isr = isr
        self._scenario = scenario
        self._max_turns = max_turns
        self._verbose = verbose
        self._state = AIState()
        self._turn = 0

        self._spatial = SpatialIntel()
        self._cnn = TacticalCNN(use_torch=False)
        self._knowledge = GameKnowledge()
        self._memory = TacticalMemory()
        self._assessor = PerformanceAssessor(vector_enabled=True)

    @property
    def phase(self) -> Phase:
        return self._state.phase

    @property
    def turn(self) -> int:
        return self._turn

    async def play_game(self) -> str:
        """Play a complete game and return the AAR."""
        self._memory.reset_events()
        self._assessor.reset()

        briefing = await self._commander.start_game()
        self._log(f"Game started. Map: {briefing.turn_number}")
        self._memory.record_milestone("game_start", 0, f"Map turn {briefing.turn_number}")

        while self._turn < self._max_turns and not self._commander.is_game_over:
            self._turn += 1

            # Get current situation
            briefing = self._commander.get_briefing()

            # Process spatial tensor
            try:
                wego_sit = self._commander._wego.get_situation()
                self._spatial.update(wego_sit.observation)
                self._cnn.update(wego_sit.observation)
                intel = self._isr.process_observation(wego_sit.observation, self._turn)

                # Performance assessment from raw observation
                obs_dict = {
                    "economy": briefing.economy,
                    "military": briefing.military_stats,
                    "done": self._commander.is_game_over,
                    "result": self._commander.game_result or "",
                    "tick": briefing.game_tick,
                }
                self._assessor.evaluate(obs_dict)
            except Exception:
                intel = self._isr.get_summary()

            # Check MSEL events
            if self._scenario:
                events, _ = self._scenario.advance_turn()
                for ev in events:
                    self._log(f"MSEL [{ev.category}]: {ev.event}")
                    self._memory.record_event("msel_event", self._turn,
                                               f"[{ev.category}] {ev.event}")

            # Decide and act
            orders = await self.decide_async(briefing, intel)

            if orders:
                self._log(f"T{self._turn} [{self._state.phase.value}] "
                         f"Orders: {len(orders)}")
                result, briefing = await self._commander.issue_orders(
                    orders, interrupt_on_contact=(self._state.phase == Phase.SCOUT)
                )
            else:
                result, briefing = await self._commander.advance_time()
                self._log(f"T{self._turn} [{self._state.phase.value}] Advancing time")

            # Post-turn assessment
            self._assess_result(result, briefing, intel)

        # Game over
        result_str = self._commander.game_result or "max turns reached"
        self._log(f"Game ended: {result_str}")

        # Save engagement to memory
        self._memory.save_engagement(
            result=result_str,
            turns=self._turn,
            stats={
                "final_phase": self._state.phase.value,
                "kills": briefing.military_stats.get("units_killed", 0),
                "units_lost": briefing.military_stats.get("units_lost", 0),
                "buildings_built": len(briefing.friendly_buildings),
                "final_cash": briefing.economy.get("cash", 0),
                "max_army_value": briefing.military_stats.get("army_value", 0),
                "contacts_detected": self._isr.get_summary().total_contacts,
            },
        )

        # Export bench results for leaderboard submission
        if _HAS_BENCH:
            try:
                wego_sit = self._commander._wego.get_situation()
                export = build_bench_export(
                    wego_sit.observation,
                    agent_name="MilSim-TacticalAI",
                    agent_type="Scripted",
                    opponent="Normal",
                )
                self._log(f"Bench export: {export.get('path', 'n/a')}")
            except Exception:
                pass

        return self.get_aar()

    async def decide_async(
        self, briefing: CommanderBriefing, intel: IntelSummary
    ) -> list[TacticalOrder]:
        """Async decision hook used by `play_game`.

        The rule-based AI just defers to `decide()`; `LLMTacticalAI`
        overrides this to run inference. Subclasses that need I/O should
        override this rather than `decide()`.
        """
        return self.decide(briefing, intel)

    def decide(
        self, briefing: CommanderBriefing, intel: IntelSummary
    ) -> list[TacticalOrder]:
        """Make tactical decisions based on current situation.

        The doctrine (rule-based) decision. `LLMTacticalAI` replaces it
        with LLM inference over the same structured briefing text.
        """
        orders = []

        # Phase transitions
        self._update_phase(briefing, intel)

        match self._state.phase:
            case Phase.ESTABLISH:
                orders = self._decide_establish(briefing)
            case Phase.BUILD:
                orders = self._decide_build(briefing)
            case Phase.SCOUT:
                orders = self._decide_scout(briefing)
            case Phase.MASS:
                orders = self._decide_mass(briefing)
            case Phase.ATTACK:
                orders = self._decide_attack(briefing, intel)
            case Phase.EXPLOIT:
                orders = self._decide_exploit(briefing, intel)

        return orders

    def _update_phase(self, briefing: CommanderBriefing, intel: IntelSummary) -> None:
        """Transition between operational phases."""
        has_cy = any(b["type"] == "fact" for b in briefing.friendly_buildings)
        has_barracks = any(b["type"] in ("tent", "barr") for b in briefing.friendly_buildings)
        unit_count = len(briefing.friendly_units)
        enemy_found = intel.active_contacts > 0

        match self._state.phase:
            case Phase.ESTABLISH:
                if has_cy:
                    self._state.phase = Phase.BUILD
                    self._log("Phase → BUILD (C2 established)")
                    self._memory.record_milestone("c2_established", self._turn)

            case Phase.BUILD:
                if has_barracks and unit_count >= 2:
                    self._state.phase = Phase.SCOUT
                    self._log("Phase → SCOUT (barracks + units ready)")
                    self._memory.record_milestone("scout_phase", self._turn)
                elif has_barracks:
                    self._state.phase = Phase.SCOUT
                    self._memory.record_milestone("scout_phase", self._turn)

            case Phase.SCOUT:
                if enemy_found:
                    self._state.phase = Phase.MASS
                    self._log(f"Phase → MASS (enemy found: {intel.active_contacts} contacts)")
                    self._memory.record_milestone("enemy_located", self._turn,
                                                   f"{intel.active_contacts} contacts")
                elif self._state.scouts_sent >= 2 and unit_count >= 4:
                    self._state.phase = Phase.MASS

            case Phase.MASS:
                if unit_count >= 6 or (enemy_found and unit_count >= 4):
                    self._state.phase = Phase.ATTACK
                    self._log(f"Phase → ATTACK ({unit_count} units ready)")
                    self._memory.record_milestone("attack_phase", self._turn,
                                                   f"{unit_count} units")

            case Phase.ATTACK:
                if self._commander.is_game_over:
                    self._state.phase = Phase.EXPLOIT

    def _decide_establish(self, briefing: CommanderBriefing) -> list[TacticalOrder]:
        """Deploy MCV to establish base."""
        mcv = next((u for u in briefing.friendly_units if u.type == "mcv"), None)
        if mcv:
            self._log("Deploying MCV")
            return [TacticalOrder(order_type=OrderType.DEPLOY, unit_ids=[mcv.actor_id])]
        return []

    def _decide_build(self, briefing: CommanderBriefing) -> list[TacticalOrder]:
        """Build base infrastructure."""
        orders = []
        available = briefing.available_production
        eco = briefing.economy
        building_in_queue = any(
            p["queue_type"] == "Building" for p in briefing.active_production
        )

        # Build next structure in order (only if nothing is building)
        if self._state.build_index < len(BUILD_ORDER) and not building_in_queue:
            target = BUILD_ORDER[self._state.build_index]

            # Handle faction-specific barracks
            if target in ("tent", "barr"):
                target = next((t for t in ("tent", "barr") if t in available), None)

            if target and target in available and eco["cash"] >= 300:
                orders.append(TacticalOrder(
                    order_type=OrderType.BUILD,
                    item_type=target,
                ))
                self._state.build_index += 1
                self._log(f"Building {target}")

        # Train units if barracks available
        has_barracks = any(b["type"] in ("tent", "barr") for b in briefing.friendly_buildings)
        training_in_queue = any(
            p["queue_type"] == "Infantry" for p in briefing.active_production
        )
        if has_barracks and not training_in_queue and "e1" in available and eco["cash"] >= 300:
            orders.append(TacticalOrder(
                order_type=OrderType.TRAIN,
                item_type="e1",
                count=2,
            ))
            self._log("Training 2x infantry")

        return orders

    def _decide_scout(self, briefing: CommanderBriefing) -> list[TacticalOrder]:
        """Send scouts to find the enemy."""
        orders = []
        available = briefing.available_production
        eco = briefing.economy

        # Keep training
        if "e1" in available and eco["cash"] >= 200:
            orders.append(TacticalOrder(
                order_type=OrderType.TRAIN,
                item_type="e1",
                count=2,
            ))

        # Keep building
        if self._state.build_index < len(BUILD_ORDER):
            target = BUILD_ORDER[self._state.build_index]
            if target in ("tent", "barr"):
                target = next((t for t in ("tent", "barr") if t in available), None)
            if target and target in available and eco["cash"] >= 500:
                orders.append(TacticalOrder(
                    order_type=OrderType.BUILD,
                    item_type=target,
                ))
                self._state.build_index += 1

        # Send idle units to scout
        idle_units = [u for u in briefing.friendly_units
                      if u.is_idle and u.type in ("e1", "e3")]

        if idle_units and self._state.scouts_sent < 4:
            scout = idle_units[0]

            # Use spatial intel for unexplored regions
            if self._spatial.has_data:
                exploration = self._spatial.get_exploration()
                if exploration.unexplored_regions:
                    region = exploration.unexplored_regions[
                        self._state.scouts_sent % len(exploration.unexplored_regions)
                    ]
                    target = ((region[0] + region[2]) // 2, (region[1] + region[3]) // 2)
                else:
                    w, h = self._spatial.width, self._spatial.height
                    targets = [(w - 10, h // 2), (10, h // 2), (w // 2, 5), (w // 2, h - 5)]
                    target = targets[self._state.scouts_sent % len(targets)]
            else:
                targets = [(100, 40), (10, 40), (56, 10), (56, 50)]
                target = targets[self._state.scouts_sent % len(targets)]

            orders.append(TacticalOrder(
                order_type=OrderType.RECONNOITER,
                unit_ids=[scout.actor_id],
                target_x=target[0],
                target_y=target[1],
            ))
            self._state.scouts_sent += 1
            self._log(f"Scout #{self._state.scouts_sent} → ({target[0]},{target[1]})")

        return orders

    def _decide_mass(self, briefing: CommanderBriefing) -> list[TacticalOrder]:
        """Build up forces for attack."""
        orders = []
        available = briefing.available_production
        eco = briefing.economy

        # Aggressive production
        if "e1" in available and eco["cash"] >= 200:
            orders.append(TacticalOrder(
                order_type=OrderType.TRAIN,
                item_type="e1",
                count=3,
            ))

        if "e3" in available and eco["cash"] >= 500:
            orders.append(TacticalOrder(
                order_type=OrderType.TRAIN,
                item_type="e3",
                count=1,
            ))

        # Set all idle units to aggressive stance
        idle = [u for u in briefing.friendly_units if u.is_idle]
        if idle:
            orders.append(TacticalOrder(
                order_type=OrderType.SET_STANCE,
                unit_ids=[u.actor_id for u in idle],
                stance=Stance.FREE_FIRE,
            ))

        # Keep building infrastructure
        if self._state.build_index < len(BUILD_ORDER):
            target = BUILD_ORDER[self._state.build_index]
            if target in ("tent", "barr"):
                target = next((t for t in ("tent", "barr") if t in available), None)
            if target and target in available and eco["cash"] >= 500:
                orders.append(TacticalOrder(
                    order_type=OrderType.BUILD,
                    item_type=target,
                ))
                self._state.build_index += 1

        return orders

    def _decide_attack(self, briefing: CommanderBriefing, intel: IntelSummary) -> list[TacticalOrder]:
        """Launch assault on enemy positions."""
        orders = []
        available = briefing.available_production
        eco = briefing.economy

        # Determine attack target
        target_x, target_y = self._get_attack_target(briefing, intel)

        # Smart production: counter enemy composition using game knowledge
        enemy_types = [u.type for u in briefing.known_enemies]
        if enemy_types and self._knowledge.available:
            counters = self._knowledge.get_counter_units(enemy_types)
            for counter_type in counters[:2]:
                if counter_type in available and eco["cash"] >= 200:
                    orders.append(TacticalOrder(
                        order_type=OrderType.TRAIN,
                        item_type=counter_type,
                        count=1,
                    ))
                    break
        elif "e1" in available and eco["cash"] >= 200:
            orders.append(TacticalOrder(
                order_type=OrderType.TRAIN,
                item_type="e1",
                count=2,
            ))

        # Send all combat units to attack
        combat_units = [u for u in briefing.friendly_units
                        if u.type in ("e1", "e3", "1tnk", "3tnk", "4tnk", "arty")]

        if combat_units and target_x > 0:
            if not self._state.attack_launched:
                self._state.attack_launched = True
                force_intel = self._knowledge.format_force_intel(
                    [u.type for u in combat_units]
                ) if self._knowledge.available else ""
                self._log(f"ASSAULT launched → ({target_x},{target_y}) "
                         f"with {len(combat_units)} units")
                if force_intel:
                    self._log(f"  {force_intel.split(chr(10))[1].strip()}")
                self._memory.record_event("assault_launched", self._turn,
                                           f"{len(combat_units)} units → ({target_x},{target_y})")

            orders.append(TacticalOrder(
                order_type=OrderType.ASSAULT,
                unit_ids=[u.actor_id for u in combat_units],
                target_x=target_x,
                target_y=target_y,
            ))

        return orders

    def _decide_exploit(self, briefing: CommanderBriefing, intel: IntelSummary) -> list[TacticalOrder]:
        """Exploit success or consolidate."""
        return []

    def _get_attack_target(self, briefing: CommanderBriefing, intel: IntelSummary) -> tuple[int, int]:
        """Determine where to attack based on CNN priorities + ISR + spatial intel."""
        # CNN attack priority scoring (considers value, approach, risk)
        if self._cnn.has_data:
            priorities = self._cnn.get_attack_priorities()
            if priorities:
                top = priorities[0]
                self._state.enemy_base_x = top.x
                self._state.enemy_base_y = top.y
                return top.x, top.y

        # Fall back to spatial threat assessment
        if self._spatial.has_data:
            threat = self._spatial.get_threat_assessment()
            if threat.total_visible_enemies > 0:
                self._state.enemy_base_x = threat.enemy_centroid[0]
                self._state.enemy_base_y = threat.enemy_centroid[1]
                return threat.enemy_centroid

        # Fall back to ISR contacts
        contacts = self._isr.active_contacts
        if contacts:
            avg_x = sum(c.cell_x for c in contacts) // len(contacts)
            avg_y = sum(c.cell_y for c in contacts) // len(contacts)
            self._state.enemy_base_x = avg_x
            self._state.enemy_base_y = avg_y
            return avg_x, avg_y

        # Use known enemy buildings
        if briefing.known_enemy_buildings:
            bldg = briefing.known_enemy_buildings[0]
            return bldg.get("cell_x", 0), bldg.get("cell_y", 0)

        # Last known position
        if self._state.enemy_base_x > 0:
            return self._state.enemy_base_x, self._state.enemy_base_y

        # Map center fallback
        if self._spatial.has_data:
            return self._spatial.width // 2, self._spatial.height // 2
        return 56, 27

    def _assess_result(self, result: TurnResult, briefing: CommanderBriefing,
                       intel: IntelSummary) -> None:
        """Post-turn assessment."""
        bda = result.bda
        unit_count = len(briefing.friendly_units)

        if bda.units_killed_this_turn > 0:
            self._log(f"  BDA: +{bda.units_killed_this_turn} kills")
            self._memory.record_event("engagement", self._turn,
                                       f"+{bda.units_killed_this_turn} kills")
        if bda.units_lost_this_turn > 0:
            self._log(f"  BDA: -{bda.units_lost_this_turn} losses")
            self._memory.record_event("casualty", self._turn,
                                       f"-{bda.units_lost_this_turn} losses")
        if bda.new_enemy_contacts > 0:
            self._log(f"  ISR: {bda.new_enemy_contacts} new contacts")
            self._memory.record_milestone("first_contact", self._turn,
                                           f"{bda.new_enemy_contacts} contacts")

        # Track idle turns for phase transitions
        if unit_count == self._state.last_unit_count:
            self._state.consecutive_idle_turns += 1
        else:
            self._state.consecutive_idle_turns = 0
        self._state.last_unit_count = unit_count

    def _log(self, msg: str) -> None:
        self._state.turn_log.append(f"T{self._turn:3d}: {msg}")
        if self._verbose:
            print(f"  AI T{self._turn:3d}: {msg}")

    def get_aar(self) -> str:
        """Generate AI decision AAR."""
        lines = [
            "=== TACTICAL AI — AFTER ACTION REVIEW ===",
            f"Turns played: {self._turn}",
            f"Final phase: {self._state.phase.value}",
            f"Game result: {self._commander.game_result or 'ongoing'}",
            f"Scouts sent: {self._state.scouts_sent}",
            f"Attack launched: {self._state.attack_launched}",
            "",
            "DECISION LOG:",
        ]
        lines.extend(self._state.turn_log)

        # Add commander AAR
        lines.extend(["", self._commander.get_aar()])

        # Add intel report
        lines.extend(["", self._isr.format_intel_report()])

        # Add spatial intelligence
        if self._spatial.has_data:
            lines.extend(["", self._spatial.format_spatial_sitrep()])

        # Add performance assessment
        perf_summary = self._assessor.format_summary()
        if perf_summary and "No performance" not in perf_summary:
            lines.extend(["", perf_summary])

        # Add event timeline from memory
        timeline = self._memory.get_timeline()
        if timeline and "No events" not in timeline:
            lines.extend(["", timeline])

        # Add scenario AAR if available
        if self._scenario:
            lines.extend(["", self._scenario.get_exercise_aar()])

        return "\n".join(lines)


# ── LLM-driven commander ──────────────────────────────────────────

LLM_SYSTEM_PROMPT = """You are a battalion commander playing Command & Conquer: Red Alert.

You receive a structured briefing each turn and reply with orders — one order
per line, using EXACTLY the order syntax in the briefing. No prose, no
markdown, no code fences, no explanations. Reply WAIT if nothing should be done.

Doctrine:
1. ESTABLISH — deploy the MCV immediately to create the construction yard
2. BUILD — power plant, barracks, refinery, war factory, then more power
3. SCOUT — send one or two cheap infantry to find the enemy base
4. MASS — build a mixed force before committing to an attack
5. ATTACK — attack-move the whole force onto the enemy position
6. ADAPT — counter enemy composition, replace losses, exploit success

Never queue a duplicate of something already in production. Keep power positive."""


class LLMTacticalAI(TacticalAI):
    """`TacticalAI` with `decide()` replaced by LLM inference.

    The LLM sees the same structured briefing the rule-based AI sees, and
    answers in the same order syntax the structured-text commander parses.

    Provider comes from `milsim.llm_provider` — by default a local Ollama
    daemon at $OLLAMA_HOST (http://localhost:11434) with no API key. If the
    daemon is unreachable or the model is not pulled, `play_game()` raises
    `LLMUnavailableError` with the command that fixes it, before turn 1.

    Args:
        llm: an LLMClient; omit to build one from config/environment.
        fallback_to_doctrine: if the LLM fails *mid-game* (or returns
            nothing parseable), fall back to the rule-based decision for
            that turn instead of aborting the run. The pre-flight
            reachability check always raises regardless of this flag.
    """

    def __init__(
        self,
        commander: Commander,
        isr: ISRManager,
        scenario: Optional[ScenarioRunner] = None,
        max_turns: int = 60,
        verbose: bool = True,
        llm: Optional[LLMClient] = None,
        system_prompt: str = "",
        fallback_to_doctrine: bool = True,
    ):
        super().__init__(
            commander=commander,
            isr=isr,
            scenario=scenario,
            max_turns=max_turns,
            verbose=verbose,
        )
        self._llm = llm
        self._system_prompt = system_prompt or LLM_SYSTEM_PROMPT
        self._fallback_to_doctrine = fallback_to_doctrine
        self._llm_turns = 0
        self._llm_failures = 0
        self._phase_updated_turn = -1

    @property
    def llm(self) -> LLMClient:
        """The LLM client, defaulting to local Ollama on first use."""
        if self._llm is None:
            self._llm = build_llm(verbose=False)
        return self._llm

    async def play_game(self) -> str:
        """Check the provider is reachable, then play as usual."""
        llm = self.llm
        self._log(f"LLM commander: {llm.config.describe()}")
        if self._verbose:
            print(f"MilSim — LLM tactical AI via {llm.config.describe()}")
        # Raises LLMUnavailableError with an actionable fix if the daemon
        # is down or the model is not installed.
        await llm.ensure_available()
        return await super().play_game()

    def build_prompt(self, briefing: CommanderBriefing, intel: IntelSummary) -> str:
        """The user-turn text: briefing + intel + phase + order syntax."""
        from milsim.llm_commander import ORDER_SCHEMA

        parts = [self._commander.format_briefing_text(briefing)]

        intel_report = self._isr.format_intel_report()
        if intel_report:
            parts.append(intel_report)

        parts.append(
            f"OPERATIONAL PHASE: {self._state.phase.value.upper()} "
            f"(turn {self._turn} of {self._max_turns}, "
            f"{intel.active_contacts} active enemy contacts)"
        )
        if self._spatial.has_data:
            parts.append(self._spatial.format_spatial_sitrep())

        parts.append(ORDER_SCHEMA)
        return "\n\n".join(p for p in parts if p)

    async def decide_async(
        self, briefing: CommanderBriefing, intel: IntelSummary
    ) -> list[TacticalOrder]:
        """Ask the LLM for this turn's orders, in the shared order syntax."""
        from milsim.llm_commander import StructuredTextCommander

        # Keep phase tracking alive so prompts and the AAR stay meaningful.
        # (Guarded below so a doctrine fallback cannot double-advance it.)
        self._update_phase(briefing, intel)

        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": self.build_prompt(briefing, intel)},
        ]

        try:
            response = await self.llm.complete_text(messages)
        except LLMError as exc:
            self._llm_failures += 1
            self._log(f"LLM call failed: {exc}")
            if not self._fallback_to_doctrine:
                raise
            self._log("Falling back to doctrine for this turn.")
            return self.decide(briefing, intel)

        orders = StructuredTextCommander.parse_orders(response)
        self._llm_turns += 1

        if not orders:
            preview = " ".join(response.split())[:120]
            self._log(f"LLM issued no parseable orders ({preview!r})")
            if self._fallback_to_doctrine:
                return self.decide(briefing, intel)
        else:
            self._log(f"LLM orders: {len(orders)} ({self.llm.config.model})")

        return orders

    def _update_phase(self, briefing: CommanderBriefing, intel: IntelSummary) -> None:
        """Advance the phase state machine at most once per turn.

        `decide_async` needs the current phase for the prompt, and the
        doctrine fallback calls `decide()` which updates it again — without
        this guard a turn could skip a phase.
        """
        if self._phase_updated_turn == self._turn:
            return
        self._phase_updated_turn = self._turn
        super()._update_phase(briefing, intel)

    def get_aar(self) -> str:
        provider = self._llm.config.describe() if self._llm else "(not initialized)"
        header = [
            "=== LLM COMMANDER ===",
            f"Provider: {provider}",
            f"LLM turns: {self._llm_turns} | failed calls: {self._llm_failures}",
            f"Doctrine fallback: {'on' if self._fallback_to_doctrine else 'off'}",
            "",
        ]
        return "\n".join(header) + super().get_aar()
