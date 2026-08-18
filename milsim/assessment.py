"""Multi-dimensional performance assessment using reward vectors.

Wraps OpenRA-RL's RewardFunction with milsim-domain scoring across
8 military lines of effort: combat, economy, infrastructure,
intelligence, composition, tempo, disruption, outcome.

Usage:
    from milsim.assessment import PerformanceAssessor
    assessor = PerformanceAssessor()
    score = assessor.evaluate(observation_dict)
    report = assessor.format_assessment()
"""

from dataclasses import dataclass, field
from typing import Optional

try:
    from openra_env.reward import OpenRARewardFunction, RewardWeights
    _HAS_REWARD = True
except ImportError:
    _HAS_REWARD = False


DIMENSIONS = [
    "combat",
    "economy",
    "infrastructure",
    "intelligence",
    "composition",
    "tempo",
    "disruption",
    "outcome",
]

DIMENSION_DESCRIPTIONS = {
    "combat": "Kill/loss ratio, damage dealt vs taken",
    "economy": "Resource efficiency, harvester management",
    "infrastructure": "Base development, power management",
    "intelligence": "Map exploration, enemy scouting",
    "composition": "Force mix, tech diversity",
    "tempo": "Operational speed, initiative",
    "disruption": "Enemy disruption, harassment effectiveness",
    "outcome": "Win/loss terminal reward",
}


@dataclass
class DimensionScore:
    """Score along one military line of effort."""
    name: str
    value: float = 0.0
    trend: float = 0.0
    description: str = ""


@dataclass
class PerformanceScore:
    """Multi-dimensional performance assessment."""
    scalar: float = 0.0
    dimensions: list[DimensionScore] = field(default_factory=list)
    cumulative_scalar: float = 0.0
    turn_count: int = 0
    strongest: str = ""
    weakest: str = ""


