#!/usr/bin/env python3
"""Deterministic, stdlib-only, offline stub engine for the Claudio benchmark.

The benchmark has to produce a full run artifact even on a machine with no
``claude`` / ``opencode`` binary and no credentials. This module is that
fallback: it replays a *canned* solution for each locked task, emits a session
log in the same JSONL shape the real engines emit (init / tool_call /
tool_result / usage / result events with input, output and cache token
counts), and returns the same contract the real engine adapters return so
``runner.py`` treats both paths identically.

Nothing here touches the network. Everything here is deterministic: token
counts come from a SHA-256 of ``(backend, task_id, trial)``, so two runs of
the same triple produce byte-identical artifacts.

Two data tables live at the bottom of this file (generated, see README):

``DEFAULT_TASKS``
    A copy of the locked manifest, used *only* when ``--tasks`` points at a
    file that does not exist, so the runner stays self-contained.
``SEEDS``
    A copy of ``benchmark/fixtures/<id>``, used *only* when that directory is
    missing, for the same reason.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path

BACKENDS = ("google_adk", "claude_code", "opencode")

# --------------------------------------------------------------------------
# canned solutions
# --------------------------------------------------------------------------
# Each entry describes the edits a competent engine run would make. Ops are
# applied in order:
#   replace: [(path, old, new)]   exact string replace (fails soft)
#   append:  [(path, text)]       add text to the end of an existing file
#   write:   {path: text}        create/overwrite a file
#   remove:  [path]              delete a file
#   commands: ["shell string"]   run in the sandbox (counts as a command)
SOLUTIONS = {}


def solution_for(task_id):
    """Return the canned solution ops for ``task_id`` (possibly ``{}``)."""
    return SOLUTIONS.get(task_id, {})


def seed_files(task_id):
    """Return ``{relpath: text}`` used when the real fixture dir is absent."""
    return SEEDS.get(task_id, {})


# --------------------------------------------------------------------------
# deterministic bookkeeping
# --------------------------------------------------------------------------

def _digest(*parts):
    return hashlib.sha256(
        "|".join(str(p) for p in parts).encode("utf-8")).hexdigest()


def token_profile(backend, task_id, trial, prompt):
    """Deterministic token counts for a stub run.

    Sizes are plausible for a small code-edit session and depend only on the
    (backend, task, trial) triple plus the prompt length, so they are stable
    across machines and across Python hash seeds.
    """
    h = _digest(backend, task_id, trial)
    base = 900 + int(h[0:4], 16) % 400 + max(1, len(prompt) // 4)
    out = 180 + int(h[4:8], 16) % 260
    cache_read = 512 + int(h[8:12], 16) % 1024
    cache_write = 64 + int(h[12:16], 16) % 128
    return {
        "input": base,
        "output": out,
        "cache_read": cache_read,
        "cache_write": cache_write,
        "total": base + out + cache_read + cache_write,
    }


def session_id(backend, task_id, trial):
    return "stub-" + _digest(backend, task_id, trial)[0:16]


# --------------------------------------------------------------------------
# applying a canned solution
# --------------------------------------------------------------------------

def _resolve(sandbox, rel):
    """Resolve ``rel`` inside ``sandbox``, refusing to escape it."""
    root = Path(sandbox).resolve()
    path = (root / rel).resolve()
    if path != root and root not in path.parents:
        raise ValueError("solution escaped the sandbox: %s" % rel)
    return path


def apply_solution(sandbox, task_id):
    """Apply the canned solution inside ``sandbox``.

    Returns ``(events, tool_errors)`` where ``events`` is a list of session-log
    event dicts describing what happened.
    """
    ops = solution_for(task_id)
    events, errors = [], 0
    seq = [0]

    def emit(event):
        seq[0] += 1
        event["seq"] = seq[0]
        events.append(event)
        return event

    for path, old, new in ops.get("replace", []):
        target = _resolve(sandbox, path)
        emit({"type": "tool_call", "name": "edit_file",
              "input": {"path": path}})
        try:
            text = target.read_text()
            if old not in text:
                raise ValueError("anchor not found")
            target.write_text(text.replace(old, new, 1))
            emit({"type": "tool_result", "name": "edit_file",
                  "is_error": False, "chars": len(new)})
        except (OSError, ValueError):
            errors += 1
            emit({"type": "tool_result", "name": "edit_file",
                  "is_error": True, "message": "anchor not found: " + path})

    for path, text in ops.get("append", []):
        target = _resolve(sandbox, path)
        emit({"type": "tool_call", "name": "append_file",
              "input": {"path": path}})
        try:
            with target.open("a") as handle:
                handle.write(text)
            emit({"type": "tool_result", "name": "append_file",
                  "is_error": False, "chars": len(text)})
        except OSError:
            errors += 1
            emit({"type": "tool_result", "name": "append_file",
                  "is_error": True, "chars": 0})

    for path, text in ops.get("write", {}).items():
        target = _resolve(sandbox, path)
        emit({"type": "tool_call", "name": "write_file",
              "input": {"path": path}})
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        emit({"type": "tool_result", "name": "write_file",
              "is_error": False, "chars": len(text)})

    for path in ops.get("remove", []):
        target = _resolve(sandbox, path)
        emit({"type": "tool_call", "name": "delete_file",
              "input": {"path": path}})
        try:
            target.unlink()
            emit({"type": "tool_result", "name": "delete_file",
                  "is_error": False, "chars": 0})
        except OSError:
            errors += 1
            emit({"type": "tool_result", "name": "delete_file",
                  "is_error": True, "chars": 0})

    for cmd in ops.get("commands", []):
        emit({"type": "tool_call", "name": "run_command",
              "input": {"command": cmd}})
        proc = subprocess.run(cmd, shell=True, cwd=str(sandbox),
                              capture_output=True, text=True)
        ok = proc.returncode == 0
        if not ok:
            errors += 1
        emit({"type": "tool_result", "name": "run_command",
              "is_error": not ok, "exit_code": proc.returncode,
              "chars": len(proc.stdout)})

    return events, errors


def _log_line(event):
    return json.dumps(event, sort_keys=True, separators=(",", ":"))


def run_stub(sandbox, backend, task, trial):
    """Run the canned engine in ``sandbox`` for ``task``.

    Returns a dict with ``stdout``, ``stderr``, ``exit_status``, ``timed_out``,
    ``tokens`` and ``errors`` -- the same contract the real adapters satisfy.
    """
    started = time.monotonic()
    task_id = task["id"]
    prompt = task.get("prompt", "")
    tokens = token_profile(backend, task_id, trial, prompt)
    sid = session_id(backend, task_id, trial)

    lines = [_log_line({"type": "init", "session_id": sid, "backend": backend,
                        "task_id": task_id, "trial": trial,
                        "engine_mode": "stub", "model": "claudio-stub-1"})]

    # A stub session first observes the failing state, then fixes it. The
    # observed failure is what makes command_errors non-zero.
    lines.append(_log_line({"type": "tool_call", "name": "run_command",
                            "input": {"command": task.get("verification", "")}}))
    lines.append(_log_line({"type": "tool_result", "name": "run_command",
                            "is_error": True, "exit_code": 1, "chars": 0}))
    lines.append(_log_line({"type": "retry", "attempt": 1,
                            "reason": "verification failed on the seed"}))

    events, tool_errors = apply_solution(sandbox, task_id)
    for event in events:
        lines.append(_log_line(event))

    duration_ms = int((time.monotonic() - started) * 1000)
    lines.append(_log_line({"type": "usage",
                            "input_tokens": tokens["input"],
                            "output_tokens": tokens["output"],
                            "cache_read_input_tokens": tokens["cache_read"],
                            "cache_creation_input_tokens": tokens["cache_write"]}))
    lines.append(_log_line({"type": "result", "subtype": "success",
                            "is_error": False, "duration_ms": duration_ms,
                            "num_turns": len(events) // 2 + 2,
                            "session_id": sid,
                            "result": "stub solution applied for " + task_id}))

    stdout_text = "\n".join(lines) + "\n"
    (sandbox / "session.jsonl").write_text(stdout_text)

    return {
        "stdout": stdout_text,
        "stderr": "",
        "exit_status": "ok",
        "timed_out": False,
        "tokens": tokens,
        "errors": {"command_errors": 1, "tool_errors": tool_errors,
                   "retries": 1, "timeouts": 0},
        "session_lines": len(lines),
    }

# --------------------------------------------------------------------------
# canned solutions (one per locked task)
# --------------------------------------------------------------------------

SOLUTIONS.update({
    "rate-limiter-offbyone": {
        "replace": [
            ("app/rate_limiter.py",
             "        # BUG (off-by-one): a bucket holding exactly ``cost`` "
             "tokens is rejected.\n        if self.tokens > cost:",
             "        if self.tokens >= cost:"),
            ("app/rate_limiter.py",
             "        # BUG: divides by the bucket size instead of the refill "
             "rate.\n        return missing / self.capacity",
             "        return missing / self.refill_per_sec"),
        ],
    },
    "csv-normalizer-dedup": {
        "replace": [
            ("app/csvnorm.py", "from decimal import Decimal",
             "from decimal import Decimal, ROUND_HALF_UP"),
            ("app/csvnorm.py",
             "        # BUG: the exporter pads files with empty rows; those "
             "are kept here.\n        rows.append(row)",
             "        if any(row.get(col) for col in REQUIRED_COLUMNS):\n"
             "            rows.append(row)"),
            ("app/csvnorm.py",
             '        # BUG: ids are compared verbatim, so "A-1" and "a-1 " '
             'count as distinct.\n        out[row["id"]] = row',
             '        key = (row.get("id") or "").strip().lower()\n'
             "        out[key] = row"),
            ("app/csvnorm.py",
             '    # BUG: quantize() defaults to ROUND_HALF_EVEN, so 2.665 '
             'becomes 2.66.\n    return Decimal(str(value)).quantize'
             '(Decimal("0.01"))',
             '    return Decimal(str(value)).quantize(Decimal("0.01"), '
             'rounding=ROUND_HALF_UP)'),
        ],
    },
    "retry-helper-skeleton": {
        "replace": [
            ("app/retry.py",
             '    TODO: implement according to SPEC.md.\n    """\n'
             '    raise NotImplementedError("retry_call is not implemented '
             'yet")',
             '    """\n'
             '    if attempts < 1:\n'
             '        raise ValueError("attempts must be positive")\n'
             "    last = None\n"
             "    for index in range(attempts):\n"
             "        try:\n"
             "            return func()\n"
             "        except exceptions as exc:\n"
             "            last = exc\n"
             "            if index + 1 < attempts:\n"
             "                sleep(delays[min(index, len(delays) - 1)])\n"
             '    raise RetryExhausted(\n'
             '        "retry_call failed after %d attempts" % attempts\n'
             "    ) from last"),
        ],
    },
    "markdown-toc-feature": {
        "write": {
            "app/toc.py": '''"""GitHub-style table of contents for markdown documents. See SPEC.md."""

import re

_HEADING = re.compile(r"^(#{1,6})\\s+(.*?)\\s*$")
_STRIP = re.compile(r"[^a-z0-9 -]")


def slugify(title):
    """Turn a heading title into a URL anchor."""
    text = title.strip().lower()
    text = _STRIP.sub("", text)
    return text.replace(" ", "-")


def build_toc(markdown_text):
    """Return ``[(level, title, anchor), ...]`` for every heading."""
    entries = []
    seen = {}
    in_fence = False
    for line in markdown_text.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _HEADING.match(line)
        if not match:
            continue
        level = len(match.group(1))
        title = match.group(2).strip()
        anchor = slugify(title)
        count = seen.get(anchor, 0)
        seen[anchor] = count + 1
        entries.append((level, title, anchor if not count else "%s-%d" % (anchor, count)))
    return entries
''',
        },
    },
})

