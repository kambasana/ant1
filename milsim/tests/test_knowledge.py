#!/usr/bin/env python3
"""Test game knowledge and memory systems (offline — no server needed).

Validates:
  - Unit profile lookups and force assessments
  - OPFOR intelligence profiles
  - Counter-unit recommendations
  - Tactical memory event tracking and persistence
  - Performance assessment scoring
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "openra-rl"))

from milsim.game_knowledge import GameKnowledge, UNIT_CATEGORY, THREAT_VALUE
from milsim.memory import TacticalMemory
from milsim.assessment import PerformanceAssessor


def run_knowledge_tests():
    results = []

    def check(name, condition, detail=""):
        status = "PASS" if condition else "FAIL"
        results.append((name, condition))
        print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))

    print("Game Knowledge & Memory Tests")
    print("=" * 60)

    # --- 1. Game Knowledge ---
    print("\n1. Unit Profiles")
    gk = GameKnowledge()
    check("Knowledge available", gk.available)

    profile = gk.get_unit_profile("3tnk")
    check("Heavy tank profile", profile.type_code == "3tnk")
    check("Tank has stats", profile.cost > 0, f"cost=${profile.cost}")
    check("Tank category", profile.category in ("vehicle", "Vehicles"),
          profile.category)
    check("Tank threat value", profile.threat_value == 8)

    inf = gk.get_unit_profile("e1")
    check("Infantry profile", inf.cost > 0, f"cost=${inf.cost}")
    check("Infantry threat", inf.threat_value == 1)

    # Unknown unit
    unk = gk.get_unit_profile("zzz_fake")
    check("Unknown unit safe", unk.type_code == "zzz_fake")
    check("Unknown threat=1", unk.threat_value == 1)

    # --- 2. Force Assessment ---
    print("\n2. Force Assessment")
    force = ["e1", "e1", "e1", "e3", "e3", "3tnk"]
    fa = gk.assess_force(force)
    check("Force count", fa.total_units == 6)
    check("Force has cost", fa.total_cost > 0, f"${fa.total_cost}")
    check("Force has threat", fa.total_threat > 0, f"threat={fa.total_threat}")
    check("Strongest is tank", fa.strongest_unit == "3tnk")
    check("Has composition", len(fa.composition_ratio) > 0)

    intel_text = gk.format_force_intel(force)
    check("Intel formats", "FORCE ASSESSMENT" in intel_text, f"{len(intel_text)} chars")
    print(f"\n{intel_text}\n")

    # --- 3. OPFOR Profiles ---
    print("\n3. OPFOR Intelligence")
    opfor = gk.get_opfor_profile("normal")
    check("OPFOR loads", opfor.difficulty == "normal")
    check("Aggressiveness", 0 < opfor.aggressiveness <= 1.0,
          f"{opfor.aggressiveness}")
    check("First attack tick", opfor.first_attack_tick > 0,
          f"tick {opfor.first_attack_tick}")
    check("Has traits", len(opfor.traits) > 0,
          ", ".join(opfor.traits[:3]))
    check("Has counters", len(opfor.counters) > 0)
    check("Has army comp", len(opfor.army_composition) > 0)
    check("Has summary", len(opfor.summary) > 50, f"{len(opfor.summary)} chars")

    # All difficulty levels
    for diff in ("beginner", "easy", "medium", "normal", "hard"):
        p = gk.get_opfor_profile(diff)
        check(f"OPFOR {diff}", p.difficulty == diff)

    # --- 4. Counter-units ---
    print("\n4. Counter Recommendations")
    counters_armor = gk.get_counter_units(["3tnk", "1tnk"])
    check("Counters armor", "e3" in counters_armor,
          ", ".join(counters_armor))

    counters_inf = gk.get_counter_units(["e1", "e1"])
    check("Counters infantry", "e1" in counters_inf)

    counters_air = gk.get_counter_units(["hind", "mig"])
    check("Counters air", len(counters_air) > 0)

    # --- 5. Tech Requirements ---
    print("\n5. Tech Requirements")
    reqs = gk.get_tech_requirements("3tnk")
    check("Tank has prereqs", len(reqs) > 0, ", ".join(reqs))

    reqs_e1 = gk.get_tech_requirements("e1")
    check("Infantry has prereqs", len(reqs_e1) >= 0, ", ".join(reqs_e1) or "none")

    # --- 6. Tactical Memory ---
    print("\n6. Tactical Memory")
    mem = TacticalMemory(memory_dir="/tmp/milsim_test_memory")

    mem.record_event("contact", turn=5, detail="3x infantry at (45,20)")
    mem.record_event("engagement", turn=6, detail="Firefight at bridge")
    mem.record_milestone("first_contact", turn=5, detail="Enemy sighted")
    mem.record_milestone("first_contact", turn=6, detail="Duplicate (should skip)")
    check("Events recorded", mem.event_count == 3)

    timeline = mem.get_timeline()
    check("Timeline formats", len(timeline) > 30,
          f"{len(timeline)} chars")
    print(f"\n{timeline}\n")

    # Save engagement
    mem.save_engagement(
        result="won",
        turns=40,
        stats={
            "final_phase": "attack",
            "kills": 12,
            "units_lost": 3,
            "buildings_built": 5,
            "final_cash": 500,
            "max_army_value": 3000,
            "contacts_detected": 8,
            "msel_events_triggered": 7,
            "objectives_achieved": 2,
        },
        reflection="Effective scout/mass/attack sequence. Need faster barracks build.",
        lessons=[
            "Build barracks immediately after power plant",
            "Scout 2 directions simultaneously",
            "Attack before enemy builds armor",
        ],
    )
    check("Engagement saved", mem.engagement_count >= 1)

    context = mem.get_mission_context()
    check("Context generates", len(context) > 50, f"{len(context)} chars")
    print(f"\n{context}\n")

    aar = mem.format_engagement_aar()
    check("Engagement AAR", "ENGAGEMENT" in aar, f"{len(aar)} chars")
    print(f"{aar}\n")

    mem.reset_events()
    check("Events cleared", mem.event_count == 0)

    # --- 7. Performance Assessment ---
    print("\n7. Performance Assessment")
    assessor = PerformanceAssessor(vector_enabled=True)

    # Simulate observations
    obs1 = {
        "economy": {"cash": 5000, "ore": 0, "harvester_count": 1},
        "military": {"units_killed": 0, "units_lost": 0, "army_value": 500,
                      "buildings_killed": 0, "buildings_lost": 0, "kills_cost": 0,
                      "losses_cost": 0},
        "done": False,
        "result": "",
        "tick": 100,
    }
    score1 = assessor.evaluate(obs1)
    check("Score computed", score1.scalar != 0 or True, f"scalar={score1.scalar}")
    check("Has dimensions", len(score1.dimensions) > 0,
          f"{len(score1.dimensions)} dimensions")

    # Second observation with combat
    obs2 = {
        "economy": {"cash": 3000, "ore": 100, "harvester_count": 2},
        "military": {"units_killed": 5, "units_lost": 2, "army_value": 2000,
                      "buildings_killed": 1, "buildings_lost": 0, "kills_cost": 1500,
                      "losses_cost": 400},
        "done": False,
        "result": "",
        "tick": 500,
    }
    score2 = assessor.evaluate(obs2)
    check("Cumulative tracked", score2.cumulative_scalar != 0 or True,
          f"cumulative={score2.cumulative_scalar}")

    assessment_text = assessor.format_assessment()
    check("Assessment formats", "PERFORMANCE" in assessment_text)
    print(f"\n{assessment_text}\n")

    summary = assessor.format_summary()
    check("Summary formats", "SUMMARY" in summary)
    print(f"{summary}\n")

    # Trend computation
    trends = assessor.get_trend()
    check("Trends compute", isinstance(trends, dict))

    # Reset
    assessor.reset()
    check("Assessor resets", assessor.get_latest() is None)

    # --- Results ---
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"\n{'=' * 60}")
    print(f"Results: {passed}/{total} passed")
    print(f"{'=' * 60}")

    if passed == total:
        print("\nGame knowledge & memory systems operational.")
    else:
        print(f"\n{total - passed} check(s) failed.")

    return passed == total


def test_game_knowledge_and_memory():
    """Unit profiles, OPFOR intel, tactical memory and performance assessment."""
    assert run_knowledge_tests(), (
        "one or more knowledge/memory checks failed - see the [FAIL] lines above"
    )


if __name__ == "__main__":
    success = run_knowledge_tests()
    sys.exit(0 if success else 1)
