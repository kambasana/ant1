"""Game knowledge layer wrapping OpenRA-RL's game_data and opponent_intel.

Provides milsim-domain access to unit statistics, tech trees, faction
info, and opponent intelligence profiles. Enriches ISR classification
and scenario OPFOR modeling with actual game mechanics data.

Usage:
    from milsim.game_knowledge import GameKnowledge
    gk = GameKnowledge()
    stats = gk.get_unit_stats("3tnk")
    threat = gk.assess_force_strength(["e1", "e1", "e3", "3tnk"])
    opfor = gk.get_opfor_briefing("normal")
"""

from dataclasses import dataclass, field
from typing import Optional

try:
    from openra_env.game_data import (
        get_unit_stats,
        get_building_stats,
        get_tech_tree,
        get_faction_info,
        get_all_units_for_side,
        get_all_buildings_for_side,
        RA_UNITS,
        RA_BUILDINGS,
    )
    from openra_env.opponent_intel import (
        get_opponent_profile,
        get_opponent_summary,
    )
    _HAS_GAME_DATA = True
except ImportError:
    _HAS_GAME_DATA = False


UNIT_CATEGORY = {
    "infantry": frozenset([
        "e1", "e2", "e3", "e4", "e6", "e7", "spy", "medi", "dog",
        "shok", "thf", "gnrl", "chan", "tany",
    ]),
    "vehicle": frozenset([
        "1tnk", "2tnk", "3tnk", "4tnk", "apc", "arty", "v2rl",
        "harv", "mcv", "mnly", "ctnk", "ttnk", "dtrk", "stnk",
        "jeep", "ftrk",
    ]),
    "aircraft": frozenset(["heli", "hind", "mig", "yak", "tran"]),
    "naval": frozenset(["dd", "ca", "sub", "msub", "pt", "lst"]),
}

THREAT_VALUE = {
    "e1": 1, "e2": 2, "e3": 4, "e4": 3, "e6": 2, "e7": 2,
    "dog": 1, "spy": 0, "medi": 0, "shok": 5, "tany": 6,
    "1tnk": 4, "2tnk": 5, "3tnk": 8, "4tnk": 10, "apc": 3,
    "arty": 5, "v2rl": 6, "harv": 1, "mcv": 2, "mnly": 2,
    "ctnk": 7, "ttnk": 6, "dtrk": 3, "stnk": 6,
    "heli": 6, "hind": 7, "mig": 8, "yak": 5,
    "dd": 6, "ca": 8, "sub": 7, "msub": 9,
}


@dataclass
class UnitProfile:
    """Enriched unit information for tactical assessment."""
    type_code: str
    name: str = ""
    category: str = "unknown"
    cost: int = 0
    hp: int = 0
    speed: int = 0
    armor: str = ""
    side: str = ""
    threat_value: int = 1
    prerequisites: list[str] = field(default_factory=list)


@dataclass
class ForceAssessment:
    """Assessment of a group of units."""
    total_units: int = 0
    total_cost: int = 0
    total_hp: int = 0
    total_threat: int = 0
    by_category: dict[str, int] = field(default_factory=dict)
    strongest_unit: str = ""
    composition_ratio: dict[str, float] = field(default_factory=dict)


@dataclass
class OPFORProfile:
    """Opponent force intelligence profile."""
    difficulty: str = ""
    aggressiveness: float = 0.0
    expansion: float = 0.0
    first_attack_tick: int = 0
    traits: list[str] = field(default_factory=list)
    counters: list[str] = field(default_factory=list)
    army_composition: dict[str, float] = field(default_factory=dict)
    win_rate: float = 0.0
    summary: str = ""


