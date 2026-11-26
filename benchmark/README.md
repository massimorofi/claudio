# benchmark — offline backend availability probes

`probes.py` answers one question for every Claudio engine backend: **can it
actually run in this environment right now?** It is deliberately a leaf module:
standard library only, no network, no shelling out, and no import of any
first-party Claudio package, so it also works inside the bare gate container
that has none of the repository's dependencies installed.

## Public API

```python
from probes import probe_all

report = probe_all()                       # real environment
report = probe_all(environ={}, which=lambda p: None)   # simulated bare box
```

`probe_all(environ=None, which=shutil.which) -> dict` never raises. `environ`
defaults to `os.environ`; `which` is injectable so tests (and a gate) can
simulate a machine with nothing installed.

```jsonc
{
  "generated_at": "2026-11-24T23:56:04+00:00",   // ISO-8601, UTC
  "backends": {
    "google_adk":  { "backend": "google_adk",  "binary": null,       ... },
    "claude_code": { "backend": "claude_code", "binary": "claude",   ... },
    "opencode":    { "backend": "opencode",    "binary": "opencode", ... }
  }
}
```

Every backend entry carries exactly these keys:

| key | type | meaning |
| --- | --- | --- |
| `backend` | str | backend id (`google_adk`, `claude_code`, `opencode`) |
| `binary` | str \| None | executable looked up with `which()`, `None` when the backend needs no binary |
| `binary_found` | bool | `True` if the binary was found **or** no binary is required |
| `module_found` | bool | `True` if the Python module is importable **or** no module is required |
| `env_present` | list[str] | configuration env vars that are set to a non-blank value |
| `env_missing` | list[str] | configuration env vars that are unset or blank |
| `available` | bool | `binary_found and module_found and bool(env_present)` |
| `reason` | str | always non-empty; the join of what is missing, or what satisfied the probe |

The "N/A counts as satisfied" convention for `binary` / `module` keeps the
`available` rule uniform across the three backends.

## What each backend is probed against

| backend | binary (`which`) | python module (`importlib.util.find_spec`) | env vars (any one is enough) |
| --- | --- | --- | --- |
| `claude_code` | `claude` | — | `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL` |
| `opencode` | `opencode` | — | `OPENAI_API_KEY`, `OPENAI_BASE_URL` |
| `google_adk` | — | `google.adk` | `GOOGLE_API_KEY`, `GEMINI_API_KEY` |

## Failure semantics

Nothing a probe can hit is an error condition; each one becomes
`available: false` plus a human-readable `reason`:

| situation | effect |
| --- | --- |
| binary not on `PATH` | `binary_found: false`, reason names the missing executable |
| `which()` itself raises (broken `PATH`, odd filesystem) | caught; treated as "not found" and reported |
| module not installed / not importable | `module_found: false`, reason names the module |
| no configuration env var (or a blank one) | `env_present: []`, reason lists the accepted names |
| unexpected crash inside a probe | caught per backend; that backend reports `available: false` with the exception type in `reason`, the other two are still probed |

A blank value (`""` or whitespace) counts as missing, because an empty
`ANTHROPIC_API_KEY` is a misconfiguration, not a credential.

## Running

```bash
# machine-readable report for the current environment
PYTHONPATH=benchmark python3 -m probes

# the probe test suite
PYTHONPATH=benchmark python3 -m pytest benchmark/tests/test_probes.py -q
```

`benchmark/tests/test_probes.py` bootstraps its own import path
(`sys.path.insert(0, str(Path(__file__).resolve().parents[1]))`), so it passes
from the repository root without any install step, and it never depends on what
is actually installed: the deterministic assertions inject `environ={}` and
`which=lambda p: None`.

## Adding a backend

Add one row to `BACKENDS` in `probes.py` — `(binary_or_None, module_or_None,
(env_var_names,))` — and add the id to the tuple that `probe_all` iterates. The
availability rule, error containment and report shape are shared, so no new
plumbing is needed.
