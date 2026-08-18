"""Cross-game tactical memory for the milsim AI.

Wraps OpenRA-RL's EventTracker and GameMemory with milsim-domain
event categories and tactical lesson extraction. The AI learns
from each engagement and carries lessons forward.

Usage:
    from milsim.memory import TacticalMemory
    mem = TacticalMemory()
    mem.record_event("contact", turn=5, detail="3x infantry at (45,20)")
    mem.record_milestone("first_contact", turn=5)
    context = mem.get_mission_context()  # inject into LLM briefing
    mem.save_engagement(result="won", turns=40, stats={...})
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    from openra_env.memory import EventTracker, GameMemory
    _HAS_MEMORY = True
except ImportError:
    _HAS_MEMORY = False


MILSIM_EVENT_TYPES = frozenset([
    "contact", "engagement", "casualty", "building_lost",
    "building_built", "unit_produced", "phase_change",
    "msel_event", "objective_update", "recon_complete",
    "assault_launched", "defense_established", "withdrawal",
    "resupply", "reinforcement", "intelligence_update",
])


@dataclass
class EngagementRecord:
    """Record of a single game engagement for post-game review."""
    result: str = ""
    turns_played: int = 0
    final_phase: str = ""
    units_produced: int = 0
    units_lost: int = 0
    kills: int = 0
    buildings_built: int = 0
    buildings_lost: int = 0
    final_cash: int = 0
    max_army_value: int = 0
    contacts_detected: int = 0
    msel_events_triggered: int = 0
    objectives_achieved: int = 0
    reflection: str = ""
    lessons: list[str] = field(default_factory=list)


class TacticalMemory:
    """Persistent tactical memory across engagements.

    Tracks in-game events during play and stores post-game reflections
    for lesson learning. Provides context injection for LLM briefings.
    """

    def __init__(self, memory_dir: Optional[str] = None):
        self._events: list[dict] = []
        self._milestones: set[str] = set()
        self._engagement_records: list[EngagementRecord] = []

        if _HAS_MEMORY:
            self._tracker = EventTracker()
            dir_path = Path(memory_dir) if memory_dir else Path.home() / ".milsim" / "memory"
            dir_path.mkdir(parents=True, exist_ok=True)
            self._game_memory = GameMemory(dir_path)
        else:
            self._tracker = None
            self._game_memory = None

    @property
    def event_count(self) -> int:
        return len(self._events)

    @property
    def engagement_count(self) -> int:
        if self._game_memory and _HAS_MEMORY:
            return self._game_memory.episode_count
        return len(self._engagement_records)

    def record_event(self, event_type: str, turn: int, detail: str = "") -> None:
        """Record a tactical event."""
        self._events.append({
            "type": event_type,
            "turn": turn,
            "detail": detail,
        })

        if self._tracker:
            self._tracker.record(event_type, turn, detail)

    def record_milestone(self, milestone: str, turn: int, detail: str = "") -> None:
        """Record a first-time milestone event."""
        if milestone in self._milestones:
            return
        self._milestones.add(milestone)

        self._events.append({
            "type": "milestone",
            "milestone": milestone,
            "turn": turn,
            "detail": detail,
        })

        if self._tracker:
            self._tracker.record_milestone(milestone, turn, detail)

    def update_from_briefing(self, briefing, turn: int) -> None:
        """Auto-detect events from a CommanderBriefing."""
        if not self._tracker:
            return

        state = {
            "funds": briefing.economy.get("cash", 0),
            "power": briefing.economy.get("power_surplus", 0),
            "buildings": [b["type"] for b in briefing.friendly_buildings],
            "units": [u.type for u in briefing.friendly_units],
            "enemies_visible": len(briefing.known_enemies),
        }
        self._tracker.update_from_state(state)

    def get_timeline(self, max_events: int = 30) -> str:
        """Format event timeline for AAR."""
        if self._tracker:
            return self._tracker.format_timeline(max_events)

        if not self._events:
            return "No events recorded."

        lines = ["TACTICAL EVENT TIMELINE", "=" * 40]
        for ev in self._events[-max_events:]:
            prefix = f"T{ev['turn']:3d}"
            if ev.get("milestone"):
                lines.append(f"{prefix} * {ev['milestone']}: {ev['detail']}")
            else:
                lines.append(f"{prefix} [{ev['type']}] {ev['detail']}")

        return "\n".join(lines)

    def save_engagement(
        self,
        result: str,
        turns: int,
        stats: dict,
        reflection: str = "",
        lessons: Optional[list[str]] = None,
    ) -> None:
        """Save a completed engagement to persistent memory."""
        record = EngagementRecord(
            result=result,
            turns_played=turns,
            final_phase=stats.get("final_phase", ""),
            units_produced=stats.get("units_produced", 0),
            units_lost=stats.get("units_lost", 0),
            kills=stats.get("kills", 0),
            buildings_built=stats.get("buildings_built", 0),
            buildings_lost=stats.get("buildings_lost", 0),
            final_cash=stats.get("final_cash", 0),
            max_army_value=stats.get("max_army_value", 0),
            contacts_detected=stats.get("contacts_detected", 0),
            msel_events_triggered=stats.get("msel_events_triggered", 0),
            objectives_achieved=stats.get("objectives_achieved", 0),
            reflection=reflection,
            lessons=lessons or [],
        )
        self._engagement_records.append(record)

        if self._game_memory:
            event_timeline = self.get_timeline()
            self._game_memory.add_episode(
                result=result,
                ticks=turns * 75,
                faction=stats.get("faction", "allies"),
                opponent=stats.get("opponent", "normal"),
                stats={
                    "kills": record.kills,
                    "losses": record.units_lost,
                    "buildings_built": record.buildings_built,
                    "army_value": record.max_army_value,
                },
                reflection=reflection,
                lessons=lessons or [],
                events=event_timeline,
            )

    def get_mission_context(self, max_entries: int = 5) -> str:
        """Get context from past engagements for LLM briefing injection."""
        if self._game_memory:
            return self._game_memory.get_context(max_entries)

        if not self._engagement_records:
            return "No prior engagement data available."

        lines = ["PRIOR ENGAGEMENT CONTEXT", "=" * 40]
        recent = self._engagement_records[-max_entries:]
        wins = sum(1 for r in self._engagement_records if r.result == "won")
        total = len(self._engagement_records)
        lines.append(f"Record: {wins}W / {total - wins}L ({wins/max(total,1):.0%} win rate)")
        lines.append("")

        for i, rec in enumerate(recent):
            lines.append(f"Engagement {i+1}: {rec.result} in {rec.turns_played} turns")
            lines.append(f"  Phase: {rec.final_phase}, Kills: {rec.kills}, Losses: {rec.units_lost}")
            if rec.lessons:
                for lesson in rec.lessons[:2]:
                    lines.append(f"  Lesson: {lesson}")
            lines.append("")

        return "\n".join(lines)

    def get_win_rate(self) -> float:
        """Get historical win rate."""
        if self._game_memory and _HAS_MEMORY:
            return self._game_memory.win_rate

        if not self._engagement_records:
            return 0.0
        wins = sum(1 for r in self._engagement_records if r.result == "won")
        return wins / len(self._engagement_records)

    def reset_events(self) -> None:
        """Clear event tracking for a new engagement."""
        self._events.clear()
        self._milestones.clear()
        if self._tracker:
            self._tracker = EventTracker()

    def format_engagement_aar(self, record: Optional[EngagementRecord] = None) -> str:
        """Format an engagement record as an AAR."""
        rec = record or (self._engagement_records[-1] if self._engagement_records else None)
        if not rec:
            return "No engagement record available."

        lines = [
            "ENGAGEMENT AFTER ACTION REVIEW",
            "=" * 40,
            f"Result: {rec.result}",
            f"Turns: {rec.turns_played}",
            f"Final phase: {rec.final_phase}",
            "",
            "COMBAT:",
            f"  Kills: {rec.kills}",
            f"  Losses: {rec.units_lost}",
            f"  K/D: {rec.kills / max(rec.units_lost, 1):.1f}",
            "",
            "PRODUCTION:",
            f"  Units produced: {rec.units_produced}",
            f"  Buildings built: {rec.buildings_built}",
            f"  Buildings lost: {rec.buildings_lost}",
            f"  Final cash: ${rec.final_cash}",
            f"  Max army value: ${rec.max_army_value}",
            "",
            "INTELLIGENCE:",
            f"  Contacts detected: {rec.contacts_detected}",
            f"  MSEL events: {rec.msel_events_triggered}",
            f"  Objectives achieved: {rec.objectives_achieved}",
        ]

        if rec.reflection:
            lines.extend(["", "REFLECTION:", rec.reflection])

        if rec.lessons:
            lines.extend(["", "LESSONS LEARNED:"])
            for i, lesson in enumerate(rec.lessons, 1):
                lines.append(f"  {i}. {lesson}")

        return "\n".join(lines)
