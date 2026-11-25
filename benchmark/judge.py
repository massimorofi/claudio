#!/usr/bin/env python3
"""Blind judge for the Claudio engine benchmark (stdlib-only, offline).

The judge turns one run directory (``results/<backend>/<task>/trial_<k>/``) into
a single anonymized *evidence* blob, scores it on three axes (Quality,
Intelligence, Coding Ability) on a 1-5 anchor scale, and writes ``judge.json``
next to the ``run.json`` it scored.

Two scoring modes:

* ``llm``        -- when ``BENCHMARK_JUDGE_CMD`` is set, the anonymized evidence
                    is piped to that command on stdin; it must print
                    ``{"quality": int, "intelligence": int, "coding": int,
                       "justifications": {...}}`` on stdout.
* ``heuristic``  -- the default (and the fallback whenever the external judge is
                    missing, fails or answers nonsense). ``heuristic_scores()``
                    is a pure function of the evidence text, so an offline run
                    is byte-for-byte reproducible.

Anonymization: backends are shuffled with ``random.Random('claudio-bench-2026')``
into stable labels ``A``/``B``/``C``. The mapping is written to
``results/judge_key.json`` so blind scoring stays auditable after the fact.

    python3 benchmark/judge.py --results benchmark/results

Every score is clamped to 1-5 and anchored on the verification result: a run
whose verification command failed can never be scored above 2 on Coding Ability
or Quality, so no judge (LLM or heuristic) can claim broken code works.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

HERE = Path(__file__).resolve().parent
DEFAULT_RESULTS = HERE / "results"

#: seed for the blind A/B/C mapping -- fixed so labels never move between runs.
ANON_SEED = "claudio-bench-2026"
#: fixed truncation budgets (characters) for the evidence pack.
PROMPT_BUDGET = 1200
DIFF_BUDGET = 6000
VERIFY_TAIL_BUDGET = 2000
#: timeout (seconds) for the optional external judge command.
JUDGE_CMD_TIMEOUT_S = 120.0

AXES = ("quality", "intelligence", "coding")
LABELS = tuple("ABCDEFGHIJKLMNOPQRSTUVWXYZ")


# --------------------------------------------------------------------------- #
# evidence
# --------------------------------------------------------------------------- #


class Evidence(str):
    """The packed evidence text, plus its ``sha256``.

    ``build_evidence()`` is documented as returning a ``str``; subclassing
    ``str`` keeps that contract (it compares, hashes and formats like the plain
    text) while letting callers read ``.sha256`` / ``.text`` directly instead of
    re-hashing.
    """

    __slots__ = ()

    @property
    def text(self) -> str:
        return str(self)

    @property
    def sha256(self) -> str:
        return evidence_sha(str(self))


def evidence_sha(text: str) -> str:
    """sha256 hex digest of an evidence blob (the audit handle in judge.json)."""
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _read(run_dir: Path, *names: str) -> tuple[str, str]:
    """Return ``(text, filename)`` for the first candidate that exists."""
    for name in names:
        path = run_dir / name
        if path.is_file():
            try:
                return path.read_text("utf-8", "replace"), name
            except OSError:
                return "", name
    return "", ""


def _clip(text: str, budget: int, head: bool = True) -> str:
    """Truncate to ``budget`` chars from the head (or tail) with a marker."""
    text = text.replace("\r\n", "\n").rstrip()
    if len(text) <= budget:
        return text
    if head:
        return text[:budget] + "\n...[truncated {0} chars]".format(len(text) - budget)
    return "...[dropped {0} chars]...\n".format(len(text) - budget) + text[-budget:]


def _markers(diff_text: str) -> tuple[int, int]:
    """Count TODO-style and hack-style markers added by the diff."""
    added = [
        ln[1:]
        for ln in diff_text.splitlines()
        if ln.startswith("+") and not ln.startswith("+++")
    ]
    todo = sum(
        1
        for ln in added
        if re.search(r"\b(TODO|FIXME|XXX)\b", ln, re.IGNORECASE)
    )
    hack = sum(
        1
        for ln in added
        if re.search(
            r"\b(HACK|WORKAROUND|not implemented|placeholder|temporarily disable)\b"
            r"|raise NotImplementedError",
            ln,
            re.IGNORECASE,
        )
    )
    return todo, hack


def _scope(run: dict[str, Any], diff_text: str) -> tuple[int, int, list[str]]:
    """Files changed, suspicious (non-source) ones, and the diff's own file count."""
    changed = [str(f) for f in (run.get("changed_files") or [])]
    if not changed:
        changed = re.findall(r"^diff --git a/(\S+) b/\S+", diff_text, re.MULTILINE)
    suspicious = [
        f
        for f in changed
        if f.endswith((".pyc", ".pyo", ".log", ".so"))
        or "__pycache__/" in f
        or f.startswith((".git/", "node_modules/"))
    ]
    return len(changed), len(suspicious), sorted(suspicious)[:5]


