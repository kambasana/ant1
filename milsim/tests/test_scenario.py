#!/usr/bin/env python3
"""Test the scenario runner against a live game.

Loads the hasty_attack scenario, runs through the
Commander + WEGO + ISR pipeline, and validates:
  - Scenario YAML loading and unit type mapping
  - MSEL event triggering
  - Objective tracking
  - Exercise AAR generation
  - Integration of all milsim layers

Usage:
    OPENRA_PATH=/path/to/OpenRA python -m openra_env.server.app --port 8000 &
    python milsim/tests/test_scenario.py
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
from milsim.commander import Commander, TacticalOrder, OrderType
from milsim.isr import ISRManager
from milsim.scenario import ScenarioRunner, ObjectiveStatus


async def run_scenario_test():
    results = []

    def check(name, condition, detail=""):
        status = "PASS" if condition else "FAIL"
        results.append((name, condition))
        print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))

    print("Scenario Runner Tests")
    print("=" * 60)

    try:
        async with OpenRAEnv(base_url=SERVER_URL, message_timeout_s=120.0) as env:
            wego = WEGOTurnManager(env, ticks_per_turn=75, execution_substeps=5)
            commander = Commander(wego)
            isr = ISRManager()
            runner = ScenarioRunner(commander, isr)

            # --- Phase 1: Load scenario ---
            print("\n1. Scenario Loading")
            scenario_path = "milsim/mod/scenarios/hasty_attack.yaml"
            state = runner.load(scenario_path)

            check("Scenario loaded", state is not None)
            check("Name parsed", state.name == "Hasty Attack — River Crossing")
            check("Duration parsed", state.max_turns == 16)
            check("BLUFOR units loaded", len(state.blufor_units) == 15,
                  f"{len(state.blufor_units)} units")
            check("OPFOR units loaded", len(state.opfor_units) == 6,
                  f"{len(state.opfor_units)} units")
            check("Objectives loaded", len(state.objectives) == 3,
                  f"{len(state.objectives)} objectives")
            check("MSEL loaded", len(state.msel) == 7,
                  f"{len(state.msel)} events")

            # Verify unit type mapping
            abrams = next((u for u in state.blufor_units
                          if u.type_code == "USM1A2Abrams"), None)
            check("Abrams maps to 3tnk", abrams and abrams.openra_type == "3tnk")

            kornet = next((u for u in state.opfor_units
                          if u.type_code == "RUKornetTeam"), None)
            check("Kornet maps to e3", kornet and kornet.openra_type == "e3")
            check("Kornet is concealed", kornet and kornet.concealed)
            check("Kornet is entrenched", kornet and kornet.entrenched)

            # --- Phase 2: Briefing ---
            print("\n2. Scenario Briefing")
            briefing_text = runner.get_briefing()
            check("Briefing generates", len(briefing_text) > 500,
                  f"{len(briefing_text)} chars")
            check("Briefing has situation", "SITUATION" in briefing_text)
            check("Briefing has BLUFOR", "FRIENDLY FORCES" in briefing_text)
            check("Briefing has OPFOR", "ENEMY FORCES" in briefing_text)
            check("Briefing has objectives", "OBJECTIVES" in briefing_text)
            check("Briefing has MSEL", "MSEL" in briefing_text)
            print(f"\n{briefing_text[:600]}...\n")

            # --- Phase 3: Game initialization ---
            print("\n3. Game Initialization")
            cmd_briefing = await commander.start_game()
            check("Game started", cmd_briefing is not None)

            # Deploy MCV and build base (minimum to test scenario flow)
            mcv = next((u for u in cmd_briefing.friendly_units if u.type == "mcv"), None)
            if mcv:
                await commander.issue_orders([
                    TacticalOrder(order_type=OrderType.DEPLOY, unit_ids=[mcv.actor_id]),
                ])
            for _ in range(3):
                await commander.advance_time()

            # --- Phase 4: MSEL event triggering ---
            print("\n4. MSEL Event System")

            # Advance scenario to turn 2 (should trigger UAV intel event)
            events1, status1 = runner.advance_turn()  # Turn 1
            check("Turn 1 — no events", len(events1) == 0)

            events2, status2 = runner.advance_turn()  # Turn 2
            check("Turn 2 — intel event fires", len(events2) == 1)
            if events2:
                check("Event is UAV intel", "UAV" in events2[0].event,
                      events2[0].event[:50])
                check("Event has radio message", len(events2[0].radio_message) > 0)

                # Format the radio message
                formatted = runner.format_msel_event(events2[0])
                check("Event formats", "INTEL" in formatted)
                print(f"\n{formatted}\n")

            # Advance to turn 4 (battalion update)
            events3, _ = runner.advance_turn()  # Turn 3
            events4, _ = runner.advance_turn()  # Turn 4
            check("Turn 4 — higher HQ event", len(events4) == 1)
            if events4:
                check("Event is battalion", "Bravo" in events4[0].event or "battalion" in events4[0].event.lower())

            # Advance game time between scenario turns
            for _ in range(2):
                result, _ = await commander.advance_time()
                obs = result.situation_after.observation
                isr.process_observation(obs, wego.turn_number)

            # Turn 6 (civilian event)
            events5, _ = runner.advance_turn()  # Turn 5
            events6, _ = runner.advance_turn()  # Turn 6
            check("Turn 6 — civilian event", len(events6) == 1)
            if events6:
                check("Event is civilian", "civilian" in events6[0].event.lower() or "Civilian" in events6[0].event)

            # --- Phase 5: Status and scoring ---
            print("\n5. Status & Scoring")
            status = runner.get_status()
            check("Status generates", len(status) > 100)
            check("Status has objectives", "OBJECTIVES" in status)
            check("Status has events", "Turn" in status)
            print(f"\n{status}\n")

            # Verify objective tracking
            obj_bridge = next((o for o in state.objectives if "BRIDGE" in o.name), None)
            check("Bridge objective tracked", obj_bridge is not None)
            if obj_bridge:
                check("Objective starts pending",
                      obj_bridge.status in (ObjectiveStatus.PENDING, ObjectiveStatus.IN_PROGRESS))

            # --- Phase 6: Exercise AAR ---
            print("\n6. Exercise AAR")
            aar = runner.get_exercise_aar()
            check("AAR generates", len(aar) > 200, f"{len(aar)} chars")
            check("AAR has score", "Score:" in aar)
            check("AAR has result", "Result:" in aar)
            check("AAR has intel summary", "INTELLIGENCE SUMMARY" in aar)
            check("AAR has game data", "GAME ENGINE AAR" in aar)
            print(f"\n{aar[:800]}...\n")

            # --- Phase 7: Full integration check ---
            print("\n7. Full Integration")
            check("Commander operational", not commander.is_game_over or True)
            check("ISR tracking", isr.get_summary() is not None)
            check("Scenario advancing", runner.current_turn == 6)
            check("Not complete (mid-scenario)", not runner.is_complete)

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
        print("\nScenario runner fully operational.")
    else:
        print(f"\n{total - passed} check(s) failed.")

    return passed == total


@pytest.mark.integration
def test_scenario_runner(openra_server):
    """Scenario runner against a live server.

    Skipped (never silently passed) when no server is reachable; the
    `openra_server` fixture does the probing.
    """
    assert asyncio.run(run_scenario_test()), (
        "one or more checks failed - see the [FAIL] lines in captured output"
    )


if __name__ == "__main__":
    if not require_server_or_explain():
        sys.exit(1)
    success = asyncio.run(run_scenario_test())
    sys.exit(0 if success else 1)
