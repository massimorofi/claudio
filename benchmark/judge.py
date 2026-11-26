#!/usr/bin/env python3
"""Blind judge for the Claudio engine benchmark (stdlib-only, fully offline).

One run directory (``results/<backend>/<task>/trial_<k>/``) is packed into a
single anonymized *evidence* string -- task prompt, ``diff.patch`` truncated to
a fixed budget, the tail of the verification output and a derived error
summary -- scored on Quality / Intelligence / Coding Ability (integers 1-5), and
written to ``judge.json`` next to the ``run.json`` it scored.

Modes
    llm        ``BENCHMARK_JUDGE_CMD`` set: rubric + anonymized evidence are
               piped to that command on stdin; it answers with a JSON object on
               stdout. Any failure (non-zero exit, junk output, out-of-range)
               falls back to the heuristic.
    heuristic  default. ``heuristic_scores()`` is a pure function of the
               evidence text, so an offline run reproduces byte-for-byte.

Anonymization: backends are shuffled into ``A``/``B``/``C`` by
``random.Random('claudio-bench-2026')`` over the sorted list, and the mapping is
written to ``results/judge_key.json`` so blind scoring stays auditable.

Anchor: verification (pytest) exit status is ground truth. A run whose
verification failed can never score above 2 on Coding or Quality -- neither the
heuristic nor a rogue external judge can claim broken code works.

    python3 benchmark/judge.py --results benchmark/results
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

HERE = Path(__file__).resolve().parent
DEFAULT_RESULTS = HERE / "results"

ANON_SEED = "claudio-bench-2026"
PROMPT_BUDGET = 1200
DIFF_BUDGET = 6000
VERIFY_TAIL_BUDGET = 2000
JUDGE_CMD_TIMEOUT_S = 120.0

AXES = ("quality", "intelligence", "coding")
LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

#: which evidence blemishes count against which axis (see ``heuristic_scores``).
AXIS_BLEMISHES = {
    "quality": ("markers", "stray", "errors", "retries", "diff_size", "files", "engine"),
    "intelligence": ("errors", "retries", "stray", "files", "timeout", "engine"),
    "coding": ("markers", "stray", "diff_size", "errors", "timeout", "engine"),
}


def evidence_sha(text: str) -> str:
    """sha256 hex digest of an evidence blob (the audit handle in judge.json)."""
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _read(run_dir: Path, *names: str) -> tuple[str, str]:
    """Return ``(text, filename)`` for the first candidate file that exists."""
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


def _changed_files(run: dict, diff_text: str) -> list[str]:
    changed = [str(f) for f in (run.get("changed_files") or [])]
    if not changed:
        changed = re.findall(r"^diff --git a/(\S+) b/\S+", diff_text, re.MULTILINE)
    return changed


def _scope(run: dict, diff_text: str) -> tuple[int, int, list[str]]:
    """Files changed, non-source (stray) ones, and up to 5 examples."""
    changed = _changed_files(run, diff_text)
    stray = [
        f
        for f in changed
        if f.endswith((".pyc", ".pyo", ".log", ".so"))
        or "__pycache__/" in f
        or f.startswith((".git/", "node_modules/"))
    ]
    return len(changed), len(stray), sorted(set(stray))[:5]


def _markers(diff_text: str) -> tuple[int, int]:
    """TODO-style and hack-style markers *added* by the diff."""
    added = [ln[1:] for ln in diff_text.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    todo = sum(1 for ln in added if re.search(r"\b(TODO|FIXME|XXX)\b", ln, re.IGNORECASE))
    hack = sum(
        1
        for ln in added
        if re.search(
            r"\b(HACK|WORKAROUND|not implemented|placeholder)\b|raise NotImplementedError",
            ln,
            re.IGNORECASE,
        )
    )
    return todo, hack



def build_evidence(run_dir: str | Path, label: str = "ENGINE") -> str:
    """Pack one run directory into a single anonymized evidence string.

    The judge sees *only* this text: task prompt, ``diff.patch`` (cut to
    ``DIFF_BUDGET``), the tail of the verification output and an error summary.
    The real backend name is scrubbed and replaced by the blind ``label``, so the
    evidence cannot reveal which engine produced the run. The sha256 of the
    returned string (see :func:`evidence_sha`) is recorded in ``judge.json``.
    """
    run_dir = Path(run_dir)
    try:
        run = json.loads((run_dir / "run.json").read_text("utf-8", "replace"))
    except (OSError, ValueError):
        run = {}

    parents = list(run_dir.parents)
    backend = str(run.get("backend") or (parents[1].name if len(parents) > 1 else ""))
    task_id = str(run.get("task_id") or (parents[0].name if parents else ""))

    prompt, _ = _read(run_dir, "prompt.txt", "task_prompt.txt")
    prompt = prompt or str(run.get("prompt", ""))
    diff_text, _ = _read(run_dir, "diff.patch", "diff.txt")
    verify_text, verify_name = _read(
        run_dir, "verify.txt", "verification.txt", "verify_output.txt", "stdout.txt"
    )

    errors = run.get("errors") or {}

    def num(key: str) -> int:
        return int(errors.get(key, 0) or 0)

    timed_out = bool(run.get("timed_out")) or num("timeouts") > 0
    n_files, n_stray, stray = _scope(run, diff_text)
    todo, hack = _markers(diff_text)

    def scrub(text: str) -> str:
        return text.replace(backend, label) if backend else text

    lines = [
        "=== BENCHMARK EVIDENCE (blind) ===",
        "engine_label: {0}".format(label),
        "task_id: {0}".format(task_id),
        "trial: {0}".format(run.get("trial", 1)),
        "",
        "--- TASK PROMPT (<= {0} chars) ---".format(PROMPT_BUDGET),
        scrub(_clip(prompt, PROMPT_BUDGET)) or "(no prompt)",
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
        "verification_passed: {0}".format("yes" if run.get("verification_passed") else "no"),
        "verify_exit_code: {0}".format(run.get("verify_exit_code", "unknown")),
        "engine_exit_code: {0}".format(run.get("engine_exit_code", "unknown")),
        "timed_out: {0}".format("yes" if timed_out else "no"),
        "errors: command_errors={0} tool_errors={1} retries={2} timeouts={3}".format(
            num("command_errors"), num("tool_errors"), num("retries"), num("timeouts")
        ),
        "diff_lines: {0}".format(run.get("diff_lines", len(diff_text.splitlines()))),
        "files_changed: {0}".format(n_files),
        "suspicious_paths: {0}".format(n_stray),
        "suspicious_examples: {0}".format(",".join(stray) or "-"),
        "todo_markers: {0}".format(todo),
        "hack_markers: {0}".format(hack),
        "=== END EVIDENCE ===",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# anonymization
# --------------------------------------------------------------------------- #


def anonymize(backends) -> dict[str, str]:
    """Stable blind mapping ``{real_backend: 'A'|'B'|'C'...}``.

    The shuffle comes from ``random.Random('claudio-bench-2026')`` over the
    *sorted* backend list, so the mapping depends only on the set of backends --
    never on argument order, filesystem order or the clock. Two calls always
    agree.
    """
    order = sorted({str(b) for b in backends if str(b)})
    if len(order) > len(LABELS):
        raise ValueError("too many backends to label with A-Z")
    rng = random.Random(ANON_SEED)
    rng.shuffle(order)
    return {backend: LABELS[i] for i, backend in enumerate(order)}


def write_judge_key(results_root: str | Path, mapping: dict[str, str]) -> Path:
    """Persist the blind mapping (both directions) so scoring stays auditable."""
    path = Path(results_root) / "judge_key.json"
    payload = {
        "seed": ANON_SEED,
        "algorithm": "random.Random(seed).shuffle(sorted(backends))",
        "backends": sorted(mapping),
        "mapping": dict(sorted(mapping.items())),
        "reverse": {label: backend for backend, label in sorted(mapping.items())},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", "utf-8")
    return path


def discover_backends(results_root: str | Path) -> list[str]:
    """Backend directories that actually contain ``<task>/trial_*/run.json``."""
    root = Path(results_root)
    if not root.is_dir():
        return []
    return sorted({p.relative_to(root).parts[0] for p in root.glob("*/*/trial_*/run.json")})



# --------------------------------------------------------------------------- #
# rubric
# --------------------------------------------------------------------------- #

RUBRIC = """\
You are a blind code judge. Score ONE benchmark run on three axes, each an
integer 1-5. You see anonymized evidence only: task prompt, diff, the tail of the
verification output and an error summary. Never guess an engine's identity.

