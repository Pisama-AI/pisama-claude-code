"""Tests for transcript reconstruction in capture_hook (input/reasoning/output)."""

import json

from pisama_claude_code.hooks.capture_hook import (
    _user_text_from_entry,
    get_last_assistant_message,
    get_last_user_message,
)


def _write(tmp_path, entries):
    p = tmp_path / "transcript.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    return str(p)


def test_user_text_helper_distinguishes_input_from_tool_results():
    assert (
        _user_text_from_entry({"type": "user", "message": {"role": "user", "content": "hello"}})
        == "hello"
    )
    assert (
        _user_text_from_entry(
            {"type": "user", "message": {"content": [{"type": "text", "text": "hi there"}]}}
        )
        == "hi there"
    )
    # tool results are NOT user input
    assert (
        _user_text_from_entry(
            {"type": "user", "message": {"content": [{"type": "tool_result", "content": "x"}]}}
        )
        is None
    )
    # interrupts / system messages are skipped
    assert (
        _user_text_from_entry({"type": "user", "message": {"content": "[Request interrupted]"}})
        is None
    )
    assert _user_text_from_entry({"type": "assistant", "message": {}}) is None


def test_collects_current_turn_input_reasoning_output(tmp_path):
    entries = [
        {"type": "user", "message": {"role": "user", "content": "old turn"}},
        {
            "type": "assistant",
            "message": {
                "model": "claude-x",
                "content": [{"type": "thinking", "thinking": "old reasoning", "signature": "s"}],
            },
        },
        # current turn boundary:
        {"type": "user", "message": {"role": "user", "content": "do the thing"}},
        {
            "type": "assistant",
            "message": {
                "model": "claude-x",
                "usage": {"input_tokens": 5, "output_tokens": 9, "cache_read_input_tokens": 1000},
                "content": [{"type": "thinking", "thinking": "step one", "signature": "s"}],
            },
        },
        {
            "type": "assistant",
            "message": {"model": "claude-x", "content": [{"type": "text", "text": "Doing it."}]},
        },
        {
            "type": "assistant",
            "message": {
                "model": "claude-x",
                "content": [
                    {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}
                ],
            },
        },
        # tool result must not be treated as input
        {
            "type": "user",
            "message": {
                "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "file.txt"}]
            },
        },
    ]
    am = get_last_assistant_message(_write(tmp_path, entries))
    assert am["input"] == "do the thing"  # boundary input, not "old turn", not tool_result
    assert am["reasoning"] == "step one"  # current turn's thinking only
    assert am["output"] == "Doing it."
    assert am["model"] == "claude-x"
    assert am["usage"]["cache_read_input_tokens"] == 1000  # cache tokens preserved
    assert len(am["tool_calls"]) == 1


def test_empty_thinking_yields_no_reasoning(tmp_path):
    # Claude Code persists thinking blocks with empty text + a signature only,
    # so reasoning is genuinely unavailable from the transcript.
    entries = [
        {"type": "user", "message": {"role": "user", "content": "q"}},
        {
            "type": "assistant",
            "message": {
                "model": "m",
                "usage": {},
                "content": [{"type": "thinking", "thinking": "", "signature": "abc"}],
            },
        },
        {
            "type": "assistant",
            "message": {"model": "m", "content": [{"type": "text", "text": "answer"}]},
        },
    ]
    am = get_last_assistant_message(_write(tmp_path, entries))
    assert am["reasoning"] is None
    assert am["output"] == "answer"


def test_input_found_when_user_message_scrolled_past_old_window(tmp_path):
    # 100 tool_use/tool_result pairs push the user message past the old 200-line
    # lookback; the widened window must still recover it.
    entries = [{"type": "user", "message": {"role": "user", "content": "the real question"}}]
    for i in range(100):
        entries.append(
            {
                "type": "assistant",
                "message": {
                    "model": "m",
                    "content": [{"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {}}],
                },
            }
        )
        entries.append(
            {
                "type": "user",
                "message": {
                    "content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": "x"}]
                },
            }
        )
    am = get_last_assistant_message(_write(tmp_path, entries))
    assert am["input"] == "the real question"
    assert get_last_user_message(_write(tmp_path, entries))["content"] == "the real question"
