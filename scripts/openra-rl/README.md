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
