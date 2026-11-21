"""Issue #57 -- pluggable backend engine (backend side).

Factory selection, the shared subprocess runner, and the OpenCode / Claude Code
adapters driven end-to-end against FAKE CLIs (executable scripts that emit a
known JSONL transcript) -- no network, no real binaries, no credentials, exactly
as the spec's test plan requires. Also the /version endpoint reporting the
selected engine, and the shared engine contract suite run across all three.

Run:  cd claudio-backend && .venv/bin/python -m pytest tests/test_issue57_backend.py -q
"""
from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path

import pytest

from claudio_core.engine import EngineError, ModelSpec, RunRequest, ToolPolicy
from claudio_core.settings import AppConfig
from claudio_core.events import EventName

from claudio_backend.engine import create_engine
from claudio_backend.engine.base import EngineContext
from claudio_backend.engine import process as proc
from claudio_backend.engine.opencode import OpenCodeEngine
from claudio_backend.engine.claude_code import ClaudeCodeEngine


def _write_fake_cli(tmp_path: Path, name: str, body: str) -> str:
    """Create an executable fake CLI script and return its absolute path."""
    script = tmp_path / name
    script.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(script)


def _request(tmp_path: Path, prompt: str = "hi") -> RunRequest:
    return RunRequest(
        run_id="r1", session_id="s1", workspace=tmp_path, instructions="",
        user_input=prompt, model=ModelSpec(model="m"), tools=ToolPolicy(),
    )


def _collect(engine, request) -> list[dict]:
    async def go():
        return [e async for e in engine.stream(request)]
    return asyncio.run(go())


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def test_factory_defaults_to_adk():
    eng = create_engine(AppConfig(), db=None)
    assert eng.descriptor.id == "adk"


def test_factory_selects_opencode():
    cfg = AppConfig.model_validate({"engine": {"kind": "opencode"}})
    eng = create_engine(cfg, db=None)
    assert eng.descriptor.id == "opencode"


def test_factory_selects_claude_code():
    cfg = AppConfig.model_validate({"engine": {"kind": "claude_code"}})
    eng = create_engine(cfg, db=None)
    assert eng.descriptor.id == "claude_code"


def test_factory_unknown_engine_raises():
    with pytest.raises(EngineError):
        create_engine(AppConfig(), db=None, kind="bogus")


def test_adk_descriptor_capabilities():
    eng = create_engine(AppConfig(), db=None)
    caps = eng.capabilities()
    assert caps.approvals is True and caps.compaction is True


def test_version_endpoint_reports_selected_engine():
    import types
    from claudio_backend.api.system import version

    eng = create_engine(AppConfig(), db=None)
    sup = types.SimpleNamespace(engine=eng)
    req = types.SimpleNamespace(app=types.SimpleNamespace(state=types.SimpleNamespace(supervisor=sup)))
    result = asyncio.run(version(req))
    assert result["engine"] == "adk"
    assert "engine_version" in result
    assert "engine_capabilities" in result


# ---------------------------------------------------------------------------
# Shared subprocess runner
# ---------------------------------------------------------------------------

def test_process_streams_lines(tmp_path):
    cli = _write_fake_cli(tmp_path, "cli.py", "import sys\nprint('a');print('b')\n")
    async def go():
        out = []
        async for line in proc.stream_lines([cli], cwd=tmp_path, env=proc.build_env(), timeout_seconds=10):
            out.append(line)
        return out
    assert asyncio.run(go()) == ["a", "b"]


def test_process_rejects_shell_string():
    with pytest.raises(proc.ProcessError):
        async def go():
            async for _ in proc.stream_lines("rm -rf /", cwd=".", env={}, timeout_seconds=1):
                pass
        asyncio.run(go())


def test_process_timeout(tmp_path):
    cli = _write_fake_cli(tmp_path, "slow.py", "import time\nprint('x')\ntime.sleep(30)\n")
    with pytest.raises(proc.ProcessError):
        async def go():
            async for _ in proc.stream_lines([cli], cwd=tmp_path, env=proc.build_env(), timeout_seconds=1):
                pass
        asyncio.run(go())


def test_process_nonzero_exit_raises_with_stderr(tmp_path):
    # A CLI that exits non-zero (bad flag, auth error) must surface as an
    # error with the stderr tail, never as an empty successful stream.
    cli = _write_fake_cli(
        tmp_path, "fail.py",
        "import sys\nsys.stderr.write('kaboom: bad flag\\n')\nsys.exit(3)\n",
    )
    with pytest.raises(proc.ProcessError) as ei:
        async def go():
            async for _ in proc.stream_lines([cli], cwd=tmp_path, env=proc.build_env(), timeout_seconds=10):
                pass
        asyncio.run(go())
    assert "3" in str(ei.value)
    assert "kaboom" in str(ei.value)


