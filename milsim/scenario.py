"""Scenario runner for military simulation exercises.

Loads YAML scenario definitions and runs them through the
Commander + WEGO + ISR pipeline with:
  - Unit type mapping (military → OpenRA types)
  - MSEL (Master Scenario Events List) injection
  - Objective tracking and scoring
  - Force preservation monitoring

Usage:
    runner = ScenarioRunner(commander, isr)
    scenario = runner.load("milsim/mod/scenarios/hasty_attack.yaml")
    runner.brief()
    while not runner.is_complete:
        orders = decide(runner.get_situation())
        runner.execute_turn(orders)
    print(runner.score())
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import yaml

from milsim.commander import Commander, TacticalOrder, OrderType, CommanderBriefing
from milsim.isr import ISRManager, IntelSummary
from milsim.wego import TurnResult


# Mapping from scenario military unit types to OpenRA Red Alert types
UNIT_TYPE_MAP = {
    # US/NATO
    "USRifleSquad": "e1",
    "USWeaponsSquad": "e3",
    "USJavelinTeam": "e3",
    "USM2A3Bradley": "apc",
    "USM1A2Abrams": "3tnk",
    "USM120Mortar": "arty",
    "USFMTV": "truk",
    "USM113Ambulance": "truk",
    "USEngineerSquad": "e6",
    "USStryker": "apc",
    "USM270MLRS": "v2rl",
    "USJavelin": "e3",
    "USAH64Apache": "heli",

    # Russian/OPFOR
    "RUMotorRifleSquad": "e1",
    "RUPKMTeam": "e3",
    "RUKornetTeam": "e3",
    "RU2B11Mortar": "arty",
    "RUBMP3": "apc",
    "RUT72B3": "4tnk",
    "RUT90": "4tnk",
    "RU2S6Tunguska": "jeep",
    "RU9K37Buk": "v2rl",
    "RUSniperTeam": "e1",
    "RUEngineerSquad": "e6",

    # Iranian/Hybrid
    "IRRifleSquad": "e1",
    "IRRPGTeam": "e3",
    "IRFAGOTTeam": "e3",
    "IRTechnical": "jeep",
    "IRT72S": "4tnk",
    "IRZulfiqar": "3tnk",
    "IRBoghammer": "pt",
    "IRC802Launcher": "v2rl",

    # Generic
    "Infantry": "e1",
    "Armor": "3tnk",
    "Artillery": "arty",
    "APC": "apc",
    "Helicopter": "heli",
    "Truck": "truk",
}

STANCE_MAP = {
    "move": "advance",
    "overwatch": "defend",
    "defend": "defend",
    "attack": "assault",
    "retreat": "withdraw",
}


class ObjectiveStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    ACHIEVED = "achieved"
    FAILED = "failed"


@dataclass
class ScenarioObjective:
    """A trackable scenario objective."""

    name: str
    type: str
    location: tuple[int, int]
    points: int
    hold_turns: int
    description: str
    status: ObjectiveStatus = ObjectiveStatus.PENDING
    turns_held: int = 0


@dataclass
class MSELEvent:
    """A scripted scenario event from the Master Scenario Events List."""

    turn: int
    category: str
    event: str
    radio_message: str
    effect: str
    triggered: bool = False


@dataclass
class ScenarioUnit:
    """A unit defined in the scenario OOB."""

    type_code: str
    openra_type: str
    designation: str
    location: tuple[int, int]
    strength: int = 100
    stance: str = "defend"
    concealed: bool = False
    entrenched: bool = False
    actor_id: Optional[int] = None


@dataclass
class ScenarioState:
    """Current state of a running scenario."""

    name: str
    description: str
    current_turn: int = 0
    max_turns: int = 16
    blufor_units: list[ScenarioUnit] = field(default_factory=list)
    opfor_units: list[ScenarioUnit] = field(default_factory=list)
    objectives: list[ScenarioObjective] = field(default_factory=list)
    msel: list[MSELEvent] = field(default_factory=list)
    events_this_turn: list[MSELEvent] = field(default_factory=list)
    score: int = 0
    initial_blufor_value: int = 0
    scoring_rubric: dict = field(default_factory=dict)
    instructor_notes: str = ""
    opfor_doctrine: str = ""


class ScenarioRunner:
    """Runs a military scenario through the Commander + WEGO + ISR pipeline.

    Handles:
      1. Loading scenario YAML and mapping unit types
      2. Injecting MSEL events at the right turns
      3. Tracking objectives (seize, preserve_force)
      4. Computing score based on rubric
      5. Generating exercise AAR
    """

    def __init__(
        self,
        commander: Commander,
        isr: ISRManager,
    ):
        self._commander = commander
        self._isr = isr
        self._state: Optional[ScenarioState] = None

    @property
    def is_complete(self) -> bool:
        if self._state is None:
            return True
        if self._state.current_turn >= self._state.max_turns:
            return True
        return self._commander.is_game_over

    @property
    def current_turn(self) -> int:
        return self._state.current_turn if self._state else 0

    def load(self, path: str) -> ScenarioState:
        """Load a scenario from a YAML file."""
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        scenario = data.get("Scenario", data)

        state = ScenarioState(
            name=scenario.get("Name", "Unnamed"),
            description=scenario.get("Description", ""),
            max_turns=scenario.get("Duration", 16),
        )

        # Load BLUFOR units
        for unit_data in scenario.get("BLUFOR", {}).get("Units", []):
            type_code = unit_data.get("Type", "Infantry")
            openra_type = UNIT_TYPE_MAP.get(type_code, "e1")
            loc = unit_data.get("Location", [0, 0])
            state.blufor_units.append(ScenarioUnit(
                type_code=type_code,
                openra_type=openra_type,
                designation=unit_data.get("Designation", ""),
                location=(loc[0], loc[1]),
                strength=unit_data.get("Strength", 100),
                stance=unit_data.get("Stance", "defend"),
            ))

        # Load OPFOR units
        for unit_data in scenario.get("OPFOR", {}).get("Units", []):
            type_code = unit_data.get("Type", "Infantry")
            openra_type = UNIT_TYPE_MAP.get(type_code, "e1")
            loc = unit_data.get("Location", [0, 0])
            state.opfor_units.append(ScenarioUnit(
                type_code=type_code,
                openra_type=openra_type,
                designation=unit_data.get("Designation", ""),
                location=(loc[0], loc[1]),
                strength=unit_data.get("Strength", 100),
                stance=unit_data.get("Stance", "defend"),
                concealed=unit_data.get("Concealed", False),
                entrenched=unit_data.get("Entrenched", False),
            ))

        state.opfor_doctrine = scenario.get("OPFOR", {}).get("Doctrine", "")

        # Load objectives
        for obj_data in scenario.get("Objectives", []):
            loc = obj_data.get("Location", [0, 0])
            state.objectives.append(ScenarioObjective(
                name=obj_data.get("Name", ""),
                type=obj_data.get("Type", "seize"),
                location=(loc[0], loc[1]),
                points=obj_data.get("Points", 0),
                hold_turns=obj_data.get("HoldTurns", 0),
                description=obj_data.get("Description", ""),
            ))

        # Load MSEL
        for event_data in scenario.get("MSEL", []):
            state.msel.append(MSELEvent(
                turn=event_data.get("Turn", 0),
                category=event_data.get("Category", ""),
                event=event_data.get("Event", ""),
                radio_message=event_data.get("RadioMessage", ""),
                effect=event_data.get("Effect", ""),
            ))

        # Load scoring
        state.scoring_rubric = scenario.get("ScoringRubric", {})
        state.instructor_notes = scenario.get("InstructorNotes", "")

        # Calculate initial force value
        state.initial_blufor_value = len(state.blufor_units) * 100

        self._state = state
        return state

    def get_briefing(self) -> str:
        """Generate the scenario briefing for the commander."""
        s = self._state
        if not s:
            return "No scenario loaded."

        lines = [
            f"{'=' * 60}",
            f"OPERATION ORDER",
            f"{'=' * 60}",
            f"",
            f"SCENARIO: {s.name}",
            f"DURATION: {s.max_turns} turns",
            f"",
            f"1. SITUATION",
            f"   {s.description}",
            f"",
            f"2. FRIENDLY FORCES ({len(s.blufor_units)} elements)",
        ]

        for u in s.blufor_units:
            lines.append(
                f"   - {u.designation:30s} ({u.type_code} → {u.openra_type}) "
                f"at ({u.location[0]},{u.location[1]}) {u.stance}"
            )

        lines.extend([
            f"",
            f"3. ENEMY FORCES ({len(s.opfor_units)} elements)",
            f"   Doctrine: {s.opfor_doctrine}",
        ])

        for u in s.opfor_units:
            concealment = " [CONCEALED]" if u.concealed else ""
            entrenchment = " [ENTRENCHED]" if u.entrenched else ""
            lines.append(
                f"   - {u.designation:30s} ({u.type_code} → {u.openra_type}) "
                f"at ({u.location[0]},{u.location[1]}){concealment}{entrenchment}"
            )

        lines.extend([
            f"",
            f"4. OBJECTIVES",
        ])
        for obj in s.objectives:
            lines.append(
                f"   - {obj.name}: {obj.description} "
                f"[{obj.type}, {obj.points}pts, hold {obj.hold_turns} turns]"
            )

        lines.extend([
            f"",
            f"5. MSEL ({len(s.msel)} scripted events)",
        ])
        for ev in s.msel:
            lines.append(f"   Turn {ev.turn:2d}: [{ev.category}] {ev.event}")

        return "\n".join(lines)

    def advance_turn(self) -> tuple[list[MSELEvent], str]:
        """Advance the scenario turn counter and trigger MSEL events.

        Call this at the start of each WEGO turn's planning phase.

        Returns:
            (list of triggered events, updated status text)
        """
        if not self._state:
            return [], ""

        self._state.current_turn += 1
        turn = self._state.current_turn

        # Trigger MSEL events for this turn
        events = []
        for ev in self._state.msel:
            if ev.turn == turn and not ev.triggered:
                ev.triggered = True
                events.append(ev)

        self._state.events_this_turn = events

        # Update objective status
        self._update_objectives()

        status = (
            f"Turn {turn}/{self._state.max_turns} | "
            f"Score: {self._compute_score()} | "
            f"Events: {len(events)}"
        )

        return events, status

    def _update_objectives(self) -> None:
        """Check objective conditions against current game state."""
        briefing = self._commander.get_briefing()

        for obj in self._state.objectives:
            if obj.status == ObjectiveStatus.ACHIEVED:
                continue

            if obj.type == "seize":
                # Check if friendly units are at the objective location
                near_obj = any(
                    abs(u.cell_x - obj.location[0]) <= 3 and
                    abs(u.cell_y - obj.location[1]) <= 3
                    for u in briefing.friendly_units
                )
                if near_obj:
                    if obj.status != ObjectiveStatus.IN_PROGRESS:
                        obj.status = ObjectiveStatus.IN_PROGRESS
                        obj.turns_held = 0
                    obj.turns_held += 1
                    if obj.turns_held >= obj.hold_turns:
                        obj.status = ObjectiveStatus.ACHIEVED
                else:
                    if obj.status == ObjectiveStatus.IN_PROGRESS:
                        obj.turns_held = 0
                        obj.status = ObjectiveStatus.PENDING

            elif obj.type == "preserve_force":
                current_units = len(briefing.friendly_units)
                initial = len(self._state.blufor_units)
                preservation = current_units / max(initial, 1)
                if preservation >= 0.7:
                    obj.status = ObjectiveStatus.ACHIEVED
                elif preservation < 0.5:
                    obj.status = ObjectiveStatus.FAILED
                else:
                    obj.status = ObjectiveStatus.IN_PROGRESS

    def _compute_score(self) -> int:
        """Compute current score from achieved objectives."""
        return sum(
            obj.points for obj in self._state.objectives
            if obj.status == ObjectiveStatus.ACHIEVED
        )

    def get_status(self) -> str:
        """Get current scenario status."""
        s = self._state
        if not s:
            return "No scenario loaded."

        lines = [
            f"=== {s.name} — Turn {s.current_turn}/{s.max_turns} ===",
            f"Score: {self._compute_score()}/{sum(o.points for o in s.objectives)}",
            "",
            "OBJECTIVES:",
        ]

        for obj in s.objectives:
            status_icon = {
                ObjectiveStatus.PENDING: "[ ]",
                ObjectiveStatus.IN_PROGRESS: "[~]",
                ObjectiveStatus.ACHIEVED: "[X]",
                ObjectiveStatus.FAILED: "[!]",
            }[obj.status]
            held = f" (held {obj.turns_held}/{obj.hold_turns})" if obj.hold_turns > 0 else ""
            lines.append(f"  {status_icon} {obj.name} ({obj.points}pts){held}")

        if s.events_this_turn:
            lines.append("\nEVENTS THIS TURN:")
            for ev in s.events_this_turn:
                lines.append(f"  [{ev.category}] {ev.event}")
                if ev.radio_message.strip():
                    for radio_line in ev.radio_message.strip().split("\n"):
                        lines.append(f"    RADIO: {radio_line.strip()}")

        return "\n".join(lines)

    def get_exercise_aar(self) -> str:
        """Generate exercise-level After Action Review."""
        s = self._state
        if not s:
            return "No scenario loaded."

        score = self._compute_score()
        max_score = sum(o.points for o in s.objectives)

        # Determine result category
        pct = score / max(max_score, 1)
        result = "DECISIVE DEFEAT"
        if pct >= 0.9:
            result = "DECISIVE VICTORY"
        elif pct >= 0.7:
            result = "TACTICAL VICTORY"
        elif pct >= 0.5:
            result = "DRAW"
        elif pct >= 0.3:
            result = "TACTICAL DEFEAT"

        lines = [
            "=" * 60,
            f"EXERCISE AFTER ACTION REVIEW",
            "=" * 60,
            f"Scenario: {s.name}",
            f"Turns played: {s.current_turn}/{s.max_turns}",
            f"Score: {score}/{max_score} ({pct:.0%})",
            f"Result: {result}",
            "",
            "OBJECTIVES:",
        ]

        for obj in s.objectives:
            lines.append(f"  [{obj.status.value:12s}] {obj.name} — {obj.description}")

        # MSEL review
        triggered = [ev for ev in s.msel if ev.triggered]
        untriggered = [ev for ev in s.msel if not ev.triggered]
        lines.extend([
            "",
            f"SCENARIO EVENTS: {len(triggered)}/{len(s.msel)} triggered",
        ])
        for ev in triggered:
            lines.append(f"  Turn {ev.turn}: {ev.event}")

        if untriggered:
            lines.append(f"\n  ({len(untriggered)} events not reached)")

        # Intel summary
        intel = self._isr.get_summary()
        lines.extend([
            "",
            "INTELLIGENCE SUMMARY:",
            f"  Total contacts tracked: {intel.total_contacts}",
            f"  Active: {intel.active_contacts} | Stale: {intel.stale_contacts} | KIA: {intel.destroyed_contacts}",
            f"  Estimated enemy strength: ${intel.estimated_enemy_strength}",
        ])

        # Game AAR
        lines.extend([
            "",
            "GAME ENGINE AAR:",
            self._commander.get_aar(),
        ])

        if s.instructor_notes:
            lines.extend([
                "",
                "INSTRUCTOR NOTES:",
                s.instructor_notes,
            ])

        return "\n".join(lines)

    def format_msel_event(self, event: MSELEvent) -> str:
        """Format a single MSEL event as a radio message."""
        lines = [
            f"--- [{event.category.upper()}] Turn {event.turn} ---",
            event.event,
        ]
        if event.radio_message.strip():
            lines.append("")
            lines.append(event.radio_message.strip())
        return "\n".join(lines)
