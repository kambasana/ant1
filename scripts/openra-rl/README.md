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

Applied separately from `milsim-fixes.patch` because the two were captured
against different upstream commits.
