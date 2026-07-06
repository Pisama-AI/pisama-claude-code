"""Agent identity capture and forwarding.

Claude Code sets agent_id/agent_type on hook payloads only when the tool call
runs inside a subagent (Task/workflow fan-out). These tests pin that the
identity survives every stage: the capture hook's local record, normalization,
the batch sync payload, and the real-time OTLP span.
"""

import json
from pathlib import Path

from pisama_claude_code.cli import normalize_trace, prepare_sync_payload
from pisama_claude_code.hooks.capture_hook import _capture
from pisama_claude_code.otel_export import convert_trace_to_otel_dict


SUBAGENT_HOOK = {
    "session_id": "sess-1",
    "tool_name": "Read",
    "tool_input": {"file_path": "/repo/wf.yml"},
    "agent_id": "a5b909cb2acc79e4c",
    "agent_type": "workflow-subagent",
    "cwd": "/repo",
}

MAIN_HOOK = {
    "session_id": "sess-1",
    "tool_name": "Bash",
    "tool_input": {"command": "ls"},
    "cwd": "/repo",
}


def _captured_record(monkeypatch, tmp_path: Path, hook_data: dict) -> dict:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # "pre" hooks skip transcript reading and real-time forwarding, so the
    # test exercises only the capture/record path.
    _capture(hook_data, "pre")
    jsonl = next((tmp_path / ".claude" / "pisama" / "traces").glob("traces-*.jsonl"))
    return json.loads(jsonl.read_text().splitlines()[-1])


def test_capture_records_subagent_identity(monkeypatch, tmp_path):
    record = _captured_record(monkeypatch, tmp_path, dict(SUBAGENT_HOOK))
    assert record["agent_id"] == "a5b909cb2acc79e4c"
    assert record["agent_type"] == "workflow-subagent"
    assert record["is_sidechain"] is True


def test_capture_records_main_loop_as_non_sidechain(monkeypatch, tmp_path):
    record = _captured_record(monkeypatch, tmp_path, dict(MAIN_HOOK))
    assert record["agent_id"] is None
    assert record["agent_type"] is None
    assert record["is_sidechain"] is False


def test_normalize_trace_passes_agent_identity_through():
    normalized = normalize_trace({
        "tool_name": "Read",
        "timestamp": "2026-07-05T10:00:00+00:00",
        "hook_type": "post",
        "session_id": "sess-1",
        "agent_id": "a5b909cb2acc79e4c",
        "agent_type": "workflow-subagent",
        "is_sidechain": True,
    })
    assert normalized["agent_id"] == "a5b909cb2acc79e4c"
    assert normalized["agent_type"] == "workflow-subagent"
    assert normalized["is_sidechain"] is True


def test_normalize_trace_infers_sidechain_for_legacy_records():
    """Records captured before is_sidechain existed infer it from agent_id."""
    with_agent = normalize_trace({
        "tool_name": "Read",
        "timestamp": "t",
        "session_id": "s",
        "agent_id": "a1",
    })
    assert with_agent["is_sidechain"] is True
    without_agent = normalize_trace({
        "tool_name": "Read",
        "timestamp": "t",
        "session_id": "s",
    })
    assert without_agent["agent_id"] is None
    assert without_agent["is_sidechain"] is False


def test_normalize_trace_recovers_identity_from_raw_hook_payload():
    """Pre-0.6.3 records lack top-level agent fields but preserved the hook
    payload under "raw" — identity must survive a re-sync of those sessions."""
    normalized = normalize_trace({
        "tool_name": "Read",
        "timestamp": "t",
        "session_id": "s",
        "raw": {
            "agent_id": "a5b909cb2acc79e4c",
            "agent_type": "workflow-subagent",
            "tool_name": "Read",
        },
    })
    assert normalized["agent_id"] == "a5b909cb2acc79e4c"
    assert normalized["agent_type"] == "workflow-subagent"
    assert normalized["is_sidechain"] is True


def test_sync_payload_includes_agent_identity():
    payload = prepare_sync_payload(
        [
            {
                "timestamp": "2026-07-05T10:00:00+00:00",
                "tool_name": "Read",
                "hook_type": "PostToolUse",
                "session_id": "sess-1",
                "working_dir": "/repo",
                "tool_input": {"file_path": "/repo/wf.yml"},
                "agent_id": "a5b909cb2acc79e4c",
                "agent_type": "workflow-subagent",
                "is_sidechain": True,
            }
        ],
        include_outputs=False,
    )
    sent = payload["traces"][0]
    assert sent["agent_id"] == "a5b909cb2acc79e4c"
    assert sent["agent_type"] == "workflow-subagent"
    assert sent["is_sidechain"] is True


def test_otel_span_carries_agent_identity():
    span = convert_trace_to_otel_dict({
        "session_id": "sess-1",
        "timestamp": "2026-07-05T10:00:00+00:00",
        "hook_type": "post",
        "tool_name": "Read",
        "tool_input": {"file_path": "/repo/wf.yml"},
        "agent_id": "a5b909cb2acc79e4c",
        "agent_type": "workflow-subagent",
        "is_sidechain": True,
    })
    attrs = {a["key"]: a["value"] for a in span["attributes"]}
    assert attrs["gen_ai.agent.id"]["stringValue"] == "a5b909cb2acc79e4c"
    assert attrs["gen_ai.agent.name"]["stringValue"] == "workflow-subagent"
    state = json.loads(attrs["gen_ai.state"]["stringValue"])
    assert state["agent_id"] == "a5b909cb2acc79e4c"
    assert state["is_sidechain"] is True


def test_otel_span_omits_agent_attrs_for_main_loop():
    span = convert_trace_to_otel_dict({
        "session_id": "sess-1",
        "timestamp": "2026-07-05T10:00:00+00:00",
        "hook_type": "post",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
        "agent_id": None,
        "agent_type": None,
        "is_sidechain": False,
    })
    attr_keys = {a["key"] for a in span["attributes"]}
    assert "gen_ai.agent.id" not in attr_keys
    assert "gen_ai.agent.name" not in attr_keys
