"""Tests for the offline backend availability probes (stdlib only, sync pytest)."""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import probes  # noqa: E402

BACKEND_IDS = {"google_adk", "claude_code", "opencode"}
ENTRY_KEYS = {
    "backend",
    "binary",
    "binary_found",
    "module_found",
    "env_present",
    "env_missing",
    "available",
    "reason",
}
EMPTY_ENV = {}
NO_WHICH = lambda p: None  # noqa: E731


def test_three_backend_ids_present():
    result = probes.probe_all(environ={}, which=NO_WHICH)
    assert set(result["backends"]) == BACKEND_IDS


def test_available_is_a_bool():
    for entry in probes.probe_all(environ={}, which=NO_WHICH)["backends"].values():
        assert isinstance(entry["available"], bool)


def test_bare_environment_never_raises_and_blocks_everything():
    result = probes.probe_all(environ={}, which=NO_WHICH)
    assert set(result["backends"]) == BACKEND_IDS
    for entry in result["backends"].values():
        assert entry["available"] is False
        assert isinstance(entry["reason"], str) and entry["reason"].strip()


def test_entry_schema_is_complete():
    backends = probes.probe_all(environ={}, which=NO_WHICH)["backends"]
    for backend_id, entry in backends.items():
        assert set(entry) == ENTRY_KEYS
        assert entry["backend"] == backend_id
        assert isinstance(entry["binary_found"], bool)
        assert isinstance(entry["module_found"], bool)
        assert isinstance(entry["env_present"], list)
        assert isinstance(entry["env_missing"], list)


def test_generated_at_is_iso8601_string():
    generated_at = probes.probe_all(environ={}, which=NO_WHICH)["generated_at"]
    assert isinstance(generated_at, str) and generated_at
    assert datetime.fromisoformat(generated_at)


def test_default_arguments_work():
    result = probes.probe_all()
    assert set(result["backends"]) == BACKEND_IDS
    for entry in result["backends"].values():
        assert isinstance(entry["available"], bool)
        assert entry["reason"]


def test_binary_probe_uses_injected_which():
    seen = []

    def which(name):
        seen.append(name)
        return "/usr/local/bin/" + name

    result = probes.probe_all(
        environ={"ANTHROPIC_API_KEY": "k", "OPENAI_API_KEY": "k"}, which=which
    )
    assert "claude" in seen and "opencode" in seen
    assert result["backends"]["claude_code"]["binary"] == "claude"
    assert result["backends"]["opencode"]["binary"] == "opencode"
    assert result["backends"]["claude_code"]["binary_found"] is True
    assert result["backends"]["opencode"]["binary_found"] is True


def test_all_requirements_met_marks_available():
    result = probes.probe_all(
        environ={"ANTHROPIC_BASE_URL": "http://example.invalid"},
        which=lambda p: "/usr/local/bin/" + p,
    )
    entry = result["backends"]["claude_code"]
    assert entry["available"] is True
    assert entry["env_present"] == ["ANTHROPIC_BASE_URL"]
    assert entry["env_missing"] == ["ANTHROPIC_API_KEY"]
    assert entry["reason"]


def test_binary_found_without_credentials_is_unavailable():
    result = probes.probe_all(
        environ={"SOMETHING_ELSE": "x"}, which=lambda p: "/usr/local/bin/" + p
    )
    entry = result["backends"]["claude_code"]
    assert entry["binary_found"] is True
    assert entry["available"] is False
    assert "ANTHROPIC_API_KEY" in entry["env_missing"]


def test_blank_env_value_counts_as_missing():
    result = probes.probe_all(
        environ={"OPENAI_API_KEY": "   "}, which=lambda p: "/usr/local/bin/" + p
    )
    entry = result["backends"]["opencode"]
    assert entry["env_present"] == []
    assert entry["available"] is False
    assert entry["reason"]


def test_crashing_which_is_contained():
    def bad_which(name):
        raise OSError("exec format error")

    result = probes.probe_all(environ={"OPENAI_API_KEY": "k"}, which=bad_which)
    for entry in result["backends"].values():
        assert isinstance(entry["available"], bool)
        assert entry["reason"]
    assert result["backends"]["opencode"]["available"] is False


def test_missing_binary_reports_reason():
    result = probes.probe_all(
        environ={"OPENAI_API_KEY": "k"}, which=lambda p: None
    )
    entry = result["backends"]["opencode"]
    assert entry["binary_found"] is False
    assert entry["available"] is False
    assert "opencode" in entry["reason"]


def test_google_adk_needs_no_binary_but_needs_module_and_key():
    result = probes.probe_all(
        environ={"GEMINI_API_KEY": "k"}, which=lambda p: None
    )
    entry = result["backends"]["google_adk"]
    assert entry["binary"] is None
    assert entry["binary_found"] is True
    assert isinstance(entry["module_found"], bool)
    assert entry["available"] is entry["module_found"]


def test_probing_imports_no_first_party_packages():
    probes.probe_all(environ={}, which=NO_WHICH)
    leaked = [
        name
        for name in sys.modules
        if name.split(".")[0] in {"claudio_backend", "claudio_core"}
    ]
    assert leaked == []


def test_source_is_offline_and_dependency_free():
    src = (Path(__file__).resolve().parents[1] / "probes.py").read_text()
    for forbidden in ("subprocess", "socket", "urllib", "requests",
                      "claudio_backend", "claudio_core"):
        assert forbidden not in src, forbidden
