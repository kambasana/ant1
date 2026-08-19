"""Provider configuration and HTTP client for every milsim AI element.

Ollama is a first-class provider, not an afterthought: with nothing
configured at all, milsim talks to a local Ollama daemon at
``$OLLAMA_HOST`` (default ``http://localhost:11434``) and no API key is
required. Hosted OpenAI-compatible endpoints (OpenAI, OpenRouter,
LM Studio, vLLM, llama.cpp, ...) work through the same code path.

Resolution order (later wins):
  1. built-in provider defaults          (ollama @ localhost:11434)
  2. config file                          (``MILSIM_LLM_CONFIG``, or explicit path)
  3. environment variables                (``OLLAMA_HOST``, ``MILSIM_LLM_MODEL``, ...)
  4. explicit keyword overrides           (``LLMProviderConfig.load(model="...")``)

Usage:
    from milsim.llm_provider import LLMProviderConfig, LLMClient

    config = LLMProviderConfig.load()          # ollama by default
    async with LLMClient(config) as llm:
        await llm.ensure_available()           # clear error if Ollama is down
        data = await llm.chat(messages, tools=tools)

Every reachability problem surfaces as :class:`LLMUnavailableError` with a
message that tells the operator what to run, never as a raw traceback from
httpx.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Optional, Sequence

import httpx

__all__ = [
    "LLMError",
    "LLMConfigError",
    "LLMUnavailableError",
    "ProviderSpec",
    "PROVIDERS",
    "LLMProviderConfig",
    "LLMClient",
    "extract_message",
    "DEFAULT_PROVIDER",
    "DEFAULT_OLLAMA_HOST",
    "DEFAULT_OLLAMA_MODEL",
]


# ── Errors ────────────────────────────────────────────────────────

class LLMError(RuntimeError):
    """Base class for milsim LLM failures. Message is operator-facing."""


class LLMConfigError(LLMError):
    """Provider/model configuration is unusable (e.g. missing API key)."""


class LLMUnavailableError(LLMError):
    """The configured endpoint could not be reached or refused the request."""


# ── Provider registry ─────────────────────────────────────────────

DEFAULT_PROVIDER = "ollama"
DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "qwen2.5:7b-instruct"


@dataclass(frozen=True)
class ProviderSpec:
    """Static facts about a provider milsim knows how to talk to."""

    name: str
    default_base_url: str
    default_model: str
    requires_api_key: bool = True
    api_key_envs: tuple[str, ...] = ()
    base_url_envs: tuple[str, ...] = ()
    model_envs: tuple[str, ...] = ()
    #: Ollama-style management API (``/api/tags``) for health checks.
    has_tags_api: bool = False
    install_hint: str = ""


PROVIDERS: dict[str, ProviderSpec] = {
    "ollama": ProviderSpec(
        name="ollama",
        default_base_url=DEFAULT_OLLAMA_HOST,
        default_model=DEFAULT_OLLAMA_MODEL,
        requires_api_key=False,
        api_key_envs=("OLLAMA_API_KEY",),
        base_url_envs=("OLLAMA_HOST", "OLLAMA_BASE_URL"),
        model_envs=("OLLAMA_MODEL",),
        has_tags_api=True,
        install_hint=(
            "Start the daemon with `ollama serve`, then pull a model with "
            "`ollama pull {model}`."
        ),
    ),
    "openai": ProviderSpec(
        name="openai",
        default_base_url="https://api.openai.com/v1",
        default_model="gpt-4o-mini",
        api_key_envs=("OPENAI_API_KEY",),
        base_url_envs=("OPENAI_BASE_URL",),
        model_envs=("OPENAI_MODEL",),
    ),
    "openrouter": ProviderSpec(
        name="openrouter",
        default_base_url="https://openrouter.ai/api/v1",
        default_model="qwen/qwen3-coder-next",
        api_key_envs=("OPENROUTER_API_KEY", "OPENAI_API_KEY"),
        base_url_envs=("OPENROUTER_BASE_URL",),
        model_envs=("OPENROUTER_MODEL",),
    ),
    "lmstudio": ProviderSpec(
        name="lmstudio",
        default_base_url="http://localhost:1234/v1",
        default_model="local-model",
        requires_api_key=False,
        base_url_envs=("LMSTUDIO_HOST", "LMSTUDIO_BASE_URL"),
        model_envs=("LMSTUDIO_MODEL",),
    ),
    # Escape hatch: any OpenAI-compatible endpoint, no built-in defaults.
    "openai-compatible": ProviderSpec(
        name="openai-compatible",
        default_base_url="http://localhost:8080/v1",
        default_model="local-model",
        requires_api_key=False,
    ),
}

_LOCAL_HOST_MARKERS = ("localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal")


def _known_provider(name: str) -> ProviderSpec:
    spec = PROVIDERS.get(name.strip().lower())
    if spec is None:
        known = ", ".join(sorted(PROVIDERS))
        raise LLMConfigError(
            f"Unknown LLM provider {name!r}. Known providers: {known}. "
            f"Set MILSIM_LLM_PROVIDER to one of them, or use "
            f"'openai-compatible' with MILSIM_LLM_BASE_URL for anything else."
        )
    return spec


def normalize_base_url(url: str) -> str:
    """Accept the many shapes people write a host in.

    ``OLLAMA_HOST`` is commonly ``127.0.0.1:11434`` with no scheme, and
    people paste full ``/v1/chat/completions`` URLs too. All are accepted.
    """
    url = (url or "").strip().rstrip("/")
    if not url:
        return url
    if "://" not in url:
        url = f"http://{url}"
    return url


def _chat_url(base_url: str) -> str:
    """Derive the chat-completions endpoint from a base URL."""
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def _root_url(base_url: str) -> str:
    """Strip any ``/v1...`` suffix — the Ollama management API lives at the root."""
    base = base_url.rstrip("/")
    for suffix in ("/v1/chat/completions", "/chat/completions", "/v1"):
        if base.endswith(suffix):
            return base[: -len(suffix)] or base
    return base


def _env_first(env: Mapping[str, str], names: Sequence[str]) -> str:
    for name in names:
        value = env.get(name, "")
        if value:
            return value
    return ""


def _as_float(value: Any, name: str) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise LLMConfigError(f"{name} must be a number, got {value!r}")


def _as_int(value: Any, name: str) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise LLMConfigError(f"{name} must be an integer, got {value!r}")


# ── Config ────────────────────────────────────────────────────────

@dataclass
class LLMProviderConfig:
    """Everything milsim needs to reach one chat-completions endpoint."""

    provider: str = DEFAULT_PROVIDER
    base_url: str = DEFAULT_OLLAMA_HOST
    model: str = DEFAULT_OLLAMA_MODEL
    api_key: str = ""
    # 1024 is not enough for a reasoning model: gpt-oss:20b spent all 512
    # tokens thinking before emitting any content, and needed ~811
    # completion tokens in total to answer a commander briefing.
    max_tokens: int = 4096
    temperature: Optional[float] = 0.3
    request_timeout_s: float = 300.0
    extra_headers: dict[str, str] = field(default_factory=dict)

    # -- construction -------------------------------------------------

    @classmethod
    def for_provider(cls, provider: str = DEFAULT_PROVIDER, **overrides: Any) -> "LLMProviderConfig":
        """Config built from a provider's defaults plus explicit overrides."""
        spec = _known_provider(provider)
        cfg = cls(
            provider=spec.name,
            base_url=spec.default_base_url,
            model=spec.default_model,
            # Local providers are slow but keyless; hosted ones are the reverse.
            request_timeout_s=300.0 if not spec.requires_api_key else 120.0,
        )
        return cfg._with_overrides(overrides)

    @classmethod
    def from_mapping(
        cls,
        data: Optional[Mapping[str, Any]] = None,
        env: Optional[Mapping[str, str]] = None,
        **overrides: Any,
    ) -> "LLMProviderConfig":
        """Build from a config mapping, then environment, then overrides."""
        data = dict(data or {})
        if "llm" in data and isinstance(data["llm"], Mapping):
            data = dict(data["llm"])
        env = os.environ if env is None else env

        provider = (
            overrides.get("provider")
            or env.get("MILSIM_LLM_PROVIDER")
            or data.get("provider")
            or DEFAULT_PROVIDER
        )
        spec = _known_provider(str(provider))
        cfg = cls.for_provider(spec.name)

        # 2. config file values
        cfg = cfg._with_overrides(data)

        # 3. environment
        env_values: dict[str, Any] = {}
        base_url = _env_first(env, ("MILSIM_LLM_BASE_URL", *spec.base_url_envs))
        if base_url:
            env_values["base_url"] = base_url
        model = _env_first(env, ("MILSIM_LLM_MODEL", *spec.model_envs))
        if model:
            env_values["model"] = model
        api_key = _env_first(env, ("MILSIM_LLM_API_KEY", *spec.api_key_envs))
        if api_key:
            env_values["api_key"] = api_key
        if env.get("MILSIM_LLM_MAX_TOKENS"):
            env_values["max_tokens"] = env["MILSIM_LLM_MAX_TOKENS"]
        if env.get("MILSIM_LLM_TEMPERATURE"):
            env_values["temperature"] = env["MILSIM_LLM_TEMPERATURE"]
        if env.get("MILSIM_LLM_TIMEOUT_S"):
            env_values["request_timeout_s"] = env["MILSIM_LLM_TIMEOUT_S"]
        cfg = cfg._with_overrides(env_values)

        # 4. explicit overrides
        return cfg._with_overrides(overrides)

    @classmethod
    def from_env(
        cls, env: Optional[Mapping[str, str]] = None, **overrides: Any
    ) -> "LLMProviderConfig":
        """Config from environment only (no config file lookup)."""
        return cls.from_mapping(None, env=env, **overrides)

    @classmethod
    def load(
        cls,
        path: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
        **overrides: Any,
    ) -> "LLMProviderConfig":
        """Full resolution: defaults → config file → environment → overrides.

        ``path`` defaults to ``$MILSIM_LLM_CONFIG``. A missing file at an
        explicitly requested path is an error; a missing default is not.
        """
        env = os.environ if env is None else env
        explicit = path is not None
        path = path or env.get("MILSIM_LLM_CONFIG", "") or ""
        data: Mapping[str, Any] = {}
        if path:
            expanded = os.path.expanduser(path)
            if os.path.exists(expanded):
                data = _read_config_file(expanded)
            elif explicit:
                raise LLMConfigError(f"LLM config file not found: {path}")
        return cls.from_mapping(data, env=env, **overrides)

    def _with_overrides(self, values: Mapping[str, Any]) -> "LLMProviderConfig":
        known = {
            "provider", "base_url", "model", "api_key", "max_tokens",
            "temperature", "request_timeout_s", "extra_headers",
        }
        clean: dict[str, Any] = {}
        for key, value in values.items():
            if key not in known or value is None or value == "":
                continue
            if key == "max_tokens":
                clean[key] = _as_int(value, "max_tokens")
            elif key == "temperature":
                clean[key] = _as_float(value, "temperature")
            elif key == "request_timeout_s":
                clean[key] = _as_float(value, "request_timeout_s")
            elif key == "base_url":
                clean[key] = normalize_base_url(str(value))
            elif key == "extra_headers":
                if not isinstance(value, Mapping):
                    raise LLMConfigError("extra_headers must be a mapping")
                clean[key] = {**self.extra_headers, **{str(k): str(v) for k, v in value.items()}}
            elif key == "provider":
                clean[key] = _known_provider(str(value)).name
            else:
                clean[key] = str(value)
        return replace(self, **clean) if clean else self

    # -- derived properties -------------------------------------------

    @property
    def spec(self) -> ProviderSpec:
        return _known_provider(self.provider)

    @property
    def chat_url(self) -> str:
        return _chat_url(normalize_base_url(self.base_url))

    @property
    def root_url(self) -> str:
        return _root_url(normalize_base_url(self.base_url))

    @property
    def is_local(self) -> bool:
        return any(marker in self.base_url for marker in _LOCAL_HOST_MARKERS)

    @property
    def requires_api_key(self) -> bool:
        """A key is needed only for remote hosts on key-requiring providers."""
        return self.spec.requires_api_key and not self.is_local

    def headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", **self.extra_headers}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def validate(self) -> "LLMProviderConfig":
        """Raise :class:`LLMConfigError` if this config cannot possibly work."""
        if not self.model:
            raise LLMConfigError(
                f"No model configured for provider '{self.provider}'. "
                f"Set MILSIM_LLM_MODEL (or {self.spec.model_envs[0] if self.spec.model_envs else 'model in the config file'})."
            )
        if self.requires_api_key and not self.api_key:
            key_env = self.spec.api_key_envs[0] if self.spec.api_key_envs else "MILSIM_LLM_API_KEY"
            raise LLMConfigError(
                f"Provider '{self.provider}' needs an API key. Set {key_env}, "
                f"or run a local model instead: unset MILSIM_LLM_PROVIDER to use "
                f"Ollama at {DEFAULT_OLLAMA_HOST} (no key required)."
            )
        return self

    def describe(self) -> str:
        key_state = "key set" if self.api_key else "no key"
        return f"{self.provider}:{self.model} @ {self.chat_url} ({key_state})"