class PerformanceAssessor:
    """Evaluates AI performance across military lines of effort.

    Uses OpenRA-RL's reward vectors when available, falls back
    to simplified scoring from observation data.
    """

    def __init__(self, vector_enabled: bool = True):
        self._history: list[PerformanceScore] = []
        self._cumulative = 0.0
        self._prev_values: dict[str, float] = {}

        if _HAS_REWARD:
            weights = RewardWeights(
                survival=0.001,
                economic_efficiency=0.01,
                aggression=0.1,
                defense=0.05,
                victory=1.0,
                defeat=-1.0,
            )
            self._reward_fn = OpenRARewardFunction(
                weights=weights,
                vector_enabled=vector_enabled,
            )
        else:
            self._reward_fn = None

    def reset(self) -> None:
        """Reset for a new engagement."""
        self._history.clear()
        self._cumulative = 0.0
        self._prev_values.clear()
        if self._reward_fn:
            self._reward_fn.reset()

    def evaluate(self, obs_dict: dict) -> PerformanceScore:
        """Evaluate current performance from an observation."""
        score = PerformanceScore(turn_count=len(self._history) + 1)

        if self._reward_fn:
            scalar, vector_dict = self._reward_fn.compute_all(obs_dict)
            score.scalar = round(scalar, 4)
            self._cumulative += scalar
            score.cumulative_scalar = round(self._cumulative, 4)

            if vector_dict:
                for dim_name in DIMENSIONS:
                    val = vector_dict.get(dim_name, 0.0)
                    prev = self._prev_values.get(dim_name, 0.0)
                    trend = val - prev
                    self._prev_values[dim_name] = val

                    score.dimensions.append(DimensionScore(
                        name=dim_name,
                        value=round(val, 4),
                        trend=round(trend, 4),
                        description=DIMENSION_DESCRIPTIONS.get(dim_name, ""),
                    ))
        else:
            score = self._fallback_evaluate(obs_dict)

        if score.dimensions:
            best = max(score.dimensions, key=lambda d: d.value)
            worst = min(score.dimensions, key=lambda d: d.value)
            score.strongest = best.name
            score.weakest = worst.name

        self._history.append(score)
        return score

    def _fallback_evaluate(self, obs_dict: dict) -> PerformanceScore:
        """Simple scoring when reward module isn't available."""
        eco = obs_dict.get("economy", {})
        mil = obs_dict.get("military", {})

        cash = eco.get("cash", 0) if isinstance(eco, dict) else 0
        kills = mil.get("units_killed", 0) if isinstance(mil, dict) else 0
        losses = mil.get("units_lost", 0) if isinstance(mil, dict) else 0
        army = mil.get("army_value", 0) if isinstance(mil, dict) else 0

        combat = (kills - losses) * 0.1
        economy = min(cash / 5000, 1.0) * 0.5
        infrastructure = min(army / 3000, 1.0) * 0.3

        scalar = combat + economy + infrastructure
        self._cumulative += scalar

        dims = [
            DimensionScore("combat", round(combat, 4), 0.0, DIMENSION_DESCRIPTIONS["combat"]),
            DimensionScore("economy", round(economy, 4), 0.0, DIMENSION_DESCRIPTIONS["economy"]),
            DimensionScore("infrastructure", round(infrastructure, 4), 0.0, DIMENSION_DESCRIPTIONS["infrastructure"]),
        ]

        return PerformanceScore(
            scalar=round(scalar, 4),
            dimensions=dims,
            cumulative_scalar=round(self._cumulative, 4),
            turn_count=len(self._history) + 1,
        )

    def get_latest(self) -> Optional[PerformanceScore]:
        """Get the most recent assessment."""
        return self._history[-1] if self._history else None

    def get_trend(self, window: int = 5) -> dict[str, float]:
        """Get performance trend over recent turns."""
        if len(self._history) < 2:
            return {}

        recent = self._history[-window:]
        trends = {}
        for dim in DIMENSIONS:
            values = []
            for score in recent:
                for d in score.dimensions:
                    if d.name == dim:
                        values.append(d.value)
                        break
            if len(values) >= 2:
                trends[dim] = round(values[-1] - values[0], 4)

        return trends

    def format_assessment(self, score: Optional[PerformanceScore] = None) -> str:
        """Format a performance assessment as text."""
        s = score or self.get_latest()
        if not s:
            return "No assessment data."

        lines = [
            "PERFORMANCE ASSESSMENT",
            "=" * 40,
            f"Turn: {s.turn_count}",
            f"Score: {s.scalar:+.4f} (cumulative: {s.cumulative_scalar:+.4f})",
        ]

        if s.dimensions:
            lines.append("")
            lines.append("LINES OF EFFORT:")
            for d in sorted(s.dimensions, key=lambda x: -x.value):
                trend_str = f" ({d.trend:+.4f})" if d.trend != 0 else ""
                bar = self._bar(d.value)
                lines.append(f"  {d.name:15s} {d.value:+.4f}{trend_str} {bar}")

            if s.strongest:
                lines.append(f"\n  Strongest: {s.strongest}")
            if s.weakest:
                lines.append(f"  Weakest:   {s.weakest}")

        return "\n".join(lines)

    def format_summary(self) -> str:
        """Format overall engagement performance summary."""
        if not self._history:
            return "No performance data."

        lines = [
            "ENGAGEMENT PERFORMANCE SUMMARY",
            "=" * 40,
            f"Turns assessed: {len(self._history)}",
            f"Cumulative score: {self._cumulative:+.4f}",
            f"Average per turn: {self._cumulative / max(len(self._history), 1):+.4f}",
        ]

        trends = self.get_trend()
        if trends:
            lines.extend(["", "TRENDS (last 5 turns):"])
            for dim, trend in sorted(trends.items(), key=lambda x: -abs(x[1])):
                arrow = "+" if trend > 0 else ""
                lines.append(f"  {dim:15s} {arrow}{trend:.4f}")

        return "\n".join(lines)

    @staticmethod
    def _bar(value: float, width: int = 10) -> str:
        clamped = max(-1, min(1, value))
        filled = int(abs(clamped) * width)
        if clamped >= 0:
            return "[" + "=" * filled + " " * (width - filled) + "]"
        return "[" + " " * (width - filled) + "-" * filled + "]"
