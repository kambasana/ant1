"""Publish live game state to a file for the C2 dashboard.

The game writes its latest observation here each turn; the control server
(:mod:`milsim.control`) reads it and serves it to the dashboard along with
process status. A file rather than a socket, because the control server has
to outlive any individual game -- it is what starts and stops them -- and a
game that dies must not take the dashboard's data source with it.

    from milsim.dashboard_feed import publish
    publish(observation, turn_number)
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from typing import Any, Optional

__all__ = ["publish", "snapshot", "state_path", "clear", "STATE_ENV"]

STATE_ENV = "MILSIM_STATE_FILE"

_lock = threading.Lock()


def state_path() -> str:
    """Where the running game publishes, and the control server reads."""
    return os.environ.get(STATE_ENV) or os.path.join(
        tempfile.gettempdir(), "milsim-state.json"
    )


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _unit(u: Any) -> dict:
    return {
        "actor_id": getattr(u, "actor_id", 0),
        "type": getattr(u, "type", ""),
        "cell_x": getattr(u, "cell_x", 0),
        "cell_y": getattr(u, "cell_y", 0),
        "hp_percent": _f(getattr(u, "hp_percent", 1.0), 1.0),
        "is_idle": bool(getattr(u, "is_idle", False)),
        "current_activity": getattr(u, "current_activity", "") or "",
        "stance": getattr(u, "stance", 0),
        "can_attack": bool(getattr(u, "can_attack", False)),
        "experience_level": getattr(u, "experience_level", 0),
    }


def _building(b: Any) -> dict:
    return {
        "actor_id": getattr(b, "actor_id", 0),
        "type": getattr(b, "type", ""),
        "cell_x": getattr(b, "cell_x", 0),
        "cell_y": getattr(b, "cell_y", 0),
        "hp_percent": _f(getattr(b, "hp_percent", 1.0), 1.0),
        "is_producing": bool(getattr(b, "is_producing", False)),
        "production_progress": _f(getattr(b, "production_progress", 0.0)),
        "producing_item": getattr(b, "producing_item", "") or "",
        "is_powered": bool(getattr(b, "is_powered", True)),
        "power_amount": getattr(b, "power_amount", 0),
        "can_produce": list(getattr(b, "can_produce", []) or []),
    }


def build_snapshot(obs: Any, turn: int = 0) -> dict:
    economy = getattr(obs, "economy", None)
    military = getattr(obs, "military", None)
    map_info = getattr(obs, "map_info", None)
    return {
        "turn": turn,
        "tick": getattr(obs, "tick", 0),
        "map": {
            "width": getattr(map_info, "width", 0) if map_info else 0,
            "height": getattr(map_info, "height", 0) if map_info else 0,
            "name": getattr(map_info, "map_name", "") if map_info else "",
        },
        "economy": {
            "cash": getattr(economy, "cash", 0) if economy else 0,
            "ore": getattr(economy, "ore", 0) if economy else 0,
            "power_provided": getattr(economy, "power_provided", 0) if economy else 0,
            "power_drained": getattr(economy, "power_drained", 0) if economy else 0,
            "resource_capacity": getattr(economy, "resource_capacity", 0) if economy else 0,
            "harvester_count": getattr(economy, "harvester_count", 0) if economy else 0,
        },
        "military": {
            "units_killed": getattr(military, "units_killed", 0) if military else 0,
            "units_lost": getattr(military, "units_lost", 0) if military else 0,
            "buildings_killed": getattr(military, "buildings_killed", 0) if military else 0,
            "buildings_lost": getattr(military, "buildings_lost", 0) if military else 0,
            "army_value": getattr(military, "army_value", 0) if military else 0,
            "active_unit_count": getattr(military, "active_unit_count", 0) if military else 0,
            "kills_cost": getattr(military, "kills_cost", 0) if military else 0,
            "deaths_cost": getattr(military, "deaths_cost", 0) if military else 0,
        },
        "units": [_unit(u) for u in (getattr(obs, "units", []) or [])],
        "buildings": [_building(b) for b in (getattr(obs, "buildings", []) or [])],
        "enemies": [_unit(u) for u in (getattr(obs, "visible_enemies", []) or [])],
        "enemy_buildings": [
            _building(b) for b in (getattr(obs, "visible_enemy_buildings", []) or [])
        ],
        "production": [
            {
                "queue_type": getattr(p, "queue_type", ""),
                "item": getattr(p, "item", ""),
                "progress": _f(getattr(p, "progress", 0.0)),
                "remaining_ticks": getattr(p, "remaining_ticks", 0),
                "remaining_cost": getattr(p, "remaining_cost", 0),
                "paused": bool(getattr(p, "paused", False)),
            }
            for p in (getattr(obs, "production", []) or [])
        ],
        "available_production": list(getattr(obs, "available_production", []) or []),
    }


def publish(obs: Any, turn: int = 0) -> None:
    """Write the latest observation. Cheap; safe to call every turn."""
    if obs is None:
        return
    snap = build_snapshot(obs, turn)
    path = state_path()
    with _lock:
        try:
            # Write-then-replace so a reader never sees a half-written file.
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".",
                                       suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(snap, fh)
            os.replace(tmp, path)
        except OSError:
            pass  # the dashboard must never be able to break a game


def snapshot() -> Optional[dict]:
    try:
        with open(state_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def clear() -> None:
    try:
        os.remove(state_path())
    except OSError:
        pass
