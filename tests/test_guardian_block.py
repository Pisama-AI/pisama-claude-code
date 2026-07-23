"""Tests for the Claude Code PreToolUse block signal.

Regression guard for the exit-code bug: a block was signalled with
``sys.exit(1)``, which Claude Code treats as a NON-blocking error — the tool
proceeds. The correct signal is exit code 2 (stderr -> Claude) and/or a stdout
JSON ``hookSpecificOutput.permissionDecision: "deny"``. These tests pin BOTH.
"""

import json

import pytest

from pisama_claude_code.hooks import guardian_hook


def test_emit_block_uses_exit_code_2(capsys):
    """A block MUST exit 2 (not 1); exit 1 is non-blocking in Claude Code."""
    with pytest.raises(SystemExit) as exc:
        guardian_hook._emit_block("loop detected")
    assert exc.value.code == guardian_hook.BLOCK_EXIT_CODE == 2


def test_emit_block_stdout_is_deny_decision(capsys):
    """stdout carries the structured PreToolUse deny decision with a reason."""
    with pytest.raises(SystemExit):
        guardian_hook._emit_block("severity 82/100: loop")
    out = capsys.readouterr()
    payload = json.loads(out.out)
    hso = payload["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    # Claude Code's PreToolUse deny value is "deny" — never "block".
    assert hso["permissionDecision"] == "deny"
    assert "loop" in hso["permissionDecisionReason"]
    # stderr also carries the reason (the exit-2 blocking channel).
    assert "loop" in out.err


def test_emit_block_defaults_reason_when_empty(capsys):
    with pytest.raises(SystemExit):
        guardian_hook._emit_block("")
    payload = json.loads(capsys.readouterr().out)
    assert payload["hookSpecificOutput"]["permissionDecisionReason"]


def test_main_blocks_when_guardian_says_block(monkeypatch, capsys):
    """The hook entrypoint emits a real block when analyze_sync blocks."""

    class _Result:
        should_block = True
        severity = 90
        issues = ["loop: Read repeated 6x"]
        message = "Critical loop — action blocked."

    # Patch the guardian analysis used inside main().
    import pisama_claude_code.guardian as guardian_mod

    monkeypatch.setattr(guardian_mod, "analyze_sync", lambda *a, **k: _Result())
    monkeypatch.setattr("sys.stdin", _FakeStdin('{"tool_name":"Read","session_id":"s1"}'))

    with pytest.raises(SystemExit) as exc:
        guardian_hook.main()
    assert exc.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_main_allows_when_guardian_says_allow(monkeypatch, capsys):
    class _Result:
        should_block = False
        severity = 10
        issues = []
        message = None

    import pisama_claude_code.guardian as guardian_mod

    monkeypatch.setattr(guardian_mod, "analyze_sync", lambda *a, **k: _Result())
    monkeypatch.setattr("sys.stdin", _FakeStdin('{"tool_name":"Read","session_id":"s1"}'))

    with pytest.raises(SystemExit) as exc:
        guardian_hook.main()
    assert exc.value.code == 0


class _FakeStdin:
    def __init__(self, data: str):
        self._data = data

    def read(self) -> str:
        return self._data