class GameKnowledge:
    """Unified game knowledge for milsim tactical decisions.

    Wraps OpenRA-RL's game_data and opponent_intel modules,
    adding milsim-domain queries like force assessment,
    threat classification, and OPFOR profiling.
    """

    def __init__(self):
        self._available = _HAS_GAME_DATA

    @property
    def available(self) -> bool:
        return self._available

    def get_unit_profile(self, unit_type: str) -> UnitProfile:
        """Get enriched profile for a unit type."""
        profile = UnitProfile(type_code=unit_type)

        if self._available:
            stats = get_unit_stats(unit_type)
            if stats:
                profile.name = stats.get("name", unit_type)
                profile.category = stats.get("category", "unknown")
                profile.cost = stats.get("cost", 0)
                profile.hp = stats.get("hp", 0)
                profile.speed = stats.get("speed", 0)
                profile.armor = stats.get("armor", "")
                profile.side = stats.get("side", "")
                profile.prerequisites = stats.get("prerequisites", [])

        profile.threat_value = THREAT_VALUE.get(unit_type, 1)

        for cat, types in UNIT_CATEGORY.items():
            if unit_type in types:
                if profile.category == "unknown":
                    profile.category = cat
                break

        return profile

    def assess_force(self, unit_types: list[str]) -> ForceAssessment:
        """Assess the combat power of a force composition."""
        assessment = ForceAssessment(total_units=len(unit_types))
        categories: dict[str, int] = {}
        strongest_threat = 0

        for ut in unit_types:
            profile = self.get_unit_profile(ut)
            assessment.total_cost += profile.cost
            assessment.total_hp += profile.hp
            assessment.total_threat += profile.threat_value

            cat = profile.category
            categories[cat] = categories.get(cat, 0) + 1

            if profile.threat_value > strongest_threat:
                strongest_threat = profile.threat_value
                assessment.strongest_unit = ut

        assessment.by_category = categories
        if assessment.total_units > 0:
            assessment.composition_ratio = {
                cat: round(count / assessment.total_units, 2)
                for cat, count in categories.items()
            }

        return assessment

    def get_opfor_profile(self, difficulty: str = "normal") -> OPFORProfile:
        """Get OPFOR intelligence profile for a difficulty level."""
        profile = OPFORProfile(difficulty=difficulty)

        if not self._available:
            return profile

        raw = get_opponent_profile(difficulty)
        if not raw:
            return profile

        level_map = {"minimal": 0.1, "low": 0.3, "moderate": 0.5, "high": 0.7, "very_high": 0.9}
        agg_val = raw.get("aggressiveness", "moderate")
        profile.aggressiveness = level_map.get(agg_val, 0.5) if isinstance(agg_val, str) else float(agg_val)

        exp_val = raw.get("expansion_tendency", raw.get("expansion", "moderate"))
        profile.expansion = level_map.get(exp_val, 0.5) if isinstance(exp_val, str) else float(exp_val)

        fat = raw.get("typical_first_attack_tick", 3000)
        profile.first_attack_tick = int(fat) if not isinstance(fat, int) else fat
        profile.traits = raw.get("behavioral_traits", raw.get("traits", []))
        profile.counters = raw.get("recommended_counters", raw.get("counters", []))
        profile.army_composition = raw.get("typical_army_composition", {})

        match_history = raw.get("recent_match_history", raw.get("match_history", []))
        if isinstance(match_history, list):
            wins = sum(1 for m in match_history if m.get("result") == "win")
            total = len(match_history)
        else:
            wins = match_history.get("wins", 0)
            total = wins + match_history.get("losses", 0)
        profile.win_rate = wins / max(total, 1)

        profile.summary = get_opponent_summary(difficulty) or ""

        return profile

    def get_counter_units(self, enemy_types: list[str]) -> list[str]:
        """Recommend counter-units for an enemy composition."""
        has_armor = any(ut in UNIT_CATEGORY["vehicle"] for ut in enemy_types)
        has_infantry = any(ut in UNIT_CATEGORY["infantry"] for ut in enemy_types)
        has_air = any(ut in UNIT_CATEGORY["aircraft"] for ut in enemy_types)

        counters = []
        if has_armor:
            counters.extend(["e3", "e3", "1tnk"])
        if has_infantry:
            counters.extend(["e1", "e1", "apc"])
        if has_air:
            counters.extend(["e1", "e1"])
        if not counters:
            counters = ["e1", "e1", "e3"]

        return counters

    def get_tech_requirements(self, unit_type: str) -> list[str]:
        """Get the building prerequisites for a unit type."""
        if not self._available:
            return []

        stats = get_unit_stats(unit_type)
        if stats:
            return stats.get("prerequisites", [])

        stats = get_building_stats(unit_type)
        if stats:
            return stats.get("prerequisites", [])

        return []

    def format_force_intel(self, unit_types: list[str]) -> str:
        """Format a force assessment as intelligence text."""
        fa = self.assess_force(unit_types)
        lines = [
            "FORCE ASSESSMENT",
            f"  Strength: {fa.total_units} units, threat value {fa.total_threat}",
            f"  Cost: ${fa.total_cost}, HP: {fa.total_hp}",
            f"  Strongest: {fa.strongest_unit}",
            "  Composition:",
        ]
        for cat, ratio in sorted(fa.composition_ratio.items(), key=lambda x: -x[1]):
            count = fa.by_category[cat]
            lines.append(f"    {cat}: {count} ({ratio:.0%})")

        return "\n".join(lines)
