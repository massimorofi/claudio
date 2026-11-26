#!/usr/bin/env python3
"""Benchmark runner for Claudio's pluggable engines (stdlib-only, offline).

For every (backend, task, trial) triple the runner:

1. creates a throwaway sandbox with ``tempfile.mkdtemp()`` **outside** the repo,
   so the worktree / gate tree is never polluted;
2. copies ``benchmark/fixtures/<id>`` into it when the fixture exists, otherwise
   seeds the sandbox from the task's inline ``seed_files``;
3. ``git init`` + commits that seed, so the run's diff is measurable;
4. runs an engine: the backend's real argv when
   ``benchmark/results/capability.json`` marks it available, else the built-in
   deterministic stub engine (``benchmark/stub_engine.py``);
5. enforces the task timeout with ``subprocess.run(timeout=...)``;
6. captures ``stdout.txt`` / ``stderr.txt`` (never ``*.log`` -- .gitignore hides
   those), runs the task verification command inside the sandbox, captures
   ``git diff`` as ``diff.patch`` and writes ``run.json``.

Artifacts land in ``<out>/<backend>/<task_id>/trial_<k>/`` and are committed.

    python3 benchmark/runner.py --tasks benchmark/tasks.json \
        --capability benchmark/results/capability.json \
        --backends google_adk,claude_code,opencode --trials 1 \
        --out benchmark/results
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DEFAULT_TIMEOUT_S = 60.0
VERIFY_TIMEOUT_S = 30.0

BACKENDS = ("google_adk", "claude_code", "opencode")


# --------------------------------------------------------------------------- #
# task model
# --------------------------------------------------------------------------- #


@dataclass
class Task:
    """One benchmark task.

    ``seed_files`` / ``solution_files`` map a repo-relative path to file text.
    They are used when no ``benchmark/fixtures/<id>`` directory is present, so a
    task is self-contained and the runner never needs network access.
    """

    id: str
    prompt: str
    verify: str = "test -f BENCHMARK_SOLUTION.md"
    timeout_s: float = DEFAULT_TIMEOUT_S
    fixture: str = ""
    target: str = "."
    seed_files: dict[str, str] = field(default_factory=dict)
    solution_files: dict[str, str] = field(default_factory=dict)
    solution_patch: str = ""
    stub: dict[str, int] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def fixture_id(self) -> str:
        return self.fixture or self.id

    def spec(self) -> dict[str, Any]:
        """JSON handed to the engine (stub) on stdin."""
        return {
            "id": self.id,
            "prompt": self.prompt,
            "target": self.target,
            "verify": self.verify,
            "solution_files": self.solution_files,
            "solution_patch": self.solution_patch,
            "stub": self.stub,
        }


def _as_task(obj: dict[str, Any]) -> Task:
    """Build a Task from a tasks.json entry, tolerating key aliases."""
    tid = str(obj.get("id") or obj.get("task_id") or "").strip()
    if not tid:
        raise ValueError(f"task entry without id: {obj!r}")
    verify = obj.get("verify") or obj.get("verify_command") or "test -f BENCHMARK_SOLUTION.md"
    timeout = obj.get("timeout_s") or obj.get("timeout") or DEFAULT_TIMEOUT_S
    return Task(
        id=tid,
        prompt=str(obj.get("prompt") or f"Resolve benchmark task {tid}."),
        verify=str(verify),
        timeout_s=float(timeout),
        fixture=str(obj.get("fixture") or ""),
        target=str(obj.get("target") or "."),
        seed_files={str(k): str(v) for k, v in (obj.get("seed_files") or {}).items()},
        solution_files={str(k): str(v) for k, v in (obj.get("solution_files") or {}).items()},
        solution_patch=str(obj.get("solution_patch") or ""),
        stub={str(k): int(v) for k, v in (obj.get("stub") or {}).items()},
        raw=obj,
    )


def load_tasks(path: Path | None) -> tuple[list[Task], str]:
    """Load tasks from *path*; fall back to the built-in set when absent."""
    if path and Path(path).is_file():
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        entries = data.get("tasks", data) if isinstance(data, dict) else data
        tasks = [_as_task(e) for e in entries]
        if tasks:
            return tasks, f"file:{path}"
    return builtin_tasks(), "builtin"


# The built-in set mirrors what benchmark/tasks.json describes. Each task is
# tiny, stdlib-only and verifies in milliseconds.
BUILTIN_SPECS: list[dict[str, Any]] = [
    {
        "id": "fix_sum_list",
        "prompt": "sum_values() in app/calc.py returns the wrong result; fix it so the tests pass.",
        "target": "app/calc.py",
        "verify": "python3 -m unittest discover -s tests -t . -q",
        "timeout_s": 30,
        "seed_files": {
            "app/__init__.py": "",
            "app/calc.py": "def sum_values(values):\n    return sum(values) - 1\n",
            "tests/__init__.py": "",
            "tests/test_calc.py": (
                "import unittest\nfrom app.calc import sum_values\n\n\n"
                "class SumValuesTest(unittest.TestCase):\n"
                "    def test_basic(self):\n        self.assertEqual(sum_values([1, 2, 3]), 6)\n\n"
                "    def test_empty(self):\n        self.assertEqual(sum_values([]), 0)\n\n\n"
                'if __name__ == "__main__":\n    unittest.main()\n'
            ),
        },
        "solution_files": {"app/calc.py": "def sum_values(values):\n    return sum(values)\n"},
    },
    {
        "id": "fix_slugify",
        "prompt": "slugify() mishandles punctuation and repeated spaces. Make the tests pass.",
        "target": "app/text.py",
        "verify": "python3 -m unittest discover -s tests -t . -q",
        "timeout_s": 30,
        "seed_files": {
            "app/__init__.py": "",
            "app/text.py": "def slugify(text):\n    return text.lower().replace(' ', '-')\n",
            "tests/__init__.py": "",
            "tests/test_text.py": (
                "import unittest\nfrom app.text import slugify\n\n\n"
                "class SlugifyTest(unittest.TestCase):\n"
                "    def test_punctuation(self):\n"
                '        self.assertEqual(slugify("Hello, World!"), "hello-world")\n\n'
                "    def test_repeated_spaces(self):\n"
                '        self.assertEqual(slugify("  A   B  "), "a-b")\n\n\n'
                'if __name__ == "__main__":\n    unittest.main()\n'
            ),
        },
        "solution_files": {
            "app/text.py": (
                "import re\n\n\ndef slugify(text):\n"
                '    text = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower())\n'
                '    return text.strip("-")\n'
            )
        },
    },
    {
        "id": "fix_parse_ints",
        "prompt": "parse_ints() crashes on non-numeric input; skip junk instead and pass the tests.",
        "target": "app/parse.py",
        "verify": "python3 -m unittest discover -s tests -t . -q",
        "timeout_s": 30,
        "seed_files": {
            "app/__init__.py": "",
            "app/parse.py": "def parse_ints(items):\n    return [int(i) for i in items]\n",
            "tests/__init__.py": "",
            "tests/test_parse.py": (
                "import unittest\nfrom app.parse import parse_ints\n\n\n"
                "class ParseIntsTest(unittest.TestCase):\n"
                "    def test_skips_junk(self):\n"
                '        self.assertEqual(parse_ints(["1", "x", "3"]), [1, 3])\n\n'
                "    def test_all_junk(self):\n        self.assertEqual(parse_ints(['x']), [])\n\n\n"
                'if __name__ == "__main__":\n    unittest.main()\n'
            ),
        },
        "solution_files": {
            "app/parse.py": (
                "def parse_ints(items):\n    out = []\n    for item in items:\n"
                "        try:\n            out.append(int(item))\n"
                "        except (TypeError, ValueError):\n            continue\n    return out\n"
            )
        },
    },
    {
        "id": "add_config_loader",
        "prompt": "app/settings.py is missing load_config(); add it so the tests pass.",
        "target": "app/settings.py",
        "verify": "python3 -m unittest discover -s tests -t . -q",
        "timeout_s": 30,
        "seed_files": {
            "app/__init__.py": "",
            "app/settings.py": "import json\n\n\ndef load_config(path):\n    raise NotImplementedError\n",
            "config.json": '{"name": "claudio", "port": 8100}\n',
            "tests/__init__.py": "",
            "tests/test_settings.py": (
                "import unittest\nfrom app.settings import load_config\n\n\n"
                "class LoadConfigTest(unittest.TestCase):\n"
                "    def test_loads(self):\n"
                '        cfg = load_config("config.json")\n'
                '        self.assertEqual(cfg["name"], "claudio")\n'
                '        self.assertEqual(cfg["port"], 8100)\n\n\n'
                'if __name__ == "__main__":\n    unittest.main()\n'
            ),
        },
        "solution_files": {
            "app/settings.py": (
                "import json\n\n\ndef load_config(path):\n"
                '    with open(path, encoding="utf-8") as fh:\n        return json.load(fh)\n'
            )
        },
    },
    {
        "id": "fix_retry_backoff",
        "prompt": "retry_call() never retries. Fix it so flammable callables are retried.",
        "target": "app/retry.py",
        "verify": "python3 -m unittest discover -s tests -t . -q",
        "timeout_s": 30,
        "stub": {"retries": 1, "command_errors": 1},
        "seed_files": {
            "app/__init__.py": "",
            "app/retry.py": (
                "def retry_call(fn, attempts=3):\n    return fn()\n"
            ),
            "tests/__init__.py": "",
            "tests/test_retry.py": (
                "import unittest\nfrom app.retry import retry_call\n\n\n"
                "class RetryCallTest(unittest.TestCase):\n"
                "    def test_retries_until_success(self):\n        calls = []\n\n"
                "        def flaky():\n            calls.append(1)\n"
                "            if len(calls) < 3:\n                raise RuntimeError('boom')\n"
                '            return "ok"\n\n'
                '        self.assertEqual(retry_call(flaky, attempts=3), "ok")\n'
                "        self.assertEqual(len(calls), 3)\n\n\n"
                'if __name__ == "__main__":\n    unittest.main()\n'
            ),
        },
        "solution_files": {
            "app/retry.py": (
                "def retry_call(fn, attempts=3):\n    last = None\n    for _ in range(max(1, attempts)):\n"
                "        try:\n            return fn()\n        except Exception as exc:  # noqa: BLE001\n"
                "            last = exc\n    raise last\n"
            )
        },
    },
    {
        "id": "fix_csv_top_row",
        "prompt": "top_seller() reports the wrong seller; make the tests pass.",
        "target": "app/report.py",
        "verify": "python3 -m unittest discover -s tests -t . -q",
        "timeout_s": 30,
        "seed_files": {
            "app/__init__.py": "",
            "app/report.py": (
                "import csv\nfrom collections import Counter\n\n\n"
                "def top_seller(path):\n"
                '    with open(path, encoding="utf-8") as fh:\n'
                "        rows = list(csv.DictReader(fh))\n"
                "    counts = Counter(r['seller'] for r in rows)\n"
                "    return counts.most_common()[-1][0]\n"
            ),
            "data/sales.csv": (
                "order_id,seller,amount\n1,alpha,10\n2,beta,20\n3,alpha,30\n4,alpha,40\n5,beta,50\n"
            ),
            "tests/__init__.py": "",
            "tests/test_report.py": (
                "import unittest\nfrom app.report import top_seller\n\n\n"
                "class TopSellerTest(unittest.TestCase):\n"
                "    def test_top(self):\n"
                '        self.assertEqual(top_seller("data/sales.csv"), "alpha")\n\n\n'
                'if __name__ == "__main__":\n    unittest.main()\n'
            ),
        },
        "solution_files": {
            "app/report.py": (
                "import csv\nfrom collections import Counter\n\n\n"
                "def top_seller(path):\n"
                '    with open(path, encoding="utf-8") as fh:\n'
                "        rows = list(csv.DictReader(fh))\n"
                "    counts = Counter(r['seller'] for r in rows)\n"
                "    return counts.most_common(1)[0][0]\n"
            )
        },
    },
    {
        "id": "fix_deadline_parse",
        "prompt": "parse_deadline() rejects ISO dates with a 'Z' suffix. Fix the tests.",
        "target": "app/timeutil.py",
        "verify": "python3 -m unittest discover -s tests -t . -q",
        "timeout_s": 30,
        "stub": {"tool_errors": 1},
        "seed_files": {
            "app/__init__.py": "",
            "app/timeutil.py": (
                "from datetime import datetime\n\n\n"
                "def parse_deadline(value):\n    return datetime.strptime(value, '%Y-%m-%d %H:%M')\n"
            ),
            "tests/__init__.py": "",
            "tests/test_timeutil.py": (
                "import unittest\nfrom datetime import datetime\nfrom app.timeutil import parse_deadline\n\n\n"
                "class ParseDeadlineTest(unittest.TestCase):\n"
                "    def test_space_form(self):\n"
                '        self.assertEqual(parse_deadline("2026-01-02 03:04"), datetime(2026, 1, 2, 3, 4))\n\n'
                "    def test_zulu_form(self):\n"
                '        self.assertEqual(parse_deadline("2026-01-02T03:04Z"), datetime(2026, 1, 2, 3, 4))\n\n\n'
                'if __name__ == "__main__":\n    unittest.main()\n'
            ),
        },
        "solution_files": {
            "app/timeutil.py": (
                "from datetime import datetime\n\n\n"
                "def parse_deadline(value):\n"
                '    text = value.strip().replace("T", " ")\n'
                "    if text.endswith('Z'):\n        text = text[:-1]\n"
                "    return datetime.strptime(text, '%Y-%m-%d %H:%M')\n"
            )
        },
    },
]


def builtin_tasks() -> list[Task]:
    return [_as_task(spec) for spec in BUILTIN_SPECS]


# --------------------------------------------------------------------------- #
# git + sandbox helpers
# --------------------------------------------------------------------------- #

GIT_ENV = {
    "GIT_AUTHOR_NAME": "claudio-bench",
    "GIT_AUTHOR_EMAIL": "claudio-bench@local",
    "GIT_COMMITTER_NAME": "claudio-bench",
    "GIT_COMMITTER_EMAIL": "claudio-bench@local",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "HOME": os.devnull,
    "PATH": os.environ.get("PATH", ""),
}


def git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), env=GIT_ENV, capture_output=True, text=True, timeout=60
    )


def make_sandbox(backend: str, task_id: str, trial: int, root: Path | None = None) -> Path:
    """Create a sandbox OUTSIDE the repository so the worktree stays clean."""
    base = Path(root).resolve() if root else None
    sandbox = Path(tempfile.mkdtemp(prefix=f"claudio-bench-{backend}-{task_id}-t{trial}-", dir=base))
    resolved = sandbox.resolve()
    if resolved == REPO or REPO in resolved.parents:
        shutil.rmtree(sandbox, ignore_errors=True)
        raise RuntimeError(f"refusing to sandbox inside the repo: {resolved}")
    return sandbox


def seed_sandbox(sandbox: Path, task: Task) -> str:
    """Populate the sandbox. Returns how it was seeded (for run.json)."""
    fixture = HERE / "fixtures" / task.fixture_id
    if fixture.is_dir():
        for item in fixture.iterdir():
            dest = sandbox / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)
        return f"fixtures/{task.fixture_id}"
    for rel, content in sorted(task.seed_files.items()):
        target = sandbox / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return "inline_seed" if task.seed_files else "empty"


def commit_seed(sandbox: Path) -> bool:
    if git(["init", "-q"], sandbox).returncode != 0:
        return False
    git(["add", "-A"], sandbox)
    res = git(["commit", "-q", "--allow-empty", "-m", "benchmark seed"], sandbox)
    return res.returncode == 0


# --------------------------------------------------------------------------- #
# capability + real engine argv
# --------------------------------------------------------------------------- #


def load_capability(path: Path | None) -> dict[str, dict[str, Any]]:
    """Return {backend: {"available": bool, ...}}; missing file => nothing available."""
    out: dict[str, dict[str, Any]] = {}
    if path and Path(path).is_file():
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return out
        backends = data.get("backends", data) if isinstance(data, dict) else {}
        for name, info in (backends or {}).items():
            if isinstance(info, bool):
                out[str(name)] = {"available": info}
            elif isinstance(info, dict):
                out[str(name)] = info
    return out


def backend_available(name: str, cap: dict[str, dict[str, Any]]) -> bool:
    info = cap.get(name)
    if info is None:
        return False
    if "available" in info:
        return bool(info["available"])
    return bool(info.get("binary") or info.get("path"))


def real_argv(name: str, task: Task, sandbox: Path, cap: dict[str, dict[str, Any]]) -> list[str] | None:
    """Real engine argv, mirroring the shipped adapters. None => not launchable."""
    info = cap.get(name, {})
    if name == "claude_code":
        binary = str(info.get("binary") or "claude")
        if not shutil.which(binary):
            return None
        argv = [binary, "-p", task.prompt, "--output-format", "stream-json", "--verbose"]
        if info.get("model"):
            argv += ["--model", str(info["model"])]
        argv += ["--permission-mode", str(info.get("permission_mode", "acceptEdits"))]
        return argv
    if name == "opencode":
        binary = str(info.get("binary") or "opencode")
        if not shutil.which(binary):
            return None
        argv = [binary, "run", task.prompt, "--format", "json", "--thinking"]
        if info.get("model"):
            argv += ["-m", str(info["model"])]
        return argv
    if name == "google_adk":
        # ADK has no CLI: it runs in-process. Only attempted when explicitly
        # opted in, because it needs a reachable model endpoint.
        if not os.environ.get("CLAUDIO_BENCH_ALLOW_REAL"):
            return None
        try:
            import importlib.util

            if importlib.util.find_spec("google.adk") is None:
                return None
        except Exception:  # noqa: BLE001
            return None
        return [sys.executable, "-c", _ADK_DRIVER_SRC, task.prompt]
    return None


# In-process ADK driver, executed as ``python3 -c <src> <prompt>`` so the task
# timeout is enforced the same way as for the CLI engines. Any failure exits
# non-zero, which makes the runner downgrade that backend to the stub.
_ADK_DRIVER_SRC = r"""
import json, os, sys

