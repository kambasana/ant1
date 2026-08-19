# Running MilSim on Windows

This guide covers installing and running the MilSim platform on Windows using
the PowerShell scripts in `scripts\windows\`.

> **Status of this document.** The setup logic was executed end to end and the
> failure modes below were reproduced against this checkout, but on Linux under
> PowerShell 7 — no step in this guide has been executed on an actual Windows
> machine. See [What has and has not been verified](#what-has-and-has-not-been-verified).

---

## The two layers

MilSim is two separable pieces, and they fail for different reasons:

| Layer | What it is | Windows story |
|---|---|---|
| **Python** — `milsim\` + `openra-rl\openra_env\` | The commander, WEGO turn system, ISR, scenarios, LLM interfaces | Pure Python, no compiled extensions required. Runs natively. |
| **Game engine** — OpenRA-RL server | Modified OpenRA (C#/.NET) behind a gRPC bridge and a FastAPI server on port 8000 | Shipped as a **Linux container** (`ghcr.io/yxc20089/openra-rl`). Needs Docker Desktop with the WSL2 backend. |

The Python layer installs and imports on Windows with no game engine present.
You only need Docker once you want to actually play a game — the rule-based AI,
the LLM commander and every scenario still need a live server on port 8000.

---

## Prerequisites

**Required**

- **Windows 10 21H2 / Windows 11** or newer.
- **PowerShell** — Windows PowerShell 5.1 (built in) or PowerShell 7+. The
  scripts support both.
- **Python 3.10 or newer**, from [python.org](https://www.python.org/downloads/windows/).
  Tick *"Add python.exe to PATH"* during install.
  3.10 is a hard floor: `milsim\__main__.py` uses a `match` statement, and
  `openra-rl` declares `requires-python = ">=3.10"`.
  Avoid the Microsoft Store build — see [troubleshooting](#python-opens-the-microsoft-store).
- **Git**, and clone with submodules — the OpenRA engine is a submodule:
  ```
  git clone --recurse-submodules <repo-url>
  ```

**Required to actually play a game**

- **Docker Desktop** with the **WSL2** backend enabled. The game server image is
  a Linux container; Windows container mode will not run it.

**Optional**

- **Ollama** for local LLM commanders — see [Pointing MilSim at Ollama](#pointing-milsim-at-ollama).
- **PyTorch**, via `setup.ps1 -WithTorch`. Only accelerates `milsim\cnn.py`,
  which falls back to pure-Python convolution kernels when torch is absent.
  Its full test suite passes either way.
- **.NET 8 SDK**, only if you intend to build the OpenRA engine natively
  instead of using the container.

---

## Quick start

From a PowerShell prompt in the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\setup.ps1
powershell -ExecutionPolicy Bypass -File scripts\windows\run.ps1
powershell -ExecutionPolicy Bypass -File scripts\windows\dashboard.ps1
```

`-ExecutionPolicy Bypass` applies to that one invocation only. It does not
change any machine or user policy. If your policy is already `RemoteSigned` or
looser you can call the scripts directly: `.\scripts\windows\setup.ps1`.

---

## Step-by-step setup

