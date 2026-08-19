#!/usr/bin/env python3
"""Provider/Ollama tests for milsim's AI elements — fully offline.

No Ollama daemon, no network, no API key: every HTTP exchange goes through
an httpx.MockTransport. Covers:
  - Ollama is the default provider and needs no API key
  - config file + environment + override precedence
  - URL normalization (bare host:port, /v1, full chat URL)
  - chat() request shape and response handling
  - unreachable daemon / missing model → actionable LLMUnavailableError
  - StructuredTextCommander and LLMTacticalAI driven by a mocked Ollama

Run:  python3 milsim/tests/test_llm_provider.py
  or  python3 -m pytest milsim/tests/test_llm_provider.py -v
"""

import asyncio
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "openra-rl"))

import httpx
import pytest

from milsim.llm_provider import (
    DEFAULT_OLLAMA_HOST,
    DEFAULT_OLLAMA_MODEL,
    LLMClient,
    LLMConfigError,
    LLMProviderConfig,
    LLMUnavailableError,
    build_llm,
    normalize_base_url,
)
from milsim.commander import CommanderBriefing, OrderType, UnitSummary
from milsim.llm_commander import FunctionCallingCommander, StructuredTextCommander
from milsim.tactical_ai import LLMTacticalAI


# ── helpers ───────────────────────────────────────────────────────

def chat_response(content="", tool_calls=None):
    """An OpenAI-shaped chat completion body, as Ollama's /v1 endpoint sends."""
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": "test-model",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def mock_client(handler, config=None, **cfg_overrides):
    """LLMClient whose HTTP layer is an httpx.MockTransport."""
    cfg = config or LLMProviderConfig.from_env(env={}, **cfg_overrides)
    return LLMClient(cfg, transport=httpx.MockTransport(handler))


def recording_handler(body, status=200, sink=None):
    """Mock transport handler that records the request it received."""
    def handle(request: httpx.Request) -> httpx.Response:
        if sink is not None:
            sink.append(request)
        if request.url.path.endswith("/api/tags"):
            return httpx.Response(200, json={"models": [{"name": DEFAULT_OLLAMA_MODEL}]})
        if isinstance(body, (dict, list)):
            return httpx.Response(status, json=body)
        return httpx.Response(status, text=str(body))
    return handle


def run(coro):
    return asyncio.run(coro)


# ── config: Ollama is the keyless default ─────────────────────────

def test_default_provider_is_ollama_without_any_env():
    cfg = LLMProviderConfig.from_env(env={})
    assert cfg.provider == "ollama"
    assert cfg.base_url == DEFAULT_OLLAMA_HOST
    assert cfg.model == DEFAULT_OLLAMA_MODEL
    assert cfg.api_key == ""
    assert cfg.requires_api_key is False
    # No API key anywhere is a valid configuration.
    cfg.validate()
    assert cfg.chat_url == "http://localhost:11434/v1/chat/completions"
    assert cfg.root_url == "http://localhost:11434"


def test_no_authorization_header_without_key():
    cfg = LLMProviderConfig.from_env(env={})
    assert "Authorization" not in cfg.headers()


def test_ollama_host_env_overrides_default():
    cfg = LLMProviderConfig.from_env(env={"OLLAMA_HOST": "http://gpu-box:11434"})
    assert cfg.base_url == "http://gpu-box:11434"
    assert cfg.chat_url == "http://gpu-box:11434/v1/chat/completions"


def test_bare_host_port_gets_a_scheme():
    # OLLAMA_HOST is conventionally written without a scheme.
    cfg = LLMProviderConfig.from_env(env={"OLLAMA_HOST": "127.0.0.1:11500"})
    assert cfg.base_url == "http://127.0.0.1:11500"
    assert cfg.chat_url == "http://127.0.0.1:11500/v1/chat/completions"
    assert cfg.is_local is True