prompt = sys.argv[1] if len(sys.argv) > 1 else ""
try:
    from google.adk.agents import Agent
    from google.adk.runners import InMemoryRunner
    from google.adk.sessions import InMemorySessionService
except Exception as exc:  # missing/incompatible ADK
    print(json.dumps({"type": "error", "code": "import_error", "message": str(exc)}))
    sys.exit(3)

model = os.environ.get("CLAUDIO_BENCH_ADK_MODEL", "gemini-2.0-flash")
try:
    agent = Agent(name="claudio_bench", model=model, instruction=prompt)
    runner = InMemoryRunner(agent=agent, app_name="claudio_bench",
                            session_service=InMemorySessionService())
    session = runner.session_service.create_session_sync(app_name="claudio_bench", user_id="bench")
    from google.genai import types
    for ev in runner.run_new(user_id="bench", session_id=session.id,
                             new_message=types.Content(role="user", parts=[types.Part(text=prompt)])):
        print(json.dumps({"type": "adk_event", "author": getattr(ev, "author", None),
                          "content": str(getattr(ev, "content", ""))[:2000]}, default=str))
except Exception as exc:  # no endpoint / bad config -> let the runner fall back
    print(json.dumps({"type": "error", "code": "adk_run_failed", "message": str(exc)[:500]}))
    sys.exit(4)