def _read_config_file(path: str) -> Mapping[str, Any]:
    """Read a YAML or JSON LLM config file."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        raise LLMConfigError(f"Cannot read LLM config file {path}: {exc}") from exc

    if path.endswith((".yaml", ".yml")):
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - pyyaml is a hard dep
            raise LLMConfigError(
                f"Reading {path} needs PyYAML — `pip install pyyaml`, or use a .json config."
            ) from exc
        try:
            data = yaml.safe_load(text) or {}
        except yaml.YAMLError as exc:
            raise LLMConfigError(f"Invalid YAML in LLM config file {path}: {exc}") from exc
    else:
        try:
            data = json.loads(text or "{}")
        except json.JSONDecodeError as exc:
            raise LLMConfigError(f"Invalid JSON in LLM config file {path}: {exc}") from exc

    if not isinstance(data, Mapping):
        raise LLMConfigError(f"LLM config file {path} must contain a mapping at the top level.")
    return data


# ── Client ────────────────────────────────────────────────────────

def extract_message(data: Mapping[str, Any]) -> dict:
    """Pull the assistant message out of an OpenAI-shaped response."""
    choices = data.get("choices") or []
    if not choices:
        raise LLMUnavailableError(
            "LLM returned no choices. The endpoint answered but produced no "
            "message — check that the model name is correct and loaded."
        )
    message = choices[0].get("message") or {}
    if not isinstance(message, Mapping):
        raise LLMUnavailableError("LLM response choice had no message object.")
    return dict(message)


class LLMClient:
    """Async chat-completions client shared by all milsim AI elements.

    Speaks the OpenAI chat-completions dialect, which Ollama serves at
    ``/v1/chat/completions`` (tool calling included). Connection problems
    are translated into :class:`LLMUnavailableError` carrying the command
    the operator should run.
    """

    def __init__(
        self,
        config: Optional[LLMProviderConfig] = None,
        *,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        client: Optional[httpx.AsyncClient] = None,
        verbose: bool = False,
    ):
        self.config = (config or LLMProviderConfig.load()).validate()
        self._transport = transport
        self._client = client
        self._owns_client = client is None
        self._verbose = verbose

    # -- lifecycle ----------------------------------------------------

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            kwargs: dict[str, Any] = {"timeout": self.config.request_timeout_s}
            if self._transport is not None:
                kwargs["transport"] = self._transport
            self._client = httpx.AsyncClient(**kwargs)
            self._owns_client = True
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "LLMClient":
        self._http()
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.aclose()

    # -- errors -------------------------------------------------------

    def _unreachable(self, exc: Exception, what: str) -> LLMUnavailableError:
        cfg = self.config
        lines = [f"Cannot reach the {cfg.provider} endpoint at {what}: {exc.__class__.__name__}: {exc}"]
        if cfg.provider == "ollama":
            lines.append(
                f"  Is Ollama running? Check with: curl {cfg.root_url}/api/tags"
            )
            lines.append(f"  Start it with: ollama serve")
            lines.append(f"  Install the model with: ollama pull {cfg.model}")
            lines.append(
                f"  Point milsim at a different host with OLLAMA_HOST "
                f"(current: {cfg.base_url}, default: {DEFAULT_OLLAMA_HOST})."
            )
        elif cfg.is_local:
            lines.append(f"  Is the local server up? Check with: curl {cfg.root_url}")
            lines.append("  Or set MILSIM_LLM_PROVIDER=ollama to use a local Ollama daemon.")
        else:
            lines.append("  Check network access and MILSIM_LLM_BASE_URL.")
            lines.append(
                "  For a fully local, keyless setup: MILSIM_LLM_PROVIDER=ollama "
                f"(defaults to {DEFAULT_OLLAMA_HOST})."
            )
        return LLMUnavailableError("\n".join(lines))

    def _bad_status(self, status: int, body: str) -> LLMUnavailableError:
        cfg = self.config
        body = (body or "").strip()[:800]
        lines = [f"{cfg.provider} endpoint returned HTTP {status} for model '{cfg.model}'."]
        lower = body.lower()
        if cfg.provider == "ollama" and (status == 404 or "not found" in lower):
            lines.append(f"  Ollama does not have that model. Install it with: ollama pull {cfg.model}")
            lines.append(f"  See what is installed with: ollama list")
        elif status in (401, 403):
            key_env = cfg.spec.api_key_envs[0] if cfg.spec.api_key_envs else "MILSIM_LLM_API_KEY"
            lines.append(f"  Authentication failed. Check {key_env}.")
            lines.append(
                "  Or run keyless against a local model: MILSIM_LLM_PROVIDER=ollama."
            )
        elif status == 429:
            lines.append("  Rate limited by the provider. Wait and retry, or switch to local Ollama.")
        elif status >= 500:
            lines.append("  The endpoint failed server-side. Retry, or switch provider.")
        if body:
            lines.append(f"  Response: {body}")
        return LLMUnavailableError("\n".join(lines))

    # -- calls --------------------------------------------------------

    async def list_models(self) -> list[str]:
        """Model names the endpoint reports (Ollama ``/api/tags``, else ``/v1/models``)."""
        cfg = self.config
        if cfg.spec.has_tags_api:
            url = f"{cfg.root_url}/api/tags"
            key = "models"
        else:
            url = f"{_root_url(cfg.base_url)}/v1/models"
            key = "data"
        try:
            resp = await self._http().get(url, headers=cfg.headers(), timeout=min(cfg.request_timeout_s, 30.0))
        except httpx.HTTPError as exc:
            raise self._unreachable(exc, url) from None
        if resp.status_code != 200:
            raise self._bad_status(resp.status_code, resp.text)
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise LLMUnavailableError(
                f"{cfg.provider} returned non-JSON from {url}: {exc}. "
                f"Is {cfg.root_url} really an LLM server?"
            ) from None
        entries = data.get(key) or []
        names = []
        for entry in entries:
            if isinstance(entry, Mapping):
                name = entry.get("name") or entry.get("model") or entry.get("id")
                if name:
                    names.append(str(name))
        return names

    async def ensure_available(self, check_model: bool = True) -> list[str]:
        """Fail fast, and loudly, before a game starts.

        Returns the installed model names. Raises :class:`LLMUnavailableError`
        with a runnable fix when the endpoint is down or the model is missing.
        """
        models = await self.list_models()
        if not check_model or not models:
            return models
        wanted = self.config.model
        # Ollama reports "qwen2.5:7b-instruct"; accept a bare "llama3.1" too.
        if wanted in models or any(m.split(":")[0] == wanted.split(":")[0] for m in models):
            return models
        available = ", ".join(sorted(models)[:12]) or "(none)"
        hint = (
            f"  Install it with: ollama pull {wanted}"
            if self.config.provider == "ollama"
            else "  Set MILSIM_LLM_MODEL to one of the available models."
        )
        raise LLMUnavailableError(
            f"Model '{wanted}' is not available on {self.config.provider} at "
            f"{self.config.root_url}.\n{hint}\n  Available: {available}"
        )

    async def chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Optional[Sequence[Mapping[str, Any]]] = None,
        *,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> dict:
        """POST a chat completion. Returns the parsed OpenAI-shaped response."""
        cfg = self.config
        payload: dict[str, Any] = {
            "model": cfg.model,
            "messages": [dict(m) for m in messages],
            "max_tokens": max_tokens if max_tokens is not None else cfg.max_tokens,
            "stream": False,
        }
        temp = cfg.temperature if temperature is None else temperature
        if temp is not None:
            payload["temperature"] = temp
        if tools:
            payload["tools"] = [dict(t) for t in tools]
            payload["tool_choice"] = "auto"

        url = cfg.chat_url
        if self._verbose:
            print(f"  [LLM] {cfg.provider}:{cfg.model} ← {len(payload['messages'])} messages")

        try:
            resp = await self._http().post(
                url, headers=cfg.headers(), json=payload, timeout=cfg.request_timeout_s
            )
        except httpx.HTTPError as exc:
            raise self._unreachable(exc, url) from None

        if resp.status_code != 200:
            raise self._bad_status(resp.status_code, resp.text)

        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise LLMUnavailableError(
                f"{cfg.provider} returned a non-JSON response from {url}: {exc}"
            ) from None

        if isinstance(data, Mapping) and data.get("error"):
            raise self._bad_status(200, json.dumps(data["error"])[:800])
        self._check_truncated_reasoning(data)
        return dict(data)

    @staticmethod
    def _check_truncated_reasoning(data):
        """Fail loudly when a reasoning model spent the whole budget thinking.

        Reasoning tokens count against ``max_tokens``. gpt-oss and friends
        return their chain of thought in a separate ``reasoning`` field, so a
        budget that is ample for the answer alone can be consumed entirely
        before a single content token is emitted. The response then looks
        perfectly valid - HTTP 200, a message, no error - with empty content,
        and the caller silently parses zero orders every turn.
        """
        try:
            choice = data["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError):
            return
        if (message.get("content") or "").strip():
            return
        if message.get("tool_calls"):
            return
        reasoning = message.get("reasoning") or message.get("reasoning_content") or ""
        if choice.get("finish_reason") != "length" or not reasoning:
            return
        used = (data.get("usage") or {}).get("completion_tokens")
        raise LLMError(
            "The model used its entire token budget on reasoning and returned "
            "no content ({0} completion tokens, finish_reason=length).\n"
            "  Reasoning models count their chain of thought against max_tokens.\n"
            "  Raise it, e.g. MILSIM_LLM_MAX_TOKENS=8192, or pick a "
            "non-reasoning model.".format(used)
        )

    async def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Optional[Sequence[Mapping[str, Any]]] = None,
        **kwargs: Any,
    ) -> dict:
        """:meth:`chat`, reduced to the assistant message."""
        return extract_message(await self.chat(messages, tools=tools, **kwargs))

    async def complete_text(
        self, messages: Sequence[Mapping[str, Any]], **kwargs: Any
    ) -> str:
        """:meth:`chat`, reduced to the assistant message text."""
        return str((await self.complete(messages, **kwargs)).get("content") or "")


def build_llm(
    config: Optional[LLMProviderConfig] = None,
    *,
    verbose: bool = False,
    **overrides: Any,
) -> LLMClient:
    """Convenience factory: resolve config (Ollama by default) and wrap it."""
    cfg = config or LLMProviderConfig.load(**overrides)
    return LLMClient(cfg, verbose=verbose)
