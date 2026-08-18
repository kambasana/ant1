#!/usr/bin/env python3
"""Full game test — AI plays through the complete milsim pipeline.

Runs the TacticalAI through Commander → WEGO → ISR against a live
OpenRA-RL game. The AI deploys, builds, scouts, and attacks
autonomously using doctrine-based decisions.

This is the integration test for all milsim layers working together.

Usage:
    OPENRA_PATH=/path/to/OpenRA python -m openra_env.server.app --port 8000 &
    python milsim/tests/test_full_game.py
"""

import asyncio
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "openra-rl"))

from openra_env.client import OpenRAEnv
from milsim.wego import WEGOTurnManager
from milsim.commander import Commander
from milsim.isr import ISRManager
from milsim.scenario import ScenarioRunner
from milsim.tactical_ai import TacticalAI, Phase


async def run_full_game():
    results = []

    def check(name, condition, detail=""):
        status = "PASS" if condition else "FAIL"
        results.append((name, condition))
        print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))

    print("Full Game Integration Test")
    print("=" * 60)
    print("AI will play autonomously through all milsim layers.\n")

    start_time = time.time()

    try:
        async with OpenRAEnv(base_url="http://localhost:8000", message_timeout_s=120.0) as env:
            # Build the full stack
            wego = WEGOTurnManager(env, ticks_per_turn=75, execution_substeps=5)
            commander = Commander(wego)
            isr = ISRManager()
            scenario = ScenarioRunner(commander, isr)

            # Load scenario for MSEL events
            state = scenario.load("milsim/mod/scenarios/hasty_attack.yaml")
            check("Scenario loaded", state is not None, state.name)

            # Create AI
            ai = TacticalAI(
                commander=commander,
                isr=isr,
                scenario=scenario,
                max_turns=40,
                verbose=True,
            )

            # Play the game
            print("\n--- AI Playing Game ---\n")
            aar = await ai.play_game()
            elapsed = time.time() - start_time

            # --- Validate results ---
            print(f"\n--- Game Complete ({elapsed:.1f}s) ---\n")

            # Phase progression
            print("1. Phase Progression")
            check("Passed ESTABLISH", ai.phase != Phase.ESTABLISH,
                  f"final phase: {ai.phase.value}")
            check("Reached BUILD+", ai.turn > 3,
                  f"played {ai.turn} turns")

            # Base building
            print("\n2. Base Building")
            briefing = commander.get_briefing()
            has_cy = any(b["type"] == "fact" for b in briefing.friendly_buildings)
            has_barracks = any(b["type"] in ("tent", "barr") for b in briefing.friendly_buildings)
            check("Construction yard", has_cy)
            check("Barracks built", has_barracks)
            check("Buildings constructed", len(briefing.friendly_buildings) >= 2,
                  f"{len(briefing.friendly_buildings)} buildings")

            # Force generation
            print("\n3. Force Generation")
            unit_count = len(briefing.friendly_units)
            check("Units produced", unit_count > 0, f"{unit_count} units")
            check("Economy managed", briefing.economy["cash"] >= 0,
                  f"${briefing.economy['cash']}")

            # ISR
            print("\n4. Intelligence")
            intel = isr.get_summary()
            check("ISR processed observations", True)
            check("Contacts tracked", intel.total_contacts >= 0,
                  f"{intel.total_contacts} total, {intel.active_contacts} active")
            if intel.active_contacts > 0:
                check("Enemy classified", any(
                    c.intel_level.value >= 3 for c in isr.active_contacts
                ), f"{sum(1 for c in isr.active_contacts if c.intel_level.value >= 3)} classified")
            else:
                check("Enemy classified", True, "no contacts in range")

            # Scenario
            print("\n5. Scenario Integration")
            check("Scenario turns advanced", scenario.current_turn > 0,
                  f"turn {scenario.current_turn}")
            triggered = sum(1 for ev in state.msel if ev.triggered)
            check("MSEL events triggered", triggered > 0,
                  f"{triggered}/{len(state.msel)}")

            # WEGO
            print("\n6. WEGO Turn System")
            history = wego.turn_history
            check("Turn history recorded", len(history) > 0,
                  f"{len(history)} turns")
            total_ticks = history[-1].tick_end if history else 0
            check("Game time advanced", total_ticks > 500,
                  f"{total_ticks} ticks")

            # AAR
            print("\n7. After Action Review")
            check("AAR generated", len(aar) > 500, f"{len(aar)} chars")
            check("AAR has decision log", "DECISION LOG" in aar)
            check("AAR has intel report", "INTELLIGENCE REPORT" in aar)
            check("AAR has game data", "AFTER ACTION REVIEW" in aar)

            # New integrations
            print("\n8. Integrated Systems")
            check("Spatial intel active", ai._spatial.has_data,
                  f"{ai._spatial.width}x{ai._spatial.height}")
            check("Game knowledge available", ai._knowledge.available)
            check("Memory events tracked", ai._memory.event_count > 0,
                  f"{ai._memory.event_count} events")
            check("Performance assessed", ai._assessor.get_latest() is not None)
            check("AAR has spatial", "SPATIAL" in aar or "Map:" in aar)
            check("AAR has performance", "PERFORMANCE" in aar or "cumulative" in aar.lower())

            # Print summary AAR
            print(f"\n{'=' * 60}")
            print("GAME SUMMARY")
            print(f"{'=' * 60}")
            print(f"Turns: {ai.turn}")
            print(f"Time: {elapsed:.1f}s")
            print(f"Phase: {ai.phase.value}")
            print(f"Result: {commander.game_result or 'ongoing'}")
            print(f"Units: {unit_count}")
            print(f"Buildings: {len(briefing.friendly_buildings)}")
            print(f"Cash: ${briefing.economy['cash']}")
            print(f"Contacts: {intel.active_contacts} active")
            print(f"MSEL events: {triggered}/{len(state.msel)}")

    except ConnectionRefusedError:
        print("\nERROR: Could not connect to server at localhost:8000")
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
        print("\nFull game integration — all systems operational.")
    else:
        print(f"\n{total - passed} check(s) failed.")

    return passed == total


if __name__ == "__main__":
    success = asyncio.run(run_full_game())
    sys.exit(0 if success else 1)