"""



# --------------------------------------------------------------------------- #
# engine output parsing
# --------------------------------------------------------------------------- #


def parse_engine_stream(text: str) -> tuple[dict[str, int], dict[str, int]]:
    """Best-effort token/error accounting from a real engine's JSONL stream.

    Understands Claude ``stream-json`` (assistant/result usage records) and
    opencode ``--format json`` (step_finish tokens). Anything unparseable is
    ignored rather than failing the run.
    """
    tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "total": 0}
    errors = {"command_errors": 0, "tool_errors": 0, "retries": 0, "timeouts": 0}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        usage = ev.get("usage") if isinstance(ev.get("usage"), dict) else None
        if usage:
            tokens["input"] += int(usage.get("input_tokens") or usage.get("input") or 0)
            tokens["output"] += int(usage.get("output_tokens") or usage.get("output") or 0)
            tokens["cache_read"] += int(usage.get("cache_read_input_tokens") or usage.get("cache_read") or 0)
            tokens["cache_write"] += int(usage.get("cache_creation_input_tokens") or 0)
        tok = ev.get("tokens") if isinstance(ev.get("tokens"), dict) else None
        if tok:
            tokens["input"] += int(tok.get("input") or 0)
            tokens["output"] += int(tok.get("output") or 0)
            tokens["cache_read"] += int(tok.get("cache") or tok.get("cache_read") or 0)
        etype = str(ev.get("type") or "")
        if etype in ("error", "tool_error"):
            errors["tool_errors"] += 1
        if ev.get("is_error") or ev.get("error"):
            errors["command_errors"] += 1
        if etype == "retry":
            errors["retries"] += 1
    tokens["total"] = tokens["input"] + tokens["output"] + tokens["cache_read"] + tokens["cache_write"]
    return tokens, errors


def stub_tokens(backend: str, task_id: str, trial: int) -> dict[str, int]:
    """Deterministic token profile, shared with stub_engine.py."""
    sys.path.insert(0, str(HERE)) if str(HERE) not in sys.path else None
    import stub_engine  # noqa: PLC0415 - local, stdlib-only

    return stub_engine.token_profile(backend, task_id, trial)


# --------------------------------------------------------------------------- #
# one run
# --------------------------------------------------------------------------- #


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def run_one(
    backend: str,
    task: Task,
    trial: int,
    out_root: Path,
    cap: dict[str, dict[str, Any]],
    sandbox_root: Path | None = None,
    force_mode: str = "auto",
    keep_sandbox: bool = False,
    downgraded: set[str] | None = None,
) -> dict[str, Any]:
    """Run one (backend, task, trial) triple and write its artifacts."""
    run_dir = Path(out_root) / backend / task.id / f"trial_{trial}"
    run_dir.mkdir(parents=True, exist_ok=True)

    sandbox = make_sandbox(backend, task.id, trial, sandbox_root)
    started = time.monotonic()
    seeded_from = seed_sandbox(sandbox, task)
    git_ok = commit_seed(sandbox)

    # ---- pick the engine -------------------------------------------------- #
    skipped = downgraded is not None and backend in downgraded
    want_real = (not skipped) and (
        force_mode == "real" or (force_mode == "auto" and backend_available(backend, cap))
    )
    argv = real_argv(backend, task, sandbox, cap) if want_real else None
    engine_mode = "real" if argv else "stub"
    notes: list[str] = []
    if want_real and not argv:
        notes.append(f"capability marked {backend} available but it was not launchable; used stub")

    if argv is None:
        argv = [
            sys.executable,
            str(HERE / "stub_engine.py"),
            "--backend", backend,
            "--task-id", task.id,
            "--trial", str(trial),
            "--sandbox", str(sandbox),
            "--delay", str(float(task.stub.get("delay_s", 0) or 0)),
        ]

    # ---- execute, enforcing the task timeout ------------------------------ #
    timed_out = False
    returncode: int | None = None
    stdout = stderr = ""
    try:
        proc = subprocess.run(
            argv,
            cwd=str(sandbox),
            input=json.dumps(task.spec()),
            capture_output=True,
            text=True,
            timeout=task.timeout_s,
        )
        returncode, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        returncode = None
        stdout = (exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = (exc.stderr or b"").decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        stderr = (stderr + f"\nrunner: engine exceeded {task.timeout_s}s and was killed\n").strip()
    except OSError as exc:
        returncode = 127
        stderr = f"runner: engine could not be launched: {exc}\n"

    engine_exit = "timeout" if timed_out else ("ok" if returncode == 0 else f"exit_{returncode}")

    # ---- verification ------------------------------------------------------ #
    verify_rc: int | None = None
    verify_out = ""
    try:
        vproc = subprocess.run(
            shlex.split(task.verify),
            cwd=str(sandbox),
            capture_output=True,
            text=True,
            timeout=VERIFY_TIMEOUT_S,
        )
        verify_rc = vproc.returncode
        verify_out = (vproc.stdout or "") + (vproc.stderr or "")
    except subprocess.TimeoutExpired:
        verify_rc = 124
        verify_out = f"verification exceeded {VERIFY_TIMEOUT_S}s"
    except (OSError, ValueError) as exc:
        verify_rc = 127
        verify_out = f"verification could not run: {exc}"
    verification_passed = verify_rc == 0

    # ---- diff -------------------------------------------------------------- #
    diff_text = ""
    changed: list[str] = []
    if git_ok:
        git(["add", "-A"], sandbox)
        d = git(["diff", "--cached", "--no-color"], sandbox)
        diff_text = d.stdout or ""
        s = git(["diff", "--cached", "--name-only"], sandbox)
        changed = [ln for ln in (s.stdout or "").splitlines() if ln.strip()]

    # A real engine that cannot actually run (no auth, no endpoint, wrong argv)
    # must not burn the whole time budget: downgrade the backend to the stub for
    # the rest of the matrix and say so in the record.
    if engine_mode == "real" and downgraded is not None and backend not in downgraded:
        if timed_out or returncode != 0 or not changed:
            downgraded.add(backend)
            notes.append(
                f"{backend} real engine produced no change on {task.id}; "
                "remaining runs for this backend fall back to the stub engine"
            )

    duration_s = round(time.monotonic() - started, 3)

    # ---- tokens + errors --------------------------------------------------- #
    if engine_mode == "stub":
        tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "total": 0}
        errors = {"command_errors": 0, "tool_errors": 0, "retries": 0, "timeouts": 0}
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "usage":
                tokens = {
                    "input": int(ev.get("input_tokens", 0)),
                    "output": int(ev.get("output_tokens", 0)),
                    "cache_read": int(ev.get("cache_read_input_tokens", 0)),
                    "cache_write": int(ev.get("cache_creation_input_tokens", 0)),
                    "total": int(ev.get("total_tokens", 0)),
                }
            elif ev.get("type") == "error":
                key = "command_errors" if ev.get("code") == "command_error" else "tool_errors"
                errors[key] += 1
            elif ev.get("type") == "retry":
                errors["retries"] += 1
        if not tokens["total"]:
            tokens = stub_tokens(backend, task.id, trial)
    else:
        tokens, errors = parse_engine_stream(stdout)
    if timed_out:
        errors["timeouts"] = errors.get("timeouts", 0) + 1
    tokens.setdefault("total", tokens.get("input", 0) + tokens.get("output", 0) + tokens.get("cache_read", 0))

    # ---- artifacts --------------------------------------------------------- #
    _write(run_dir / "stdout.txt", stdout)
    _write(run_dir / "stderr.txt", stderr)
    _write(run_dir / "session.jsonl", stdout if stdout.endswith("\n") or not stdout else stdout + "\n")
    _write(run_dir / "diff.patch", diff_text)
    _write(
        run_dir / "verify.txt",
        f"command: {task.verify}\nexit_status: {verify_rc}\n\n{verify_out}",
    )
    _write(run_dir / "prompt.txt", task.prompt + "\n")

    record: dict[str, Any] = {
        "backend": backend,
        "task_id": task.id,
        "trial": trial,
        "engine_mode": engine_mode,
        "duration_s": duration_s,
        "exit_status": engine_exit,
        "timed_out": timed_out,
        "tokens": {
            "input": int(tokens.get("input", 0)),
            "output": int(tokens.get("output", 0)),
            "cache_read": int(tokens.get("cache_read", 0)),
            "cache_write": int(tokens.get("cache_write", 0)),
            "total": int(tokens.get("total", 0)),
        },
        "errors": {
            "command_errors": int(errors.get("command_errors", 0)),
            "tool_errors": int(errors.get("tool_errors", 0)),
            "retries": int(errors.get("retries", 0)),
            "timeouts": int(errors.get("timeouts", 0)),
        },
        "verification_passed": bool(verification_passed),
        "engine_exit_code": returncode,
        "verify_command": task.verify,
        "verify_exit_code": verify_rc,
        "seeded_from": seeded_from,
        "changed_files": changed,
        "diff_lines": len(diff_text.splitlines()),
        "session_events": len([ln for ln in stdout.splitlines() if ln.strip().startswith("{")]),
        "timeout_s": task.timeout_s,
        "sandbox_outside_repo": True,
        "notes": notes,
    }
    _write(run_dir / "run.json", json.dumps(record, indent=2, sort_keys=True) + "\n")

    if keep_sandbox:
        record["sandbox"] = str(sandbox)
    else:
        shutil.rmtree(sandbox, ignore_errors=True)
    return record


# --------------------------------------------------------------------------- #
# capability probe
# --------------------------------------------------------------------------- #


def probe_capability() -> dict[str, dict[str, Any]]:
    """Detect which real engines are actually usable on this machine."""
    out: dict[str, dict[str, Any]] = {}
    for name in BACKENDS:
        entry: dict[str, Any] = {"available": False, "engine_mode": "stub"}
        if name == "claude_code":
            path = shutil.which("claude")
            entry.update({"binary": path or "claude", "argv": "claude -p <prompt> --output-format stream-json"})
            entry["available"] = bool(path)
            entry["reason"] = "claude binary found" if path else "claude binary not on PATH"
        elif name == "opencode":
            path = shutil.which("opencode")
            entry.update({"binary": path or "opencode", "argv": "opencode run <prompt> --format json"})
            entry["available"] = bool(path)
            entry["reason"] = "opencode binary found" if path else "opencode binary not on PATH"
        else:
            try:
                import importlib.util

                found = importlib.util.find_spec("google.adk") is not None
            except Exception:  # noqa: BLE001
                found = False
            opted_in = bool(os.environ.get("CLAUDIO_BENCH_ALLOW_REAL"))
            entry.update({"binary": None, "argv": "in-process google.adk runner"})
            entry["available"] = bool(found and opted_in)
            if not found:
                entry["reason"] = "google-adk not installed (offline gate)"
            elif not opted_in:
                entry["reason"] = (
                    "google-adk importable but in-process runs need a reachable model "
                    "endpoint; set CLAUDIO_BENCH_ALLOW_REAL=1 to enable"
                )
            else:
                entry["reason"] = "google-adk importable and real runs opted in"
        if entry["available"]:
            entry["engine_mode"] = "real"
        out[name] = entry
    return {"generated_by": "benchmark/runner.py --probe-capability", "backends": out}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _summary_line(idx: int, total: int, rec: dict[str, Any]) -> str:
    tok = rec["tokens"]
    err = rec["errors"]
    return (
        f"[{idx:>2}/{total}] backend={rec['backend']:<11} task={rec['task_id']:<20} "
        f"trial={rec['trial']} mode={rec['engine_mode']:<4} exit={rec['exit_status']:<7} "
        f"verify={'PASS' if rec['verification_passed'] else 'FAIL'} "
        f"dur={rec['duration_s']:.2f}s tokens=in:{tok['input']}/out:{tok['output']}"
        f"/cache:{tok['cache_read']}/total:{tok['total']} "
        f"errors=cmd:{err['command_errors']},tool:{err['tool_errors']},"
        f"retry:{err['retries']},timeout:{err['timeouts']}"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Claudio engine benchmark runner")
    ap.add_argument("--tasks", default="benchmark/tasks.json")
    ap.add_argument("--capability", default="benchmark/results/capability.json")
    ap.add_argument("--backends", default=",".join(BACKENDS))
    ap.add_argument("--trials", type=int, default=1)
    ap.add_argument("--out", default="benchmark/results")
    ap.add_argument("--sandbox-root", default=None, help="parent dir for sandboxes (default: system temp)")
    ap.add_argument("--engine-mode", choices=("auto", "stub", "real"), default="auto")
    ap.add_argument("--only", default=None, help="comma-separated task ids to run")
    ap.add_argument("--keep-sandbox", action="store_true")
    ap.add_argument("--probe-capability", action="store_true", help="write a probed capability.json and exit")
    args = ap.parse_args(argv)

    if args.probe_capability:
        path = Path(args.capability)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(probe_capability(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {path}")
        return 0

    tasks, source = load_tasks(Path(args.tasks))
    if args.only:
        wanted = {t.strip() for t in args.only.split(",") if t.strip()}
        tasks = [t for t in tasks if t.id in wanted]
    cap = load_capability(Path(args.capability))
    backends = [b.strip() for b in args.backends.split(",") if b.strip()]
    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    total = len(backends) * len(tasks) * max(1, args.trials)
    print(
        f"claudio-benchmark: tasks={len(tasks)} (from {source}) backends={','.join(backends)} "
        f"trials={args.trials} runs={total} out={out_root} "
        f"sandbox_root={args.sandbox_root or tempfile.gettempdir()} (outside repo)"
    )

    records: list[dict[str, Any]] = []
    downgraded: set[str] = set()
    idx = 0
    wall0 = time.monotonic()
    for backend in backends:
        for task in tasks:
            for trial in range(1, max(1, args.trials) + 1):
                idx += 1
                rec = run_one(
                    backend,
                    task,
                    trial,
                    out_root,
                    cap,
                    sandbox_root=Path(args.sandbox_root) if args.sandbox_root else None,
                    force_mode=args.engine_mode,
                    keep_sandbox=args.keep_sandbox,
                    downgraded=downgraded,
                )
                records.append(rec)
                print(_summary_line(idx, total, rec), flush=True)

    wall = time.monotonic() - wall0
    passed = sum(1 for r in records if r["verification_passed"])
    timeouts = sum(1 for r in records if r["timed_out"])
    real = sum(1 for r in records if r["engine_mode"] == "real")
    tokens = sum(r["tokens"]["total"] for r in records)
    print(
        f"SUMMARY runs={len(records)} verify_pass={passed}/{len(records)} timeouts={timeouts} "
        f"real_engine_runs={real} stub_engine_runs={len(records) - real} "
        f"total_tokens={tokens} wall={wall:.2f}s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
