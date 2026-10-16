"""Tests for the command allowlist: the positive gate for daemon elevated commands."""
from __future__ import annotations

import pytest

from claudio.settings import AppConfig
from claudio.autonomous.allowlist import (
    DEFAULT_ALLOWLIST,
    effective_allowlist,
    is_allowed,
    validate_command_entry,
    validate_service_name,
    validate_vacuum_size,
    sudoers_snippet,
)


# ---- entry validation --------------------------------------------------


def test_default_allowlist_entries_are_all_valid():
    # Every shipped default must pass its own validator.
    for entry in DEFAULT_ALLOWLIST:
        assert validate_command_entry(entry) == entry.strip()


def test_validate_rejects_relative_path():
    with pytest.raises(ValueError):
        validate_command_entry("apt-get update")


def test_validate_rejects_shell_metachars():
    for bad in ["/usr/bin/foo; rm -rf /", "/usr/bin/foo | tee", "/usr/bin/$(evil)", "/usr/bin/a > /x"]:
        with pytest.raises(ValueError):
            validate_command_entry(bad)


def test_validate_rejects_catastrophic():
    with pytest.raises(ValueError):
        validate_command_entry("/usr/bin/mkfs.ext4 /dev/sda1")


def test_validate_rejects_empty():
    with pytest.raises(ValueError):
        validate_command_entry("   ")


def test_validate_rejects_non_string():
    with pytest.raises(ValueError):
        validate_command_entry(123)


# ---- effective allowlist -----------------------------------------------


def test_effective_uses_defaults_when_unset():
    cfg = AppConfig()
    assert effective_allowlist(cfg) == DEFAULT_ALLOWLIST


def test_effective_uses_override_when_set():
    cfg = AppConfig()
    cfg.autonomous.command_allowlist = ["/usr/bin/foo bar"]
    assert effective_allowlist(cfg) == ["/usr/bin/foo bar"]


# ---- is_allowed --------------------------------------------------------


def test_is_allowed_exact_match():
    cfg = AppConfig()
    assert is_allowed("/usr/bin/apt-get update", cfg) is True


def test_is_allowed_tolerates_whitespace():
    cfg = AppConfig()
    assert is_allowed("  /usr/bin/apt-get   update  ", cfg) is True


def test_is_allowed_rejects_unlisted():
    cfg = AppConfig()
    assert is_allowed("/usr/bin/rm -rf /tmp/x", cfg) is False


def test_is_allowed_rejects_partial_prefix():
    cfg = AppConfig()
    # A prefix of an allowed command is not itself allowed.
    assert is_allowed("/usr/bin/apt-get", cfg) is False


def test_is_allowed_uses_override():
    cfg = AppConfig()
    cfg.autonomous.command_allowlist = ["/usr/bin/custom thing"]
    assert is_allowed("/usr/bin/custom thing", cfg) is True
    assert is_allowed("/usr/bin/apt-get update", cfg) is False  # default no longer applies


# ---- service name ------------------------------------------------------


def test_validate_service_name_ok():
    cfg = AppConfig()
    cfg.autonomous.recovery.restart_allowlist = ["nginx", "postgresql", "docker@foo"]
    assert validate_service_name("nginx", cfg) == "nginx"


def test_validate_service_name_rejects_unlisted():
    cfg = AppConfig()
    cfg.autonomous.recovery.restart_allowlist = ["nginx"]
    with pytest.raises(ValueError):
        validate_service_name("redis", cfg)


def test_validate_service_name_rejects_bad_format():
    cfg = AppConfig()
    cfg.autonomous.recovery.restart_allowlist = ["bad name"]
    with pytest.raises(ValueError):
        validate_service_name("bad name", cfg)  # space not allowed


# ---- vacuum size -------------------------------------------------------


def test_validate_vacuum_size_ok():
    assert validate_vacuum_size("200M") == "200M"
    assert validate_vacuum_size("1G") == "1G"
    assert validate_vacuum_size("512M") == "512M"


def test_validate_vacuum_size_rejects_bad():
    for bad in ["200", "M", "lots", "200MB", "-5M", ""]:
        with pytest.raises(ValueError):
            validate_vacuum_size(bad)


# ---- sudoers snippet ---------------------------------------------------


def test_sudoers_snippet_includes_fixed_and_restart_commands():
    cfg = AppConfig()
    cfg.autonomous.recovery.restart_allowlist = ["nginx", "postgresql"]
    snippet = sudoers_snippet(cfg)
    assert "claudio ALL=(root) NOPASSWD: /usr/bin/apt-get update" in snippet
    assert "claudio ALL=(root) NOPASSWD: /usr/bin/systemctl restart nginx" in snippet
    assert "claudio ALL=(root) NOPASSWD: /usr/bin/systemctl restart postgresql" in snippet


def test_sudoers_snippet_dedupes():
    cfg = AppConfig()
    # A restart command already in the allowlist should not be emitted twice.
    cfg.autonomous.command_allowlist = ["/usr/bin/systemctl restart nginx"]
    cfg.autonomous.recovery.restart_allowlist = ["nginx"]
    snippet = sudoers_snippet(cfg)
    assert snippet.count("systemctl restart nginx") == 1
