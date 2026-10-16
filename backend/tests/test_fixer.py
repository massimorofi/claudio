"""Tests for the fix proposal + execution layer (agent proposes, operator approves)."""
from __future__ import annotations

import os

import pytest

from claudio.settings import AppConfig
from claudio.autonomous import fixer
from claudio.autonomous.fixer import (
    FixProposal,
    _is_catastrophic,
    _ensure_sudo_stdin,
    _extract_json,
    propose_fix,
    execute_fix,
)


# ---- catastrophic blocklist -------------------------------------------


def test_catastrophic_detected():
    assert _is_catastrophic("sudo rm -rf /")
    assert _is_catastrophic("rm -rf /")
    assert _is_catastrophic("mkfs.ext4 /dev/sda1")
    assert _is_catastrophic("dd if=/dev/zero of=/dev/sda")
    assert _is_catastrophic(":(){ :|:& };:")
    assert _is_catastrophic("chmod -R 777 /")
    assert _is_catastrophic("shutdown now")


def test_normal_commands_not_catastrophic():
    assert not _is_catastrophic("sudo journalctl --vacuum-size=200M")
    assert not _is_catastrophic("df -h")
    assert not _is_catastrophic("rm -rf ./build")  # relative, not root
    assert not _is_catastrophic("apt-get update")


# ---- sudo stdin rewriting ---------------------------------------------


def test_ensure_sudo_stdin_adds_dash_s():
    assert _ensure_sudo_stdin("sudo systemctl restart nginx") == "sudo -S systemctl restart nginx"


def test_ensure_sudo_stdin_handles_leading_whitespace():
    assert _ensure_sudo_stdin("   sudo foo bar") == "sudo -S foo bar"


def test_ensure_sudo_stdin_leaves_non_sudo_unchanged():
    assert _ensure_sudo_stdin("df -h") == "df -h"
    assert _ensure_sudo_stdin("echo hi") == "echo hi"


def test_ensure_sudo_stdin_does_not_double_add():
    assert _ensure_sudo_stdin("sudo -S already done") == "sudo -S already done"


def test_ensure_sudo_stdin_does_not_match_sudo_in_word():
    # "sudoedit" or "pseudo" should not be treated as a sudo invocation
    assert _ensure_sudo_stdin("sudoedit /etc/hosts") == "sudoedit /etc/hosts"
    assert _ensure_sudo_stdin("pseudo-command") == "pseudo-command"


# ---- JSON extraction ---------------------------------------------------


def test_extract_plain_json():
    out = _extract_json('{"command": "ls", "explanation": "x", "risk": "low", "reversible": true}')
    assert out["command"] == "ls"
    assert out["risk"] == "low"


def test_extract_fenced_json():
    out = _extract_json('```json\n{"command": "uptime"}\n```')
    assert out["command"] == "uptime"


def test_extract_json_with_surrounding_prose():
    out = _extract_json('Sure! Here it is: {"command": "free -h"} hope that helps')
    assert out["command"] == "free -h"


def test_extract_invalid_raises():
    with pytest.raises(ValueError):
        _extract_json("no json here at all")


# ---- propose_fix (LLM mocked) -----------------------------------------


class _FakeMsg:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMsg(content)


class _FakeResp:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, content):
        self._content = content

    async def create(self, **kwargs):
        return _FakeResp(self._content)


class _FakeChat:
    def __init__(self, content):
        self.completions = _FakeCompletions(content)


class _FakeClient:
    def __init__(self, content):
        self.chat = _FakeChat(content)


async def test_propose_fix_parses_valid(monkeypatch):
    payload = '{"command": "sudo journalctl --vacuum-size=200M", "explanation": "free space", "risk": "low", "reversible": true}'
    monkeypatch.setattr(fixer, "AsyncOpenAI", lambda **kw: _FakeClient(payload))
    cfg = AppConfig()
    issue = {"title": "disk full", "severity": "warning", "detail": "/ at 97%"}
    prop = await propose_fix(issue, cfg)
    assert isinstance(prop, FixProposal)
    assert prop.command == "sudo journalctl --vacuum-size=200M"
    assert prop.risk == "low"
    assert prop.reversible is True


async def test_propose_fix_rejects_catastrophic(monkeypatch):
    payload = '{"command": "rm -rf /", "explanation": "nope", "risk": "high", "reversible": false}'
    monkeypatch.setattr(fixer, "AsyncOpenAI", lambda **kw: _FakeClient(payload))
    cfg = AppConfig()
    issue = {"title": "disk full", "severity": "warning", "detail": "/ at 97%"}
    with pytest.raises(ValueError):
        await propose_fix(issue, cfg)


async def test_propose_fix_normalizes_bad_risk(monkeypatch):
    payload = '{"command": "df -h", "explanation": "check", "risk": "enormous", "reversible": true}'
    monkeypatch.setattr(fixer, "AsyncOpenAI", lambda **kw: _FakeClient(payload))
    cfg = AppConfig()
    prop = await propose_fix({"title": "x", "severity": "info", "detail": ""}, cfg)
    assert prop.risk == "medium"  # unknown risk coerced to medium


# ---- execute_fix ------------------------------------------------------


async def test_execute_fix_success():
    res = await execute_fix("echo hello")
    assert res["ok"] is True
    assert res["exit_code"] == 0
    assert "hello" in res["stdout"]


async def test_execute_fix_nonzero_exit():
    res = await execute_fix("exit 3")
    assert res["ok"] is False
    assert res["exit_code"] == 3


async def test_execute_fix_refuses_catastrophic():
    res = await execute_fix("rm -rf /")
    assert res["ok"] is False
    assert "Refused" in res["stderr"]


async def test_execute_fix_ignores_password_on_non_sudo():
    # A password supplied for a command that doesn't start with sudo is simply
    # not fed to stdin — the command runs normally.
    res = await execute_fix("echo hi", password="hunter2")
    assert res["ok"] is True
    assert "hi" in res["stdout"]
    # The password must never leak into the captured output.
    assert "hunter2" not in res["stdout"]
    assert "hunter2" not in res["stderr"]


async def test_execute_fix_feeds_password_to_sudo_stdin(tmp_path, monkeypatch):
    # Use a fake `sudo` that just echoes its stdin, so we can assert the
    # password is delivered through stdin (not argv) when the command starts
    # with sudo.
    fake = tmp_path / "sudo"
    fake.write_text("#!/bin/sh\ncat\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    res = await execute_fix("sudo cat", password="hunter2")
    assert res["ok"] is True
    assert "hunter2" in res["stdout"]


async def test_execute_fix_password_not_in_argv(tmp_path, monkeypatch):
    # A fake sudo that prints its own argv proves the password is never passed
    # as a command-line argument (where it would be visible in `ps`).
    fake = tmp_path / "sudo"
    fake.write_text("#!/bin/sh\necho \"ARGS:$*\"\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    res = await execute_fix("sudo whoami", password="hunter2")
    assert res["ok"] is True
    assert "hunter2" not in res["stdout"]
    assert "whoami" in res["stdout"]