def test_url_normalization_is_idempotent_for_full_urls():
    assert normalize_base_url("http://x:1/v1/") == "http://x:1/v1"
    cfg = LLMProviderConfig.from_env(
        env={"OLLAMA_HOST": "http://x:1/v1/chat/completions"}
    )
    assert cfg.chat_url == "http://x:1/v1/chat/completions"
    assert cfg.root_url == "http://x:1"


def test_model_from_env():
    assert LLMProviderConfig.from_env(env={"OLLAMA_MODEL": "qwen3:8b"}).model == "qwen3:8b"
    assert LLMProviderConfig.from_env(env={"MILSIM_LLM_MODEL": "mistral"}).model == "mistral"
    # MILSIM_LLM_MODEL is the provider-agnostic name and wins.
    both = LLMProviderConfig.from_env(
        env={"MILSIM_LLM_MODEL": "mistral", "OLLAMA_MODEL": "qwen3:8b"}
    )
    assert both.model == "mistral"


def test_numeric_env_values_are_parsed():
    cfg = LLMProviderConfig.from_env(
        env={"MILSIM_LLM_MAX_TOKENS": "256", "MILSIM_LLM_TEMPERATURE": "0.0",
             "MILSIM_LLM_TIMEOUT_S": "12.5"}
    )
    assert cfg.max_tokens == 256
    assert cfg.temperature == 0.0
    assert cfg.request_timeout_s == 12.5


def test_bad_numeric_env_value_is_a_config_error():
    with pytest.raises(LLMConfigError) as exc:
        LLMProviderConfig.from_env(env={"MILSIM_LLM_MAX_TOKENS": "lots"})
    assert "max_tokens" in str(exc.value)


def test_unknown_provider_names_are_rejected_with_the_known_list():
    with pytest.raises(LLMConfigError) as exc:
        LLMProviderConfig.from_env(env={"MILSIM_LLM_PROVIDER": "skynet"})
    msg = str(exc.value)
    assert "ollama" in msg and "skynet" in msg


def test_remote_provider_without_key_points_back_at_ollama():
    cfg = LLMProviderConfig.from_env(env={"MILSIM_LLM_PROVIDER": "openai"})
    assert cfg.requires_api_key is True
    with pytest.raises(LLMConfigError) as exc:
        cfg.validate()
    msg = str(exc.value)
    assert "OPENAI_API_KEY" in msg
    assert "ollama" in msg.lower()


def test_api_key_is_sent_when_configured():
    cfg = LLMProviderConfig.from_env(
        env={"MILSIM_LLM_PROVIDER": "openrouter", "OPENROUTER_API_KEY": "sk-test"}
    )
    cfg.validate()
    assert cfg.headers()["Authorization"] == "Bearer sk-test"


def test_config_file_then_env_then_override(tmp_path):
    path = tmp_path / "llm.json"
    path.write_text(json.dumps({
        "llm": {"provider": "ollama", "model": "from-file", "base_url": "http://file-host:11434"}
    }))

    from_file = LLMProviderConfig.load(str(path), env={})
    assert from_file.model == "from-file"
    assert from_file.base_url == "http://file-host:11434"

    # environment beats the file
    from_env = LLMProviderConfig.load(str(path), env={"MILSIM_LLM_MODEL": "from-env"})
    assert from_env.model == "from-env"
    assert from_env.base_url == "http://file-host:11434"

    # explicit override beats both
    override = LLMProviderConfig.load(
        str(path), env={"MILSIM_LLM_MODEL": "from-env"}, model="from-arg"
    )
    assert override.model == "from-arg"


def test_yaml_config_file_and_milsim_llm_config_env(tmp_path):
    path = tmp_path / "llm.yaml"
    path.write_text("llm:\n  provider: ollama\n  model: yaml-model\n")
    cfg = LLMProviderConfig.load(env={"MILSIM_LLM_CONFIG": str(path)})
    assert cfg.model == "yaml-model"
    assert cfg.provider == "ollama"


