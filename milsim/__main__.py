"""MilSim game runner — play Red Alert through the LLM commander interface.

Usage:
    # Rule-based AI (no LLM needed)
    python -m milsim --mode rule

    # Structured text with any OpenAI-compatible LLM
    python -m milsim --mode text --model gpt-4o --api-key $OPENAI_API_KEY

    # Function-calling with tool support
    python -m milsim --mode fc --model claude-sonnet-4-20250514 --base-url https://api.anthropic.com/v1

    # MCP server (stdio, for Claude Desktop / Claude Code)
    python -m milsim --mode mcp

    # FastAdvance mode (uses MCP client for server-side interrupt detection)
    python -m milsim --mode rule --fast
"""

import argparse
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "openra-rl"))


def parse_args():
    p = argparse.ArgumentParser(description="MilSim — Military Simulation Commander")
    p.add_argument("--mode", choices=["rule", "text", "fc", "mcp"], default="rule",
                    help="Commander mode: rule (scripted AI), text (structured text LLM), "
                         "fc (function-calling LLM), mcp (MCP server)")
    p.add_argument("--server", default="http://localhost:8000",
                    help="OpenRA-RL server URL")
    p.add_argument("--fast", action="store_true",
                    help="Use FastAdvance (MCP client) for server-side interrupts")
    p.add_argument("--max-turns", type=int, default=60,
                    help="Maximum turns to play")
    p.add_argument("--scenario", default="milsim/mod/scenarios/hasty_attack.yaml",
                    help="Scenario YAML file")
    p.add_argument("--quiet", action="store_true", help="Suppress turn-by-turn output")

    # LLM options (for text/fc modes)
    p.add_argument("--model", default="", help="LLM model name")
    p.add_argument("--base-url", default="", help="LLM API base URL")
    p.add_argument("--api-key", default="", help="LLM API key")
    return p.parse_args()


async def run_rule_based(args):
    """Run the rule-based tactical AI."""
    from openra_env.client import OpenRAEnv
    from milsim.wego import WEGOTurnManager
    from milsim.commander import Commander
    from milsim.isr import ISRManager
    from milsim.scenario import ScenarioRunner
    from milsim.tactical_ai import TacticalAI

    mcp_client = None
    if args.fast:
        from openra_env.mcp_ws_client import OpenRAMCPClient
        mcp_client = await OpenRAMCPClient(base_url=args.server).connect()

    async with OpenRAEnv(base_url=args.server, message_timeout_s=120.0) as env:
        wego = WEGOTurnManager(
            env, ticks_per_turn=75, execution_substeps=5,
            mcp_client=mcp_client,
        )
        commander = Commander(wego)
        isr = ISRManager()
        scenario = ScenarioRunner(commander, isr)

        if os.path.exists(args.scenario):
            scenario.load(args.scenario)

        ai = TacticalAI(
            commander=commander,
            isr=isr,
            scenario=scenario,
            max_turns=args.max_turns,
            verbose=not args.quiet,
        )

        print(f"MilSim — Rule-based AI {'(FastAdvance)' if args.fast else ''}")
        print(f"Server: {args.server}")
        print(f"Max turns: {args.max_turns}")
        print("=" * 60)

        aar = await ai.play_game()

        print("\n" + aar)

    if mcp_client:
        await mcp_client.close()


async def run_structured_text(args):
    """Run with structured text LLM interface."""
    from openra_env.client import OpenRAEnv
    from milsim.wego import WEGOTurnManager
    from milsim.commander import Commander
    from milsim.isr import ISRManager
    from milsim.scenario import ScenarioRunner
    from milsim.llm_commander import CommanderBridge, StructuredTextCommander

    if not args.model:
        print("ERROR: --model required for text mode")
        sys.exit(1)

    import httpx

    async with OpenRAEnv(base_url=args.server, message_timeout_s=120.0) as env:
        wego = WEGOTurnManager(env, ticks_per_turn=75, execution_substeps=5)
        commander = Commander(wego)
        isr = ISRManager()
        scenario = ScenarioRunner(commander, isr)

        if os.path.exists(args.scenario):
            scenario.load(args.scenario)

        bridge = CommanderBridge(commander, isr, scenario, verbose=not args.quiet)
        text_cmd = StructuredTextCommander(bridge)

        await bridge.start()

        print(f"MilSim — Structured Text LLM ({args.model})")
        print("=" * 60)

        system_prompt = (
            "You are a military commander playing Red Alert. "
            "Read the briefing and respond with orders, one per line. "
            "Use the order syntax shown at the bottom of the briefing."
        )

        base_url = args.base_url or "https://openrouter.ai/api/v1"
        api_key = args.api_key or os.environ.get("OPENAI_API_KEY", "")

        turn = 0
        while not bridge.game_over and turn < args.max_turns:
            turn += 1
            briefing = text_cmd.format_briefing()

            if not args.quiet:
                print(f"\n--- Turn {turn} ---")

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": briefing},
            ]

            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{base_url}/chat/completions",
                    json={
                        "model": args.model,
                        "messages": messages,
                        "max_tokens": 512,
                        "temperature": 0.3,
                    },
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=60.0,
                )
                resp.raise_for_status()
                data = resp.json()

            llm_response = data["choices"][0]["message"]["content"]
            orders = text_cmd.parse_orders(llm_response)

            if not args.quiet:
                print(f"LLM orders: {len(orders)}")
                for o in orders:
                    print(f"  {o.order_type.value} {o.item_type or ''} {o.unit_ids or ''}")

            result_text = await bridge.execute(orders)
            if not args.quiet:
                print(result_text)

        print("\n" + bridge.get_aar())


