"""TERMINATE / BLOCK must be durable across tool calls.

The guardian hook runs as a FRESH subprocess per tool call. A terminate that
lives only in an in-memory ``set`` evaporates on the next call, so the run
proceeds if detection does not re-fire. These tests simulate the fresh
subprocess with a NEW adapter/guardian instance sharing the same ``pisama_dir``
and assert the block survives.
"""

import pytest

from pisama_core.injection import EnforcementLevel

from pisama_claude_code.adapter import ClaudeCodeAdapter
from pisama_claude_code.guardian import Guardian, GuardianConfig


def test_terminate_persists_to_disk(temp_pisama_dir):
    a1 = ClaudeCodeAdapter(pisama_dir=temp_pisama_dir)
    a1.inject_fix(
        directive="stop",
        level=EnforcementLevel.TERMINATE,
        session_id="sess-A",
        metadata={"severity": 95, "issues": ["loop"]},
    )
    # Fresh instance == fresh subprocess: it must still see the terminate.
    a2 = ClaudeCodeAdapter(pisama_dir=temp_pisama_dir)
    assert a2.is_session_blocked("sess-A") is True
    rec = a2.get_block_record("sess-A")
    assert rec is not None
    assert rec["level"] == "terminate"
    assert rec["severity"] == 95


def test_block_persists_and_unblock_clears(temp_pisama_dir):
    a1 = ClaudeCodeAdapter(pisama_dir=temp_pisama_dir)
    a1.inject_fix(
        directive="review",
        level=EnforcementLevel.BLOCK,
        session_id="sess-B",
        metadata={"severity": 70, "issues": ["coordination"]},
    )
    a2 = ClaudeCodeAdapter(pisama_dir=temp_pisama_dir)
    assert a2.is_session_blocked("sess-B") is True
    assert a2.get_block_record("sess-B")["level"] == "block"

    # Acknowledge (unblock) — a third fresh instance must see it cleared.
    assert a2.unblock_session("sess-B") is True
    a3 = ClaudeCodeAdapter(pisama_dir=temp_pisama_dir)
    assert a3.is_session_blocked("sess-B") is False
    assert a3.get_block_record("sess-B") is None


def test_non_blocking_levels_do_not_persist(temp_pisama_dir):
    a1 = ClaudeCodeAdapter(pisama_dir=temp_pisama_dir)
    a1.inject_fix(
        directive="suggestion",
        level=EnforcementLevel.SUGGEST,
        session_id="sess-C",
        metadata={"severity": 45},
    )
    a2 = ClaudeCodeAdapter(pisama_dir=temp_pisama_dir)
    assert a2.is_session_blocked("sess-C") is False


@pytest.mark.asyncio
async def test_guardian_reblocks_terminated_session_without_detection(temp_pisama_dir):
    """A terminated session is re-blocked on the next call even for a benign
    tool that would NOT trip any detector — proving the run truly ends."""
    # First: record a terminate via one adapter (as a prior tool call would).
    seed = ClaudeCodeAdapter(pisama_dir=temp_pisama_dir)
    seed.inject_fix(
        directive="stop",
        level=EnforcementLevel.TERMINATE,
        session_id="sess-D",
        metadata={"severity": 99, "issues": ["repeated violations"]},
    )

    # Fresh guardian (fresh subprocess) analyzing a totally benign tool call.
    guardian = Guardian(
        config=GuardianConfig(enabled=True, mode="manual"),
        pisama_dir=temp_pisama_dir,
    )
    benign = {
        "tool_name": "Read",
        "tool_input": {"file_path": "/tmp/readme.md"},
        "session_id": "sess-D",
    }
    result = await guardian.analyze(benign, session_id="sess-D")
    assert result.should_block is True
    assert "terminate" in result.action_taken


@pytest.mark.asyncio
async def test_guardian_allows_unblocked_benign_session(temp_pisama_dir):
    """Sanity: a session with no durable block runs detection normally and a
    benign single Read does not block."""
    guardian = Guardian(
        config=GuardianConfig(enabled=True, mode="manual"),
        pisama_dir=temp_pisama_dir,
    )
    benign = {
        "tool_name": "Read",
        "tool_input": {"file_path": "/tmp/readme.md"},
        "session_id": "sess-E",
    }
    result = await guardian.analyze(benign, session_id="sess-E")
    assert result.should_block is False
