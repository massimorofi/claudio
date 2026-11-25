#!/usr/bin/env python3
"""Tests for the capability probe and its committed artifact.

Run: ``PYTHONPATH=benchmark python3 -m pytest benchmark/tests/test_capability.py -q``
"""
from __future__ import annotations

import ast
import json
import os
import re
import stat
import sys
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BENCH_DIR.parent
if str(BENCH_DIR) not in sys.path:  # works with or without PYTHONPATH=benchmark
    sys.path.insert(0, str(BENCH_DIR))

import capability  # noqa: E402
import probes  # noqa: E402

EXPECTED = {"google_adk", "claude_code", "opencode"}
ARTIFACT = BENCH_DIR / "results" / "capability.json"


# --------------------------------------------------------------------------- #
# build_capability contract
# --------------------------------------------------------------------------- #
def test_backends_are_exactly_the_three_engines():
    report = capability.build_capability()
    assert set(report["backends"]) == EXPECTED


def test_each_backend_has_bool_available_and_str_fallback():
    report = capability.build_capability()
    for name, entry in report["backends"].items():
        assert isinstance(entry["available"], bool), name
        assert isinstance(entry["fallback"], str) and entry["fallback"], name
        assert isinstance(entry.get("missing"), list), name


def test_fallback_is_stub_driver_only_when_unavailable():
    report = capability.build_capability()
    for name, entry in report["backends"].items():
        if entry["available"]:
            assert entry["fallback"] == "none", name
        else:
            assert entry["fallback"] == "stub_driver", name
            assert entry["missing"], f"{name} unavailable but listed no prerequisite"


def test_notes_is_a_non_empty_list_of_strings():
    notes = capability.build_capability()["notes"]
    assert isinstance(notes, list) and notes
    assert all(isinstance(n, str) and n.strip() for n in notes)


def test_generated_at_is_utc_seconds():
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
                        capability.build_capability()["generated_at"])


# --------------------------------------------------------------------------- #
# Determinism / idempotence
# --------------------------------------------------------------------------- #
def test_deterministic_apart_from_generated_at():
    environ = {"PATH": "", "OPENAI_API_KEY": "sk-test"}
    a = capability.build_capability(environ)
    b = capability.build_capability(environ)
    a.pop("generated_at"), b.pop("generated_at")
    assert capability.render(a) == capability.render(b)


def test_render_is_sorted_indented_with_trailing_newline():
    text = capability.render(capability.build_capability())
    assert text.endswith("\n")
    assert json.dumps(json.loads(text), indent=2, sort_keys=True) + "\n" == text


# --------------------------------------------------------------------------- #
# Probe logic driven by an explicit environ (no host assumptions)
# --------------------------------------------------------------------------- #
def _fake_binary(tmp_path: Path, name: str) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    target = bin_dir / name
    target.write_text("#!/bin/sh\necho fake\n")
    target.chmod(target.stat().st_mode | stat.S_IEXEC)
    return bin_dir


def test_missing_binary_and_credential_flip_available_off(tmp_path):
    report = capability.build_capability({"PATH": str(tmp_path)})
    entry = report["backends"]["opencode"]
    assert entry["available"] is False
    assert entry["fallback"] == "stub_driver"
    assert any("opencode" in m for m in entry["missing"])
    assert any("credential" in m for m in entry["missing"])


def test_binary_and_credential_make_backend_available(tmp_path):
    bin_dir = _fake_binary(tmp_path, "opencode")
    report = capability.build_capability(
        {"PATH": str(bin_dir), "OPENAI_API_KEY": "sk-test"}
    )
    entry = report["backends"]["opencode"]
    assert entry["available"] is True
    assert entry["fallback"] == "none"
    assert entry["missing"] == []
    assert entry["binary_path"] == str(bin_dir / "opencode")


def test_placeholder_credential_does_not_count(tmp_path):
    bin_dir = _fake_binary(tmp_path, "claude")
    entry = capability.build_capability(
        {"PATH": str(bin_dir), "ANTHROPIC_API_KEY": "changeme"}
    )["backends"]["claude_code"]
    assert entry["available"] is False
    assert entry["fallback"] == "stub_driver"


def test_binary_override_env_var_is_honoured(tmp_path):
    bin_dir = _fake_binary(tmp_path, "claude-custom")
    entry = capability.build_capability({
        "PATH": str(bin_dir),
        "ANTHROPIC_API_KEY": "sk-ant-test",
        "CLAUDIO__ENGINE__CLAUDE_CODE__BINARY": "claude-custom",
    })["backends"]["claude_code"]
    assert entry["binary"] == "claude-custom"
    assert entry["available"] is True


def test_probe_all_never_raises_on_junk_environ():
    report = probes.probe_all({"PATH": 1, "OPENAI_API_KEY": None})
    assert set(report["backends"]) == EXPECTED


def test_unknown_backend_is_reported_not_raised():
    entry = probes.probe_backend("nope", {})
    assert entry["available"] is False and entry["missing"]


# --------------------------------------------------------------------------- #
# Constraints: stdlib-only, offline
# --------------------------------------------------------------------------- #
def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_probe_modules_import_only_stdlib():
    allowed = {"__future__", "ast", "json", "os", "sys", "shutil", "importlib",
               "datetime", "pathlib", "probes", "capability", "benchmark"}
    for filename in ("probes.py", "capability.py"):
        extra = _imported_modules(BENCH_DIR / filename) - allowed
        assert not extra, f"{filename} imports non-stdlib: {sorted(extra)}"


def test_no_network_or_subprocess_module_is_used():
    for filename in ("probes.py", "capability.py"):
        modules = _imported_modules(BENCH_DIR / filename)
        for forbidden in ("socket", "ssl", "urllib", "http", "requests",
                          "subprocess", "aiohttp"):
            assert forbidden not in modules, f"{filename} imports {forbidden}"


def test_probing_does_not_import_the_engine_packages():
    capability.build_capability()
    assert "claudio_backend" not in sys.modules
    assert "claudio_core" not in sys.modules


# --------------------------------------------------------------------------- #
# The committed artifact
# --------------------------------------------------------------------------- #
def test_committed_artifact_exists_and_parses():
    assert ARTIFACT.is_file(), "run: python3 benchmark/capability.py"
    data = json.loads(ARTIFACT.read_text())
    backends = data["backends"]
    assert set(backends) == EXPECTED
    for name, entry in backends.items():
        assert isinstance(entry["available"], bool), name
        assert isinstance(entry["fallback"], str), name
    assert isinstance(data["notes"], list) and data["notes"]


def test_committed_artifact_is_stable_formatting():
    text = ARTIFACT.read_text()
    assert text == json.dumps(json.loads(text), indent=2, sort_keys=True) + "\n"


def test_no_log_files_are_written_by_the_probe(tmp_path):
    # *.log is gitignored; an artifact there would silently vanish from the commit.
    assert not list(BENCH_DIR.rglob("*.log")), "benchmark/ must not contain .log files"
    assert os.path.basename(ARTIFACT) == "capability.json"
