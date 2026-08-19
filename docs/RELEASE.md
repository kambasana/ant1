# Release checklist

What must pass before a MilSim release ships. Every gate below is a command with
an expected result, not a judgement call.

> **There is no CI for this project.** The only GitHub Actions workflow in the
> repository (`.github/workflows/deploy-azure-naming-tool-to-azure-webapps-dotnet-core.yml`)
> belongs to unrelated leftover scaffolding under `src/AzureNamingTool*` and
> does not build, test or lint `milsim\` or `openra-rl\`. Until that changes,
> **every gate here is manual and someone has to actually run it.** Record the
> results in the sign-off table at the bottom.

Baselines quoted below were measured on Linux, Python 3.11.15, at the time this
document was written. Re-measure rather than assume.

---

## Gate 0 — Preconditions

- [ ] Release branch cut, and the working tree is clean (`git status --porcelain` is empty).
- [ ] `openra-rl/` is present. It is NOT a submodule of this repo — it is
      gitignored here and cloned separately
      (`git clone --recurse-submodules https://github.com/yxc20089/OpenRA-RL.git openra-rl`).
      A plain clone of this repo contains no Python packaging at all.
- [ ] `openra-rl/config.yaml` contains no machine-specific absolute paths or
      credentials from a developer's box.
- [ ] No API keys, tokens or `.env` files are staged. Check the diff, not just
      `.gitignore`.

---

## Gate 1 — Automated tests

### 1a. openra-rl unit tests

```
cd openra-rl
python -m pytest tests -q
```

**Required: all pass.** Baseline `647 passed, 1 warning`.

The one warning is a pydantic-settings `IncompleteFieldDefinitionWarning` raised
from `test_cli.py::TestMCPServer::test_mcp_server_module_imports`. It is
pre-existing. A *new* warning is worth a look; this one is not a blocker.

### 1b. milsim offline test scripts

These are **standalone scripts, not pytest tests.** They define no `test_*`
functions, so `pytest milsim/tests` collects zero items and exits reporting
success. That is a trap: pytest passing here means nothing was run.

Run them directly, from the repository root:

```
python milsim/tests/test_cnn.py
python milsim/tests/test_knowledge.py
python milsim/tests/test_llm_commander.py
```

**Required: each prints `Results: N/N passed` and exits 0.**
Baseline: 62, 45 and 62 checks respectively — 169 total.

Run these **without** PyTorch installed at least once. `milsim/cnn.py` has a
pure-Python fallback for when torch is absent, and that fallback is what most
users will actually execute.

### 1c. milsim integration scripts (need a live server)

The remaining seven scripts construct an `OpenRAEnv` and require a running
OpenRA-RL server on port 8000:

```
milsim/tests/test_milsim_basics.py     milsim/tests/test_scenario.py
milsim/tests/test_commander.py         milsim/tests/test_spatial.py
milsim/tests/test_isr.py               milsim/tests/test_wego_turns.py
milsim/tests/test_full_game.py
```

```
python -m openra_env.cli.main server start
python milsim/tests/test_milsim_basics.py
python milsim/tests/test_wego_turns.py
python milsim/tests/test_full_game.py
```

- [ ] `test_milsim_basics.py` passes — the minimum foundation gate.
- [ ] `test_wego_turns.py` passes — turn phase cycling, BDA, SITREP/AAR.
- [ ] `test_full_game.py` completes a game end to end.
- [ ] Any script skipped is recorded, with the reason, in the sign-off table.

---

## Gate 2 — Lint

`ruff` is declared in the `[dev]` extra and configured in
`openra-rl/pyproject.toml` with `line-length = 120`, `target-version = "py310"`.

**Lint is not clean today.** Current baseline:

| Target | Command | Findings |
|---|---|---|
| `openra_env` + its tests | `cd openra-rl && python -m ruff check openra_env tests` | 354 |
| `milsim` | `python -m ruff check milsim --line-length 120` | 182 |

