"""Security and metadata contracts backed by real files and subprocesses."""

from __future__ import annotations

import importlib.metadata
import json
import os
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from pisama_claude_code import __version__
from pisama_claude_code.cli import api_url, prepare_sync_payload
from pisama_claude_code.lite_config import LiteConfig
from pisama_claude_code.lite_storage import LiteStorage
from pisama_claude_code.private_files import append_private_text, write_private_text
from pisama_claude_code.proxy import store_record
from pisama_claude_code.trace_types import SkillSource, TraceType, classify_trace


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.mark.skipif(os.name == "nt", reason="Windows does not expose POSIX mode bits")
def test_private_file_helpers_restrict_and_atomically_replace(tmp_path):
    target = tmp_path / "private" / "config.json"
    write_private_text(target, '{"value": 1}')
    write_private_text(target, '{"value": 2}')
    append_private_text(target, "\n")

    assert target.read_text() == '{"value": 2}\n'
    assert _mode(target.parent) == 0o700
    assert _mode(target) == 0o600
    assert list(target.parent.glob(f".{target.name}.*")) == []


@pytest.mark.skipif(os.name == "nt", reason="Windows does not expose POSIX mode bits")
def test_every_sensitive_local_store_is_private(tmp_path):
    lite_config_path = tmp_path / "lite" / "config.yaml"
    LiteConfig(
        anthropic_api_key="private-anthropic-key",
        platform_api_key="private-platform-key",
    ).save(lite_config_path)
    lite_db_path = tmp_path / "lite-db" / "pisama.db"
    LiteStorage(lite_db_path)
    proxy_dir = tmp_path / "proxy"
    store_record({"ts": "2026-07-23T12:00:00+00:00", "reasoning": "private"}, proxy_dir)
    proxy_record = next(proxy_dir.glob("calls-*.jsonl"))

    assert _mode(lite_config_path) == 0o600
    assert _mode(lite_config_path.parent) == 0o700
    assert _mode(lite_db_path) == 0o600
    assert _mode(lite_db_path.parent) == 0o700
    assert _mode(proxy_record) == 0o600
    assert _mode(proxy_dir) == 0o700


@pytest.mark.skipif(os.name == "nt", reason="Windows does not expose POSIX mode bits")
def test_capture_does_not_archive_untokenized_raw_payload(tmp_path):
    """The old ``raw`` copy bypassed tokenization and duplicated every secret."""
    code = """
from pisama_claude_code.hooks.capture_hook import _capture
_capture({
    "session_id": "private-session",
    "tool_name": "Bash",
    "tool_input": {"command": "printf safe"},
    "tool_result": "captured output",
    "agent_id": "worker-1",
}, "pre")
"""
    environment = {
        **os.environ,
        "HOME": str(tmp_path),
        "USERPROFILE": str(tmp_path),
        "PISAMA_TOKENIZATION": "0",
    }
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    traces_dir = tmp_path / ".claude" / "pisama" / "traces"
    jsonl_path = next(traces_dir.glob("traces-*.jsonl"))
    record = json.loads(jsonl_path.read_text())
    assert "raw" not in record
    assert record["agent_id"] == "worker-1"
    assert _mode(traces_dir) == 0o700
    assert _mode(jsonl_path) == 0o600
    assert _mode(traces_dir / "pisama.db") == 0o600


def _create_guardian_db(home: Path, rows: list[tuple[str, str]]) -> None:
    db_path = home / ".claude" / "pisama" / "traces" / "pisama.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE traces ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, tool_name TEXT)"
        )
        connection.executemany(
            "INSERT INTO traces (session_id, tool_name) VALUES (?, ?)",
            rows,
        )


def _run_fallback_guardian(
    home: Path, session_id: str, tool_name: str
) -> subprocess.CompletedProcess:
    code = """
import json
import sys
from pisama_claude_code.hooks.guardian_hook import _fallback_detection
payload = json.loads(sys.stdin.read())
_fallback_detection(payload["hook"], payload["session_id"])
"""
    return subprocess.run(
        [sys.executable, "-c", code],
        input=json.dumps({"hook": {"tool_name": tool_name}, "session_id": session_id}),
        env={**os.environ, "HOME": str(home), "USERPROFILE": str(home)},
        capture_output=True,
        text=True,
        check=False,
    )


def test_fallback_guardian_never_combines_independent_sessions(tmp_path):
    _create_guardian_db(
        tmp_path,
        [("other-session", "Bash")] * 10 + [("target-session", "Read")] * 3,
    )
    allowed = _run_fallback_guardian(tmp_path, "target-session", "Read")
    assert allowed.returncode == 0


def test_fallback_guardian_blocks_a_real_same_session_loop(tmp_path):
    _create_guardian_db(tmp_path, [("target-session", "Read")] * 4)
    blocked = _run_fallback_guardian(tmp_path, "target-session", "Read")
    assert blocked.returncode == 1
    assert "Loop detected" in blocked.stderr


def test_versions_and_api_prefix_come_from_canonical_package_metadata():
    assert importlib.metadata.version("pisama-claude-code") == __version__
    assert prepare_sync_payload([], include_outputs=False)["version"] == __version__
    assert (
        api_url(
            {"api_url": "https://api.pisama.ai/api/v1/"},
            "/auth/token",
        )
        == "https://api.pisama.ai/api/v1/auth/token"
    )


@pytest.mark.parametrize(
    ("path", "working_dir", "expected_source"),
    [
        (
            "/workspace/repo/.claude/skills/reviewer/SKILL.md",
            "/workspace/repo",
            SkillSource.PROJECT,
        ),
        (
            "/home/dev/.claude/skills/reviewer/SKILL.md",
            "/workspace/repo",
            SkillSource.PERSONAL,
        ),
        (
            r"C:\Users\dev\.claude\skills\reviewer\SKILL.md",
            r"C:\workspace\repo",
            SkillSource.PERSONAL,
        ),
    ],
)
def test_skill_classification_is_cross_platform(path, working_dir, expected_source):
    trace = classify_trace(
        {
            "tool_name": "Read",
            "tool_input": {"file_path": path},
            "working_dir": working_dir,
            "timestamp": "2026-07-23T12:00:00Z",
        }
    )
    assert trace.trace_type is TraceType.SKILL
    assert trace.skill_name == "reviewer"
    assert trace.skill_source is expected_source
    assert trace.timestamp.isoformat() == "2026-07-23T12:00:00+00:00"


def test_trace_classification_tolerates_malformed_external_fields():
    trace = classify_trace(
        {
            "tool_name": None,
            "tool_input": "not-a-mapping",
            "timestamp": "not-a-timestamp",
            "metadata": ["not-a-mapping"],
        }
    )
    assert trace.trace_type is TraceType.TOOL
    assert trace.tool_name == "unknown"
    assert trace.metadata == {}