def build_evidence(run_dir: str | Path, label: str = "ENGINE") -> Evidence:
    """Pack one run directory into a single anonymized evidence blob.

    The blob is the *only* thing a judge sees: task prompt, ``diff.patch``
    (truncated to ``DIFF_BUDGET``), the tail of the verification output and a
    derived error/scope summary. The real backend name is scrubbed everywhere and
    replaced by the blind ``label`` (``A``/``B``/``C``), so the judge cannot tell
    which engine produced the run.

    Returns an :class:`Evidence` (a ``str``); ``.sha256`` is the evidence hash
    recorded in ``judge.json``.
    """
    run_dir = Path(run_dir)
    try:
        run = json.loads((run_dir / "run.json").read_text("utf-8", "replace"))
    except (OSError, ValueError):
        run = {}

    parents = list(run_dir.parents)
    backend = str(run.get("backend") or (parents[1].name if len(parents) > 1 else ""))
    task_id = str(run.get("task_id") or (parents[0].name if parents else ""))
    trial = run.get("trial", 1)

    prompt, _ = _read(run_dir, "prompt.txt", "task_prompt.txt")
    if not prompt:
        prompt = str(run.get("prompt", ""))
    diff_text, _ = _read(run_dir, "diff.patch", "diff.txt")
    verify_text, verify_name = _read(
        run_dir, "verify.txt", "verification.txt", "verify_output.txt", "stdout.txt"
    )
    stderr_text, _ = _read(run_dir, "stderr.txt")

    errors = run.get("errors") or {}
    ce = int(errors.get("command_errors", 0) or 0)
    te = int(errors.get("tool_errors", 0) or 0)
    rt = int(errors.get("retries", 0) or 0)
    to = int(errors.get("timeouts", 0) or 0)
    timed_out = bool(run.get("timed_out")) or to > 0
    vp = bool(run.get("verification_passed"))
    verify_rc = run.get("verify_exit_code", run.get("exit_status"))
    n_files, n_suspicious, suspicious = _scope(run, diff_text)
    todo, hack = _markers(diff_text)

    def scrub(text: str) -> str:
        if backend:
            text = text.replace(backend, label)
        return text

    lines: list[str] = [
        "=== BENCHMARK EVIDENCE (blind) ===",
        "engine_label: {0}".format(label),
        "task_id: {0}".format(task_id),
        "trial: {0}".format(trial),
        "",
        "--- TASK PROMPT (<= {0} chars) ---".format(PROMPT_BUDGET),
        scrub(_clip(prompt, PROMPT_BUDGET)),
        "",
        "--- DIFF.PATCH (<= {0} chars) ---".format(DIFF_BUDGET),
        scrub(_clip(diff_text, DIFF_BUDGET)) if diff_text else "(no diff.patch)",
        "",
        "--- VERIFICATION OUTPUT: {0} (tail <= {1} chars) ---".format(
            verify_name or "(missing)", VERIFY_TAIL_BUDGET
        ),
        scrub(_clip(verify_text, VERIFY_TAIL_BUDGET, head=False))
        if verify_text
        else "(no verification output)",
        "",
        "--- ERROR SUMMARY ---",
        "verification_passed: {0}".format("yes" if vp else "no"),
        "verify_exit_code: {0}".format(verify_rc),
        "exit_status: {0}".format(run.get("exit_status", "unknown")),
        "timed_out: {0}".format("yes" if timed_out else "no"),
        "engine_exit_code: {0}".format(run.get("engine_exit_code", "unknown")),
        "errors: command_errors={0} tool_errors={1} retries={2} timeouts={3}".format(
            ce, te, rt, to
        ),
        "diff_lines: {0}".format(run.get("diff_lines", len(diff_text.splitlines()))),
        "files_changed: {0}".format(n_files),
        "suspicious_paths: {0}".format(n_suspicious),
        "suspicious_examples: {0}".format(",".join(suspicious) or "-"),
        "todo_markers: {0}".format(todo),
        "hack_markers: {0}".format(hack),
        "stderr_bytes: {0}".format(len(stderr_text)),
        "=== END EVIDENCE ===",
    ]
    return Evidence("\n".join(lines) + "\n")


