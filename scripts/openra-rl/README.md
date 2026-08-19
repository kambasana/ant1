# openra-rl patches

`openra-rl/` is **not** part of this repository. It is gitignored
(`.gitignore:12`) and cloned separately:

```
git clone --recurse-submodules https://github.com/yxc20089/OpenRA-RL.git openra-rl
```

That means any change made inside `openra-rl/` lives only in that nested
checkout and disappears the next time someone clones. The patch here exists
so that work is not lost.

## milsim-fixes.patch

Against `yxc20089/OpenRA-RL` at `5dadd44`. Twenty files: 8 deletions,
2 additions, 10 modifications.

**Test collection.** Nine loose `test_*.py` scripts at the package root were
manual debug tools, not tests, and eight of them broke `pytest` outright when
it was pointed at `openra-rl/` rather than `openra-rl/tests/` — an `ImportError`
on a nonexistent `openra_rl_training` package, and one that opened a gRPC
channel at import time. They move to `scripts/manual/` under names that pytest
does not collect. `scripts/test_integration.py` became
`scripts/integration_check.py` for the same reason: it collected four phantom
tests that all errored on a missing `port` fixture.

**Ollama.** Wizard and CLI config work, plus `examples/config-ollama-remote.yaml`
for pointing the agent at a non-local Ollama host.

`openra-rl/tests/` itself was already green (647 passing) and is untouched.

## Applying

```
cd openra-rl
git apply ../scripts/openra-rl/milsim-fixes.patch
```

Check it still applies after pulling openra-rl:

```
git apply --check ../scripts/openra-rl/milsim-fixes.patch
```

## This is a stopgap

The right home for these changes is a pull request upstream. Until that
lands, the patch has to be reapplied after every fresh clone of openra-rl,
and it will rot as upstream moves. Treat a failing `--check` as the signal
to rebase it or push it upstream, not to force it.

## amd64-image-tag.patch

Against a current `yxc20089/OpenRA-RL`. One file, `openra_env/cli/docker_manager.py`.

`_image_tag()` hardcodes `latest`, and the published `latest` manifest is
**arm64-only**. On any x86 host `server start` dies with:

```
no matching manifest for linux/amd64/v3 in the manifest list entries
```

Docker 29 asks for a microarchitecture variant, so the message mentions
`amd64/v3`, but an explicit `--platform linux/amd64` fails the same way —
there is genuinely no amd64 entry under `latest`. Every **versioned** tag
(0.2.1, 0.3.1, 0.4.0, 0.4.1) does carry amd64.

The patch adds an `OPENRA_RL_VERSION` environment override so a version can be
pinned without editing code. Verified on Windows: with the variable set,
`_image_tag()` returns the pinned tag; without it, `latest` as before.

Workaround needing no patch — pull a versioned tag and retag it locally:

```
docker pull --platform linux/amd64 ghcr.io/yxc20089/openra-rl:0.4.1
docker tag ghcr.io/yxc20089/openra-rl:0.4.1 ghcr.io/yxc20089/openra-rl:latest
```

Applied separately from `milsim-fixes.patch`, not because the checkouts differ
— both sit on `5dadd44` — but because `milsim-fixes.patch` was captured from a
tree that already had its twenty files applied, while this one was captured from
a tree that did not. Regenerating either as a combined diff silently drops the
other. Apply `milsim-fixes.patch` first, then this one.

## Both patches, from a fresh clone

```powershell
git clone --depth 1 https://github.com/yxc20089/OpenRA-RL.git openra-rl
cd openra-rl
git apply ..\scripts\openra-rl\milsim-fixes.patch
git apply ..\scripts\openra-rl\amd64-image-tag.patch
```

## The OpenRA submodule

Six tests in `openra-rl/tests` (`test_config.py::TestBotTypeMapping` and
`test_mcp_tools.py::TestReplayConfig`) build a launch command and need
`OpenRA.dll` or `launch-rl.sh` to exist. A `--depth 1` clone without
`--recurse-submodules` leaves `openra-rl/OpenRA/` empty and those six fail
with `FileNotFoundError`; the other 641 pass. Add `--recurse-submodules` if
you want that suite fully green — it is a large fetch and nothing else needs it.

## The server holds one session, and a killed client keeps it

`Server at capacity: 1/1 sessions active. Cannot accept new connections.`

The server accepts a single concurrent session. A client that is killed
mid-run — an interrupted pytest, a background probe, a cancelled game —
leaves that slot occupied, and every later run fails. The symptom is not
always the capacity message: a reset against an occupied server can surface
as `websockets.exceptions.ConnectionClosedOK: received 1000 (OK)`, which
reads like a protocol bug and is not one.

This cost two misdiagnoses in one session. Before believing any connection
error, clear stray clients and restart:

```powershell
Get-Process python | Where-Object {
  (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)").CommandLine -match 'milsim|pytest'
} | Stop-Process -Force
python -m openra_env.cli.main server stop
python -m openra_env.cli.main server start
```

## The enemy bot does spawn

Worth recording, because the absence of contacts suggests otherwise. With a
session live, the engine's own launch line shows both players:

```
dotnet /opt/openra/bin/OpenRA.dll ... Launch.Map=singles.oramap \
  Launch.Bots=Multi1:rl-agent,Multi0:normal
```

`Multi0` is the opponent. The `normal` there is an OpenRA bot name, not the
`beginner / easy / medium / hard / brutal` tier list in `config.yaml` — those
are separate vocabularies, and an earlier note here claiming the CLI default
was an invalid tier was wrong. `--difficulty hard` was tested on a clean
server and starts a game normally.

The OpenRA process is spawned per session and force-killed when the socket
closes, so `ps` inside the container shows nothing between runs. That is
normal, not a crash.

## Open: `test_isr` and `test_spatial` find nothing

`0 total contacts` and `0 corridors found`. Given the bot demonstrably
spawns, the likely cause is distance: `singles.oramap` is 128×128 and the
tests do not scout far enough to break fog of war. A smaller map, or an
explicit scouting phase, would settle it. Not yet confirmed.

## Open: text mode issues orders that never take effect

`--mode text` against a live server produces parsed orders every turn while
`Units` stays at 1 and `Cash` stays at $5000 — nothing reaches the game.
`--mode rule` on the same server deploys the MCV and builds correctly, so
the engine and the session are fine and the fault is in the text order
path. The model also invents item types (`fac`, `factory`, `har` alongside
the valid `powr`), which schema validation on the order parser would catch.