HARD ANCHOR: the verification result (pytest/unittest exit status) is ground
truth. If verification did NOT pass the code is broken, and Coding Ability and
Quality MUST be 1 or 2 no matter how clean the diff looks. Do not claim broken
code works.

Quality -- correctness and craft of the delivered change.
  5 = verification passed, no error/retry noise, no TODO/hack markers, only
      relevant files touched.
  4 = passed with one blemish (a stray artifact, one tool error, one marker).
  3 = passed but noisy: repeated retries, several out-of-scope files, or a diff
      much larger than the task.
  2 = passed only marginally, or several blemishes stack at once (noise plus
      out-of-scope files), or the real work is left as TODO/placeholder.
  1 = verification failed, timed out, or the diff does not address the task.

Intelligence -- how well the agent reasoned and recovered.
  5 = understood the task first try: no command/tool errors, no retries, tight
      scope, changes match the prompt exactly.
  4 = one minor misstep, self-corrected immediately.
  3 = needed retries or hit tool/command errors but recovered to a passing state.
  2 = flailed: repeated failed commands, scope creep, or fixed the test instead
      of the code.
  1 = gave up, timed out, or verification never passed.

Coding Ability -- technical competence of the code itself.
  5 = idiomatic, minimal, correct; tests pass cleanly.
  4 = correct and readable with small style nits.
  3 = correct but clumsy: duplication, dead code, oversized diff.
  2 = incomplete or hacky (TODO, NotImplementedError, hardcoded expected values).
  1 = does not compile / verification failed / no real change produced.