- [ ] Neither count has **increased** relative to the previous release. That is
      the gate — not zero.
- [ ] Any new file added in this release is individually clean.
- [ ] If the counts changed, the new baselines are written into this table as
      part of the release commit.

Do not run `ruff --fix` across the tree as part of a release. A 500-file
autofix diff cannot be reviewed alongside a release and belongs in its own
change.

---

## Gate 3 — Windows acceptance

This is the highest-risk gate, because the Windows scripts have **never been
executed on Windows**. `docs/WINDOWS.md` states exactly what was and was not
verified. Until a release has cleared this gate on real hardware, Windows
support is provisional and should be described that way in release notes.

Run on a machine that has never had this project installed:

- [ ] **Windows PowerShell 5.1**, clean checkout, no venv:
      ```
      powershell -ExecutionPolicy Bypass -File scripts\windows\setup.ps1
      ```
      Ends with `RESULT: all checks passed` and `Setup complete.`, exit code 0.
- [ ] **PowerShell 7**, same:
      ```
      pwsh -File scripts\windows\setup.ps1 -Recreate
      ```
- [ ] The verification stage reports `encoding check : ok`. If it reports a
      mangled em dash, `PYTHONUTF8=1` is not taking effect and every scenario
      description in the product is corrupt.
- [ ] `setup.ps1` correctly rejects the Microsoft Store `python.exe` alias when
      that is the only `python` on `PATH`. This branch has never run.
- [ ] `setup.ps1` finds an interpreter via `py -3` when `python` is absent.
      Also never run.
- [ ] Install into a path containing a space (`C:\Users\First Last\...`) and
      confirm setup, run and `dashboard.ps1 -Serve` all still work. Windows user
      profile names commonly contain spaces and this is where quoting bugs
      surface.
- [ ] `.\scripts\windows\run.ps1` with **no server running** exits 1 and prints
      `Nothing is listening at http://localhost:8000` plus the `server start`
      command.
- [ ] `.\scripts\windows\run.ps1` against a **real** OpenRA-RL server plays a
      game to completion and prints an AAR.
- [ ] `.\scripts\windows\run.ps1 -Scenario nosuchthing` exits 1 and lists the
      four real scenarios.
- [ ] `.\scripts\windows\run.ps1 -Mode text -Ollama -Model <model>` drives a
      game against a local Ollama daemon.
- [ ] `.\scripts\windows\dashboard.ps1` opens the C2 dashboard in the default
      **browser** — not an editor.
- [ ] `.\scripts\windows\dashboard.ps1 -Serve` serves it over HTTP, and Ctrl+C
      stops the server without leaving an orphaned `python.exe`.
- [ ] Docker Desktop is confirmed in **Linux containers** mode; the game image
      is a Linux container and will not run under Windows containers.

---

## Gate 4 — Linux / macOS acceptance

- [ ] `milsim/setup_openra_rl.sh` still works, or its divergence from
      `scripts/windows/setup.ps1` is deliberate and documented. The two scripts
      currently do different things: the shell script clones OpenRA-RL, patches
      `Map.cs` for the CS0121 `SHA1Hash` ambiguity and builds the .NET engine;
      the PowerShell script sets up only the Python layer and defers the engine
      to Docker.
- [ ] `python -m milsim --mode rule` plays a game against a locally built engine.
- [ ] `python -m openra_env.cli.main doctor` reports all green.

---

## Gate 5 — Packaging

- [ ] `version` in `openra-rl/pyproject.toml` is bumped and matches the release tag.
- [ ] `python -m openra_env.cli.main version` prints that version.
- [ ] A wheel builds and the console scripts are generated:
      ```
      cd openra-rl && python -m pip wheel --no-deps -w /tmp/wheels .
      ```
      Confirm `openra-rl` and `openra-rl-mcp` entry points exist in the wheel.
