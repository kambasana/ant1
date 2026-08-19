#!/usr/bin/env python3
"""Test the Commander interface over a live game.

Exercises the full pipeline: Commander → WEGO → OpenRA-RL
using tactical orders (deploy, build, train, assault).

Usage:
    OPENRA_PATH=/path/to/OpenRA python -m openra_env.server.app --port 8000 &
    python milsim/tests/test_commander.py
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "openra-rl"))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

from _openra_server import SERVER_URL, require_server_or_explain, start_hint

# openra-rl is a separate checkout (see scripts/openra-rl/README.md), so
# openra_env is absent from a clean clone — directly here, or pulled in
# transitively by milsim.commander. Skip visibly rather than dying at
# collection, which takes the whole file down.
pytest.importorskip("openra_env", reason="openra-rl is not installed")

from openra_env.client import OpenRAEnv
from milsim.wego import WEGOTurnManager
from milsim.commander import Commander, TacticalOrder, OrderType, Stance


async def run_commander_test():
    results = []

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

    print("Commander Interface Tests")
    print("=" * 60)

    try:
        async with OpenRAEnv(base_url=SERVER_URL, message_timeout_s=120.0) as env:
            wego = WEGOTurnManager(env, ticks_per_turn=50, execution_substeps=5)
            commander = Commander(wego)

            # Start game
            print("\n1. Game Initialization")
            briefing = await commander.start_game()
            check("Briefing received", briefing is not None)
            check("Turn 1", briefing.turn_number == 1)
            check("Units in briefing", len(briefing.friendly_units) > 0,
                  f"{len(briefing.friendly_units)} units")
            check("Economy in briefing", briefing.economy["cash"] > 0,
                  f"${briefing.economy['cash']}")
            check("SITREP in briefing", len(briefing.sitrep) > 50)

            # Print formatted briefing
            text = commander.format_briefing_text(briefing)
            check("Briefing text formats", len(text) > 100)
            print(f"\n{text}\n")

            # Turn 1: Deploy MCV
            print("\n2. Deploy MCV (Establish C2)")
            mcv = next((u for u in briefing.friendly_units if u.type == "mcv"), None)
            check("MCV in briefing", mcv is not None)

            if mcv:
                result, briefing = await commander.issue_orders([
                    TacticalOrder(order_type=OrderType.DEPLOY, unit_ids=[mcv.actor_id]),
                ])
                check("Deploy order executed", result is not None)
                check("Turn result has BDA", result.bda is not None)

            # Wait for deployment
            for _ in range(2):
                result, briefing = await commander.advance_time()
            check("C2 established",
                  any(b["type"] == "fact" for b in briefing.friendly_buildings))

            # Build base
            print("\n3. Build Base (Logistics)")
            result, briefing = await commander.issue_orders([
                TacticalOrder(order_type=OrderType.BUILD, item_type="powr"),
            ])
            note("Power plant ordered")

            # Advance for construction
            for _ in range(4):
                result, briefing = await commander.advance_time()

            has_power = any(b["type"] == "powr" for b in briefing.friendly_buildings)
            check("Power plant built", has_power)

            # Build barracks
            available = briefing.available_production
            barracks = next((t for t in ("tent", "barr") if t in available), None)
            if barracks:
                result, briefing = await commander.issue_orders([
                    TacticalOrder(order_type=OrderType.BUILD, item_type=barracks),
                ])
                # Wait for it rather than budgeting a fixed five turns: against
                # a live server the barracks took seven, so the old budget
                # asserted on a building still in production. Same fix as
                # test_wego_turns.
                BARRACKS_MAX_TURNS = 15
                has_barracks = False
                waited = 0
                for waited in range(1, BARRACKS_MAX_TURNS + 1):
                    has_barracks = any(
                        b["type"] in ("tent", "barr") for b in briefing.friendly_buildings
                    )
                    if has_barracks:
                        break
                    result, briefing = await commander.advance_time()
                has_barracks = has_barracks or any(
                    b["type"] in ("tent", "barr") for b in briefing.friendly_buildings
                )
                check("Barracks built", has_barracks, f"{barracks} after {waited} turn(s)")
            else:
                check("Barracks built", False, f"not available: {available[:5]}")

            # Train infantry
            print("\n4. Train Forces")
            result, briefing = await commander.issue_orders([
                TacticalOrder(order_type=OrderType.TRAIN, item_type="e1", count=3),
            ])
            note("Infantry training ordered")

            for _ in range(3):
                result, briefing = await commander.advance_time()

            infantry = [u for u in briefing.friendly_units if u.type == "e1"]
            check("Infantry produced", len(infantry) > 0, f"{len(infantry)} riflemen")

            # Set stance
            print("\n5. Stance Directives")
            if infantry:
                inf_ids = [u.actor_id for u in infantry]
                result, briefing = await commander.issue_orders([
                    TacticalOrder(
                        order_type=OrderType.SET_STANCE,
                        unit_ids=inf_ids,
                        stance=Stance.FREE_FIRE,
                    ),
                ])
                note("Stance orders issued", "free_fire")

            # Reconnoiter order
            print("\n6. Reconnaissance")
            if infantry:
                scout = infantry[0]
                result, briefing = await commander.issue_orders([
                    TacticalOrder(
                        order_type=OrderType.RECONNOITER,
                        unit_ids=[scout.actor_id],
                        target_x=scout.cell_x + 30,
                        target_y=scout.cell_y,
                    ),
                ])
                note("Recon order issued", f"scout → ({scout.cell_x + 30},{scout.cell_y})")

                # Advance to let scout move
                for _ in range(3):
                    result, briefing = await commander.advance_time()

                scout_now = next((u for u in briefing.friendly_units
                                  if u.actor_id == scout.actor_id), None)
                if scout_now:
                    moved = scout_now.cell_x != scout.cell_x or scout_now.cell_y != scout.cell_y
                    active = not scout_now.is_idle
                    check("Scout responding", moved or active,
                          f"pos=({scout_now.cell_x},{scout_now.cell_y}) idle={scout_now.is_idle}")
                else:
                    check("Scout responding", False, "scout lost")

            # Commander briefing format
            print("\n7. Commander Briefing Quality")
            briefing = commander.get_briefing()
            text = commander.format_briefing_text(briefing)
            check("Briefing has unit disposition", "UNIT DISPOSITION" in text)
            check("Briefing has available orders", "AVAILABLE ORDERS" in text)
            check("Briefing has economy data", "Cash" in briefing.sitrep)
            print(f"\n{text}\n")

            # AAR
            print("\n8. After Action Review")
            aar = commander.get_aar()
            check("AAR available", len(aar) > 100, f"{len(aar)} chars")
            check("Game still running", not commander.is_game_over)

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
        print("\nCommander interface fully operational.")
    else:
        print(f"\n{total - passed} check(s) failed.")

    return passed == total


@pytest.mark.integration
def test_commander_interface(openra_server):
    """Commander order interface against a live server.

    Skipped (never silently passed) when no server is reachable; the
    `openra_server` fixture does the probing.
    """
    assert asyncio.run(run_commander_test()), (
        "one or more checks failed - see the [FAIL] lines in captured output"
    )


if __name__ == "__main__":
    if not require_server_or_explain():
        sys.exit(1)
    success = asyncio.run(run_commander_test())
    sys.exit(0 if success else 1)