### 1. Run the setup script

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\setup.ps1
```

| Option | Effect |
|---|---|
| `-Python <path>` | Use a specific interpreter instead of auto-detecting |
| `-VenvPath <path>` | Put the virtual environment somewhere other than `.venv` |
| `-Recreate` | Delete and rebuild an existing environment |
| `-WithTorch` | Also install PyTorch |

It performs six steps, and stops with a non-zero exit code on the first failure:

1. **Checks the repository layout** — `milsim\__main__.py` and
   `openra-rl\pyproject.toml` must exist.
2. **Finds a Python 3.10+ interpreter**, trying `py -3`, then `python`, then
   `python3`. Interpreters resolving into `WindowsApps` are rejected as Store
   alias stubs, and anything older than 3.10 is reported by version and path.
3. **Creates a virtual environment** at `.venv`.
4. **Installs `openra-rl` in editable mode with its `[dev]` extras** — that
   single install brings in every dependency `milsim` needs (`pydantic`,
   `httpx`, `pyyaml`, `websockets`, `mcp`, `grpcio`, `fastapi`, `uvicorn`) plus
   `pytest` and `ruff`. If the extras fail it retries without them.
5. **Registers the repository root on `sys.path`** by writing
   `milsim_repo_root.pth` into the venv's `site-packages`. This is what makes
   `import milsim` work from any directory — see
   [why milsim is not pip-installable](#why-milsim-is-not-pip-installable).
6. **Verifies the install.** It imports all fourteen `openra_env` and `milsim`
   modules, reports whether torch is present, and loads
   `milsim\mod\scenarios\hasty_attack.yaml` through the real
   `ScenarioRunner.load()` code path, asserting that the U+2014 em dash in the
   scenario name survives. The verification deliberately runs from a directory
   that is *not* the repository root, so a passing result proves the `.pth`
   works rather than proving your current directory happened to be right.

A successful run ends with `RESULT: all checks passed` and `Setup complete.`

### 2. Never activate the venv

The scripts call `.venv\Scripts\python.exe` by absolute path and never invoke
`Activate.ps1`. That is deliberate: activation is itself a PowerShell script and
is blocked under the default `Restricted` execution policy, which is the single
most common way a Windows Python setup appears broken. Nothing here requires you
to change your execution policy permanently.

If you want an interactive shell anyway:

```powershell
.\.venv\Scripts\python.exe -m pip list
```

### 3. Start the game server

```powershell
.\.venv\Scripts\python.exe -m openra_env.cli.main doctor
.\.venv\Scripts\python.exe -m openra_env.cli.main server start
```

`doctor` reports Docker CLI, Docker daemon, game image, server and Python status
in one screen. `server start` pulls `ghcr.io/yxc20089/openra-rl:latest` on first
use, which is a large download.

Other useful subcommands: `server status`, `server logs`, `server stop`.

---

## Running the simulation

```powershell
.\scripts\windows\run.ps1
```

| Option | Default | Meaning |
|---|---|---|
| `-Mode` | `rule` | `rule` (scripted AI, no LLM), `text` (structured-text LLM), `fc` (function-calling LLM), `mcp` (MCP stdio server) |
| `-Scenario` | `hasty_attack` | Bare name, repo-relative path, or absolute path |
| `-Server` | `http://localhost:8000` | OpenRA-RL server URL |
| `-MaxTurns` | `60` | Turn cap |
| `-Model` | — | LLM model name; required for `text` and `fc` |
| `-BaseUrl` | — | LLM API base URL |
| `-ApiKey` | — | LLM API key; falls back to `$env:OPENAI_API_KEY` inside milsim |
| `-Ollama` | off | Target a local Ollama daemon |
| `-OllamaHost` | `http://localhost:11434` | Ollama address; implies `-Ollama` |
| `-Fast` | off | FastAdvance via the MCP client, for server-side interrupt detection |
| `-Quiet` | off | Suppress turn-by-turn output |
| `-SkipHealthCheck` | off | Launch without probing the server first |
| `-VenvPath` | `.venv` | Alternative environment |

Available scenarios: `hasty_attack`, `strait_of_hormuz`, `gaza_urban_clearance`,
`eastern_europe_defense`. Pass a name that does not exist and the script lists
the real ones.

Examples:

```powershell
# Rule-based AI, default scenario
.\scripts\windows\run.ps1

# A different scenario, shorter game, server-side interrupts
.\scripts\windows\run.ps1 -Scenario strait_of_hormuz -MaxTurns 20 -Fast

# Hosted LLM with function calling
.\scripts\windows\run.ps1 -Mode fc -Model gpt-4o -ApiKey $env:OPENAI_API_KEY

# MCP stdio server for Claude Desktop / Claude Code
.\scripts\windows\run.ps1 -Mode mcp
```

### What the launcher does for you

Three things that are easy to get wrong by hand:

- **Sets `PYTHONUTF8=1` and `PYTHONIOENCODING=utf-8`.** Required — see
  [scenario text is mangled](#scenario-text-is-mangled-or-raises-unicodedecodeerror).
- **Resolves `-Scenario` to an absolute path and checks it exists.** The
  built-in default inside `milsim\__main__.py` is the *relative* path
  `milsim/mod/scenarios/hasty_attack.yaml`, guarded by `os.path.exists()`. Run
  milsim from the wrong directory and the scenario is silently skipped with no
  error and no scenario loaded. The launcher makes that impossible.
- **Probes the server before launching.** If `GET /health` fails it makes a raw
  TCP connection to distinguish *nothing is listening* from *something is
  listening but unhealthy*, and prints the right next command for each.

---

## Pointing MilSim at Ollama

1. Install Ollama for Windows from <https://ollama.com/download/windows>. The
   installer registers a background service listening on `127.0.0.1:11434`.

2. Pull a model. Tool-capable models are required for `-Mode fc`; `-Mode text`
   works with any chat model.

   ```powershell
   ollama pull qwen3:8b
   ollama list
   ```

3. Run with `-Ollama`:

   ```powershell
   .\scripts\windows\run.ps1 -Mode text -Ollama -Model qwen3:8b
   ```

`-Ollama` checks that `GET http://localhost:11434/api/tags` answers before
starting anything, then sets:

- `--base-url http://localhost:11434/v1` — milsim posts to
  `{base_url}/chat/completions`, so the `/v1` suffix is required and the script
  adds it for you.
- `--api-key ollama` — a placeholder; Ollama ignores the `Authorization` header.
- `$env:OLLAMA_HOST` — read by `milsim\llm_provider.py`, which the tactical AI
  path uses instead of the `--base-url` flag.

For Ollama on another machine, or on a non-default port:

```powershell
.\scripts\windows\run.ps1 -Mode text -OllamaHost http://192.168.1.20:11434 -Model qwen3:8b
```

**Context window.** Local models default to a small context. MilSim briefings are
long, and a truncated context shows up as the commander ignoring orders or
repeating itself. Create a larger-context variant:

```powershell
ollama create qwen3-32k --from qwen3:8b --parameter num_ctx 32768
.\scripts\windows\run.ps1 -Mode text -Ollama -Model qwen3-32k
```

**Timeouts.** milsim uses a 60 second per-request timeout in `text` and `fc`
modes. A large model on CPU will exceed that. Prefer a smaller model, or a GPU.

---

## The C2 dashboard

```powershell
.\scripts\windows\dashboard.ps1
```

`milsim\c2-dashboard.html` is a single self-contained page. It makes no network
calls back to the simulation, so it opens straight off disk and works with or
without a running game server.

| Option | Effect |
|---|---|
| `-Browser chrome\|msedge\|firefox` | Force a browser instead of the system default |
| `-Serve` | Serve over HTTP from a local static server instead of `file://` |
| `-Port <n>` | Port for `-Serve` (default 8080) |
| `-PrintOnly` | Print the URL and exit |
| `-VenvPath <path>` | Alternative environment, used only by `-Serve` |

The script hands a `file://` **URI** to the shell rather than the file path,
which routes it to the registered default *browser* instead of whatever
application owns the `.html` extension — on many Windows installs that is an
editor, not a browser.

Use `-Serve` if an enterprise policy blocks local `file://` pages; it starts
`python -m http.server` scoped to `milsim\` and opens `http://localhost:8080/c2-dashboard.html`.
Press Ctrl+C to stop it.

The page pulls Inter and IBM Plex Mono from Google Fonts on first load. Offline
it falls back to Consolas and the system UI font; nothing else degrades.

---

## Troubleshooting

Items marked **[verified]** were reproduced against this checkout. Items marked
**[platform]** are standard Windows failure modes that were not triggered here
but are worth knowing.

### Scripts will not run: "running scripts is disabled on this system"

**[platform]** The default execution policy blocks `.ps1` files. Do not change
the policy — invoke per call:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\setup.ps1
```

If you downloaded the repository as a ZIP, Windows also marks the files with a
Mark-of-the-Web that survives extraction:

```powershell
Get-ChildItem -Path scripts\windows -Filter *.ps1 | Unblock-File
```

### Python opens the Microsoft Store

**[platform]** Windows ships stub `python.exe` / `python3.exe` "App Execution
Aliases" in `%LOCALAPPDATA%\Microsoft\WindowsApps` that open the Store instead
of running Python. `setup.ps1` detects any interpreter resolving into
`WindowsApps` and skips it with a warning.

Fix it permanently: **Settings → Apps → Advanced app settings → App execution
aliases**, turn off `python.exe` and `python3.exe`. Or install from python.org
and use the `py -3` launcher, which `setup.ps1` tries first.

### "No Python 3.10+ interpreter found"

The script prints the version and path of every too-old interpreter it found.
Install 3.10 or newer from python.org, or point at one you already have:

```powershell
.\scripts\windows\setup.ps1 -Python "C:\Python312\python.exe"
```

### "ensurepip is not available" when creating the venv

**[platform]** Python was installed without the optional `pip` feature. Re-run
the python.org installer, choose *Modify*, and enable `pip`.

### `ModuleNotFoundError: No module named 'milsim'`

**[verified]** `milsim\` is a directory at the repository root with no
`__init__.py` and no packaging metadata, so pip cannot install it. It is
importable only when the repository root is on `sys.path`.

`setup.ps1` handles this by writing `milsim_repo_root.pth` into the venv's
`site-packages`, and `run.ps1` additionally sets the working directory to the
repository root. If you see this error:

- You are using a different interpreter than the one `setup.ps1` configured.
  Use `.\.venv\Scripts\python.exe` explicitly.
- Or the venv was rebuilt by hand. Re-run `setup.ps1`.
- Or, as a one-off, run from the repository root:
  `cd <repo>; .\.venv\Scripts\python.exe -m milsim`

### Scenario text is mangled, or raises UnicodeDecodeError

**[verified]** `milsim\scenario.py` opens scenario YAML with a bare
`open(path)` — no `encoding=` argument. Every scenario file contains U+2014 em
dashes (75 of them across the four scenarios). On Windows, `open()` uses the
process ANSI codepage rather than UTF-8, so:

| Codepage | Result |
|---|---|
| cp1252 (Western), cp1251, cp437 | Decodes without error, but text is corrupted: `Hasty Attack â€" River Crossing` |
| cp932 (Japanese), cp949 (Korean) | `UnicodeDecodeError: illegal multibyte sequence` and the run aborts |

Both were confirmed by decoding the actual scenario bytes under each codepage.

`setup.ps1` and `run.ps1` both set `PYTHONUTF8=1`, which enables Python's UTF-8
mode and fixes it. If you launch milsim by hand, set it yourself:

```powershell
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe -m milsim
```

To make it permanent for your account:

```powershell
[Environment]::SetEnvironmentVariable("PYTHONUTF8", "1", "User")
```

Alternatively enable **Settings → Time & language → Language & region →
Administrative language settings → Change system locale → Beta: Use Unicode UTF-8
for worldwide language support**, which sets the system codepage to UTF-8. That
is a machine-wide change and affects other software; `PYTHONUTF8=1` is the
narrower fix.

`setup.ps1` verifies this end to end and prints your locale encoding, so a
passing setup means the mitigation is working on your machine.

### The scenario is silently ignored

**[verified]** `milsim\__main__.py` defaults `--scenario` to the relative path
`milsim/mod/scenarios/hasty_attack.yaml` and loads it only
`if os.path.exists(args.scenario)`. Launched from any directory other than the
repository root — for instance from a desktop shortcut, where the working
directory is often `C:\Windows\System32` — the file is not found, the load is
skipped without a message, and the game runs with no scenario at all.

Use `run.ps1`, which always passes an absolute path and fails loudly if the file
is missing.

### "Nothing is listening at http://localhost:8000"

The game server is not running. Start it:

```powershell
.\.venv\Scripts\python.exe -m openra_env.cli.main doctor
.\.venv\Scripts\python.exe -m openra_env.cli.main server start
```

If `doctor` says *Docker daemon: not running*, start Docker Desktop and wait for
the whale icon to settle. If Docker Desktop is in **Windows containers** mode,
switch it back to Linux containers — the game image is a Linux container.

### "Something is listening but /health did not answer"

**[platform]** Most often a corporate HTTP proxy intercepting localhost.
`Invoke-WebRequest` honours the system proxy, so a proxy configuration without
a localhost bypass will swallow the health probe.

Add `localhost` and `127.0.0.1` to the proxy bypass list in **Settings → Network
& Internet → Proxy**, or bypass the check:

```powershell
.\scripts\windows\run.ps1 -SkipHealthCheck
```

Otherwise the server started but is unhealthy — check
`.\.venv\Scripts\python.exe -m openra_env.cli.main server logs`.

### pip fails building a wheel

**[platform]** Some transitive dependency had no prebuilt Windows wheel for your
Python version and fell back to compiling. Install the *Desktop development with
C++* workload from the Visual Studio Build Tools, or use a Python version with
better wheel coverage (3.11 or 3.12 rather than the newest release).

Note the editable install needs network access to PyPI even for a local path,
because it fetches the `hatchling` build backend.

### Paths longer than 260 characters

**[platform]** Deep virtual environment paths can exceed the legacy `MAX_PATH`
limit. Either clone closer to the drive root (`C:\dev\ant1`), or enable long
paths:

```powershell
# Run as Administrator
New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" `
  -Name "LongPathsEnabled" -Value 1 -PropertyType DWORD -Force
```

### torch is missing

**[verified]** Expected and harmless. `milsim\cnn.py` guards its torch import
and falls back to pure-Python convolution kernels; its full test suite (62
checks) passes without torch installed. Install it only if you want the
accelerated path:

```powershell
.\scripts\windows\setup.ps1 -WithTorch
```

### The dashboard opens in an editor instead of a browser

`.html` is associated with something other than a browser. Force one:

```powershell
.\scripts\windows\dashboard.ps1 -Browser msedge
```

### `-Serve` exits immediately

Port 8080 is already in use. Pick another:

```powershell
.\scripts\windows\dashboard.ps1 -Serve -Port 8091
```

---

## Why milsim is not pip-installable

`milsim\` sits at the repository root with no `__init__.py` and no
`pyproject.toml` of its own, and `openra-rl\pyproject.toml` packages only
`openra_env`. It therefore cannot be installed by pip, and `import milsim`
depends entirely on the repository root being on `sys.path`. That is why:

- `setup.ps1` writes a `.pth` file rather than adding a console-script entry
  point. An entry point would produce a `milsim.exe` that breaks the moment the
  path registration is missing.
- `run.ps1` sets the working directory to the repository root as a second line
  of defence.
- **`openra-rl\pyproject.toml` was not modified.** Adding a `milsim` entry point
  there would advertise a command that cannot resolve its own package.

Making `milsim` a real distribution — an `__init__.py`, its own `pyproject.toml`
or an addition to the existing one — would remove this whole class of problem,
but it requires changes to `milsim\` itself.

---

## What has and has not been verified

**Verified by execution:**

- `setup.ps1` runs to completion and its verification stage passes: fourteen
  modules import, the scenario loads through the real `ScenarioRunner.load()`
  path, and the em dash survives.
- `run.ps1` passes the correct argument vector to `python -m milsim` — every
  flag name checked against `milsim\__main__.py`'s argparse definition — with
  the scenario resolved to an absolute path, `PYTHONUTF8=1` and
  `PYTHONIOENCODING=utf-8` exported, and the working directory at the repository
  root. Exit codes propagate.
- Every failure path: missing venv, unknown scenario, missing `-Model`, server
  down, Ollama down. All exit non-zero with actionable messages.
- `dashboard.ps1` produces a correct, space-escaped `file://` URI.
- The codepage behaviour in the encoding section, by decoding the real scenario
  bytes as cp1252, cp932, cp949, cp1251 and cp437.

**Verified by static analysis:**

- All three scripts parse without error under the PowerShell 7.4.6 AST parser.
- An AST scan found no PowerShell 7-only constructs: no ternary operator, no
  `&&`/`||` pipeline chains, no `??`/`?.`/`??=`, no PS6+ automatic variables
  (`$IsWindows`, `$PSStyle`), and no three-argument `Join-Path`. Every cmdlet
  and parameter used exists in Windows PowerShell 5.1.

**Not verified:**

- **No part of this was executed on Windows.** The scripts were run under
  PowerShell 7 on Linux, which exercises all the logic but takes the
  `.venv/bin/python` branch rather than `.venv\Scripts\python.exe`, and never
  exercises `py -3` detection, WindowsApps stub rejection, `Start-Process`
  browser launching, or Docker Desktop.
- **Windows PowerShell 5.1 was not run at all** — no 5.1 interpreter exists on
  this platform. Compatibility rests on the static analysis above.
- The game server itself was never started; the health-check paths were
  exercised against a stub HTTP server, not a real OpenRA-RL instance.

Treat the first run on a real Windows machine as the acceptance test. See
[docs/RELEASE.md](RELEASE.md).