def test_missing_explicit_config_file_is_an_error(tmp_path):
    with pytest.raises(LLMConfigError) as exc:
        LLMProviderConfig.load(str(tmp_path / "nope.yaml"), env={})
    assert "not found" in str(exc.value)


def test_describe_never_leaks_the_key():
    cfg = LLMProviderConfig.from_env(
        env={"MILSIM_LLM_PROVIDER": "openrouter", "OPENROUTER_API_KEY": "sk-secret"}
    )
    assert "sk-secret" not in cfg.describe()
    assert "key set" in cfg.describe()


# ── client: request shape ─────────────────────────────────────────

def test_chat_posts_openai_shape_to_ollama_v1_endpoint():
    seen = []
    client = mock_client(recording_handler(chat_response("BUILD powr"), sink=seen))
    data = run(client.chat([{"role": "user", "content": "hi"}]))

    assert data["choices"][0]["message"]["content"] == "BUILD powr"
    request = seen[-1]
    assert str(request.url) == "http://localhost:11434/v1/chat/completions"
    assert "authorization" not in {k.lower() for k in request.headers}
    payload = json.loads(request.content)
    assert payload["model"] == DEFAULT_OLLAMA_MODEL
    assert payload["stream"] is False
    assert payload["messages"] == [{"role": "user", "content": "hi"}]


def test_chat_forwards_tools_for_function_calling():
    seen = []
    client = mock_client(recording_handler(chat_response("ok"), sink=seen))
    tools = [{"type": "function", "function": {"name": "get_briefing", "parameters": {}}}]
    run(client.chat([{"role": "user", "content": "hi"}], tools=tools))
    payload = json.loads(seen[-1].content)
    assert payload["tools"] == tools
    assert payload["tool_choice"] == "auto"


def test_complete_text_returns_the_message_content():
    client = mock_client(recording_handler(chat_response("MOVE 1 TO 2,3")))
    assert run(client.complete_text([{"role": "user", "content": "x"}])) == "MOVE 1 TO 2,3"


def test_tool_calls_survive_complete():
    calls = [{"id": "c1", "type": "function",
              "function": {"name": "advance_turn", "arguments": "{}"}}]
    client = mock_client(recording_handler(chat_response(tool_calls=calls)))
    message = run(client.complete([{"role": "user", "content": "x"}]))
    assert message["tool_calls"][0]["function"]["name"] == "advance_turn"


# ── client: failures are actionable, not tracebacks ───────────────

def test_daemon_down_gives_an_actionable_error():
    def refuse(request):
        raise httpx.ConnectError("[Errno 111] Connection refused", request=request)

    client = mock_client(refuse)
    with pytest.raises(LLMUnavailableError) as exc:
        run(client.chat([{"role": "user", "content": "hi"}]))

    msg = str(exc.value)
    assert "Cannot reach the ollama endpoint" in msg
    assert "ollama serve" in msg
    assert f"ollama pull {DEFAULT_OLLAMA_MODEL}" in msg
    assert "OLLAMA_HOST" in msg
    assert DEFAULT_OLLAMA_HOST in msg
    # The httpx exception is translated, not re-raised.
    assert exc.value.__cause__ is None


def test_timeout_is_also_translated():
    def slow(request):
        raise httpx.ReadTimeout("timed out", request=request)

    client = mock_client(slow)
    with pytest.raises(LLMUnavailableError) as exc:
        run(client.chat([{"role": "user", "content": "hi"}]))
    assert "ReadTimeout" in str(exc.value)
    assert "ollama serve" in str(exc.value)


def test_model_not_pulled_tells_you_to_pull_it():
    client = mock_client(
        recording_handler({"error": {"message": "model 'qwen2.5:7b-instruct' not found"}}, status=404),
        model="qwen2.5:7b-instruct",
    )
    with pytest.raises(LLMUnavailableError) as exc:
        run(client.chat([{"role": "user", "content": "hi"}]))
    msg = str(exc.value)
    assert "HTTP 404" in msg
    assert "ollama pull qwen2.5:7b-instruct" in msg