SOLUTIONS.update({
    "split-legacy-report": {
        "write": {
            "app/report/__init__.py": '''"""Refactored sales report: ``model`` holds data + maths, ``render`` text."""

from app.report.model import Line, Report, line_net, summarize
from app.report.render import render_report

__all__ = ["Line", "Report", "line_net", "summarize", "render_report"]
''',
            "app/report/model.py": '''"""Data model and totals for a sales report."""

from dataclasses import dataclass, field


@dataclass
class Line:
    sku: str
    qty: int
    unit_price: float
    discount_pct: float = 0.0


@dataclass
class Report:
    title: str
    lines: list = field(default_factory=list)


def line_net(line):
    gross = line.qty * line.unit_price
    return gross - gross * line.discount_pct / 100.0


def summarize(report):
    total = 0.0
    for ln in report.lines:
        total += line_net(ln)
    count = len(report.lines)
    average = total / count if count else 0.0
    return {
        "title": report.title,
        "count": count,
        "total": round(total, 2),
        "average": round(average, 2),
    }
''',
            "app/report/render.py": '''"""Markdown rendering for a sales report."""

from app.report.model import line_net, summarize


def render_report(report):
    data = summarize(report)
    out = [
        "# " + data["title"],
        "",
        "- lines: %d" % data["count"],
        "- total: %.2f" % data["total"],
        "- average: %.2f" % data["average"],
        "",
    ]
    for ln in report.lines:
        out.append("| %s | %d | %.2f" % (ln.sku, ln.qty, line_net(ln)) + " |")
    return "\\n".join(out) + "\\n"
''',
            "app/legacy_report.py": '''"""Deprecated shim: the report module was split into ``app.report``.

Kept so old imports keep working; the implementation now lives in
``app/report/model.py`` and ``app/report/render.py``.
"""

from app.report import Line, Report, line_net, render_report, summarize

__all__ = ["Line", "Report", "line_net", "render_report", "summarize"]
''',
        },
    },
    "dedupe-discount-logic": {
        "replace": [
            ("app/pricing.py",
             'def line_total(qty, unit_price):\n'
             '    """Net price of ``qty`` units at ``unit_price`` after the '
             'tier discount."""\n'
             "    rate = _tier_rate(qty)\n"
             "    if rate > MAX_DISCOUNT_PCT:\n"
             "        rate = MAX_DISCOUNT_PCT\n"
             "    return round(qty * unit_price * (1.0 - rate), 2)",
             'def _apply_tier(qty, unit_price):\n'
             '    """Single source of truth for the tier discount on one line."""\n'
             "    rate = _tier_rate(qty)\n"
             "    if rate > MAX_DISCOUNT_PCT:\n"
             "        rate = MAX_DISCOUNT_PCT\n"
             "    return round(qty * unit_price * (1.0 - rate), 2)\n"
             "\n"
             "\n"
             "def line_total(qty, unit_price):\n"
             '    """Net price of ``qty`` units at ``unit_price`` after the '
             'tier discount."""\n'
             "    return _apply_tier(qty, unit_price)"),
            ("app/pricing.py",
             "    total = 0.0\n"
             "    for qty, unit_price in items:\n"
             "        # STALE COPY of the tier logic: the 15% cap is missing "
             "and the top\n"
             "        # tier wrongly discounts 25%.\n"
             "        if qty >= 100:\n"
             "            rate = 0.25\n"
             "        elif qty >= 50:\n"
             "            rate = 0.10\n"
             "        elif qty >= 10:\n"
             "            rate = 0.05\n"
             "        else:\n"
             "            rate = 0.0\n"
             "        total += qty * unit_price * (1.0 - rate)\n"
             "    return round(total, 2)",
             "    total = 0.0\n"
             "    for qty, unit_price in items:\n"
             "        total += _apply_tier(qty, unit_price)\n"
             "    return round(total, 2)"),
        ],
    },
    "inventory-qa": {
        "write": {
            "findings.txt": "dead_code: titleize\n"
                            "util_functions: 4\n"
                            "public_api: format_size,humanize_bytes,"
                            "make_slug,pipeline\n",
        },
    },
    "log-triage": {
        "write": {
            "findings.txt": "root_cause: db-pool-exhausted\n"
                            "error_count: 5\n"
                            "first_warning_component: db\n"
                            "last_error_line: 10\n",
        },
    },
})