# --------------------------------------------------------------------------- #
# anonymization
# --------------------------------------------------------------------------- #


def anonymize(backends: Iterable[str]) -> dict[str, str]:
    """Stable blind mapping ``{real_backend: 'A'|'B'|'C'|...}``.

    The shuffle is driven by ``random.Random('claudio-bench-2026')`` over the
    *sorted* backend list, so the mapping depends only on the set of backends --
    never on argument order, filesystem order or the current time. Calling it
    twice always yields the same answer.
    """
    order = sorted({str(b) for b in backends if str(b)})
    rng = random.Random(ANON_SEED)
    rng.shuffle(order)
    if len(order) > len(LABELS):
        raise ValueError("too many backends to label with A-Z")
    return {backend: LABELS[i] for i, backend in enumerate(order)}


def invert_anonymization(mapping: dict[str, str]) -> dict[str, str]:
    """``{real: label}`` -> ``{label: real}`` (the shape written to the key file)."""
    return {label: backend for backend, label in sorted(mapping.items())}


def write_judge_key(results_root: str | Path, mapping: dict[str, str]) -> Path:
    """Persist the blind mapping so scoring stays auditable after the fact."""
    path = Path(results_root) / "judge_key.json"
    payload = {
        "seed": ANON_SEED,
        "algorithm": "random.Random(seed).shuffle(sorted(backends))",
        "backends": sorted(mapping),
        "mapping": invert_anonymization(mapping),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", "utf-8")
    return path


def read_judge_key(results_root: str | Path) -> dict[str, str] | None:
    """Load ``{real: label}`` from ``<results_root>/judge_key.json`` if present."""
    path = Path(results_root) / "judge_key.json"
    try:
        payload = json.loads(path.read_text("utf-8", "replace"))
    except (OSError, ValueError):
        return None
    inverse = payload.get("mapping") or {}
    return {backend: label for label, backend in inverse.items()}


def discover_backends(results_root: str | Path) -> list[str]:
    """Backend directories that actually contain ``<task>/trial_*`` runs."""
    root = Path(results_root)
    found: set[str] = set()
    if not root.is_dir():
        return []
    for run_json in sorted(root.glob("*/*/trial_*/run.json")):
        found.add(run_json.relative_to(root).parts[0])
    return sorted(found)


# --------------------------------------------------------------------------- #
# rubric
# --------------------------------------------------------------------------- #

RUBRIC = """\
You are a blind code-judge. Score ONE benchmark run on three axes, each an
integer 1-5. You are shown anonymized evidence only: the task prompt, the diff,
the tail of the verification output and an error summary. Never guess an engine's
identity.

HARD ANCHOR: the verification result (pytest/unittest exit status) is ground
truth. If verification did NOT pass, the code is broken: Coding Ability and
Quality MUST be 1 or 2 no matter how clean the diff looks. Do not claim broken
code works.

Quality -- correctness and craft of the delivered change.
  5 = verification passed, no error/retry noise, no TODO/hack markers, only
      relevant files touched.
  4 = passed; one blemish (a stray artifact, one tool error, a small marker).
  3 = passed but noisy: repeated retries, several out-of-scope files, or a
      diff much larger than the task.
  2 = passed only marginally, or meaningful work left as TODO/placeholder.
  1 = verification failed, timed out, or the diff does not address the task.

Intelligence -- how well the agent reasoned and recovered.
  5 = understood the task first try: no command/tool errors, no retries, tight
      scope, changes match the prompt exactly.
  4 = minor misstep, self-corrected immediately.
  3 = needed retries or hit tool/command errors but recovered to a passing state.
  2 = flailed: repeated failed commands, scope creep, or fixed the test instead
      of the code.
  1 = gave up, timed out, or verification never passed.

Coding Ability -- technical competence of the code itself.
  5 = idiomatic, minimal, correct; tests pass with no warnings.
  4 = correct and readable with small style nits.
  3 = correct but clumsy: duplication, dead code, oversized diff.
  2 = incomplete or hacky (TODO, NotImplementedError, hardcoded expected values).
  1 = does not compile / verification failed / no real change produced.

Answer with EXACTLY one JSON object, no prose:
{"quality": <1-5>, "intelligence": <1-5>, "coding": <1-5>,
 "justifications": {"quality": "<one sentence>",
                    "intelligence": "<one sentence>",
                    "coding": "<one sentence>"}}
"""


def rubric_prompt() -> str:
    """The fixed 1-5 anchor rubric handed to any external judge."""
    return RUBRIC


# --------------------------------------------------------------------------- #
# deterministic heuristic scoring
# --------------------------------------------------------------------------- #

_FIELD_PATTERNS = {
    "verification_passed": r"^verification_passed:\s*(\S+)",
    "verify_exit_code": r"^verify_exit_code:\s*(\S+)",
    "exit_status": r"^exit_status:\s*(\S+)",
    "timed_out": r"^timed_out:\s*(\S+)",
    "engine_exit_code": r"^engine_exit_code:\s*(\S+)",
    "errors_line": r"^errors:\s*(.+)$",
    "diff_lines": r"^diff_lines:\s*(\d+)",
    "files_changed": r"^files_changed:\s*(\d+)",
    "suspicious_paths": r"^suspicious_paths:\s*(\d+)",
    "todo_markers": r"^todo_markers:\s*(\d+)",
    "hack_markers": r"^hack_markers:\s*(\d+)",
}


def parse_evidence(evidence: str) -> dict[str, Any]:
    """Read the structured summary lines back out of an evidence blob."""
    facts: dict[str, Any] = {}
    for key, pattern in _FIELD_PATTERNS.items():
        m = re.search(pattern, evidence, re.MULTILINE)
        facts[key] = m.group(1).strip() if m else ""
    ok = facts["verification_passed"] == "yes"
    err_counts: dict[str, int] = {}
    for token in facts["errors_line"].split():
        if "=" in token:
            name, _, value = token.partition("=")
            if value.isdigit():
                err_counts[name] = int(value)
    for name in ("command_errors", "tool_errors", "retries", "timeouts"):
        err_counts.setdefault(name, 0)
    return {
        "verification_passed": ok,
        "timed_out": facts["timed_out"] == "yes",
        "engine_failed": facts["engine_exit_code"] not in ("0", "unknown", ""),
        "verify_rc": facts["verify_exit_code"],
        "errors": err_counts,
        "diff_lines": int(facts["diff_lines"] or 0),
        "files_changed": int(facts["files_changed"] or 0),
        "suspicious_paths": int(facts["suspicious_paths"] or 0),
        "todo_markers": int(facts["todo_markers"] or 0),
        "hack_markers": int(facts["hack_markers"] or 0),
        "has_diff": "no diff.patch" not in evidence,
        "verify_ok_word": bool(re.search(r"^\s*(OK|PASSED)\s*$", evidence, re.MULTILINE)),
    }


def _clamp(value: int) -> int:
    return max(1, min(5, int(value)))


def anchor_to_verification(scores: dict[str, int], facts: dict[str, Any]) -> dict[str, int]:
    """Cap scores when verification failed -- broken code cannot be scored as work."""
    if not facts["verification_passed"]:
        scores["coding"] = min(scores["coding"], 2)
        scores["quality"] = min(scores["quality"], 2)
        scores["intelligence"] = min(scores["intelligence"], 3)
    if not facts["has_diff"]:
        scores["coding"] = min(scores["coding"], 1)
    return {axis: _clamp(scores[axis]) for axis in AXES}


def heuristic_scores(evidence: str) -> dict[str, Any]:
    """Deterministic 1-5 scores derived purely from the evidence text.

    Inputs: verification pass/fail, error counts, diff size, scope discipline
    (files touched, non-source artifacts) and TODO/hack markers. Same evidence
    in, same scores out -- always.
    """
    f = parse_evidence(evidence)
    err = f["errors"]
    err_total = err["command_errors"] + err["tool_errors"]
    markers = f["todo_markers"] + f["hack_markers"]
    passed = f["verification_passed"]
    just: dict[str, str] = {}

    # --- Coding Ability ----------------------------------------------------
    if not passed:
        coding = 1 if (f["timed_out"] or f["engine_failed"]) else 2
        just["coding"] = (
            "Verification did not pass (verify_exit_code={0}), so the change is "
            "not working code regardless of style.".format(f["verify_rc"] or "n/a")
        )
    else:
        coding = 5
        notes = []
        if err_total:
            coding -= 1
            notes.append("{0} tool/command error(s)".format(err_total))
        if err["retries"] >= 2:
            coding -= 1
            notes.append("{0} retries".format(err["retries"]))
        if markers:
            coding -= 1
            notes.append("{0} TODO/hack marker(s)".format(markers))
        if f["diff_lines"] > 400:
            coding -= 1
            notes.append("diff of {0} lines is oversized".format(f["diff_lines"]))
        just["coding"] = (
            "Verification passed (exit {0}) so the code demonstrably works".format(
                f["verify_rc"] if f["verify_rc"] != "" else "0"
            )
            + ("; docked for " + ", ".join(notes) if notes else "; no defects visible")
            + "."
        )
    coding = _clamp(coding)

    # --- Quality -----------------------------------------------------------
    quality = 5 if passed else 2
    qnotes = []
    if f["timed_out"]:
        quality -= 1
        qnotes.append("run timed out")
    if f["suspicious_paths"]:
        quality -= 1
        qnotes.append("{0} non-source artifact(s) committed".format(f["suspicious_paths"]))
    if markers:
        quality -= 1
        qnotes.append("{0} TODO/hack marker(s)".format(markers))
    if f["files_changed"] > 8:
        quality -= 1
        qnotes.append("{0} files touched".format(f["files_changed"]))
    if not passed:
        qnotes.insert(0, "verification failed")
    elif not qnotes:
        qnotes.append("clean diff, verification passed")
    just["quality"] = "Quality " + str(_clamp(quality)) + ": " + ", ".join(qnotes) + "."
    quality = _clamp(quality)

    # --- Intelligence ------------------------------------------------------
    intelligence = 5 if passed else 2
    inotes = []
    if err_total:
        intelligence -= 1
        inotes.append("{0} tool/command error(s) recovered from".format(err_total))
    if err["retries"]:
        intelligence -= 1
        inotes.append("{0} retried action(s)".format(err["retries"]))
    if f["timed_out"]:
        intelligence -= 1
        inotes.append("timeout")
    if f["suspicious_paths"]:
        intelligence -= 1
        inotes.append("scope discipline: {0} stray path(s)".format(f["suspicious_paths"]))
    if markers:
        intelligence -= 1
        inotes.append("left {0} marker(s) behind".format(markers))
    if not passed:
        inotes.insert(0, "never reached a passing verification")
    elif not inotes:
        inotes.append("first-try success with tight scope")
    just["intelligence"] = "Intelligence " + str(_clamp(intelligence)) + ": " + ", ".join(inotes) + "."
    intelligence = _clamp(intelligence)

    scores = anchor_to_verification(
        {"quality": quality, "intelligence": intelligence, "coding": coding}, f
    )
    return {"scores": scores, "justifications": just}


# --------------------------------------------------------------------------- #
# external (LLM) judge
# --------------------------------------------------------------------------- #

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _coerce_scores(raw: Any) -> dict[str, int] | None:
    """Normalise an arbitrary judge answer into ``{axis: int 1-5}``."""
    if not isinstance(raw, dict):
        return None
    out: dict[str, int] = {}
    for axis in AXES:
        value = raw.get(axis, raw.get(axis.capitalize()))
        try:
            out[axis] = _clamp(int(round(float(value))))
        except (TypeError, ValueError):
            return None
    return out


def llm_scores(cmd: str, evidence: str) -> dict[str, Any] | None:
    """Shell out to ``BENCHMARK_JUDGE_CMD`` with the anonymized evidence.

    The command receives ``{"rubric": ..., "evidence": ...}`` on stdin and must
    print a JSON object with ``quality``/``intelligence``/``coding`` (and
    optionally ``justifications``). Returns ``None`` on any problem -- a missing
    binary, a non-zero exit, unparseable or out-of-range output -- so the caller
    can fall back to the deterministic heuristic instead of failing the gate.
    """
    payload = json.dumps({"rubric": RUBRIC, "evidence": evidence})
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            input=payload,
            capture_output=True,
            text=True,
            timeout=JUDGE_CMD_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    match = _JSON_RE.search(proc.stdout or "")
    if not match:
        return None
    try:
        answer = json.loads(match.group(0))
    except ValueError:
        return None
    scores = _coerce_scores(answer)
    if scores is None:
        return None
    raw_just = answer.get("justifications")
    just = raw_just if isinstance(raw_just, dict) else {}
    return {
        "scores": scores,
        "justifications": {
            axis: str(just.get(axis) or "Scored by the external judge.").strip()
            for axis in AXES
        },
    }


# --------------------------------------------------------------------------- #
# judging one run
# --------------------------------------------------------------------------- #


def judge(
    run_dir: str | Path,
    results_root: str | Path | None = None,
    mapping: dict[str, str] | None = None,
    write: bool = True,
) -> dict[str, Any]:
    """Score one run directory and write ``judge.json`` beside its ``run.json``.

    Uses ``BENCHMARK_JUDGE_CMD`` when set (``mode="llm"``) and the deterministic
    heuristic otherwise (``mode="heuristic"``). Scores are always integers 1-5
    and always anchored on the verification result.
    """
    run_dir = Path(run_dir)
    if results_root is None:
        parents = list(run_dir.parents)
        results_root = parents[2] if len(parents) > 2 else DEFAULT_RESULTS
    results_root = Path(results_root)
    if mapping is None:
        mapping = read_judge_key(results_root) or anonymize(discover_backends(results_root))

    try:
        run = json.loads((run_dir / "run.json").read_text("utf-8", "replace"))
    except (OSError, ValueError):
        run = {}
    parents = list(run_dir.parents)
    backend = str(run.get("backend") or (parents[1].name if len(parents) > 1 else "unknown"))
    label = mapping.get(backend, "ENGINE")

    evidence = build_evidence(run_dir, label="Engine {0}".format(label))
    facts = parse_evidence(evidence)

    cmd = os.environ.get("BENCHMARK_JUDGE_CMD", "").strip()
    mode, result = "heuristic", None
    if cmd:
        result = llm_scores(cmd, evidence)
        if result is not None:
            mode = "llm"
    if result is None:
        result = heuristic_scores(evidence)

    scores = anchor_to_verification(dict(result["scores"]), facts)
    record = {
        "backend_anon": label,
        "mode": mode,
        "scores": {axis: int(scores[axis]) for axis in AXES},
        "justifications": {axis: str(result["justifications"].get(axis, "")) for axis in AXES},
        "evidence_sha": evidence.sha256,
    }
    if write:
        (run_dir / "judge.json").write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n", "utf-8"
        )
    return record