def test_auth_failure_suggests_going_local():
    cfg = LLMProviderConfig.from_env(
        env={"MILSIM_LLM_PROVIDER": "openrouter", "OPENROUTER_API_KEY": "sk-bad"}
    )
    client = mock_client(recording_handler({"error": "no"}, status=401), config=cfg)
    with pytest.raises(LLMUnavailableError) as exc:
        run(client.chat([{"role": "user", "content": "hi"}]))
    msg = str(exc.value)
    assert "OPENROUTER_API_KEY" in msg
    assert "MILSIM_LLM_PROVIDER=ollama" in msg


def test_non_json_body_is_reported_clearly():
    client = mock_client(recording_handler("<html>not an llm</html>"))
    with pytest.raises(LLMUnavailableError) as exc:
        run(client.chat([{"role": "user", "content": "hi"}]))
    assert "non-JSON" in str(exc.value)


def test_empty_choices_is_reported_clearly():
    client = mock_client(recording_handler({"choices": []}))
    with pytest.raises(LLMUnavailableError) as exc:
        run(client.complete([{"role": "user", "content": "hi"}]))
    assert "no choices" in str(exc.value)


# ── preflight: ensure_available ───────────────────────────────────

def test_ensure_available_uses_the_ollama_tags_api():
    seen = []

    def handle(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={"models": [{"name": "qwen2.5:7b-instruct"},
                                                    {"name": "qwen3:8b"}]})

    client = mock_client(handle, model="qwen2.5:7b-instruct")
    models = run(client.ensure_available())
    assert seen == ["http://localhost:11434/api/tags"]
    assert models == ["qwen2.5:7b-instruct", "qwen3:8b"]


def test_ensure_available_accepts_a_bare_model_name():
    def handle(request):
        return httpx.Response(200, json={"models": [{"name": "qwen2.5:7b-instruct"}]})

    client = mock_client(handle, model="qwen2.5")
    assert run(client.ensure_available()) == ["qwen2.5:7b-instruct"]


def test_ensure_available_lists_what_is_installed_when_model_is_missing():
    def handle(request):
        return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})

    client = mock_client(handle, model="qwen2.5:7b-instruct")
    with pytest.raises(LLMUnavailableError) as exc:
        run(client.ensure_available())
    msg = str(exc.value)
    assert "ollama pull qwen2.5:7b-instruct" in msg
    assert "qwen3:8b" in msg


def test_ensure_available_when_daemon_is_down():
    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)

    client = mock_client(refuse)
    with pytest.raises(LLMUnavailableError) as exc:
        run(client.ensure_available())
    assert "ollama serve" in str(exc.value)