# --------------------------------------------------------------------------
# fallback corpus (generated from benchmark/tasks.json + benchmark/fixtures)
# Used ONLY when those paths are absent, so the runner stays self-contained.
# --------------------------------------------------------------------------

DEFAULT_TASKS = [{'id': 'rate-limiter-offbyone', 'category': 'bug_fix', 'difficulty': 'easy', 'prompt': 'Fix the token-bucket rate limiter in app/rate_limiter.py. Two defects are reported in README.md: allow() rejects a request whose cost equals the tokens available (a capacity-3 bucket only serves 2 requests), and wait_seconds() divides by the bucket size instead of the refill rate. Repair app/rate_limiter.py only; tests/test_rate_limiter.py is the contract and must not be edited.', 'fixture_dir': 'benchmark/fixtures/rate-limiter-offbyone', 'setup': '', 'verification': 'python3 -m pytest -q -p no:cacheprovider tests/test_rate_limiter.py', 'seed_must_fail': True, 'max_seconds': 420, 'rubric': ['5: both defects fixed with minimal diff, allow() uses >= and wait_seconds() uses the refill rate, all tests pass, no test edits.', '4: all tests pass but the diff also touches the tests or adds unused helpers.', '3: the off-by-one is fixed but wait_seconds() is still wrong (or vice versa).', '2: partial change that leaves at least two tests failing.', '1: no useful change, or the public API was changed to dodge the tests.']}, {'id': 'csv-normalizer-dedup', 'category': 'bug_fix', 'difficulty': 'medium', 'prompt': "Fix app/csvnorm.py: (1) blank filler rows from the exporter must be dropped by parse_rows, (2) dedupe must treat order ids case- and whitespace-insensitively and keep the last row per id, (3) round_money must round half up (2.665 -> 2.67) instead of banker's rounding, and total_amount must use it per row. Only app/csvnorm.py may change; tests/test_csvnorm.py defines the required behaviour.", 'fixture_dir': 'benchmark/fixtures/csv-normalizer-dedup', 'setup': '', 'verification': 'python3 -m pytest -q -p no:cacheprovider tests/test_csvnorm.py', 'seed_must_fail': True, 'max_seconds': 600, 'rubric': ['5: all three defects fixed, Decimal rounding explicit (ROUND_HALF_UP), blank-row rule handles whitespace-only rows, tests pass untouched.', '4: all tests pass with a slightly brittle blank-row or key-normalisation rule.', '3: two of the three defects fixed.', '2: one defect fixed, or floats used where Decimal is required.', '1: tests still failing or the tests were edited to pass.']}, {'id': 'retry-helper-skeleton', 'category': 'feature', 'difficulty': 'medium', 'prompt': 'Implement retry_call in app/retry.py exactly as specified in SPEC.md: bounded attempts, injected sleep callable, delay list whose last entry is reused, immediate propagation of exceptions outside the exceptions tuple, RetryExhausted with the attempt count in its message and the last error as __cause__. Standard library only; do not edit tests/test_retry.py.', 'fixture_dir': 'benchmark/fixtures/retry-helper-skeleton', 'setup': '', 'verification': 'python3 -m pytest -q -p no:cacheprovider tests/test_retry.py', 'seed_must_fail': True, 'max_seconds': 600, 'rubric': ['5: SPEC implemented exactly (attempts validation, delay reuse, __cause__ chaining, no sleep on success), tests pass untouched.', '4: behaviour correct but edge cases such as attempts=1 or attempts<1 handled loosely.', '3: happy path works, RetryExhausted semantics or delay reuse wrong.', '2: only some tests pass and real sleeps are introduced.', '1: NotImplementedError left, or the tests were edited.']}, {'id': 'markdown-toc-feature', 'category': 'feature', 'difficulty': 'medium', 'prompt': 'Implement slugify and build_toc in app/toc.py following SPEC.md: GitHub-style anchors (lower-case, strip punctuation other than space/hyphen, spaces to hyphens), ATX headings with 1-6 hashes and at most three leading spaces, headings inside ``` or ~~~ fenced blocks ignored, duplicate anchors suffixed -1, -2. Standard library only; tests/test_toc.py is the contract.', 'fixture_dir': 'benchmark/fixtures/markdown-toc-feature', 'setup': '', 'verification': 'python3 -m pytest -q -p no:cacheprovider tests/test_toc.py', 'seed_must_fail': True, 'max_seconds': 600, 'rubric': ['5: both functions implemented per spec, fence tracking correct, dedupe suffixes correct, tests pass untouched.', '4: correct output but the fence rule or the anchor dedupe is implemented fragilely.', '3: anchors right but headings/levels mis-parsed (or vice versa).', '2: only the slugify tests pass.', '1: stub left in place or the tests were edited.']}, {'id': 'split-legacy-report', 'category': 'refactor_debug', 'difficulty': 'hard', 'prompt': 'Refactor app/legacy_report.py into the package layout demanded by README.md: app/report/model.py (Line, Report, line_net, summarize), app/report/render.py (render_report importing from .model) and app/report/__init__.py re-exporting all five names. While splitting, fix the ZeroDivisionError that summarize raises on a report with no lines (average must be 0.0) and delete the moved code from app/legacy_report.py so it no longer defines render_report. Behaviour of the public API is unchanged; tests/test_report.py must not be edited.', 'fixture_dir': 'benchmark/fixtures/split-legacy-report', 'setup': '', 'verification': 'python3 -m pytest -q -p no:cacheprovider tests/test_report.py && test -f app/report/model.py && test -f app/report/render.py && grep -Eq "from (\\.model|app\\.report\\.model) import" app/report/render.py && ! grep -q "def render_report" app/legacy_report.py', 'seed_must_fail': True, 'max_seconds': 900, 'rubric': ['5: clean model/render split, no import cycle, empty-report bug fixed, monolith no longer defines render_report, tests pass.', '4: split and bug fix correct but the legacy file still carries dead duplicated logic.', '3: package created but rendering still duplicated in two places, or one test fails.', '2: files created but the public API is incomplete (import errors).', '1: no split performed; only the ZeroDivisionError patched, or tests edited.']}, {'id': 'dedupe-discount-logic', 'category': 'refactor_debug', 'difficulty': 'medium', 'prompt': 'app/pricing.py has a stale copy-paste of the tier logic: bulk_quote inlines its own ladder, lost the MAX_DISCOUNT_PCT cap and gives the top tier 25% instead of 15%. Extract one shared helper named _apply_tier(qty, unit_price) that both line_total and bulk_quote call, delete the inlined ladder from bulk_quote so no tier table is duplicated, and keep the rounding behaviour. tests/test_pricing.py defines the required behaviour and must not be edited.', 'fixture_dir': 'benchmark/fixtures/dedupe-discount-logic', 'setup': '', 'verification': 'python3 -m pytest -q -p no:cacheprovider tests/test_pricing.py && grep -q "def _apply_tier" app/pricing.py && test "$(grep -c \'_apply_tier\' app/pricing.py)" -ge 3 && ! grep -q "elif qty >= 50" app/pricing.py', 'seed_must_fail': True, 'max_seconds': 600, 'rubric': ['5: single _apply_tier helper is the only tier path, cap respected, ladder deleted, tests pass untouched.', '4: helper extracted and tests pass but a redundant tier check remains in a public function.', '3: helper added yet bulk_quote still disagrees with line_total in some case.', '2: duplication removed but the discount bug remains (or the reverse).', '1: no refactor, or the tests were edited to pass.']}, {'id': 'inventory-qa', 'category': 'codebase_qa', 'difficulty': 'easy', 'prompt': 'Read-only audit. Read QUESTION.md, pkg/__init__.py, pkg/core.py and pkg/util.py, then write findings.txt in the sandbox root with exactly the three lines QUESTION.md specifies: dead_code (the pkg/util.py function no other file references), util_functions (count of top-level functions in pkg/util.py) and public_api (the __all__ names of pkg/__init__.py, sorted, comma-separated, no spaces). Do not modify anything under pkg/.', 'fixture_dir': 'benchmark/fixtures/inventory-qa', 'setup': '', 'verification': 'test -f findings.txt && grep -qx "dead_code: titleize" findings.txt && grep -qx "util_functions: 4" findings.txt && grep -qx "public_api: format_size,humanize_bytes,make_slug,pipeline" findings.txt', 'seed_must_fail': True, 'max_seconds': 300, 'rubric': ['5: findings.txt has exactly the three required lines, all values correct and in the requested format.', '4: values correct but one formatting slip (extra blank line, spaces after commas).', '3: two of the three answers correct.', '2: only the trivially visible answer (public_api) correct.', '1: findings.txt missing, empty, or the package was edited instead of audited.']}, {'id': 'log-triage', 'category': 'codebase_qa', 'difficulty': 'medium', 'prompt': 'Read-only root-cause analysis. Read QUESTION.md, logs/service.txt, notes/context.md and app/worker.py, then write findings.txt in the sandbox root with exactly the four lines QUESTION.md specifies: root_cause (one of db-pool-exhausted, api-timeout, disk-full), error_count (lines whose level is ERROR), first_warning_component (component of the earliest WARN or ERROR line) and last_error_line (1-based line number of the last ERROR line). Do not modify the log, the notes or the code.', 'fixture_dir': 'benchmark/fixtures/log-triage', 'setup': 'python3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)"', 'verification': 'test -f findings.txt && grep -qx "root_cause: db-pool-exhausted" findings.txt && grep -qx "error_count: 5" findings.txt && grep -qx "first_warning_component: db" findings.txt && grep -qx "last_error_line: 10" findings.txt', 'seed_must_fail': True, 'max_seconds': 420, 'rubric': ['5: findings.txt has the four lines exactly, root cause correctly attributed to the drained connection pool rather than the api symptoms.', '4: root cause right, one counted field off by one.', '3: two fields correct, root cause guessed as api-timeout.', '2: only the mechanical counts correct.', '1: findings.txt missing or the answers were guessed without reading the log.']}]

