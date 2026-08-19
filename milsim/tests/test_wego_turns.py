#!/usr/bin/env python3
"""Test the WEGO turn system over a live OpenRA-RL game.

Runs a short game exercising the full WEGO cycle:
  - Deploy MCV → establish C2
  - Build power + barracks with auto-placement
  - Train infantry
  - Execute multiple turns tracking BDA and AAR

Validates turn phase cycling, BDA computation, SITREP/AAR
formatting, and game state advancement.

Usage:
    OPENRA_PATH=/path/to/OpenRA python -m openra_env.server.app --port 8000 &
    python milsim/tests/test_wego_turns.py
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "openra-rl"))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

from _openra_server import SERVER_URL, require_server_or_explain, start_hint

from openra_env.client import OpenRAEnv
from openra_env.models import ActionType, CommandModel

from milsim.wego import WEGOTurnManager, TurnPhase


def _placement_offset(cy_pos_x, cy_pos_y, index):
    """Calculate placement position relative to construction yard."""
    cx = cy_pos_x // 1024
    cy_y = cy_pos_y // 1024
    offsets = [
        (3, 0), (-3, 0), (0, 3), (0, -3),
        (3, 3), (-3, 3), (3, -3), (-3, -3),
        (6, 0), (-6, 0), (0, 6), (0, -6),
    ]
    dx, dy = offsets[index % len(offsets)]
    return cx + dx, cy_y + dy


def _auto_place_commands(obs, placement_counter):
    """Generate PLACE_BUILDING commands for any completed buildings."""
    commands = []
    cy = next((b for b in obs.buildings if b.type == "fact"), None)
    if not cy:
        return commands, placement_counter
    for prod in obs.production:
        if prod.queue_type == "Building" and prod.progress >= 0.99:
            x, y = _placement_offset(cy.pos_x, cy.pos_y, placement_counter)
            commands.append(CommandModel(
                action=ActionType.PLACE_BUILDING,
                item_type=prod.item,
                target_x=x,
                target_y=y,
            ))
            placement_counter += 1
    return commands, placement_counter


async def run_wego_test():
    results = []
    placement_counter = 0

    def check(name, condition, detail=""):
        status = "PASS" if condition else "FAIL"
        results.append((name, condition))
        print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))

    def note(name, detail=""):
        """Log something observed but not asserted.

        Deliberately NOT appended to `results`: a line that can never fail is
        not a passing check, and counting it inflates "Results: n/m passed".
        """
        print(f"  [INFO] {name}" + (f" — {detail}" if detail else ""))

    print("WEGO Turn System Tests")
    print("=" * 60)

    try:
        async with OpenRAEnv(base_url=SERVER_URL, message_timeout_s=120.0) as env:
            wego = WEGOTurnManager(env, ticks_per_turn=50, execution_substeps=5)

            # Start game
            print("\n1. Game Start & Initial Situation")
            situation = await wego.start()
            check("Game started", situation is not None)
            check("Turn 1", wego.turn_number == 1)
            check("Planning phase", wego.phase == TurnPhase.PLANNING)
            check("Units present", len(situation.friendly_units) > 0,
                  f"{len(situation.friendly_units)} units")

            sitrep = wego.format_sitrep()
            check("SITREP generates", len(sitrep) > 100, f"{len(sitrep)} chars")
            print(f"\n{sitrep}\n")

            # Turn 1: Deploy MCV
            print("\n2. Turn 1: Deploy MCV")
            mcv = next((u for u in situation.friendly_units if u.type == "mcv"), None)
            check("MCV found", mcv is not None)

            if mcv:
                result = await wego.execute_turn([
                    CommandModel(action=ActionType.DEPLOY, actor_id=mcv.actor_id),
                ])
                check("Turn executed", result is not None)
                check("Ticks advanced", result.ticks_executed > 0,
                      f"{result.ticks_executed} ticks")
                check("BDA computed", result.bda is not None)
                check("Phase returns to planning", wego.phase == TurnPhase.PLANNING)
                check("Turn incremented", wego.turn_number == 2)

            # Turns 2-3: Wait for MCV deployment to complete
            print("\n3. Turns 2-3: Wait for deployment")
            for _ in range(2):
                result = await wego.execute_turn([CommandModel(action=ActionType.NO_OP)])
            obs = result.situation_after.observation
            has_cy = any(b.type == "fact" for b in obs.buildings)
            check("Construction yard built", has_cy)
            check("Cash available", obs.economy.cash > 0, f"${obs.economy.cash}")

            # Turn 4: Queue power plant
            print("\n4. Turn 4: Queue power plant")
            orders = [CommandModel(action=ActionType.BUILD, item_type="powr")]
            place_cmds, placement_counter = _auto_place_commands(obs, placement_counter)
            orders.extend(place_cmds)
            result = await wego.execute_turn(orders)
            note("Power plant queued")
            note("Cash delta tracked", f"delta={result.bda.cash_delta}")

            # Turns 5-8: Advance time for construction + auto-place
            print("\n5. Turns 5-8: Build & auto-place")
            for i in range(4):
                obs = result.situation_after.observation
                place_cmds, placement_counter = _auto_place_commands(obs, placement_counter)
                orders = place_cmds if place_cmds else [CommandModel(action=ActionType.NO_OP)]
                result = await wego.execute_turn(orders)
                if result.game_over:
                    break

            obs = result.situation_after.observation
            has_power = any(b.type == "powr" for b in obs.buildings)
            check("Power plant placed", has_power)
            check("Game advancing", obs.tick > 200, f"tick={obs.tick}")

            # Turn 9: Queue barracks
            print("\n6. Turn 9: Queue barracks")
            available = obs.available_production
            barracks_type = next((t for t in ("tent", "barr") if t in available), None)
            orders = []
            place_cmds, placement_counter = _auto_place_commands(obs, placement_counter)
            orders.extend(place_cmds)

            if barracks_type:
                orders.append(CommandModel(action=ActionType.BUILD, item_type=barracks_type))
                note("Barracks type found", barracks_type)
            else:
                check("Barracks type found", False, f"available: {available[:8]}")
                orders.append(CommandModel(action=ActionType.NO_OP))

            result = await wego.execute_turn(orders)

            # Turns 10-14: Build barracks + auto-place
            print("\n7. Turns 10-14: Build barracks & advance")
            for _ in range(5):
                obs = result.situation_after.observation
                place_cmds, placement_counter = _auto_place_commands(obs, placement_counter)
                orders = place_cmds if place_cmds else [CommandModel(action=ActionType.NO_OP)]
                result = await wego.execute_turn(orders)
                if result.game_over:
                    break

            obs = result.situation_after.observation
            has_barracks = any(b.type in ("tent", "barr") for b in obs.buildings)
            check("Barracks built", has_barracks)

            # Turn 15: Train infantry
            print("\n8. Turn 15: Train infantry")
            check("Barracks available to queue infantry", has_barracks)
            if has_barracks:
                orders = [
                    CommandModel(action=ActionType.TRAIN, item_type="e1"),
                    CommandModel(action=ActionType.TRAIN, item_type="e1"),
                    CommandModel(action=ActionType.TRAIN, item_type="e1"),
                ]
                place_cmds, placement_counter = _auto_place_commands(obs, placement_counter)
                orders.extend(place_cmds)
                result = await wego.execute_turn(orders)
            else:
                result = await wego.execute_turn([CommandModel(action=ActionType.NO_OP)])

            # Turns 16-18: Let infantry train
            for _ in range(3):
                obs = result.situation_after.observation
                place_cmds, placement_counter = _auto_place_commands(obs, placement_counter)
                orders = place_cmds if place_cmds else [CommandModel(action=ActionType.NO_OP)]
                result = await wego.execute_turn(orders)
                if result.game_over:
                    break

            obs = result.situation_after.observation
            infantry = [u for u in obs.units if u.type == "e1"]
            check("Infantry produced", len(infantry) > 0, f"{len(infantry)} riflemen")

            # Final assessment
            print("\n9. Final Assessment")
            sitrep = wego.format_sitrep()
            check("Final SITREP", len(sitrep) > 100)
            print(f"\n{sitrep}\n")

            # AAR
            print("\n10. After Action Review")
            aar = wego.generate_aar_summary()
            check("AAR generates", len(aar) > 100, f"{len(aar)} chars")
            check("Turn history recorded", len(wego.turn_history) >= 15,
                  f"{len(wego.turn_history)} turns logged")
            print(f"\n{aar}\n")

            # Integrity
            print("\n11. Turn History Integrity")
            history = wego.turn_history
            ticks_monotonic = all(
                history[i].tick_end <= history[i + 1].tick_start
                for i in range(len(history) - 1)
            )
            check("Ticks monotonically increase", ticks_monotonic)
            check("Turn numbers sequential",
                  all(h.turn_number == i + 1 for i, h in enumerate(history)))
            check("Game not prematurely over", not wego.is_game_over or wego.game_result != "")

    # ConnectionRefusedError is a subclass of ConnectionError; the client
    # wraps connect failures in a plain ConnectionError. Anything broader
    # (a real OSError, a bad hostname) still gets the full traceback below.
    except ConnectionError as e:
        print(f"\nERROR: {e}")
        print(start_hint())
        return False
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"\n{'=' * 60}")
    print(f"Results: {passed}/{total} passed")
    print(f"{'=' * 60}")

    if passed == total:
        print("\nWEGO turn system fully operational.")
    else:
        print(f"\n{total - passed} check(s) failed.")

    return passed == total


@pytest.mark.integration
def test_wego_turn_system(openra_server):
    """Full WEGO turn cycle against a live server.

    Skipped (never silently passed) when no server is reachable; the
    `openra_server` fixture does the probing.
    """
    assert asyncio.run(run_wego_test()), (
        "one or more checks failed - see the [FAIL] lines in captured output"
    )


if __name__ == "__main__":
    if not require_server_or_explain():
        sys.exit(1)
    success = asyncio.run(run_wego_test())
    sys.exit(0 if success else 1)
