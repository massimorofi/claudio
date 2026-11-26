#!/usr/bin/env python3
"""Deterministic, offline, stdlib-only stub engine for the Claudio benchmark.

The benchmark must run end-to-end on a gate machine that has no engine binary
(``claude`` / ``opencode``) and no network. This module plays the role of one:
it reads a task spec on stdin, writes a deterministic JSONL session log to
stdout (usage records with input/output/cache token counts, tool_call /
tool_result / error events) and applies a canned solution patch inside the
sandbox working directory it is given.

It is deliberately a *pure function of its inputs*: the same
(backend, task_id, trial) always yields byte-identical output, so runs are
diffable and regressions are visible.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path


def _digest(*parts: object) -> str:
    """Stable hash of the given parts (independent of PYTHONHASHSEED)."""
    payload = "|".join(str(p) for p in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _num(*parts: object) -> int:
    """Stable non-negative integer derived from *parts*."""
    return int(_digest(*parts)[:8], 16)


def token_profile(backend: str, task_id: str, trial: int) -> dict:
    """Deterministic token counts for one run.

    ``total`` is the billable sum (input + output + cache_read) so consumers can
    compare against a real engine's accounting without knowing how each CLI
    splits cached from uncached input.
    """
    inp = 900 + _num(backend, task_id, trial, "in") % 2400
    out = 180 + _num(backend, task_id, trial, "out") % 900
    cache = _num(backend, task_id, trial, "cache") % 1200
    return {
        "input": inp,
        "output": out,
        "cache_read": cache,
        "cache_write": 0,
        "total": inp + out + cache,
    }


class Log:
    """Buffered JSONL writer: one event per line on stdout."""

    def __init__(self) -> None:
        self._clock = _num("clock") % 1000
        self.events: list[dict] = []

    def emit(self, etype: str, **fields: object) -> dict:
        self._clock += 37
        ev: dict = {"type": etype, "t_ms": self._clock}
        ev.update(fields)
        self.events.append(ev)
        sys.stdout.write(json.dumps(ev, sort_keys=True, separators=(",", ":")) + "\n")
        sys.stdout.flush()
        return ev


# --------------------------------------------------------------------------- #
# canned solution application
# --------------------------------------------------------------------------- #


def _safe_join(root: Path, rel: str) -> Path:
    """Join *rel* onto *root*, refusing to escape the sandbox."""
    target = (root / rel).resolve()
    root_r = root.resolve()
    if target != root_r and root_r not in target.parents:
        raise ValueError(f"path escapes sandbox: {rel!r}")
    return target


def apply_unified_diff(root: Path, text: str) -> list[str]:
    """Apply a (small, well-formed) unified diff. Returns touched paths.

    Only the subset the benchmark needs is supported: file creation, file
    modification with leading/trailing context, and pure deletions of lines.
    A hunk that does not match raises ValueError so the caller can fall back.
    """
    touched: list[str] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if not lines[i].startswith("--- "):
            i += 1
            continue
        old = lines[i][4:].split("\t")[0].strip()
        if i + 1 >= len(lines) or not lines[i + 1].startswith("+++ "):
            raise ValueError("malformed diff header")
        new = lines[i + 1][4:].split("\t")[0].strip()
        path = new if new not in ("/dev/null",) else old
        path = path[2:] if path.startswith("a/") or path.startswith("b/") else path
        i += 2
        # locate the first hunk header
        while i < len(lines) and not lines[i].startswith("@@"):
            i += 1
        if i >= len(lines):
            raise ValueError("no hunk header")
        target = _safe_join(root, path)
        if old == "/dev/null":
            body = [ln[1:] for ln in lines[i:] if ln.startswith("+")]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("\n".join(body) + ("\n" if body else ""), encoding="utf-8")
            touched.append(path)
            i = len(lines)
            continue
        original = target.read_text(encoding="utf-8").splitlines() if target.exists() else []
        out = list(original)
        while i < len(lines) and lines[i].startswith("@@"):
            i += 1
            removed = [ln[1:] for ln in _hunk(lines, i)]
            ctx_head = [ln[1:] for ln in lines[i:] if ln.startswith(" ")][:3]
            idx = _find(out, removed, ctx_head)
            if idx < 0:
                raise ValueError(f"hunk did not apply to {path}")
            add = [ln[1:] for ln in lines[i + len(removed) :] if ln.startswith("+")]
            out[idx : idx + len(removed)] = add
            i = _hunk_end(lines, i)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
        touched.append(path)
    return touched


def _hunk(lines: list[str], start: int) -> list[str]:
    """Yield the hunk body lines starting at *start* (context + removals)."""
    out = []
    for ln in lines[start:]:
        if ln.startswith(("+", "-", " ", "\\")) or ln == "":
            if ln.startswith("@@"):
                break
            out.append(ln)
        else:
            break
    return out


def _hunk_end(lines: list[str], start: int) -> int:
    j = start
    while j < len(lines) and (lines[j].startswith(("+", "-", " ", "\\")) or lines[j] == ""):
        j += 1
    return j


def _find(buf: list[str], removed: list[str], ctx: list[str]) -> int:
    if removed:
        for k in range(len(buf) - len(removed) + 1):
            if buf[k : k + len(removed)] == removed:
                return k
        return -1
    for c in ctx:
        if c in buf:
            return buf.index(c)
    return 0 if not buf else -1


def apply_solution(root: Path, spec: dict) -> tuple[list[str], str]:
    """Apply the canned solution inside *root*. Returns (touched, how)."""
    files = spec.get("solution_files") or {}
    if files:
        touched = []
        for rel, content in sorted(files.items()):
            target = _safe_join(root, str(rel))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(content), encoding="utf-8")
            touched.append(str(rel))
        return touched, "solution_files"
    for key in ("solution_patch", "solution_patch_file"):
        text = spec.get(key)
        if not text:
            continue
        if key == "solution_patch_file":
            p = Path(str(text))
            text = p.read_text(encoding="utf-8") if p.is_file() else ""
        if text:
            try:
                return apply_unified_diff(root, str(text)), "solution_patch"
            except Exception as exc:  # noqa: BLE001 - fall back, never crash the run
                root.joinpath("BENCHMARK_PATCH_ERROR.txt").write_text(
                    f"canned patch could not be applied: {exc}\n", encoding="utf-8"
                )
                return ["BENCHMARK_PATCH_ERROR.txt"], "solution_patch_failed"
    note = (
        "# Stub solution\n\n"
        f"No canned solution was supplied for task `{spec.get('id', '?')}`; "
        "the stub engine recorded a run and left this marker so the diff, "
        "verification and artifact pipeline are still exercised end-to-end.\n"
    )
    root.joinpath("BENCHMARK_SOLUTION.md").write_text(note, encoding="utf-8")
    return ["BENCHMARK_SOLUTION.md"], "fallback_note"


# --------------------------------------------------------------------------- #
# session simulation
# --------------------------------------------------------------------------- #


def run_session(backend: str, task_id: str, trial: int, sandbox: Path, spec: dict) -> dict:
    """Emit the deterministic session log and apply the canned solution."""
    log = Log()
    stub = spec.get("stub") or {}
    prompt = str(spec.get("prompt") or f"Resolve benchmark task {task_id}.")
    tokens = token_profile(backend, task_id, trial)
    n_cmd_errors = int(stub.get("command_errors", 0))
    n_tool_errors = int(stub.get("tool_errors", 0))
    n_retries = int(stub.get("retries", 0))

    log.emit(
        "session_start",
        backend=backend,
        task_id=task_id,
        trial=trial,
        engine="claudio-bench-stub/1",
        model=f"stub-{backend}",
    )
    log.emit("message", role="user", content=prompt)

    # turn 1: read the failing target
    log.emit("tool_call", id="tc_1", name="read_file", input={"path": spec.get("target", ".")})
    log.emit("tool_result", id="tc_1", is_error=False, output_bytes=_num(task_id, "rb") % 4096)

    for k in range(n_tool_errors):
        log.emit(
            "tool_call",
            id=f"tc_err_{k}",
            name="edit_file",
            input={"path": spec.get("target", "."), "attempt": k},
        )
        log.emit(
            "error",
            code="tool_error",
            subtype="tool_error",
            message=f"simulated tool error {k + 1}/{n_tool_errors} on {task_id}",
        )
        log.emit("tool_result", id=f"tc_err_{k}", is_error=True, output="tool call failed")
        log.emit("retry", attempt=k + 1, reason="tool_error")

    for k in range(n_cmd_errors):
        log.emit(
            "tool_call",
            id=f"tc_cmd_{k}",
            name="run_command",
            input={"command": spec.get("verify", "python3 -m unittest -q")},
        )
        log.emit(
            "error",
            code="command_error",
            subtype="command_error",
            message=f"simulated command failure {k + 1}/{n_cmd_errors} on {task_id}",
        )
        log.emit("tool_result", id=f"tc_cmd_{k}", is_error=True, output="exit status 1")
        log.emit("retry", attempt=k + 1, reason="command_error")

    touched, how = apply_solution(sandbox, spec)
    log.emit(
        "tool_call",
        id="tc_patch",
        name="apply_patch",
        input={"files": touched, "source": how},
    )
    log.emit("tool_result", id="tc_patch", is_error=False, output=f"patched {len(touched)} file(s)")
    log.emit("message", role="assistant", content=f"Applied canned solution ({how}): {', '.join(touched) or 'no files'}.")
    log.emit(
        "usage",
        input_tokens=tokens["input"],
        output_tokens=tokens["output"],
        cache_read_input_tokens=tokens["cache_read"],
        cache_creation_input_tokens=tokens["cache_write"],
        total_tokens=tokens["total"],
    )
    log.emit(
        "session_end",
        num_turns=len([e for e in log.events if e["type"] == "tool_call"]),
        exit="success",
        solution_source=how,
    )
    return {"tokens": tokens, "touched": touched, "solution_source": how, "events": log.events}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="deterministic offline stub engine")
    ap.add_argument("--backend", required=True)
    ap.add_argument("--task-id", required=True)
    ap.add_argument("--trial", type=int, default=1)
    ap.add_argument("--sandbox", required=True)
    ap.add_argument("--delay", type=float, default=0.0, help="artificial seconds to burn (timeout tests)")
    ap.add_argument("--spec-file", default=None, help="task spec JSON; defaults to stdin")
    args = ap.parse_args(argv)

    raw = Path(args.spec_file).read_text(encoding="utf-8") if args.spec_file else sys.stdin.read()
    try:
        spec = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        spec = {}
    if not isinstance(spec, dict):
        spec = {}

    if args.delay > 0:
        time.sleep(args.delay)

    sandbox = Path(args.sandbox)
    if not sandbox.is_dir():
        sys.stderr.write(f"stub_engine: sandbox does not exist: {sandbox}\n")
        return 2
    result = run_session(args.backend, args.task_id, args.trial, sandbox, spec)
    sys.stderr.write(
        "stub_engine: task=%s touched=%s tokens=%s\n"
        % (args.task_id, ",".join(result["touched"]) or "-", result["tokens"]["total"])
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