def test_process_missing_binary():
    with pytest.raises(proc.ProcessError):
        async def go():
            async for _ in proc.stream_lines(["/nonexistent/cli-xyz"], cwd=".", env=proc.build_env(), timeout_seconds=5):
                pass
        asyncio.run(go())


# ---------------------------------------------------------------------------
# OpenCode adapter (fake CLI)
# ---------------------------------------------------------------------------

def _opencode_engine(tmp_path, body):
    cli = _write_fake_cli(tmp_path, "opencode.py", body)
    cfg = AppConfig.model_validate({"engine": {"kind": "opencode", "opencode": {"binary": cli}}})
    return OpenCodeEngine(EngineContext(config=cfg, db=None))


def test_opencode_start_validates_binary(tmp_path):
    eng = _opencode_engine(tmp_path, "print('1.0.0')")
    asyncio.run(eng.start())  # binary exists -> ok
    assert eng.descriptor.id == "opencode"


def test_opencode_start_missing_binary_fails(tmp_path):
    cfg = AppConfig.model_validate({"engine": {"kind": "opencode", "opencode": {"binary": "/no/such/opencode"}}})
    eng = OpenCodeEngine(EngineContext(config=cfg, db=None))
    with pytest.raises(EngineError):
        asyncio.run(eng.start())


def test_opencode_streams_events(tmp_path):
    body = (
        "import sys\nsys.stdin.read()\n"
        "import json\n"
        "print(json.dumps({'type':'token','text':'Hel'}))\n"
        "print(json.dumps({'type':'token','text':'lo'}))\n"
        "print(json.dumps({'type':'run_completed','final_output':'Hello','turns':1}))\n"
    )
    eng = _opencode_engine(tmp_path, body)
    events = _collect(eng, _request(tmp_path))
    kinds = [e["event"] for e in events]
    assert EventName.TOKEN.value in kinds
    assert kinds.count(EventName.RUN_COMPLETED.value) == 1
    completed = [e for e in events if e["event"] == EventName.RUN_COMPLETED.value][0]
    assert completed["data"]["final_output"] == "Hello"


def test_opencode_translates_tool_events(tmp_path):
    body = (
        "import sys;sys.stdin.read()\nimport json\n"
        "print(json.dumps({'type':'tool_call','id':'c1','name':'read_file','arguments':{'path':'a'}}))\n"
        "print(json.dumps({'type':'tool_result','id':'c1','name':'read_file','output':'data'}))\n"
        "print(json.dumps({'type':'run_completed','final_output':'done'}))\n"
    )
    eng = _opencode_engine(tmp_path, body)
    events = _collect(eng, _request(tmp_path))
    tc = [e for e in events if e["event"] == EventName.TOOL_CALL.value][0]
    assert tc["data"]["call_id"] == "c1"
    assert isinstance(tc["data"]["arguments"], str)  # JSON string per contract
    tr = [e for e in events if e["event"] == EventName.TOOL_RESULT.value][0]
    assert tr["data"]["output"] == "data"


def test_opencode_malformed_line_is_skipped(tmp_path):
    body = "import sys;sys.stdin.read()\nprint('not json')\nprint('{\"type\":\"run_completed\",\"final_output\":\"x\"}')\n"
    eng = _opencode_engine(tmp_path, body)
    events = _collect(eng, _request(tmp_path))
    assert [e for e in events if e["event"] == EventName.RUN_COMPLETED.value]


def test_opencode_conversation_mirror(tmp_path):
    eng = _opencode_engine(tmp_path, "print('x')")
    sid = f"mirror-{os.getpid()}-{id(eng)}"  # unique: the mirror lives in the
    wid = f"ws-{os.getpid()}-{id(eng)}"       # shared backend data dir
    async def go():
        assert await eng.get_conversation_items(sid, wid) == []
        await eng.replace_conversation(sid, wid, "SUMMARY")
        items = await eng.get_conversation_items(sid, wid)
        assert items and "SUMMARY" in json.dumps(items)
    asyncio.run(go())


# ---------------------------------------------------------------------------
# Claude Code adapter (fake CLI) + Anthropic gate
# ---------------------------------------------------------------------------

def _claude_engine(tmp_path, body, base_url="https://api.anthropic.com/v1"):
    cli = _write_fake_cli(tmp_path, "claude.py", body)
    cfg = AppConfig.model_validate({
        "llm": {"base_url": base_url, "api_key": "k"},
        "engine": {"kind": "claude_code", "claude_code": {"binary": cli}},
    })
    return ClaudeCodeEngine(EngineContext(config=cfg, db=None))


