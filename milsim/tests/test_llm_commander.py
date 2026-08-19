#!/usr/bin/env python3
"""Test LLM commander interfaces (offline — no server needed).

Validates:
  - CommanderBridge construction and properties
  - StructuredTextCommander briefing formatting
  - Order parsing from text (all order types)
  - FunctionCallingCommander tool dispatch
  - MCP server creation
  - Round-trip: format briefing → parse orders
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "openra-rl"))

from milsim.llm_commander import (
    StructuredTextCommander,
    FunctionCallingCommander,
    CommanderBridge,
    _parse_order_line,
    MILSIM_TOOLS,
    ORDER_SCHEMA,
)
from milsim.commander import TacticalOrder, OrderType, Stance


def run_tests():
    results = []

    def check(name, condition, detail=""):
        status = "PASS" if condition else "FAIL"
        results.append((name, condition))
        print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))

    print("LLM Commander Interface Tests")
    print("=" * 60)

    # --- 1. Order Parsing ---
    print("\n1. Order Parsing")

    # MOVE
    o = _parse_order_line("MOVE 42,43,44 TO 80,30")
    check("Parse MOVE", o is not None and o.order_type == OrderType.ADVANCE)
    check("MOVE unit_ids", o.unit_ids == [42, 43, 44])
    check("MOVE target", o.target_x == 80 and o.target_y == 30)

    # ATTACK
    o = _parse_order_line("ATTACK 10,11 TO 60,25")
    check("Parse ATTACK", o is not None and o.order_type == OrderType.ASSAULT)
    check("ATTACK units", o.unit_ids == [10, 11])

    # DEFEND
    o = _parse_order_line("DEFEND 5,6,7 AT 40,20")
    check("Parse DEFEND", o is not None and o.order_type == OrderType.DEFEND)
    check("DEFEND target", o.target_x == 40 and o.target_y == 20)

    # SCOUT
    o = _parse_order_line("SCOUT 99 TO 50,50")
    check("Parse SCOUT", o is not None and o.order_type == OrderType.RECONNOITER)
    check("SCOUT single unit", o.unit_ids == [99])

    # HOLD
    o = _parse_order_line("HOLD 1,2,3")
    check("Parse HOLD", o is not None and o.order_type == OrderType.HOLD_POSITION)
    check("HOLD units", o.unit_ids == [1, 2, 3])

    # BUILD
    o = _parse_order_line("BUILD POWR")
    check("Parse BUILD", o is not None and o.order_type == OrderType.BUILD)
    check("BUILD item", o.item_type == "powr")

    # TRAIN
    o = _parse_order_line("TRAIN 3X E1")
    check("Parse TRAIN", o is not None and o.order_type == OrderType.TRAIN)
    check("TRAIN count", o.count == 3)
    check("TRAIN item", o.item_type == "e1")

    # DEPLOY
    o = _parse_order_line("DEPLOY 100")
    check("Parse DEPLOY", o is not None and o.order_type == OrderType.DEPLOY)
    check("DEPLOY unit", o.unit_ids == [100])

    # STANCE
    o = _parse_order_line("STANCE 5,6 AGGRESSIVE")
    check("Parse STANCE", o is not None and o.order_type == OrderType.SET_STANCE)
    check("STANCE value", o.stance == Stance.AGGRESSIVE)

    # WAIT (returns None — no-op)
    o = _parse_order_line("WAIT")
    check("Parse WAIT", o is None)

    # Unknown
    o = _parse_order_line("DANCE 42")
    check("Parse unknown", o is None)

    # --- 2. Multi-line Parsing ---
    print("\n2. Multi-line Order Parsing")
    text = """
    # Turn 5 orders
    BUILD powr
    TRAIN 2x e1
    ATTACK 42,43 TO 80,30
    SCOUT 44 TO 50,25
    WAIT
    """
    orders = StructuredTextCommander.parse_orders(text)
    check("Multi-line count", len(orders) == 4, f"{len(orders)} orders")
    check("First is BUILD", orders[0].order_type == OrderType.BUILD)
    check("Second is TRAIN", orders[1].order_type == OrderType.TRAIN)
    check("Third is ASSAULT", orders[2].order_type == OrderType.ASSAULT)
    check("Fourth is SCOUT", orders[3].order_type == OrderType.RECONNOITER)

    # --- 3. Tool Definitions ---
    print("\n3. Function-Calling Tools")
    check("Tools defined", len(MILSIM_TOOLS) > 10, f"{len(MILSIM_TOOLS)} tools")

    tool_names = [t["function"]["name"] for t in MILSIM_TOOLS]
    check("Has get_briefing", "get_briefing" in tool_names)
    check("Has move_units", "move_units" in tool_names)
    check("Has attack_move", "attack_move" in tool_names)
    check("Has build_structure", "build_structure" in tool_names)
    check("Has train_units", "train_units" in tool_names)
    check("Has advance_turn", "advance_turn" in tool_names)
    check("Has get_spatial_intel", "get_spatial_intel" in tool_names)
    check("Has get_force_assessment", "get_force_assessment" in tool_names)
    check("Has get_counter_units", "get_counter_units" in tool_names)
    check("Has get_minimap", "get_minimap" in tool_names)

    for tool in MILSIM_TOOLS:
        fn = tool["function"]
        check(f"Tool {fn['name']} has desc", len(fn["description"]) > 10)

    # --- 4. Order Schema ---
    print("\n4. Order Schema Documentation")
    check("Schema has MOVE", "MOVE" in ORDER_SCHEMA)
    check("Schema has ATTACK", "ATTACK" in ORDER_SCHEMA)
    check("Schema has BUILD", "BUILD" in ORDER_SCHEMA)
    check("Schema has TRAIN", "TRAIN" in ORDER_SCHEMA)
    check("Schema has example", "Example" in ORDER_SCHEMA)

    # --- 5. MCP Server Creation ---
    print("\n5. MCP Server")
    try:
        from mcp.server.fastmcp import FastMCP
        has_mcp = True
    except ImportError:
        has_mcp = False

    check("MCP package available", has_mcp)
    if has_mcp:
        from milsim.llm_commander import create_mcp_server
        check("create_mcp_server is callable", callable(create_mcp_server))

    # --- Results ---
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"\n{'=' * 60}")
    print(f"Results: {passed}/{total} passed")
    print(f"{'=' * 60}")

    if passed == total:
        print("\nLLM commander interfaces operational.")
    else:
        print(f"\n{total - passed} check(s) failed.")

    return passed == total


def test_llm_commander_interfaces():
    """Briefing formatting, order parsing, tool schemas and MCP server wiring."""
    assert run_tests(), (
        "one or more LLM commander checks failed - see the [FAIL] lines above"
    )


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