def judge_results(results_root: str | Path, write: bool = True) -> list[dict[str, Any]]:
    """Score every ``run.json`` under ``results_root`` and refresh the key file."""
    results_root = Path(results_root)
    backends = discover_backends(results_root)
    mapping = anonymize(backends)
    if write:
        write_judge_key(results_root, mapping)
    records = []
    for run_json in sorted(results_root.glob("*/*/trial_*/run.json")):
        records.append(judge(run_json.parent, results_root, mapping, write=write))
    return records


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Blind judge for Claudio engine benchmark runs (stdlib-only)."
    )
    parser.add_argument(
        "--results",
        default=str(DEFAULT_RESULTS),
        help="results root holding <backend>/<task>/trial_<k>/run.json",
    )
    parser.add_argument(
        "--run",
        action="append",
        default=None,
        help="score only these run directories (repeatable); bypasses the sweep",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="compute and print the verdicts without writing judge.json / judge_key.json",
    )
    parser.add_argument("--quiet", action="store_true", help="only print the SUMMARY line")
    args = parser.parse_args(argv)

    results_root = Path(args.results)
    if not results_root.is_dir():
        print("judge: no results directory: {0}".format(results_root), file=sys.stderr)
        return 1

    mode = "llm" if os.environ.get("BENCHMARK_JUDGE_CMD", "").strip() else "heuristic"
    backends = discover_backends(results_root)
    mapping = anonymize(backends)
    if not args.dry_run:
        write_judge_key(results_root, mapping)
    pairs: list[tuple[Any, dict[str, Any]]] = []
    if args.run:
        for r in args.run:
            run_dir = Path(r)
            pairs.append(
                (run_dir.relative_to(results_root), judge(run_dir, results_root, mapping, write=not args.dry_run))
            )
        records = [rec for _, rec in pairs]
    else:
        records = []
        for run_json in sorted(results_root.glob("*/*/trial_*/run.json")):
            record = judge(run_json.parent, results_root, mapping, write=not args.dry_run)
            records.append(record)
            pairs.append((run_json.parent.relative_to(results_root), record))

    if not args.quiet:
        for rel, record in pairs:
            print(
                "{0} {1} q={2} i={3} c={4} sha={5}".format(
                    record["backend_anon"],
                    rel,
                    record["scores"]["quality"],
                    record["scores"]["intelligence"],
                    record["scores"]["coding"],
                    record["evidence_sha"][:12],
                )
            )
    print(
        "SUMMARY judged={0} mode={1} key={2}".format(
            len(records), mode, "judge_key.json"
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
