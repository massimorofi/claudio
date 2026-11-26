# claudio-backend — Test / Performance Baseline (Increment 0a gate fix)

Restore point: CLAUDIO_BK_0.2.0 -> 36693e09

> Tag note (verified with `git rev-parse`): the tag `CLAUDIO_BK_0.2.0`
> already exists (lightweight, `git cat-file -t` -> `commit`) and points at
> `c441308b` ("Perf plan pre-flight:
> record CLAUDIO_BK_0.2.0 restore tag..."). `36693e09` ("Gortex indexing
> embedded in claudio") is an ancestor of it, 26 commits back. Both SHAs exist;
> the tag was **not** recreated or moved by this task.

This file is the **regression baseline**. Every later Increment 0a task is
measured against the numbers and the failure list below. A later task is
green only if it adds no new `FAILED`/`ERROR` line and does not drop a passing
test.

Purpose: the two previous 0a rounds reported "tests passing" in their own
output and still **failed the gate**. Root cause is recorded in
[Environment hazards](#environment-hazards-why-earlier-rounds-passed-locally-and-failed-the-gate)
below, and the fix is `tests/conftest.py`.

## Canonical invocation

Discovered from `claudio-backend/pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
pythonpath = ["src"]
```

There is **no** `pytest.ini`, `setup.cfg` or root-level `pyproject.toml`; the
only pytest config in the repo is `claudio-backend/pyproject.toml`, so
`rootdir` is `claudio-backend` and `inifile` is `claudio-backend/pyproject.toml`
whenever the run is anchored at the backend (from the repo root too, because
pytest walks the args' ancestors looking for an inifile).

**The canonical command — run it exactly like this:**

```bash
cd claudio-backend && python3 -m pytest tests -q -p no:cacheprovider
```

Equivalent, and the form the gate harness uses (harmless, `pythonpath` in the
ini already does the same thing):

```bash
cd claudio-backend && PYTHONPATH=src python3 -m pytest tests -q -p no:cacheprovider
```

Collect-only check:

```bash
cd claudio-backend && PYTHONPATH=src python3 -m pytest tests -q -p no:cacheprovider --collect-only
# -> 245 tests collected in 3.64s   (0 collection errors)
```

## Baseline run

Command actually executed (full suite, credentials unset in the parent env):

```bash
cd claudio-backend && env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY -u ANTHROPIC_AUTH_TOKEN -u OPENAI_BASE_URL COLUMNS=400 python3 -m pytest tests -q -p no:cacheprovider --tb=no -rf
```

`COLUMNS=400` is there only so the short test summary is **not** truncated to
the terminal width; without it pytest prints `... - asser...` and the lines
cannot be diffed mechanically.

Result:

```
13 failed, 230 passed, 2 skipped in 17.52s
```

* collected: **245**
* passed: **230**
* failed: **13**
* skipped: **2**
* errors during collection: **0**
* wall time: **~17.5 s** (18.14 s on the first run; treat ~18 s as the budget)

## Pre-existing failures (copy VERBATIM — diff against this block)

One `FAILED ...` line per failure, exactly as pytest printed it. There are no
`ERROR ...` lines at baseline.

```
FAILED tests/test_contract_shapes.py::test_session_summary_matches_ts - assert 0 >= 1
FAILED tests/test_contract_shapes.py::test_approval_request_matches_ts - AssertionError: expected a pending approval to exercise the shape
FAILED tests/test_contract_shapes.py::test_run_failed_frame_matches_ts - assert 422 == 200
FAILED tests/test_e2e.py::test_chat_stream_completes - assert 422 == 200
FAILED tests/test_e2e.py::test_chat_approval_pause_resume - assert 422 == 200
FAILED tests/test_e2e.py::test_sessions_lifecycle - assert 0 >= 1
FAILED tests/test_e2e.py::test_turn_count_matches_model_responses - assert 422 == 200
FAILED tests/test_issue54_gortex.py::test_live_config_gortex_uses_http_endpoint - FileNotFoundError: [Errno 2] No such file or directory: '/work/massimorofi-claudio/7e64cd56-008e-4594-8a2a-ba3a7f31f276-a1/claudio-backend/config/config.yaml'
FAILED tests/test_issue54_gortex.py::test_live_config_gortex_enabled - FileNotFoundError: [Errno 2] No such file or directory: '/work/massimorofi-claudio/7e64cd56-008e-4594-8a2a-ba3a7f31f276-a1/claudio-backend/config/config.yaml'
FAILED tests/test_issue54_gortex.py::test_gortex_entry_parses_as_mcpserverentry - assert 0 == 1
FAILED tests/test_issue54_gortex.py::test_end_to_end_enabled_gortex_reaches_prompt - assert False
FAILED tests/test_issue54_gortex.py::test_compose_defines_gortex_service - FileNotFoundError: [Errno 2] No such file or directory: '/work/massimorofi-claudio/7e64cd56-008e-4594-8a2a-ba3a7f31f276-a1/claudio-backend/config/config.yaml'
FAILED tests/test_issue54_gortex.py::test_gortex_config_sends_auth_header - FileNotFoundError: [Errno 2] No such file or directory: '/work/massimorofi-claudio/7e64cd56-008e-4594-8a2a-ba3a7f31f276-a1/claudio-backend/config/config.yaml'
```

### Normalized failure keys (path-independent, use these to diff)

The four `FileNotFoundError` lines above embed this worktree's absolute path, so
they will not match verbatim in another checkout. Diff on the node IDs instead —
this is the complete, stable baseline failure set (13):

```
tests/test_contract_shapes.py::test_session_summary_matches_ts
tests/test_contract_shapes.py::test_approval_request_matches_ts
tests/test_contract_shapes.py::test_run_failed_frame_matches_ts
tests/test_e2e.py::test_chat_stream_completes
tests/test_e2e.py::test_chat_approval_pause_resume
tests/test_e2e.py::test_sessions_lifecycle
tests/test_e2e.py::test_turn_count_matches_model_responses
tests/test_issue54_gortex.py::test_live_config_gortex_uses_http_endpoint
tests/test_issue54_gortex.py::test_live_config_gortex_enabled
tests/test_issue54_gortex.py::test_gortex_entry_parses_as_mcpserverentry
tests/test_issue54_gortex.py::test_end_to_end_enabled_gortex_reaches_prompt
tests/test_issue54_gortex.py::test_compose_defines_gortex_service
tests/test_issue54_gortex.py::test_gortex_config_sends_auth_header
```

Mechanical diff recipe:

```bash
cd claudio-backend && COLUMNS=400 python3 -m pytest tests -q -p no:cacheprovider --tb=no -rf \
  | grep -E '^(FAILED|ERROR)' | sed 's/^FAILED //; s/^ERROR //; s/ - .*//' | sort > /tmp/now.txt
# compare /tmp/now.txt against the 13 node IDs above
```

## Pre-existing skips (2)

```
SKIPPED [1] tests/test_issue57_backend.py:580: ADK terminal-event contract covered by test_e2e.py
SKIPPED [1] tests/test_issue57_backend.py:590: ADK event encoding covered by test_event_protocol.py
```

## Why these 13 fail (so nobody re-diagnoses them)

* `test_issue54_gortex.py` (6) — reads a **live** `claudio-backend/config/config.yaml`.
  That file is not tracked; only `config/config.example.yaml` is
  (`git ls-files config` -> `config/config.example.yaml`). Environment gap, not a code defect.
* `test_e2e.py` (4) and `test_contract_shapes.py` (3) — all fail against the
  running-backend fixture (real uvicorn + fake model server) with either
  `assert 422 == 200` (the API rejects the request body the test posts) or
  `assert 0 >= 1` (no session row came back). These are pre-existing product/test
  mismatches carried into this baseline; **not** caused by Increment 0a and not
  caused by `tests/conftest.py` — the same 13 fail with the conftest removed.

## Environment hazards — why earlier rounds "passed" locally and failed the gate

1. **A stale editable install shadows the worktree.** `pip list` shows
   `claudio-backend 0.1.0 /srv/claudio/claudio-backend` and
   `claudio-core 0.1.0 /srv/claudio/claudio-core`. Outside pytest,
   `python3 -c "import claudio_backend"` resolves to
   `/srv/claudio/claudio-backend/src/claudio_backend/__init__.py` — a
   **different checkout**, not the code under review. Any run that does not apply
   `pythonpath = ["src"]` (bare `pytest` from an unexpected cwd, `-c <file>`,
   `--rootdir` overrides) therefore tests the wrong tree and can report green
   while the worktree is broken. `tests/conftest.py::_pin_local_packages()`
   now pins `claudio-backend/src` (and the sibling `claudio-core/src`) to
   `sys.path[0]` and evicts any module already loaded from elsewhere, so the
   worktree wins unconditionally. Verified: with the `/srv` entries removed from
   `sys.path`, importing `tests/conftest.py` alone makes both packages resolve
   inside the worktree.
2. **`config/config.yaml` is not in the repo.** Only `config/config.example.yaml`
   is tracked, so `test_issue54_gortex.py` cannot pass in a clean checkout. Do
   not "fix" it by creating `config/config.yaml` — it is outside the writable
   paths of Increment 0a and it is an environment gap, not a code gap.
3. **Truncated summary lines.** Always pass `COLUMNS=400` when capturing the
   failure list, or the diff is against lines ending in `...asser...`.
4. **`test_llm_call_boundary.py` does not exist** in this checkout
   (`find tests -name 'test_llm_call_boundary*'` -> empty). The already-landed
   0a(a) coverage that is present is the `tests/test_issue58_*.py` family, led by
   `test_issue58_accounting.py`.

## Hermeticity guarantees now enforced by `tests/conftest.py`

Two autouse fixtures run around **every** test:

| Fixture | Effect |
| --- | --- |
| `_strip_provider_credentials` | `monkeypatch.delenv` for `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `OPENAI_BASE_URL` (plus `ANTHROPIC_BASE_URL`, `OPENAI_API_BASE`, `OPENAI_API_KEY_FILE`). A test that reads a real key sees nothing. |
| `_block_external_network` | Patches `socket.create_connection` and `socket.socket.connect` to raise `AssertionError` for any non-loopback target. **Loopback stays open on purpose** — `test_e2e.py` boots a real uvicorn on `127.0.0.1`, and several tests point clients at `127.0.0.1:<port>` fakes. |

Plus the import-time `_pin_local_packages()` described above.

Proof that the guarantees hold (temporary self-check test, run then deleted):

```bash
cd claudio-backend && env ANTHROPIC_API_KEY=sk-ant-leaked OPENAI_API_KEY=sk-leaked \
  ANTHROPIC_AUTH_TOKEN=tok OPENAI_BASE_URL=https://evil.invalid/v1 \
  python3 -m pytest <selfcheck> -q -p no:cacheprovider
# 3 passed  -> creds absent from os.environ; create_connection(("api.anthropic.com",443))
#              raised; a 127.0.0.1 bind/connect still worked
```

### Accounting tests under stripped credentials

Run with the four provider vars **deliberately populated** with junk, so the
stripping is proven rather than vacuous:

```bash
cd claudio-backend && env ANTHROPIC_API_KEY=sk-ant-leaked OPENAI_API_KEY=sk-leaked \
  ANTHROPIC_AUTH_TOKEN=tok OPENAI_BASE_URL=https://evil.invalid/v1 \
  python3 -m pytest tests/test_issue58_accounting.py tests/test_issue58_bench.py \
  tests/test_issue58_budgets.py tests/test_issue58_concurrency.py \
  tests/test_issue58_router.py tests/test_issue58_streaming.py \
  tests/test_issue58_tier1.py -q -p no:cacheprovider
# 44 passed in 5.23s
```

Per file: `accounting` 16, `bench` 2, `budgets` 8, `concurrency` 6, `router` 3,
`streaming` 3, `tier1` 6 = **44 passed, 0 failed, 0 skipped**.

## Contract for later Increment 0a tasks

1. Run the canonical command above.
2. Diff the failure set against the 13 node IDs. **New** failure => regression you caused.
3. `passed` must not drop below 230 minus whatever you legitimately convert from
   failing to passing; `failed` must never exceed 13.
4. Never add a test that needs a provider credential or a non-loopback socket —
   the conftest will fail it, and it will fail the gate too.
5. Do not recreate the tag `CLAUDIO_BK_0.2.0`; it already exists.

## Known warnings (do not chase these)

* `PytestDeprecationWarning: The configuration option "asyncio_default_fixture_loop_scope" is unset`
  from `pytest_asyncio/plugin.py:306`. Cosmetic: pytest-asyncio 1.5.0 keeps the
  current (fixture-scope) behaviour until it is set explicitly. Setting it to
  `"function"` would change async-fixture semantics for the whole suite, so it is
  deliberately **left alone** here; it is not part of the 0a gate.
* The suite writes runtime state into `claudio-backend/data/` (gitignored at
  `.gitignore:36`), so a run leaves no untracked noise in `git status`.

## What this task changed

* **Added** `claudio-backend/tests/conftest.py` — import pinning + credential
  stripping + non-loopback network block. No feature code, no `src/` edits.
* **Added** this file, `claudio-backend/tests/PERF_BASELINE.md`.
* `claudio-backend/pyproject.toml` was **not** modified: it already declares
  `testpaths`/`pythonpath`/`asyncio_mode`, and the conftest makes the suite
  correct even when that ini is not picked up.