Answer with EXACTLY one JSON object and no prose:
{"quality": <1-5>, "intelligence": <1-5>, "coding": <1-5>,
 "justifications": {"quality": "<one sentence>", "intelligence": "<one sentence>",
                    "coding": "<one sentence>"}}
"""


def rubric_prompt() -> str:
    """The fixed 1-5 anchor rubric handed to any external judge."""
    return RUBRIC


# --------------------------------------------------------------------------- #
# deterministic heuristic scoring
# --------------------------------------------------------------------------- #

_INT_FIELDS = ("diff_lines", "files_changed", "suspicious_paths", "todo_markers", "hack_markers")


def parse_evidence(evidence: str) -> dict:
    """Read the ERROR SUMMARY block of an evidence blob back into facts."""
    facts: dict = {
        "verification_passed": False,
        "timed_out": False,
        "engine_failed": False,
        "errors": {},
        "has_diff": "(no diff.patch)" not in evidence,
    }
    facts.update({name: 0 for name in _INT_FIELDS})
    tail = evidence.rpartition("--- ERROR SUMMARY ---")[2]
    for line in tail.splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key, value = key.strip(), value.strip()
        if key == "verification_passed":
            facts[key] = value == "yes"
        elif key == "timed_out":
            facts[key] = value == "yes"
        elif key == "engine_exit_code":
            facts["engine_failed"] = value not in ("0", "unknown", "")
        elif key == "errors":
            for token in value.split():
                name, _, raw = token.partition("=")
                if raw.isdigit():
                    facts["errors"][name] = int(raw)
        elif key in _INT_FIELDS and value.isdigit():
            facts[key] = int(value)
    for name in ("command_errors", "tool_errors", "retries", "timeouts"):
        facts["errors"].setdefault(name, 0)
    return facts


def _clamp(value: int) -> int:
    return max(1, min(5, int(value)))


def _anchor(scores: dict[str, int], facts: dict) -> dict[str, int]:
    """Cap scores when verification failed: broken code cannot be scored as work."""
    if not facts["verification_passed"]:
        scores["coding"] = min(scores["coding"], 2)
        scores["quality"] = min(scores["quality"], 2)
        scores["intelligence"] = min(scores["intelligence"], 3)
    if not facts["has_diff"]:
        scores["coding"] = min(scores["coding"], 1)
    return {axis: _clamp(scores[axis]) for axis in AXES}


def heuristic_scores(evidence: str) -> dict:
    """Deterministic 1-5 scores derived purely from the evidence text.

    Each axis starts at 5 and is docked by the worst *rubric blemish* it sees
    (verification pass/fail, error and retry counts, diff size, scope discipline
    i.e. stray non-source paths and file count, TODO/hack markers), with one
    extra point off when three or more blemish categories stack. A run that
    verified never falls below 3 unless the work is left as TODO/placeholder
    (2), and :func:`_anchor` then caps every axis for a failed verification, so
    the same evidence always produces the same integers.
    """
    f = parse_evidence(evidence)
    errors = f["errors"]
    noise = errors["command_errors"] + errors["tool_errors"]
    retries = errors["retries"]
    markers = f["todo_markers"] + f["hack_markers"]
    stray = f["suspicious_paths"]

    blemishes: dict[str, tuple[int, str]] = {}
    if markers:
        blemishes["markers"] = (3, "{0} TODO/hack marker(s)".format(markers))
    if stray:
        blemishes["stray"] = (
            2 if stray >= 3 else 1,
            "{0} out-of-scope artifact(s)".format(stray),
        )
    if noise:
        blemishes["errors"] = (2 if noise >= 3 else 1, "{0} command/tool error(s)".format(noise))
    if retries:
        blemishes["retries"] = (2 if retries >= 2 else 1, "{0} retries".format(retries))
    if f["diff_lines"] > 400:
        blemishes["diff_size"] = (1, "diff of {0} lines is oversized".format(f["diff_lines"]))
    if f["files_changed"] > 8:
        blemishes["files"] = (1, "{0} files touched".format(f["files_changed"]))
    if f["timed_out"]:
        blemishes["timeout"] = (4, "run timed out")
    if f["engine_failed"]:
        blemishes["engine"] = (2, "engine exited non-zero")

    scores: dict[str, int] = {}
    justifications: dict[str, str] = {}
    for axis in AXES:
        hits = [blemishes[n] for n in AXIS_BLEMISHES[axis] if n in blemishes]
        notes = [note for _, note in hits]
        if not f["has_diff"]:
            score, notes = 1, ["no change produced"]
        elif not f["verification_passed"]:
            score = 1 if (f["timed_out"] or hits) else 2
            notes = ["verification failed, so the code does not work"] + notes
        elif not hits:
            score, notes = 5, ["clean pass, no blemishes recorded"]
        else:
            penalty = max(pen for pen, _ in hits) + (1 if len(hits) >= 3 else 0)
            floor = 2 if ("markers" in blemishes or len(hits) >= 3) else 3
            score = max(_clamp(5 - penalty), floor)
        scores[axis] = _clamp(score)
        justifications[axis] = "{0} {1}: {2}.".format(
            axis.capitalize(), score, "; ".join(notes) or "no blemishes recorded"
        )
    return {"scores": _anchor(scores, f), "justifications": justifications}


# --------------------------------------------------------------------------- #
# optional external (LLM) judge
# --------------------------------------------------------------------------- #


def llm_scores(cmd: str, evidence: str) -> dict | None:
    """Ask an external judge command for scores, or ``None`` to mean "fall back".

    ``evidence`` (plus the rubric) is piped to ``cmd`` on stdin as JSON; the
    command must print ``{"quality": .., "intelligence": .., "coding": ..,
    "justifications": {..}}``. A missing command, non-zero exit, unparsable or
    out-of-range answer all return ``None`` so the run stays reproducible.
    """
    payload = json.dumps({"rubric": rubric_prompt(), "evidence": evidence})
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            input=payload.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=JUDGE_CMD_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    match = re.search(r"\{.*\}", proc.stdout.decode("utf-8", "replace"), re.DOTALL)
    if not match:
        return None
    try:
        raw = json.loads(match.group(0))
    except ValueError:
        return None
    if not isinstance(raw, dict):
        return None
    source = raw.get("scores") if isinstance(raw.get("scores"), dict) else raw
    scores: dict[str, int] = {}
    for axis in AXES:
        value = source.get(axis)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        scores[axis] = _clamp(int(value))
    just = raw.get("justifications")
    just = just if isinstance(just, dict) else {}
    return {
        "scores": scores,
        "justifications": {
            axis: str(just.get(axis, "external judge gave no justification")) for axis in AXES
        },
    }


# --------------------------------------------------------------------------- #
# judging
# --------------------------------------------------------------------------- #


def _backend_of(run_dir: Path, run: dict) -> str:
    parents = list(run_dir.parents)
    return str(run.get("backend") or (parents[1].name if len(parents) > 1 else "unknown"))


def judge(run_dir: str | Path, mapping: dict[str, str] | None = None, write: bool = True) -> dict:
    """Score one run directory and write ``judge.json`` beside its ``run.json``.

    Uses ``BENCHMARK_JUDGE_CMD`` when set (``mode: "llm"``) and otherwise the
    deterministic ``heuristic_scores`` (``mode: "heuristic"``). Scores are always
    clamped to 1-5 and re-anchored on the verification result, and
    ``evidence_sha`` pins exactly what was scored.
    """
    run_dir = Path(run_dir)
    try:
        run = json.loads((run_dir / "run.json").read_text("utf-8", "replace"))
    except (OSError, ValueError):
        run = {}
    backend = _backend_of(run_dir, run)
    if mapping is None:
        mapping = anonymize([backend])
    label = mapping.get(backend, "ENGINE")

    evidence = build_evidence(run_dir, label)
    facts = parse_evidence(evidence)
    cmd = os.environ.get("BENCHMARK_JUDGE_CMD", "").strip()
    result = llm_scores(cmd, evidence) if cmd else None
    mode = "llm"
    if result is None:
        result, mode = heuristic_scores(evidence), "heuristic"

    payload = {
        "backend_anon": label,
        "mode": mode,
        "scores": _anchor(dict(result["scores"]), facts),
        "justifications": {axis: str(result["justifications"].get(axis, "")) for axis in AXES},
        "evidence_sha": evidence_sha(evidence),
    }
    if write:
        (run_dir / "judge.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", "utf-8"
        )
    return payload


def judge_results(results_root: str | Path, write: bool = True) -> list[tuple[Path, dict]]:
    """Judge every ``<backend>/<task>/trial_*/run.json`` under ``results_root``."""
    root = Path(results_root)
    backends = discover_backends(root)
    if not backends:
        return []
    mapping = anonymize(backends)
    write_judge_key(root, mapping)
    runs = sorted(root.glob("*/*/trial_*/run.json"))
    return [(p.parent, judge(p.parent, mapping, write=write)) for p in runs]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Blind judge for benchmark runs.")
    parser.add_argument("--results", default=str(DEFAULT_RESULTS), help="results directory")
    parser.add_argument("--dry-run", action="store_true", help="score without writing")
    args = parser.parse_args(argv)

    scored = judge_results(args.results, write=not args.dry_run)
    if not scored:
        print("no run.json found under {0}".format(args.results), file=sys.stderr)
        return 1
    root = Path(args.results)
    for run_dir, payload in scored:
        s = payload["scores"]
        print(
            "{0} {1} q={2} i={3} c={4} sha={5}".format(
                payload["backend_anon"],
                run_dir.relative_to(root).as_posix(),
                s["quality"],
                s["intelligence"],
                s["coding"],
                payload["evidence_sha"][:12],
            )
        )
    modes = {payload["mode"] for _, payload in scored}
    print(
        "SUMMARY judged={0} mode={1} key={2}".format(
            len(scored), ",".join(sorted(modes)), Path(args.results) / "judge_key.json"
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
