#!/usr/bin/env python3
"""Test the ISR system against a live game.

Exercises the full intelligence pipeline:
  - Deploy base → build army → scout toward enemy
  - Contact detection and graduation through intel levels
  - Contact persistence when leaving line of sight
  - Intel report formatting
  - Threat axis computation

Usage:
    OPENRA_PATH=/path/to/OpenRA python -m openra_env.server.app --port 8000 &
    python milsim/tests/test_isr.py
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
from openra_env.models import ActionType, CommandModel, OpenRAAction

from milsim.isr import ISRManager, IntelLevel, ContactType
from milsim.wego import WEGOTurnManager
from milsim.commander import Commander, TacticalOrder, OrderType


async def run_isr_test():
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

    print("ISR System Tests")
    print("=" * 60)

    try:
        async with OpenRAEnv(base_url=SERVER_URL, message_timeout_s=120.0) as env:
            wego = WEGOTurnManager(env, ticks_per_turn=75, execution_substeps=5)
            commander = Commander(wego)
            isr = ISRManager(
                turns_to_track=2,
                turns_to_classify=1,
                turns_to_identify=3,
                decay_rate=3,
                max_stale_turns=15,
            )

            # --- Phase 1: Setup base ---
            print("\n1. Base Setup")
            briefing = await commander.start_game()
            obs = wego.get_situation().observation

            # Process initial observation (no enemies expected)
            summary = isr.process_observation(obs, wego.turn_number)
            check("ISR initialized", summary is not None)
            check("No initial contacts", summary.total_contacts == 0)

            # Deploy MCV
            mcv = next((u for u in briefing.friendly_units if u.type == "mcv"), None)
            if mcv:
                await commander.issue_orders([
                    TacticalOrder(order_type=OrderType.DEPLOY, unit_ids=[mcv.actor_id]),
                ])
            for _ in range(2):
                await commander.advance_time()

            # Build barracks quickly
            obs = wego.get_situation().observation
            await commander.issue_orders([
                TacticalOrder(order_type=OrderType.BUILD, item_type="powr"),
            ])
            for _ in range(5):
                await commander.advance_time()

            obs = wego.get_situation().observation
            available = obs.available_production
            barracks = next((t for t in ("tent", "barr") if t in available), None)
            if barracks:
                await commander.issue_orders([
                    TacticalOrder(order_type=OrderType.BUILD, item_type=barracks),
                ])
                for _ in range(5):
                    await commander.advance_time()

            # Train scouts
            result, briefing = await commander.issue_orders([
                TacticalOrder(order_type=OrderType.TRAIN, item_type="e1", count=4),
            ])
            for _ in range(4):
                result, briefing = await commander.advance_time()

            obs = wego.get_situation().observation
            infantry = [u for u in obs.units if u.type == "e1"]
            check("Scouts trained", len(infantry) >= 2, f"{len(infantry)} scouts")

            # --- Phase 2: ISR fundamentals ---
            print("\n2. ISR Fundamentals")

            # Process each turn through ISR
            summary = isr.process_observation(obs, wego.turn_number)
            check("Summary generates", summary is not None)
            check("Base position tracked", isr._base_x > 0 or isr._base_y > 0,
                  f"base=({isr._base_x},{isr._base_y})")

            # Intel report format
            report = isr.format_intel_report()
            check("Intel report formats", "INTELLIGENCE REPORT" in report)
            print(f"\n{report}\n")

            # --- Phase 3: Scout toward enemy ---
            print("\n3. Reconnaissance Mission")

            # Map is 128x128, we're likely near one corner
            # Send scouts toward map center and far corners
            obs = wego.get_situation().observation
            scouts = [u for u in obs.units if u.type == "e1"]
            map_w = obs.map_info.width
            map_h = obs.map_info.height

            if len(scouts) >= 2:
                # Scout 1: toward center
                s1 = scouts[0]
                # Scout 2: toward opposite corner
                s2 = scouts[1]

                target_x = map_w // 2
                target_y = map_h // 2

                result, briefing = await commander.issue_orders([
                    TacticalOrder(
                        order_type=OrderType.RECONNOITER,
                        unit_ids=[s1.actor_id],
                        target_x=target_x,
                        target_y=target_y,
                    ),
                    TacticalOrder(
                        order_type=OrderType.RECONNOITER,
                        unit_ids=[s2.actor_id],
                        target_x=map_w - 10,
                        target_y=map_h - 10,
                    ),
                ])
                note("Recon orders issued",
                      f"→ center ({target_x},{target_y}) and far corner")

            # Advance many turns to let scouts travel and find enemy
            contacts_found = False
            max_scout_turns = 40
            print(f"\n  Scouting (up to {max_scout_turns} turns)...")

            for turn_idx in range(max_scout_turns):
                result, briefing = await commander.advance_time()
                obs = wego.get_situation().observation
                summary = isr.process_observation(obs, wego.turn_number)

                if summary.active_contacts > 0 and not contacts_found:
                    contacts_found = True
                    print(f"  Contact! Turn {wego.turn_number}: "
                          f"{summary.active_contacts} contacts")

                if wego.is_game_over:
                    break

                # Continue scouting for a few more turns after first contact
                if contacts_found and turn_idx > 5:
                    # Keep observing to graduate intel levels
                    pass

                # Early exit if we have enough data
                if summary.active_contacts >= 3 and any(
                    c.intel_level >= IntelLevel.TRACKED
                    for c in isr.active_contacts
                ):
                    break

            # --- Phase 4: Intel Assessment ---
            print("\n4. Intelligence Assessment")
            summary = isr.get_summary()
            check("Contacts detected", summary.total_contacts > 0 or contacts_found,
                  f"{summary.total_contacts} total contacts")

            if summary.total_contacts > 0:
                # Check intel graduation
                detected = len(isr.get_contacts_by_level(IntelLevel.DETECTED))
                tracked = len(isr.get_contacts_by_level(IntelLevel.TRACKED))
                classified = len(isr.get_contacts_by_level(IntelLevel.CLASSIFIED))
                identified = len(isr.get_contacts_by_level(IntelLevel.IDENTIFIED))

                check("Detection works", detected > 0,
                      f"{detected} contacts at DETECTED+")
                check("Graduation works", tracked > 0 or classified > 0 or identified > 0,
                      f"tracked={tracked} classified={classified} identified={identified}")

                # Check contact data quality
                best_contact = max(isr.active_contacts,
                                   key=lambda c: c.intel_level,
                                   default=None)
                if best_contact:
                    check("Contact has position",
                          best_contact.cell_x > 0 or best_contact.cell_y > 0,
                          f"({best_contact.cell_x},{best_contact.cell_y})")
                    check("Contact has category",
                          best_contact.category != ContactType.UNKNOWN,
                          best_contact.category)
                    check("Contact has confidence",
                          best_contact.confidence > 0,
                          f"{best_contact.confidence:.0%}")

                # Check threat axis
                threat = isr.get_threat_axis()
                check("Threat axis computed", threat is not None,
                      f"({threat[0]:+d},{threat[1]:+d})" if threat else "")

                # Check area query
                if best_contact:
                    nearby = isr.get_contacts_in_area(
                        best_contact.cell_x, best_contact.cell_y, 20
                    )
                    check("Area query works", len(nearby) > 0,
                          f"{len(nearby)} contacts within 20 cells")

                # Estimated strength
                check("Strength estimated", summary.estimated_enemy_strength > 0,
                      f"${summary.estimated_enemy_strength}")
            else:
                # Map might be large, enemy not found yet
                check("Detection works", False, "no contacts found in scout range")
                check("Graduation works", False, "n/a")

            # --- Phase 5: Intel Report ---
            print("\n5. Intelligence Report")
            report = isr.format_intel_report()
            check("Report includes contacts", "CONTACT DETAILS" in report or "Contacts: 0" in report)
            check("Report includes categories", "BY CATEGORY" in report)
            check("Report includes strength", "ESTIMATED ENEMY STRENGTH" in report)
            print(f"\n{report}\n")

            # --- Phase 6: Contact persistence ---
            print("\n6. Contact Persistence & Decay")
            if summary.total_contacts > 0:
                # Get a contact and check its tracking data
                any_contact = next(iter(isr.contacts.values()))
                check("Contact has ID", any_contact.contact_id.startswith("C"))
                check("First seen recorded", any_contact.first_seen_turn > 0,
                      f"turn {any_contact.first_seen_turn}")
                check("Observation count", any_contact.turns_observed >= 1,
                      f"{any_contact.turns_observed} observations")

                # Simulate decay by processing observation without enemies
                # (The actual game keeps updating, so contacts may still be visible)
                pre_level = any_contact.intel_level
                check("Intel level tracked", pre_level >= IntelLevel.DETECTED,
                      any_contact.intel_label)
            else:
                note("Contact has ID", "skipped — no contacts")
                note("First seen recorded", "skipped")
                note("Observation count", "skipped")
                note("Intel level tracked", "skipped")

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
        print("\nISR system fully operational.")
    else:
        failed = total - passed
        print(f"\n{failed} check(s) failed.")
        if not contacts_found:
            print("Note: Enemy not found during scouting — map may be large.")
            print("ISR core logic is validated; detection depends on scout range.")

    return passed == total


@pytest.mark.integration
def test_isr_system(openra_server):
    """ISR contact/track pipeline against a live server.

    Skipped (never silently passed) when no server is reachable; the
    `openra_server` fixture does the probing.
    """
    assert asyncio.run(run_isr_test()), (
        "one or more checks failed - see the [FAIL] lines in captured output"
    )


if __name__ == "__main__":
    if not require_server_or_explain():
        sys.exit(1)
    success = asyncio.run(run_isr_test())
    sys.exit(0 if success else 1)
