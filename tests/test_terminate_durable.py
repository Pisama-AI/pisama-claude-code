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
async def test_mid_severity_block_is_durable(temp_pisama_dir):
    """A sev 60-79 manual block (enforcement level DIRECT, which inject_fix would
    NOT persist) must still be durable: a fresh guardian re-blocks the session on
    a benign call. Regression for the severity-band persistence hole."""
    from pisama_claude_code.guardian import GuardianResult

    guardian = Guardian(
        config=GuardianConfig(enabled=True, mode="manual"),
        pisama_dir=temp_pisama_dir,
    )
    # Directly drive the manual-mode handler at sev 70 (DIRECT level) to model a
    # mid-severity block without depending on which detector produces 70.
    result = guardian._handle_manual_mode(70, ["mid loop"], "break_loop", "mid-sess")
    assert result.should_block is True
    # Persist as analyze() would on should_block.
    guardian.adapter.record_block("mid-sess", "block", 70, ["mid loop"], result.message)

    # Fresh instance (fresh subprocess) must still see the block on a benign tool.
    fresh = Guardian(config=GuardianConfig(enabled=True, mode="manual"), pisama_dir=temp_pisama_dir)
    benign = {"tool_name": "Read", "tool_input": {"file_path": "/tmp/x"}, "session_id": "mid-sess"}
    out = await fresh.analyze(benign, session_id="mid-sess")
    assert out.should_block is True


@pytest.mark.asyncio
async def test_analyze_persists_durable_block_on_real_detection(temp_pisama_dir):
    """End-to-end: a REAL loop detection through analyze() (no manual
    record_block) persists a durable record, and a FRESH guardian then re-blocks
    a benign call. Proves persistence comes from analyze()'s should_block hook."""
    g = Guardian(
        config=GuardianConfig(enabled=True, mode="manual", pattern_window=20),
        pisama_dir=temp_pisama_dir,
    )
    hook = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "session_id": "real-persist"}
    for _ in range(7):
        r = await g.analyze(hook, session_id="real-persist")
        if r.should_block:
            break
    # A durable record must exist purely from analyze() persisting on should_block.
    assert ClaudeCodeAdapter(pisama_dir=temp_pisama_dir).get_block_record("real-persist") is not None
    # Fresh guardian re-blocks a totally benign tool.
    fresh = Guardian(config=GuardianConfig(enabled=True, mode="manual"), pisama_dir=temp_pisama_dir)
    out = await fresh.analyze(
        {"tool_name": "Read", "tool_input": {"file_path": "/tmp/z"}, "session_id": "real-persist"},
        session_id="real-persist",
    )
    assert out.should_block is True


@pytest.mark.asyncio
async def test_repeated_blocks_escalate_to_durable_terminate(temp_pisama_dir):
    """Repeated blocked tool calls durably escalate BLOCK -> TERMINATE across
    fresh subprocesses (the in-memory EnforcementEngine never reaches TERMINATE)."""
    # Seed an initial block.
    ClaudeCodeAdapter(pisama_dir=temp_pisama_dir).record_block(
        "esc-sess", "block", 65, ["loop"], "blocked", block_count=1
    )
    levels = []
    # Each fresh guardian.analyze on a benign tool = one re-block (agent kept going).
    for _ in range(4):
        g = Guardian(config=GuardianConfig(enabled=True, mode="manual"), pisama_dir=temp_pisama_dir)
        out = await g.analyze(
            {"tool_name": "Read", "tool_input": {}, "session_id": "esc-sess"},
            session_id="esc-sess",
        )
        assert out.should_block is True
        levels.append(ClaudeCodeAdapter(pisama_dir=temp_pisama_dir).get_block_record("esc-sess")["level"])
    # Must have durably escalated to terminate by the cap.
    assert "terminate" in levels
    assert ClaudeCodeAdapter(pisama_dir=temp_pisama_dir).get_block_record("esc-sess")["level"] == "terminate"


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