def test_claude_code_gate_blocks_openai_endpoint(tmp_path):
    eng = _claude_engine(tmp_path, "print('1.0')", base_url="http://127.0.0.1:1234/v1")
    with pytest.raises(EngineError) as ei:
        asyncio.run(eng.start())
    assert "endpoint" in str(ei.value).lower() or ei.value.code == "engine_endpoint_unsupported"


def test_claude_code_gate_allows_with_flag(tmp_path):
    cli = _write_fake_cli(tmp_path, "claude.py", "print('1.0')")
    cfg = AppConfig.model_validate({
        "llm": {"base_url": "http://127.0.0.1:1234/v1"},
        "engine": {"kind": "claude_code", "claude_code": {"binary": cli, "allow_openai_endpoint": True}},
    })
    eng = ClaudeCodeEngine(EngineContext(config=cfg, db=None))
    asyncio.run(eng.start())  # allowed after explicit opt-in


def test_claude_code_gate_allows_via_env_despite_config_pinning(tmp_path, monkeypatch):
    # Regression (issue #57): a seeded config.yaml pinning allow_openai_endpoint
    # =false must not override the operator's deployment env var.
    cli = _write_fake_cli(tmp_path, "claude.py", "print('1.0')")
    monkeypatch.setenv("CLAUDE_CODE_ALLOW_OPENAI_ENDPOINT", "true")
    cfg = AppConfig.model_validate({
        "llm": {"base_url": "http://192.168.1.172:8731/"},
        "engine": {"kind": "claude_code", "claude_code": {"binary": cli, "allow_openai_endpoint": False}},
    })
    eng = ClaudeCodeEngine(EngineContext(config=cfg, db=None))
    asyncio.run(eng.start())  # env wins over the pinned config field


def test_claude_code_argv_includes_verbose(tmp_path):
    # Regression: `claude -p --output-format stream-json` REQUIRES --verbose;
    # without it the CLI exits 1 with empty stdout and the chat shows
    # "Completed · no output" instead of a reply.
    eng = _claude_engine(tmp_path, "print('x')")
    argv = eng._argv(_request(tmp_path))
    assert "--verbose" in argv
    assert argv[argv.index("--output-format") + 1] == "stream-json"


def test_claude_code_cli_failure_surfaces_as_run_failed(tmp_path):
    body = (
        "import sys\nsys.stdin.read()\n"
        "sys.stderr.write('Error: stream-json requires --verbose\\n')\n"
        "sys.exit(1)\n"
    )
    eng = _claude_engine(tmp_path, body)
    events = _collect(eng, _request(tmp_path))
    kinds = [e["event"] for e in events]
    assert EventName.RUN_FAILED.value in kinds
    assert EventName.RUN_COMPLETED.value not in kinds
    failed = [e for e in events if e["event"] == EventName.RUN_FAILED.value][0]
    assert "requires --verbose" in failed["data"]["error"]["message"]
    assert failed["data"]["error"]["code"] == "engine_config_error"


def test_claude_code_passes_configured_model(tmp_path):
    # Regression: without --model/ANTHROPIC_MODEL the CLI uses its built-in
    # default (claude-opus-5-5), which a custom gateway does not serve, and
    # every run fails with "issue with the selected model".
    cli = _write_fake_cli(tmp_path, "claude.py", "print('1.0')")
    cfg = AppConfig.model_validate({
        "llm": {"base_url": "https://api.anthropic.com/v1", "model": "my-local-model"},
        "engine": {"kind": "claude_code", "claude_code": {"binary": cli}},
    })
    eng = ClaudeCodeEngine(EngineContext(config=cfg, db=None))
    argv = eng._argv(_request(tmp_path))
    assert "--model" in argv
    assert argv[argv.index("--model") + 1] == "my-local-model"
    env = eng._env()
    assert env["ANTHROPIC_MODEL"] == "my-local-model"
    assert env["ANTHROPIC_SMALL_FAST_MODEL"] == "my-local-model"


def test_claude_code_base_url_normalized_for_cli(tmp_path):
    # Regression: llm.base_url follows the OpenAI convention (ends /v1), but
    # Claude Code appends /v1/messages itself -- passing the /v1 form makes it
    # hit /v1/v1/messages and fail with a misleading "selected model" error.
    cli = _write_fake_cli(tmp_path, "claude.py", "print('1.0')")
    for raw, want in (
        ("http://192.168.1.172:8731/v1", "http://192.168.1.172:8731/"),
        ("http://192.168.1.172:8731/v1/", "http://192.168.1.172:8731/"),
        ("http://192.168.1.172:8731/", "http://192.168.1.172:8731/"),
        ("https://api.anthropic.com/v1", "https://api.anthropic.com/"),
        ("", ""),
    ):
        cfg = AppConfig.model_validate({
            "llm": {"base_url": raw},
            "engine": {"kind": "claude_code", "claude_code": {"binary": cli}},
        })
        eng = ClaudeCodeEngine(EngineContext(config=cfg, db=None))
        assert eng._env()["ANTHROPIC_BASE_URL"] == want, raw