- [ ] Install the wheel into a throwaway venv and confirm `import openra_env`
      succeeds **from a directory outside the repository**. A pass that only
      works inside the checkout means the import was resolving from the source
      tree, not the installed package.
- [ ] `milsim` is knowingly **not** packaged. It has no `__init__.py` and no
      distribution metadata, and is importable only via the repository root on
      `sys.path`. If this release claims to package it, that claim is wrong —
      see the *Why milsim is not pip-installable* section of `docs/WINDOWS.md`.

---

## Gate 6 — Documentation

- [ ] `docs/WINDOWS.md` matches the shipped scripts: every option in its tables
      exists, and every option the scripts accept is documented.
- [ ] The *What has and has not been verified* section of `docs/WINDOWS.md` is
      updated. Once a release clears Gate 3 on real hardware, the "Not verified"
      list must shrink accordingly. Shipping with a stale claim in either
      direction is a defect.
- [ ] `docs/OPENRA_RL_INTEGRATION.md` status table reflects what actually works
      in this release, not what is planned.
- [ ] `README.md` at the repository root still describes the Azure Naming Tool
      and says nothing about MilSim. Either fix it or explicitly accept it —
      it is the first thing anyone sees.
- [ ] Release notes list breaking changes to the `milsim` CLI flags
      (`--mode`, `--server`, `--scenario`, `--max-turns`, `--model`,
      `--base-url`, `--api-key`, `--fast`, `--quiet`). `scripts/windows/run.ps1`
      is built against exactly these names and breaks silently if any are
      renamed.

---

## Gate 7 — Security and hygiene

- [ ] No API keys in scenario files, config, examples or test scripts.
- [ ] `-ApiKey` is not written into any log or AAR output.
- [ ] `SECURITY.md` contact details are current.
- [ ] Docker image tag pinned or explicitly floating on `:latest` by decision,
      not by accident (`openra_env/cli/docker_manager.py`, `IMAGE_REPO`).

---

## Known defects carried into release

Record anything shipping unfixed. These are the ones known at the time of
writing:

| Defect | Impact | Workaround in place |
|---|---|---|
| `milsim/scenario.py` opens YAML without `encoding=`; scenario files contain U+2014 | Mojibake on cp1252 Windows, `UnicodeDecodeError` on cp932/cp949 | `setup.ps1` and `run.ps1` set `PYTHONUTF8=1`; documented in `docs/WINDOWS.md` |
| `milsim/__main__.py` defaults `--scenario` to a relative path behind `os.path.exists()` | Scenario silently not loaded when run from any other directory | `run.ps1` always passes an absolute path and validates it |
| `milsim` has no `__init__.py` or packaging metadata | Not pip-installable; `import milsim` needs the repo root on `sys.path` | `setup.ps1` writes a `.pth`; `run.ps1` sets the working directory |
| `milsim/tests/*.py` are not pytest tests | `pytest milsim/tests` collects nothing and exits 0, looking like a pass | Gate 1b runs them as scripts |
| No CI covers `milsim` or `openra-rl` | Every gate is manual and skippable | This checklist |

The first three are fixable only inside `milsim/*.py`. Fixing them at the source
would let the mitigations be deleted, and is the recommended follow-up.

---

## Sign-off

| Gate | Owner | Date | Result | Notes |
|---|---|---|---|---|
| 0 Preconditions | | | | |
| 1a openra-rl tests | | | | expected 647 passed |
| 1b milsim offline scripts | | | | expected 62 / 45 / 62 |
| 1c milsim integration | | | | needs a live server |
| 2 Lint | | | | baseline 354 / 182 |
| 3 Windows acceptance | | | | 5.1 **and** 7 |
| 4 Linux / macOS | | | | |
| 5 Packaging | | | | |
| 6 Documentation | | | | |
| 7 Security | | | | |

A release does not ship with an empty Result cell.
