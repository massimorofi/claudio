#!/usr/bin/env python3
"""Benchmark runner driver for the Claudio engine comparison.

For every (backend, task, trial) triple this creates a throwaway sandbox
*outside* the repository, seeds it from ``benchmark/fixtures/<id>``, commits
the seed with git, runs an engine against it (the real CLI/library when
``capability.json`` says the backend is available, otherwise the deterministic
stub engine), runs the task's verification command, captures the resulting
``git diff`` and writes a ``run.json`` summary.

Artifacts land in ``<out>/<backend>/<task_id>/trial_<k>/``::

    run.json          the machine-readable summary (see RUN_KEYS)
    session.jsonl     engine session log (JSONL)
    stdout.txt        raw engine stdout   (.txt, not .log: .gitignore hides *.log)
    stderr.txt        raw engine stderr
    verification.txt  output of the verification command
    diff.patch        git diff of the sandbox against the seed commit

Stdlib only, no network.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stub_engine  # noqa: E402

BACKENDS = ("google_adk", "claude_code", "opencode")

RUN_KEYS = {"backend", "task_id", "trial", "engine_mode", "duration_s",
            "exit_status", "timed_out", "tokens", "errors",
            "verification_passed"}

# Files the sandbox may grow during a run that must never reach diff.patch.
_JUNK = ("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache")

GIT_ENV = {
    "GIT_AUTHOR_NAME": "claudio-bench",
    "GIT_AUTHOR_EMAIL": "claudio-bench@local",
    "GIT_COMMITTER_NAME": "claudio-bench",
    "GIT_COMMITTER_EMAIL": "claudio-bench@local",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+00:00",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+00:00",
}


class EngineUnavailable(RuntimeError):
    """Raised when a backend marked available cannot actually be executed."""


# --------------------------------------------------------------------------
# inputs
# --------------------------------------------------------------------------

def load_tasks(path):
    """Load the locked task manifest, falling back to the built-in copy."""
    path = Path(path)
    if path.is_file():
        data = json.loads(path.read_text())
        tasks = data.get("tasks", data) if isinstance(data, dict) else data
        source = str(path)
    else:
        tasks = list(stub_engine.DEFAULT_TASKS)
        source = "%s (built-in fallback: %s not found)" % (
            Path(__file__).name, path)
    if not tasks:
        raise SystemExit("no tasks found in %s" % path)
    return tasks, source


def load_capability(path):
    """Return ``{backend: entry}`` from a capability report (possibly empty)."""
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    backends = data.get("backends", data) if isinstance(data, dict) else {}
    return {k: v for k, v in backends.items() if isinstance(v, dict)}


def resolve_binary(entry, default):
    """Absolute path of the backend binary, or ``None`` when not runnable."""
    name = entry.get("binary") or default
    if not name:
        return None
    found = shutil.which(name)
    if found:
        return found
    candidate = Path(name)
    return str(candidate) if candidate.is_file() and os.access(str(candidate), os.X_OK) else None


# --------------------------------------------------------------------------
# sandbox
# --------------------------------------------------------------------------

def make_sandbox(repo_root, backend, task_id, trial, parent=None):
    """Create a sandbox directory guaranteed to live outside ``repo_root``."""
    sandbox = Path(tempfile.mkdtemp(prefix="claudio-bench-%s-%s-t%d-" % (
        backend, task_id, trial), dir=parent))
    real_root = Path(os.path.realpath(repo_root))
    real_box = Path(os.path.realpath(sandbox))
    if real_root in real_box.parents or real_box == real_root:
        shutil.rmtree(str(real_box), ignore_errors=True)
        raise RuntimeError("sandbox %s is inside the worktree" % real_box)
    return real_box


def seed_sandbox(sandbox, task, repo_root):
    """Copy the fixture tree into the sandbox (or materialise the fallback)."""
    fixture = None
    if task.get("fixture_dir"):
        candidate = Path(task["fixture_dir"])
        if not candidate.is_absolute():
            candidate = Path(repo_root) / candidate
        if candidate.is_dir():
            fixture = candidate
    if fixture is not None:
        shutil.copytree(str(fixture), str(sandbox), dirs_exist_ok=True)
        return "fixture:%s" % fixture.name
    files = stub_engine.seed_files(task["id"])
    for rel, text in files.items():
        target = sandbox / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return "embedded-seed"


def git(sandbox, *args):
    env = dict(os.environ)
    env.update(GIT_ENV)
    return subprocess.run(["git", "-C", str(sandbox)] + list(args),
                          capture_output=True, text=True, env=env)


def init_repo(sandbox):
    git(sandbox, "init", "-q")
    git(sandbox, "config", "user.email", "claudio-bench@local")
    git(sandbox, "config", "user.name", "claudio-bench")
    git(sandbox, "config", "commit.gpgsign", "false")
    git(sandbox, "add", "-A")
    git(sandbox, "commit", "-q", "--allow-empty", "-m", "seed: benchmark fixture")


def clean_junk(sandbox):
    for root, dirs, _files in os.walk(str(sandbox)):
        for name in list(dirs):
            if name in _JUNK:
                shutil.rmtree(os.path.join(root, name), ignore_errors=True)
                dirs.remove(name)


def capture_diff(sandbox):
    clean_junk(sandbox)
    git(sandbox, "add", "-A")
    return git(sandbox, "diff", "--cached").stdout


# --------------------------------------------------------------------------
# engines
# --------------------------------------------------------------------------

def real_argv(backend, binary, task):
    """Command line used for a real CLI engine run."""
    prompt = task.get("prompt", "")
    if backend == "claude_code":
        return [binary, "-p", prompt, "--output-format", "stream-json",
                "--verbose"]
    if backend == "opencode":
        return [binary, "run", prompt, "--format", "json"]
    raise EngineUnavailable("no CLI argv for backend %s" % backend)


def run_google_adk_in_process(sandbox, task, timeout):
    """Drive the ADK engine in-process. Raises EngineUnavailable if absent."""
    def _call():
        try:
            from google.adk.runners import InMemoryRunner  # type: ignore
        except Exception as exc:  # pragma: no cover - offline path
            raise EngineUnavailable("google_adk not importable: %r" % exc)
        runner = InMemoryRunner(agent_name="claudio",
                                app_name="claudio_bench")  # pragma: no cover
        return runner

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_call)
        future.result(timeout=timeout)
    raise EngineUnavailable("google_adk in-process run not configured")


def parse_stream(text):
    """Extract token counts and error events from a JSONL engine stream."""
    tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    errors = {"command_errors": 0, "tool_errors": 0, "retries": 0}
    for line in text.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        kind = event.get("type")
        usage = event.get("usage")
        if isinstance(usage, dict):
            tokens["input"] += int(usage.get("input_tokens") or 0)
            tokens["output"] += int(usage.get("output_tokens") or 0)
            tokens["cache_read"] += int(usage.get("cache_read_input_tokens") or 0)
            tokens["cache_write"] += int(usage.get("cache_creation_input_tokens") or 0)
        tk = event.get("tokens")
        if isinstance(tk, dict):
            tokens["input"] += int(tk.get("input") or 0)
            tokens["output"] += int(tk.get("output") or 0)
            cache = tk.get("cache")
            if isinstance(cache, dict):
                tokens["cache_read"] += int(cache.get("read") or 0)
                tokens["cache_write"] += int(cache.get("write") or 0)
        if kind == "tool_result" and event.get("is_error"):
            errors["tool_errors"] += 1
            if event.get("name") == "run_command":
                errors["command_errors"] += 1
        if kind in ("error", "retry"):
            errors["retries"] += 1
    tokens["total"] = (tokens["input"] + tokens["output"]
                       + tokens["cache_read"] + tokens["cache_write"])
    return tokens, errors


def run_engine(backend, task, trial, sandbox, capability, timeout_cap):
    """Run one engine (real if available, else stub) in ``sandbox``."""
    entry = capability.get(backend, {}) if capability else {}
    timeout = float(task.get("max_seconds", 600))
    if timeout_cap:
        timeout = min(timeout, float(timeout_cap))
    mode = "stub"
    result = None

    if entry.get("available"):
        try:
            if backend == "google_adk":
                run_google_adk_in_process(sandbox, task, timeout)
            else:
                binary = resolve_binary(entry, backend)
                if binary is None:
                    raise EngineUnavailable("binary for %s not found" % backend)
                argv = real_argv(backend, binary, task)
                mode = "real"
                result = _run_subprocess(argv, sandbox, timeout)
        except EngineUnavailable:
            mode, result = "stub", None
            entry = {}

    if result is None:
        mode = "stub"
        result = stub_engine.run_stub(sandbox, backend, task, trial)

    tokens, errors = parse_stream(result["stdout"])
    if not any(tokens.values()):
        tokens = dict(result["tokens"])
    merged = dict(result["errors"])
    merged["command_errors"] = max(merged.get("command_errors", 0),
                                   errors["command_errors"])
    merged["tool_errors"] = max(merged.get("tool_errors", 0),
                                errors["tool_errors"])
    merged["retries"] = max(merged.get("retries", 0), errors["retries"])
    merged.setdefault("timeouts", 1 if result["timed_out"] else 0)
    result["engine_mode"] = mode
    result["tokens"] = tokens
    result["errors"] = merged
    result["timeout_used"] = timeout
    return result


def _run_subprocess(argv, sandbox, timeout):
    started = time.monotonic()
    try:
        proc = subprocess.run(argv, cwd=str(sandbox), capture_output=True,
                              text=True, timeout=timeout)
        out, err, rc, timed_out = proc.stdout, proc.stderr, proc.returncode, False
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or b"").decode("utf-8", "replace") if isinstance(
            exc.stdout, bytes) else (exc.stdout or "")
        err = (exc.stderr or b"").decode("utf-8", "replace") if isinstance(
            exc.stderr, bytes) else (exc.stderr or "")
        rc, timed_out = 124, True
    except OSError as exc:
        raise EngineUnavailable("exec failed: %r" % exc)
    status = "timeout" if timed_out else ("ok" if rc == 0 else "failed")
    return {
        "stdout": out,
        "stderr": err,
        "exit_status": status,
        "timed_out": timed_out,
        "returncode": rc,
        "tokens": {"input": 0, "output": 0, "cache_read": 0,
                   "cache_write": 0, "total": 0},
        "errors": {"command_errors": 0, "tool_errors": 0, "retries": 0,
                   "timeouts": 1 if timed_out else 0},
        "duration": time.monotonic() - started,
    }


# --------------------------------------------------------------------------
# verification + artifacts
# --------------------------------------------------------------------------

def run_verification(sandbox, task, timeout_cap):
    command = task.get("verification") or "true"
    timeout = float(task.get("max_seconds", 600))
    if timeout_cap:
        timeout = min(timeout, float(timeout_cap))
    try:
        proc = subprocess.run(command, shell=True, cwd=str(sandbox),
                              capture_output=True, text=True, timeout=timeout)
        return proc.returncode == 0, proc.stdout + proc.stderr, False
    except subprocess.TimeoutExpired as exc:
        text = getattr(exc, "stdout", "") or ""
        if isinstance(text, bytes):
            text = text.decode("utf-8", "replace")
        return False, text + "\n[verification timed out]", True


def write_artifacts(box, task, backend, trial, result, verify_out,
                    verification_passed, diff_text, duration):
    run = {
        "backend": backend,
        "task_id": task["id"],
        "trial": trial,
        "engine_mode": result["engine_mode"],
        "duration_s": round(duration, 3),
        "exit_status": result["exit_status"],
        "timed_out": bool(result["timed_out"]),
        "tokens": result["tokens"],
        "errors": result["errors"],
        "verification_passed": bool(verification_passed),
        "verification_command": task.get("verification", ""),
        "timeout_s": result.get("timeout_used"),
        "sandbox": str(box),
        "session_lines": result.get("session_lines"),
    }
    run["schema_keys_ok"] = RUN_KEYS <= set(run)
    files = {
        "session.jsonl": result["stdout"],
        "stdout.txt": result["stdout"],
        "stderr.txt": result["stderr"],
        "verification.txt": verify_out,
        "diff.patch": diff_text,
        "run.json": json.dumps(run, indent=2, sort_keys=True) + "\n",
    }
    return run, files


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------

def run_one(backend, task, trial, repo_root, out_root, capability, args):
    started = time.monotonic()
    sandbox = make_sandbox(repo_root, backend, task["id"], trial,
                           parent=args.sandbox_dir)
    try:
        seed_sandbox(sandbox, task, repo_root)
        init_repo(sandbox)
        result = run_engine(backend, task, trial, sandbox, capability,
                            args.timeout_cap)
        verification_passed, verify_out, _ = run_verification(
            sandbox, task, args.timeout_cap)
        diff_text = capture_diff(sandbox)
        duration = time.monotonic() - started
        run, files = write_artifacts(sandbox, task, backend, trial, result,
                                     verify_out, verification_passed,
                                     diff_text, duration)
        box = Path(out_root) / backend / task["id"] / ("trial_%d" % trial)
        box.mkdir(parents=True, exist_ok=True)
        for name, text in files.items():
            (box / name).write_text(text)
        return run, box
    finally:
        if not args.keep_sandbox:
            shutil.rmtree(str(sandbox), ignore_errors=True)


def commit_artifacts(repo_root, out_root):
    """Commit the produced artifacts so the tree stays clean afterwards."""
    root = Path(repo_root)
    if subprocess.run(["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
                      capture_output=True).returncode != 0:
        return "not a git repo"
    rel = os.path.relpath(os.path.realpath(out_root), str(root))
    if rel.startswith(".."):
        return "outside repo"
    subprocess.run(["git", "-C", str(root), "add", "-A", "--", rel],
                   capture_output=True)
    probe = subprocess.run(["git", "-C", str(root), "diff", "--cached",
                            "--quiet", "--", rel], capture_output=True)
    if probe.returncode == 0:
        return "unchanged"
    proc = subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=claudio-bench@local",
         "-c", "user.name=claudio-bench", "commit", "-q", "-m",
         "T4: benchmark run artifacts"], capture_output=True, text=True)
    return "committed" if proc.returncode == 0 else "commit failed"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tasks", default="benchmark/tasks.json")
    parser.add_argument("--capability",
                        default="benchmark/results/capability.json")
    parser.add_argument("--backends", default=",".join(BACKENDS))
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--out", default="benchmark/results")
    parser.add_argument("--repo-root", default=None,
                        help="repo whose tree is copied; defaults to this checkout")
    parser.add_argument("--timeout-cap", type=float, default=0.0,
                        help="clamp every per-run timeout (seconds)")
    parser.add_argument("--only", default=None,
                        help="comma-separated task ids to run")
    parser.add_argument("--keep-sandbox", action="store_true")
    parser.add_argument("--sandbox-dir", default=None,
                        help="parent dir for sandboxes (must be outside the repo)")
    parser.add_argument("--no-commit", dest="commit", action="store_false")
    parser.set_defaults(commit=True)
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root or Path(__file__).resolve().parents[1])
    tasks, source = load_tasks(args.tasks)
    if args.only:
        wanted = {x.strip() for x in args.only.split(",") if x.strip()}
        tasks = [t for t in tasks if t["id"] in wanted]
    capability = load_capability(args.capability)
    backends = [b.strip() for b in args.backends.split(",") if b.strip()]
    out_root = Path(args.out)
    if not out_root.is_absolute():
        out_root = repo_root / out_root
    out_root.mkdir(parents=True, exist_ok=True)

    total = len(backends) * len(tasks) * args.trials
    print("benchmark runner: %d runs (%d backends x %d tasks x %d trials)"
          % (total, len(backends), len(tasks), args.trials))
    print("tasks: %s" % source)
    print("capability: %s" % (args.capability
                              if Path(args.capability).is_file() else "absent -> stub"))

    index, failures = 0, []
    started = time.monotonic()
    for backend in backends:
        for task in tasks:
            for trial in range(1, args.trials + 1):
                index += 1
                run, box = run_one(backend, task, trial, repo_root, out_root,
                                   capability, args)
                tok = run["tokens"]
                err = run["errors"]
                print("[%d/%d] %s %s trial_%d engine=%s exit=%s verify=%s "
                      "%.2fs tokens in=%d out=%d cache=%d total=%d "
                      "err c/t/r/to=%d/%d/%d/%d -> %s"
                      % (index, total, run["backend"], run["task_id"],
                         run["trial"], run["engine_mode"], run["exit_status"],
                         "PASS" if run["verification_passed"] else "FAIL",
                         run["duration_s"], tok["input"], tok["output"],
                         tok.get("cache_read", 0), tok["total"],
                         err["command_errors"], err["tool_errors"],
                         err["retries"], err["timeouts"], box))
                if not run["verification_passed"]:
                    failures.append("%s/%s" % (backend, task["id"]))

    elapsed = time.monotonic() - started
    passed = total - len(failures)
    print("summary: %d/%d runs verified in %.1fs (out=%s)"
          % (passed, total, elapsed, out_root))
    if failures:
        print("failed verification: %s" % ", ".join(sorted(failures)))
    if args.commit:
        print("artifacts: %s" % commit_artifacts(repo_root, out_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