async def run_function_calling(args):
    """Run with function-calling LLM interface."""
    from openra_env.client import OpenRAEnv
    from milsim.wego import WEGOTurnManager
    from milsim.commander import Commander
    from milsim.isr import ISRManager
    from milsim.scenario import ScenarioRunner
    from milsim.llm_commander import CommanderBridge, FunctionCallingCommander

    if not args.model:
        print("ERROR: --model required for fc mode")
        sys.exit(1)

    import httpx
    import json

    async with OpenRAEnv(base_url=args.server, message_timeout_s=120.0) as env:
        wego = WEGOTurnManager(env, ticks_per_turn=75, execution_substeps=5)
        commander = Commander(wego)
        isr = ISRManager()
        scenario = ScenarioRunner(commander, isr)

        if os.path.exists(args.scenario):
            scenario.load(args.scenario)

        bridge = CommanderBridge(commander, isr, scenario, verbose=not args.quiet)
        fc = FunctionCallingCommander(bridge)

        await bridge.start()

        print(f"MilSim — Function-Calling LLM ({args.model})")
        print("=" * 60)

        system_prompt = fc.get_system_prompt()
        tools = fc.get_tools()
        base_url = args.base_url or "https://openrouter.ai/api/v1"
        api_key = args.api_key or os.environ.get("OPENAI_API_KEY", "")

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "Game started. Call get_briefing to see the initial situation."},
        ]

        turn = 0
        while not bridge.game_over and turn < args.max_turns * 5:
            turn += 1

            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{base_url}/chat/completions",
                    json={
                        "model": args.model,
                        "messages": messages,
                        "tools": tools,
                        "tool_choice": "auto",
                        "max_tokens": 1024,
                        "temperature": 0.3,
                    },
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=60.0,
                )
                resp.raise_for_status()
                data = resp.json()

            choice = data["choices"][0]
            msg = choice["message"]
            messages.append(msg)

            if not msg.get("tool_calls"):
                if msg.get("content") and not args.quiet:
                    print(f"LLM: {msg['content'][:200]}")
                messages.append({
                    "role": "user",
                    "content": "Continue playing. Call get_briefing or advance_turn.",
                })
                continue

            for tc in msg["tool_calls"]:
                fn_name = tc["function"]["name"]
                fn_args = json.loads(tc["function"]["arguments"])

                if not args.quiet:
                    print(f"  Tool: {fn_name}({fn_args})")

                result_text = await fc.handle_tool_call(fn_name, fn_args)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result_text[:4000],
                })

            if len(messages) > 60:
                messages = [messages[0]] + messages[-30:]

        print("\n" + bridge.get_aar())


async def run_mcp_server(args):
    """Run as MCP server for Claude Desktop / Claude Code."""
    from openra_env.client import OpenRAEnv
    from milsim.wego import WEGOTurnManager
    from milsim.commander import Commander
    from milsim.isr import ISRManager
    from milsim.scenario import ScenarioRunner
    from milsim.llm_commander import CommanderBridge, create_mcp_server

    env = OpenRAEnv(base_url=args.server, message_timeout_s=300.0)
    await env.__aenter__()

    wego = WEGOTurnManager(env, ticks_per_turn=75, execution_substeps=5)
    commander = Commander(wego)
    isr = ISRManager()
    scenario = ScenarioRunner(commander, isr)

    if os.path.exists(args.scenario):
        scenario.load(args.scenario)

    bridge = CommanderBridge(commander, isr, scenario, verbose=True)
    server = create_mcp_server(bridge)

    print("MilSim MCP server starting (stdio)...", file=sys.stderr)
    server.run()


def main():
    args = parse_args()

    match args.mode:
        case "rule":
            asyncio.run(run_rule_based(args))
        case "text":
            asyncio.run(run_structured_text(args))
        case "fc":
            asyncio.run(run_function_calling(args))
        case "mcp":
            asyncio.run(run_mcp_server(args))


if __name__ == "__main__":
    main()