SEEDS = {'rate-limiter-offbyone': {'README.md': '# Bug fix: rate limiter off-by-one\n\n`app/rate_limiter.py` implements the token bucket used by the notification\ndispatcher. Two defects were reported from production:\n\n1. A brand-new bucket with `capacity=3` only lets through **two** requests\n   instead of three, and `allow()` rejects a request whose cost equals the\n   tokens that are available.\n2. `wait_seconds()` returns nonsense when the refill rate differs from the\n   bucket size (e.g. `capacity=4, refill_per_sec=2` reports 0.25 s for one\n   missing token instead of 0.5 s).\n\n## Definition of done\n\n- `python3 -m pytest -q tests/test_rate_limiter.py` passes.\n- Only `app/rate_limiter.py` changes; the tests describe the intended\n  behaviour and must not be edited.\n', 'app/rate_limiter.py': '"""Token-bucket rate limiter used by the notification dispatcher.\n\nTwo bugs are reported against this module (see README.md). Fix them without\nchanging the public API or the tests.\n"""\n\nimport time\n\n\nclass RateLimiter:\n    """Allow up to ``capacity`` requests, refilling continuously."""\n\n    def __init__(self, capacity, refill_per_sec, clock=time.monotonic):\n        if capacity <= 0:\n            raise ValueError("capacity must be positive")\n        if refill_per_sec <= 0:\n            raise ValueError("refill_per_sec must be positive")\n        self.capacity = float(capacity)\n        self.refill_per_sec = float(refill_per_sec)\n        self.clock = clock\n        self.tokens = float(capacity)\n        self._last = clock()\n\n    def _refill(self):\n        now = self.clock()\n        elapsed = now - self._last\n        if elapsed < 0:\n            raise ValueError("clock went backwards")\n        self._last = now\n        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_sec)\n\n    def allow(self, cost=1):\n        """Spend ``cost`` tokens if the bucket holds them."""\n        self._refill()\n        if cost > self.capacity:\n            return False\n        # BUG (off-by-one): a bucket holding exactly ``cost`` tokens is rejected.\n        if self.tokens > cost:\n            self.tokens -= cost\n            return True\n        return False\n\n    def wait_seconds(self, cost=1):\n        """Seconds to wait until ``cost`` tokens are available."""\n        self._refill()\n        missing = cost - self.tokens\n        if missing <= 0:\n            return 0.0\n        # BUG: divides by the bucket size instead of the refill rate.\n        return missing / self.capacity\n', 'tests/test_rate_limiter.py': 'import pytest\n\nfrom app.rate_limiter import RateLimiter\n\n\nclass FakeClock:\n    def __init__(self, t=0.0):\n        self.t = t\n\n    def __call__(self):\n        return self.t\n\n    def advance(self, dt):\n        self.t += dt\n\n\ndef test_burst_allows_exactly_capacity():\n    rl = RateLimiter(capacity=3, refill_per_sec=1, clock=FakeClock())\n    assert [rl.allow() for _ in range(3)] == [True, True, True]\n    assert rl.allow() is False\n\n\ndef test_wait_seconds_is_zero_when_tokens_available():\n    rl = RateLimiter(capacity=2, refill_per_sec=4, clock=FakeClock())\n    assert rl.wait_seconds(2) == 0.0\n\n\ndef test_wait_seconds_uses_refill_rate():\n    rl = RateLimiter(capacity=4, refill_per_sec=2, clock=FakeClock())\n    assert rl.allow(4) is True\n    assert rl.wait_seconds(1) == pytest.approx(0.5)\n    assert rl.wait_seconds(3) == pytest.approx(1.5)\n\n\ndef test_refill_accumulates_over_time():\n    clock = FakeClock()\n    rl = RateLimiter(capacity=2, refill_per_sec=1, clock=clock)\n    assert rl.allow(2) is True\n    assert rl.allow(1) is False\n    clock.advance(1.0)\n    assert rl.allow(1) is True\n    clock.advance(10.0)\n    assert rl.allow(2) is True\n    assert rl.allow(1) is False\n'}, 'csv-normalizer-dedup': {'README.md': "# Bug fix: CSV order normaliser\n\n`app/csvnorm.py` cleans the nightly exporter file before it is loaded into the\nledger. Three defects were reported:\n\n1. Padded/blank rows from the exporter end up in the output.\n2. Order ids are matched verbatim, so `A-1`, `a-1` and `a-1 ` are treated as\n   three different orders instead of one.\n3. Money is rounded with banker's rounding, so `2.665` lands as `2.66` where\n   finance expects `2.67` (round half up).\n\n## Definition of done\n\n- `python3 -m pytest -q tests/test_csvnorm.py` passes.\n- Only `app/csvnorm.py` changes; the tests state the required behaviour.\n", 'app/csvnorm.py': '"""Normalise exported CSV order rows: drop blanks, dedupe by id, total amounts.\n\nThe finance exporter emits ids with inconsistent case/padding and amounts with\nthree decimals. Reported defects are listed in README.md.\n"""\n\nimport csv\nimport io\nfrom decimal import Decimal\n\nREQUIRED_COLUMNS = ("id", "customer", "amount")\n\n\ndef parse_rows(text):\n    """Return a list of dicts with every value stripped of surrounding space."""\n    reader = csv.DictReader(io.StringIO(text))\n    missing = set(REQUIRED_COLUMNS) - set(reader.fieldnames or [])\n    if missing:\n        raise ValueError("missing columns: " + ", ".join(sorted(missing)))\n    rows = []\n    for raw in reader:\n        row = {k: (v.strip() if isinstance(v, str) else v) for k, v in raw.items()}\n        # BUG: the exporter pads files with empty rows; those are kept here.\n        rows.append(row)\n    return rows\n\n\ndef dedupe(rows):\n    """Keep the last row seen for each order id."""\n    out = {}\n    for row in rows:\n        # BUG: ids are compared verbatim, so "A-1" and "a-1 " count as distinct.\n        out[row["id"]] = row\n    return list(out.values())\n\n\ndef round_money(value):\n    """Round an amount to cents the way the finance team rounds (half up)."""\n    # BUG: quantize() defaults to ROUND_HALF_EVEN, so 2.665 becomes 2.66.\n    return Decimal(str(value)).quantize(Decimal("0.01"))\n\n\ndef total_amount(rows):\n    total = Decimal("0.00")\n    for row in rows:\n        total += round_money(row["amount"])\n    return total.quantize(Decimal("0.01"))\n', 'tests/test_csvnorm.py': 'from decimal import Decimal\n\nfrom app.csvnorm import dedupe, parse_rows, round_money, total_amount\n\nCSV = """id,customer,amount\nA-1,Acme,10.005\na-1 ,Acme,11.00\nB-2,Bo,5.00\n"""\n\n\ndef test_round_money_uses_half_up():\n    assert round_money("2.665") == Decimal("2.67")\n    assert round_money("0.005") == Decimal("0.01")\n    assert round_money("2.674") == Decimal("2.67")\n\n\ndef test_blank_rows_are_dropped():\n    text = "id,customer,amount\\nA-1,Acme,1.00\\n,,\\n"\n    assert [r["id"] for r in parse_rows(text)] == ["A-1"]\n\n\ndef test_dedupe_is_case_and_space_insensitive():\n    rows = parse_rows(CSV)\n    kept = dedupe(rows)\n    assert len(kept) == 2\n    assert sorted(r["amount"] for r in kept) == ["11.00", "5.00"]\n\n\ndef test_total_rounds_each_row_half_up():\n    rows = [{"id": "x", "customer": "c", "amount": "2.665"},\n            {"id": "y", "customer": "c", "amount": "0.005"}]\n    assert total_amount(rows) == Decimal("2.68")\n\n\ndef test_missing_column_raises():\n    import pytest\n\n    with pytest.raises(ValueError):\n        parse_rows("id,customer\\nA-1,Acme\\n")\n'}, 'retry-helper-skeleton': {'SPEC.md': '# Feature spec: `retry_call`\n\nImplement `retry_call` in `app/retry.py`. Required behaviour (the tests in\n`tests/test_retry.py` are the contract):\n\n```python\nretry_call(func, attempts=3, delays=(0.1, 0.3), sleep=time.sleep,\n           exceptions=(Exception,))\n```\n\n1. Call `func()` with no arguments. Return its result as soon as it succeeds.\n2. `attempts` is the **total** number of calls, so `attempts=1` means "no\n   retry". `attempts < 1` raises `ValueError` before `func` is called.\n3. When `func` raises an exception whose type is in `exceptions`, sleep and try\n   again. `sleep(seconds)` is called with the delay for the gap that was just\n   waited: the first retry waits `delays[0]`, the second `delays[1]`, and if\n   there are more retries than delays, the **last** delay is reused.\n4. `sleep` must never be called when the call succeeds or when the exception is\n   not in `exceptions`.\n5. An exception whose type is not in `exceptions` propagates immediately,\n   without any further attempt.\n6. If every attempt fails, raise `RetryExhausted` whose message contains the\n   number of attempts and whose `__cause__` is the last exception raised.\n\nDo not import anything beyond the standard library, and do not sleep for real in\nthe tests: they inject their own `sleep`.\n', 'app/__init__.py': '"""Retry helper package."""\n', 'app/retry.py': '"""Retry helper. See SPEC.md for the required behaviour."""\n\nimport time\n\n\nclass RetryExhausted(Exception):\n    """Raised when every attempt of a call failed."""\n\n\ndef retry_call(func, attempts=3, delays=(0.1, 0.3), sleep=time.sleep,\n               exceptions=(Exception,)):\n    """Call ``func`` until it succeeds or ``attempts`` are used up.\n\n    TODO: implement according to SPEC.md.\n    """\n    raise NotImplementedError("retry_call is not implemented yet")\n', 'tests/test_retry.py': 'import pytest\n\nfrom app.retry import RetryExhausted, retry_call\n\n\ndef test_returns_first_success_without_extra_sleep():\n    calls = []\n    sleeps = []\n\n    def flaky():\n        calls.append(1)\n        if len(calls) < 3:\n            raise ValueError("boom")\n        return "ok"\n\n    result = retry_call(flaky, attempts=5, delays=(0.5, 1.5), sleep=sleeps.append)\n    assert result == "ok"\n    assert len(calls) == 3\n    assert sleeps == [0.5, 1.5]\n\n\ndef test_last_delay_is_reused_and_cause_is_kept():\n    sleeps = []\n\n    def always_fail():\n        raise KeyError("nope")\n\n    with pytest.raises(RetryExhausted) as exc:\n        retry_call(always_fail, attempts=4, delays=(0.2,), sleep=sleeps.append,\n                   exceptions=(KeyError,))\n    assert sleeps == [0.2, 0.2, 0.2]\n    assert "4" in str(exc.value)\n    assert isinstance(exc.value.__cause__, KeyError)\n\n\ndef test_unlisted_exception_propagates_immediately():\n    calls = []\n\n    def boom():\n        calls.append(1)\n        raise TypeError("bad")\n\n    with pytest.raises(TypeError):\n        retry_call(boom, attempts=3, delays=(0.1,), sleep=lambda s: None,\n                   exceptions=(ValueError,))\n    assert calls == [1]\n\n\ndef test_single_attempt_never_sleeps():\n    sleeps = []\n\n    def boom():\n        raise ValueError("once")\n\n    with pytest.raises(RetryExhausted):\n        retry_call(boom, attempts=1, delays=(0.1,), sleep=sleeps.append)\n    assert sleeps == []\n\n\ndef test_attempts_must_be_positive():\n    with pytest.raises(ValueError):\n        retry_call(lambda: 1, attempts=0)\n'}, 'markdown-toc-feature': {'SPEC.md': '# Feature spec: markdown table of contents\n\nImplement `slugify` and `build_toc` in `app/toc.py`. The tests in\n`tests/test_toc.py` are the contract.\n\n## `slugify(title) -> str`\n\nApplied in this exact order:\n\n1. Lower-case the string.\n2. Strip leading and trailing whitespace.\n3. Delete every character that is **not** a letter, a digit, a space or a hyphen.\n4. Replace each remaining space with a single hyphen (consecutive spaces become\n   consecutive hyphens).\n\nExamples: `Hello, World!` -> `hello-world`, `Ship It: v2.0` -> `ship-it-v20`,\n`snake_case and CAPS` -> `snakecase-and-caps`, `  Trim   Me  ` -> `trim---me`.\n\n## `build_toc(markdown_text) -> list[tuple[int, str, str]]`\n\nReturn one `(level, title, anchor)` tuple per ATX heading, in document order:\n\n- A heading is a line with at most three leading spaces, then 1-6 `#`, then at\n  least one space, then the title. The title has surrounding whitespace\n  stripped; the level is the number of `#`.\n- Heading-looking lines **inside a fenced code block are not headings**. A fence\n  opens and closes on any line whose stripped content starts with ``` or `~~~`.\n- The anchor is `slugify(title)`. If the same anchor already appeared earlier,\n  append `-1`, then `-2`, and so on, once per repetition.\n\nStandard library only.\n', 'app/__init__.py': '"""Markdown helpers."""\n', 'app/toc.py': '"""GitHub-style table of contents for markdown documents. See SPEC.md."""\n\n\ndef slugify(title):\n    """Turn a heading title into a URL anchor."""\n    raise NotImplementedError("slugify is not implemented yet")\n\n\ndef build_toc(markdown_text):\n    """Return ``[(level, title, anchor), ...]`` for every heading."""\n    raise NotImplementedError("build_toc is not implemented yet")\n', 'tests/test_toc.py': 'from app.toc import build_toc, slugify\n\nDOC = """# Title\nintro text\n\n## Hello, World!\n\n```text\n## Inside a fence\n```\n\n### Sub *emph*\n### Sub *emph*\n####   Padded heading\n"""\n\n\ndef test_slugify_examples():\n    assert slugify("Hello, World!") == "hello-world"\n    assert slugify("Ship It: v2.0") == "ship-it-v20"\n    assert slugify("snake_case and CAPS") == "snakecase-and-caps"\n    assert slugify("  Trim   Me  ") == "trim---me"\n\n\ndef test_headings_in_order_with_levels():\n    entries = build_toc(DOC)\n    assert [(lvl, title) for lvl, title, _ in entries] == [\n        (1, "Title"),\n        (2, "Hello, World!"),\n        (3, "Sub *emph*"),\n        (3, "Sub *emph*"),\n        (4, "Padded heading"),\n    ]\n\n\ndef test_code_fences_are_skipped():\n    titles = [title for _, title, _ in build_toc(DOC)]\n    assert "Inside a fence" not in titles\n\n\ndef test_duplicate_anchors_get_numeric_suffixes():\n    assert [anchor for _, _, anchor in build_toc(DOC)] == [\n        "title",\n        "hello-world",\n        "sub-emph",\n        "sub-emph-1",\n        "padded-heading",\n    ]\n\n\ndef test_empty_document_has_no_entries():\n    assert build_toc("just a paragraph\\n") == []\n'}, 'split-legacy-report': {'README.md': '# Refactor + bug fix: split the legacy report module\n\n`app/legacy_report.py` holds the data model, the totals and the markdown\nrenderer in one file, and `summarize()` crashes with `ZeroDivisionError` on a\nreport that has no lines.\n\n## Required end state\n\nCreate the package `app/report/` with exactly this layout:\n\n- `app/report/model.py` — `Line`, `Report`, `line_net`, `summarize`\n  (no rendering code, no imports of the render module).\n- `app/report/render.py` — `render_report`, importing what it needs from\n  `.model`.\n- `app/report/__init__.py` — re-export `Line`, `Report`, `line_net`,\n  `summarize`, `render_report` from those two modules.\n\nThen remove the moved logic from `app/legacy_report.py`: it must no longer\ndefine `render_report` (delete the file or leave a short note in it — the code\nmust go).\n\n## Definition of done\n\n- `summarize(Report("Empty"))` returns `average == 0.0` instead of raising.\n- `python3 -m pytest -q tests/test_report.py` passes.\n- Do not edit the tests; they define the public API.\n', 'app/__init__.py': '"""Report package (to be created by the refactor)."""\n', 'app/legacy_report.py': '"""Legacy monolithic sales report module.\n\nEverything (model, totals, rendering) lives in this one file and it has grown\nunwieldy. It also crashes on an empty report. See README.md for the refactor\nthat is required.\n"""\n\nfrom dataclasses import dataclass, field\n\n\n@dataclass\nclass Line:\n    sku: str\n    qty: int\n    unit_price: float\n    discount_pct: float = 0.0\n\n\n@dataclass\nclass Report:\n    title: str\n    lines: list = field(default_factory=list)\n\n\ndef line_net(line):\n    gross = line.qty * line.unit_price\n    return gross - gross * line.discount_pct / 100.0\n\n\ndef summarize(report):\n    total = 0.0\n    for ln in report.lines:\n        total += line_net(ln)\n    # BUG: divides by len(lines) unconditionally -> ZeroDivisionError on empty.\n    average = total / len(report.lines)\n    return {\n        "title": report.title,\n        "count": len(report.lines),\n        "total": round(total, 2),\n        "average": round(average, 2),\n    }\n\n\ndef render_report(report):\n    data = summarize(report)\n    out = [\n        "# " + data["title"],\n        "",\n        "- lines: %d" % data["count"],\n        "- total: %.2f" % data["total"],\n        "- average: %.2f" % data["average"],\n        "",\n    ]\n    for ln in report.lines:\n        out.append("| %s | %d | %.2f |" % (ln.sku, ln.qty, line_net(ln)))\n    return "\\n".join(out) + "\\n"\n', 'tests/test_report.py': 'import pytest\n\nfrom app.report import Line, Report, line_net, render_report, summarize\n\n\ndef test_summarize_totals():\n    report = Report("Q3", [Line("A", 2, 10.0), Line("B", 1, 4.0, discount_pct=25)])\n    assert summarize(report) == {"title": "Q3", "count": 2, "total": 23.0, "average": 11.5}\n\n\ndef test_summarize_empty_report_is_zero_average():\n    assert summarize(Report("Empty")) == {\n        "title": "Empty",\n        "count": 0,\n        "total": 0.0,\n        "average": 0.0,\n    }\n\n\ndef test_line_net_applies_discount():\n    assert line_net(Line("A", 3, 5.0, discount_pct=10)) == pytest.approx(13.5)\n\n\ndef test_render_report_markdown():\n    text = render_report(Report("Q3", [Line("A", 2, 10.0)]))\n    assert text.splitlines()[:2] == ["# Q3", ""]\n    assert "- total: 20.00" in text\n    assert "| A | 2 | 20.00 |" in text\n    assert text.endswith("\\n")\n\n\ndef test_render_report_empty():\n    assert "lines: 0" in render_report(Report("Empty"))\n\n\ndef test_model_and_render_are_split():\n    from app.report import model, render\n\n    assert hasattr(model, "summarize") and hasattr(model, "line_net")\n    assert hasattr(model, "Line") and hasattr(model, "Report")\n    assert hasattr(render, "render_report")\n'}, 'dedupe-discount-logic': {'README.md': '# Refactor + bug fix: one tier table\n\n`app/pricing.py` computes order lines. `bulk_quote` is a stale copy-paste of the\ntier logic inside `line_total`: it lost the `MAX_DISCOUNT_PCT` cap and gives the\ntop tier 25% instead of 15%, so the two functions disagree.\n\n## Required end state\n\n- Both public functions must compute a line through **one** shared helper named\n  `_apply_tier(qty, unit_price)` in `app/pricing.py`.\n- The inlined `if qty >= 100: ... elif qty >= 50: ...` ladder must be deleted\n  from `bulk_quote` (no duplicated tier table anywhere).\n- The cap and the `TIERS` table stay the single source of truth.\n\n## Definition of done\n\n- `python3 -m pytest -q tests/test_pricing.py` passes.\n- `grep -c "_apply_tier" app/pricing.py` reports at least 3 lines (the\n  definition plus both call sites).\n- Do not edit the tests; they define the required behaviour.\n', 'app/__init__.py': '"""Pricing package."""\n', 'app/pricing.py': '"""Pricing helpers.\n\n``line_total`` and ``bulk_quote`` grew from the same copy-paste block: the tier\ntable is duplicated and the copy inside ``bulk_quote`` is stale (it lost the\ndiscount cap). See README.md for the required refactor.\n"""\n\nTIERS = ((0, 0.0), (10, 0.05), (50, 0.10), (100, 0.15))\nMAX_DISCOUNT_PCT = 0.15\n\n\ndef _tier_rate(qty):\n    rate = 0.0\n    for threshold, pct in TIERS:\n        if qty >= threshold:\n            rate = pct\n    return rate\n\n\ndef line_total(qty, unit_price):\n    """Net price of ``qty`` units at ``unit_price`` after the tier discount."""\n    rate = _tier_rate(qty)\n    if rate > MAX_DISCOUNT_PCT:\n        rate = MAX_DISCOUNT_PCT\n    return round(qty * unit_price * (1.0 - rate), 2)\n\n\ndef bulk_quote(items):\n    """Total for an order of ``(qty, unit_price)`` pairs."""\n    total = 0.0\n    for qty, unit_price in items:\n        # STALE COPY of the tier logic: the 15% cap is missing and the top\n        # tier wrongly discounts 25%.\n        if qty >= 100:\n            rate = 0.25\n        elif qty >= 50:\n            rate = 0.10\n        elif qty >= 10:\n            rate = 0.05\n        else:\n            rate = 0.0\n        total += qty * unit_price * (1.0 - rate)\n    return round(total, 2)\n', 'tests/test_pricing.py': 'from app.pricing import bulk_quote, line_total\n\n\ndef test_line_total_applies_tier():\n    assert line_total(5, 10.0) == 50.0\n    assert line_total(50, 10.0) == 450.0\n    assert line_total(100, 10.0) == 850.0\n\n\ndef test_bulk_quote_matches_line_total_per_item():\n    items = [(5, 4.0), (12, 3.0), (60, 2.0), (120, 1.0)]\n    assert bulk_quote(items) == round(sum(line_total(q, p) for q, p in items), 2)\n\n\ndef test_bulk_quote_respects_discount_cap():\n    assert bulk_quote([(120, 10.0)]) == line_total(120, 10.0) == 1020.0\n\n\ndef test_bulk_quote_empty_order_is_zero():\n    assert bulk_quote([]) == 0.0\n'}, 'inventory-qa': {'QUESTION.md': '# Read-only audit: package inventory\n\nRead `pkg/__init__.py`, `pkg/core.py` and `pkg/util.py`. **Do not modify any\nfile in `pkg/`.** Answer three questions by writing a file named\n`findings.txt` in the sandbox root.\n\n`findings.txt` must contain exactly these three lines, in this order, with no\nextra text:\n\n```\ndead_code: <name of the function defined in pkg/util.py that no other file in\n            this fixture ever references>\nutil_functions: <number of top-level functions defined in pkg/util.py>\npublic_api: <the names exported by pkg/__init__.py __all__, sorted\n             alphabetically, comma-separated, no spaces>\n```\n\nThe `dead_code:` line must be a single token on one line, i.e.\n`dead_code: some_name`. Write the file with plain text (no markdown fences).\n', 'pkg/__init__.py': '"""Public surface of the example package."""\n\nfrom .core import format_size, make_slug, pipeline\nfrom .util import humanize_bytes\n\n__all__ = ["format_size", "humanize_bytes", "make_slug", "pipeline"]\n', 'pkg/core.py': '"""Core helpers of the ``pkg`` example package."""\n\nfrom .util import clamp, humanize_bytes, slugify\n\n\ndef make_slug(title, max_len=40):\n    return slugify(title)[: clamp(max_len, 1, 100)]\n\n\ndef format_size(num):\n    return humanize_bytes(num)\n\n\ndef pipeline(items):\n    return [make_slug(item) for item in items]\n', 'pkg/util.py': '"""Small text helpers used by pkg.core."""\n\n\ndef slugify(text):\n    keep = [c for c in text.lower() if c.isalnum() or c in " -"]\n    return "".join(keep).replace(" ", "-")\n\n\ndef titleize(text):\n    return " ".join(word.capitalize() for word in text.split())\n\n\ndef clamp(value, low, high):\n    return max(low, min(value, high))\n\n\ndef humanize_bytes(num):\n    for unit in ("B", "KB", "MB"):\n        if num < 1024:\n            return "%.1f%s" % (num, unit)\n        num /= 1024.0\n    return "%.1fGB" % num\n'}, 'log-triage': {'QUESTION.md': '# Read-only root-cause read: the 09:20 incident\n\nRead `logs/service.txt` (10 lines), `notes/context.md` and `app/worker.py`.\n**Do not modify those files.** Write your answer to a file named `findings.txt`\nin the sandbox root.\n\n`findings.txt` must contain exactly these four lines, in this order, with no\nextra text and no markdown fences:\n\n```\nroot_cause: <one of db-pool-exhausted | api-timeout | disk-full>\nerror_count: <number of lines whose level is exactly ERROR>\nfirst_warning_component: <the component field of the earliest WARN or ERROR line>\nlast_error_line: <1-based line number of the last ERROR line>\n```\n\nThe component is the token that follows the level on each log line\n(`worker`, `db`, `api`).\n', 'app/worker.py': '"""Worker that produced logs/service.txt (context only - do not change it)."""\n\nimport logging\n\nLOG = logging.getLogger("worker")\nPOOL_SIZE = 4\n\n\ndef handle(task_id, store):\n    """Fetch a task payload; every failure in the incident window was a pool wait."""\n    try:\n        conn = store.acquire(timeout=5)\n    except TimeoutError as exc:  # pragma: no cover - incident path\n        LOG.error(\'task_failed task=%s err="%s"\', task_id, exc)\n        raise\n    try:\n        return store.run(conn, task_id)\n    finally:\n        store.release(conn)\n', 'logs/service.txt': '2026-05-04T09:00:01Z INFO  worker pool_size=4\n2026-05-04T09:00:02Z INFO  db connected host=pg-primary\n2026-05-04T09:12:44Z WARN  db slow_query=1.9s stmt=orders_rollup\n2026-05-04T09:20:10Z ERROR api timeout upstream=worker req=8841\n2026-05-04T09:20:11Z ERROR worker task_failed task=8841 err="pool timeout after 5s"\n2026-05-04T09:21:02Z ERROR worker task_failed task=8842 err="pool timeout after 5s"\n2026-05-04T09:21:30Z WARN  api retry req=8842 attempt=1\n2026-05-04T09:22:05Z ERROR worker task_failed task=8843 err="pool timeout after 5s"\n2026-05-04T09:23:00Z INFO  worker recovered pool_size=4\n2026-05-04T09:24:12Z ERROR api timeout upstream=worker req=8850\n', 'notes/context.md': '# Incident context (2026-05-04 morning)\n\n- The API gateway sits in front of the worker; the gateway times out after 2 s\n  whenever a worker task has not finished.\n- Workers share one database connection pool (`POOL_SIZE` in `app/worker.py`).\n  `store.acquire(timeout=5)` raises `TimeoutError` when the pool is drained.\n- On-call notes: "the api timeouts look like symptoms, the worker errors name\n  the actual resource".\n'}}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("task_id")
    parser.add_argument("sandbox")
    args = parser.parse_args()
    out = run_stub(Path(args.sandbox), "claude_code",
                   {"id": args.task_id, "prompt": "", "verification": ""}, 1)
    print(json.dumps({k: v for k, v in out.items() if k != "stdout"}, indent=2))