def test_build_llm_defaults_to_ollama(monkeypatch):
    for var in ("MILSIM_LLM_PROVIDER", "MILSIM_LLM_MODEL", "MILSIM_LLM_BASE_URL",
                "MILSIM_LLM_API_KEY", "MILSIM_LLM_CONFIG", "OLLAMA_HOST",
                "OLLAMA_MODEL", "OPENAI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    llm = build_llm()
    assert llm.config.provider == "ollama"
    assert llm.config.chat_url == "http://localhost:11434/v1/chat/completions"


# ── AI elements driven by a mocked Ollama ─────────────────────────

def test_structured_text_commander_parses_mocked_ollama_orders():
    llm = mock_client(recording_handler(chat_response(
        "BUILD powr\nTRAIN 3x e1\nATTACK 42,43 TO 80,30"
    )))
    tc = StructuredTextCommander(bridge=None, llm=llm)
    orders = run(tc.decide(briefing_text="COMMANDER BRIEFING — Turn 1"))

    assert [o.order_type for o in orders] == [
        OrderType.BUILD, OrderType.TRAIN, OrderType.ASSAULT
    ]
    assert orders[0].item_type == "powr"
    assert orders[1].count == 3
    assert orders[2].unit_ids == [42, 43]
    assert (orders[2].target_x, orders[2].target_y) == (80, 30)


def test_structured_text_commander_builds_its_own_ollama_client_by_default(monkeypatch):
    monkeypatch.delenv("MILSIM_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("MILSIM_LLM_CONFIG", raising=False)
    tc = StructuredTextCommander(bridge=None)
    assert tc.llm.config.provider == "ollama"


def _fake_briefing(unit_type="mcv"):
    unit = UnitSummary(
        actor_id=7, type=unit_type, cell_x=10, cell_y=10, hp_percent=1.0,
        is_idle=True, stance="defend", can_attack=False, speed=5, attack_range=0,
    )
    return CommanderBriefing(
        turn_number=1, game_tick=100, friendly_units=[unit], friendly_buildings=[],
        known_enemies=[], known_enemy_buildings=[],
        economy={"cash": 5000, "power_surplus": 0, "power_provided": 0,
                 "power_drained": 0, "harvesters": 0},
        military_stats={"units_killed": 0, "units_lost": 0, "army_value": 0},
        available_production=["powr", "tent"],
        sitrep="SITREP",
    )


class _FakeCommander:
    is_game_over = False

    def format_briefing_text(self, briefing=None):
        return "COMMANDER BRIEFING — Turn 1"


class _FakeISR:
    def format_intel_report(self):
        return "INTEL: no contacts"


def _llm_ai(handler, **kwargs):
    return LLMTacticalAI(
        commander=_FakeCommander(), isr=_FakeISR(), verbose=False,
        llm=mock_client(handler), **kwargs
    )


def test_llm_tactical_ai_decides_from_mocked_ollama():
    seen = []
    ai = _llm_ai(recording_handler(chat_response("BUILD powr\nDEPLOY 7"), sink=seen))
    orders = run(ai.decide_async(_fake_briefing(), SimpleNamespace(active_contacts=0)))

    assert [o.order_type for o in orders] == [OrderType.BUILD, OrderType.DEPLOY]
    # The LLM really saw the structured briefing and the order syntax.
    prompt = json.loads(seen[-1].content)["messages"][-1]["content"]
    assert "COMMANDER BRIEFING" in prompt
    assert "ORDER FORMAT" in prompt
    assert "OPERATIONAL PHASE" in prompt


def test_llm_tactical_ai_falls_back_to_doctrine_when_ollama_dies_mid_game():
    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)

    ai = _llm_ai(refuse, fallback_to_doctrine=True)
    orders = run(ai.decide_async(_fake_briefing("mcv"), SimpleNamespace(active_contacts=0)))
    # Doctrine deploys the MCV rather than aborting the run...
    assert [o.order_type for o in orders] == [OrderType.DEPLOY]
    # ...and the actionable message is in the decision log for the AAR.
    assert any("ollama serve" in line for line in ai._state.turn_log)


def test_llm_tactical_ai_raises_actionable_error_when_fallback_is_off():
    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)

    ai = _llm_ai(refuse, fallback_to_doctrine=False)
    with pytest.raises(LLMUnavailableError) as exc:
        run(ai.decide_async(_fake_briefing(), SimpleNamespace(active_contacts=0)))
    assert "ollama serve" in str(exc.value)


def test_llm_tactical_ai_preflight_fails_before_turn_one():
    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)

    ai = _llm_ai(refuse, fallback_to_doctrine=True)
    with pytest.raises(LLMUnavailableError) as exc:
        run(ai.play_game())
    # Preflight always raises, even with fallback on: a whole game played by
    # the rule-based AI when you asked for an LLM is not a useful result.
    assert "ollama serve" in str(exc.value)
    assert ai.turn == 0