def test_engine_config_error_is_terminal():
    # A rejected model/endpoint must finalize the run, not replan forever.
    from claudio_backend.runs.manager import _TERMINAL_FAILURE_CODES
    assert "engine_config_error" in _TERMINAL_FAILURE_CODES


def test_claude_code_result_is_error_fails_run(tmp_path):
    # The CLI exits 0 even on a failed run (unknown model, auth problem);
    # the failure lives in the result line's is_error flag.
    body = (
        "import sys;sys.stdin.read()\nimport json\n"
        "print(json.dumps({'type':'result','result':'There is an issue with the selected model','is_error':True,'usage':{}}))\n"
    )
    eng = _claude_engine(tmp_path, body)
    events = _collect(eng, _request(tmp_path))
    kinds = [e["event"] for e in events]
    assert EventName.RUN_FAILED.value in kinds
    assert EventName.RUN_COMPLETED.value not in kinds
    failed = [e for e in events if e["event"] == EventName.RUN_FAILED.value][0]
    assert "selected model" in failed["data"]["error"]["message"]
    assert failed["data"]["error"]["code"] == "engine_config_error"


def test_claude_code_streams_events(tmp_path):
    body = (
        "import sys;sys.stdin.read()\nimport json\n"
        "print(json.dumps({'type':'assistant','message':{'content':[{'type':'text','text':'Hi'}]}}))\n"
        "print(json.dumps({'type':'result','result':'Hi','usage':{'input_tokens':5,'output_tokens':2},'total_cost_usd':0.0,'num_turns':1}))\n"
    )
    eng = _claude_engine(tmp_path, body)
    events = _collect(eng, _request(tmp_path))
    kinds = [e["event"] for e in events]
    assert EventName.TOKEN.value in kinds
    completed = [e for e in events if e["event"] == EventName.RUN_COMPLETED.value][0]
    assert completed["data"]["usage"]["input_tokens"] == 5


# ---------------------------------------------------------------------------
# Shared engine contract suite (parametrized across the three engines)
# ---------------------------------------------------------------------------

@pytest.fixture(params=["adk", "opencode", "claude_code"])
def any_engine(request, tmp_path, monkeypatch):
    kind = request.param
    if kind == "adk":
        # ADK is in-process; drive it with a scripted engine stand-in is out of
        # scope here -- assert its descriptor/contract surface instead.
        eng = create_engine(AppConfig(), db=None)
        return kind, eng
    if kind == "opencode":
        cli = _write_fake_cli(tmp_path, "oc.py",
            "import sys;sys.stdin.read()\nimport json\n"
            "print(json.dumps({'type':'token','text':'x'}))\n"
            "print(json.dumps({'type':'run_completed','final_output':'x'}))\n")
        cfg = AppConfig.model_validate({"engine": {"kind": "opencode", "opencode": {"binary": cli}}})
        return kind, OpenCodeEngine(EngineContext(config=cfg, db=None))
    cli = _write_fake_cli(tmp_path, "cc.py",
        "import sys;sys.stdin.read()\nimport json\n"
        "print(json.dumps({'type':'result','result':'x','usage':{}}))\n")
    cfg = AppConfig.model_validate({
        "llm": {"base_url": "https://api.anthropic.com/v1"},
        "engine": {"kind": "claude_code", "claude_code": {"binary": cli}},
    })
    return kind, ClaudeCodeEngine(EngineContext(config=cfg, db=None))


def test_contract_engine_id(any_engine):
    kind, eng = any_engine
    assert eng.descriptor.id == kind


def test_contract_single_terminal_event(any_engine):
    kind, eng = any_engine
    if kind == "adk":
        pytest.skip("ADK terminal-event contract covered by test_e2e.py")
    events = _collect(eng, _request(Path.cwd()))
    terminals = [e for e in events if e["event"] in (EventName.RUN_COMPLETED.value, EventName.RUN_FAILED.value)]
    assert len(terminals) == 1


def test_contract_events_pass_encode(any_engine):
    from claudio_core.events import encode_event
    kind, eng = any_engine
    if kind == "adk":
        pytest.skip("ADK event encoding covered by test_event_protocol.py")
    for e in _collect(eng, _request(Path.cwd())):
        if e["event"] in (EventName.RUN_COMPLETED.value, EventName.RUN_FAILED.value,
                          EventName.TOKEN.value, EventName.TOOL_CALL.value,
                          EventName.TOOL_RESULT.value):
            encode_event(e["event"], e["data"])  # must not raise