def test_phase_machine_advances_once_per_turn_with_fallback():
    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)

    ai = _llm_ai(refuse, fallback_to_doctrine=True)
    briefing = _fake_briefing()
    briefing.friendly_buildings = [{"type": "fact"}, {"type": "tent"}]
    briefing.friendly_units = briefing.friendly_units * 2
    ai._turn = 1
    run(ai.decide_async(briefing, SimpleNamespace(active_contacts=0)))
    # ESTABLISH → BUILD only; the doctrine fallback must not re-run the
    # phase machine and skip straight past BUILD in a single turn.
    assert ai.phase.value == "build"


# ── function-calling loop against a scripted Ollama ───────────────

class _FakeBridge:
    """Minimal CommanderBridge stand-in for the tool-calling loop."""

    def __init__(self):
        self.turn = 1
        self.game_over = False
        self.executed = []
        self._knowledge = SimpleNamespace(available=False)
        self._memory = SimpleNamespace(get_mission_context=lambda: "")
        self._spatial = SimpleNamespace(has_data=False)
        self._cnn = SimpleNamespace(has_data=False)

    def get_briefing(self):
        return _fake_briefing()

    async def execute(self, orders):
        self.executed.append(orders)
        self.game_over = True
        return "TURN 1 RESULT"

    def get_aar(self):
        return "=== AAR ==="


def _scripted_ollama(responses):
    """Mock transport that replays `responses` in order, one per chat call."""
    queue = list(responses)
    seen = []

    def handle(request):
        if request.url.path.endswith("/api/tags"):
            return httpx.Response(200, json={"models": [{"name": DEFAULT_OLLAMA_MODEL}]})
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=queue.pop(0) if queue else chat_response("done"))

    return handle, seen


def test_function_calling_loop_runs_against_mocked_ollama():
    tool_call = lambda i, name, args: {
        "id": f"call-{i}", "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }
    handler, seen = _scripted_ollama([
        chat_response(tool_calls=[tool_call(1, "get_briefing", {})]),
        chat_response(tool_calls=[
            tool_call(2, "build_structure", {"item_type": "powr"}),
            tool_call(3, "advance_turn", {}),
        ]),
    ])
    bridge = _FakeBridge()
    fc = FunctionCallingCommander(bridge, llm=mock_client(handler))

    aar = run(fc.play_game(max_turns=5, verbose=False))

    assert aar == "=== AAR ==="
    assert len(bridge.executed) == 1
    assert bridge.executed[0][0].item_type == "powr"
    # Tools were advertised, and tool results were fed back as role=tool.
    assert any(t["function"]["name"] == "advance_turn" for t in seen[0]["tools"])
    roles = [m["role"] for m in seen[-1]["messages"]]
    assert "tool" in roles


def test_function_calling_loop_survives_unparseable_tool_arguments():
    handler, seen = _scripted_ollama([
        chat_response(tool_calls=[{
            "id": "call-1", "type": "function",
            "function": {"name": "build_structure", "arguments": "{not json"},
        }]),
        chat_response(tool_calls=[{
            "id": "call-2", "type": "function",
            "function": {"name": "advance_turn", "arguments": "{}"},
        }]),
    ])
    bridge = _FakeBridge()
    fc = FunctionCallingCommander(bridge, llm=mock_client(handler))
    run(fc.play_game(max_turns=5, verbose=False))

    tool_replies = [m["content"] for m in seen[-1]["messages"] if m["role"] == "tool"]
    assert any("Could not parse arguments" in c for c in tool_replies)
    assert len(bridge.executed) == 1


def test_function_calling_loop_preflights_the_daemon():
    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)

    fc = FunctionCallingCommander(_FakeBridge(), llm=mock_client(refuse))
    with pytest.raises(LLMUnavailableError) as exc:
        run(fc.play_game(max_turns=5, verbose=False))
    assert "ollama serve" in str(exc.value)


def test_function_calling_commander_defaults_to_ollama(monkeypatch):
    monkeypatch.delenv("MILSIM_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("MILSIM_LLM_CONFIG", raising=False)
    fc = FunctionCallingCommander(_FakeBridge())
    assert fc.llm.config.provider == "ollama"
    assert fc.llm.config.api_key == ""


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
