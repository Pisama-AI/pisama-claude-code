"""Trace Converter for Claude Code.

Converts Claude Code hook data to universal PISAMA Span format.
"""

import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pisama_core.traces import Platform, Span, SpanKind, SpanStatus


class TraceConverter:
    """Converts Claude Code hook data to universal Span format.

    Claude Code hooks receive JSON with fields like:
    - tool_name / tool: The tool being called
    - tool_input / input: The input parameters
    - tool_output / output: The output (for post hooks)
    - session_id: The Claude session ID

    This converter normalizes these to the universal Span model.
    """

    # Map Claude tool names to SpanKind
    TOOL_KIND_MAP = {
        "Bash": SpanKind.TOOL,
        "Read": SpanKind.TOOL,
        "Write": SpanKind.TOOL,
        "Edit": SpanKind.TOOL,
        "Glob": SpanKind.TOOL,
        "Grep": SpanKind.TOOL,
        "Task": SpanKind.AGENT,  # Subagent spawn
        "WebFetch": SpanKind.TOOL,
        "WebSearch": SpanKind.TOOL,
        "AskUserQuestion": SpanKind.USER_INPUT,
        "mcp__*": SpanKind.TOOL,
    }

    def __init__(self):
        self._session_traces: dict[str, str] = {}  # session_id -> trace_id

    def to_span(
        self,
        hook_data: dict[str, Any],
        hook_type: str = "pre",
    ) -> Span:
        """Convert hook data to universal Span.

        Args:
            hook_data: Raw data from Claude Code hook
            hook_type: "pre" or "post" hook

        Returns:
            Universal Span object
        """
        # Extract tool info
        tool_name = hook_data.get("tool_name") or hook_data.get("tool", "unknown")
        tool_input = hook_data.get("tool_input") or hook_data.get("input", {})
        tool_output = hook_data.get("tool_output") or hook_data.get("output")

        # Session and trace tracking
        session_id = hook_data.get("session_id") or os.environ.get("CLAUDE_SESSION_ID", "unknown")

        # Get or create trace_id for this session
        if session_id not in self._session_traces:
            self._session_traces[session_id] = str(uuid.uuid4())
        trace_id = self._session_traces[session_id]

        # Generate span ID
        span_id = str(uuid.uuid4())

        # Determine span kind
        kind = self._get_span_kind(tool_name)

        # Determine status
        if hook_type == "pre":
            status = SpanStatus.IN_PROGRESS
        else:
            # Post hook - check for errors
            error = hook_data.get("error")
            if error:
                status = SpanStatus.ERROR
            else:
                status = SpanStatus.OK

        # Build attributes
        attributes = {
            "hook_type": hook_type,
            "working_dir": hook_data.get("working_dir") or os.getcwd(),
        }

        # Add Claude-specific attributes
        if "conversation_id" in hook_data:
            attributes["conversation_id"] = hook_data["conversation_id"]
        if "model" in hook_data:
            attributes["model"] = hook_data["model"]

        # Create span
        return Span(
            span_id=span_id,
            parent_id=None,  # Claude hooks don't provide parent info
            trace_id=trace_id,
            name=tool_name,
            kind=kind,
            platform=Platform.CLAUDE_CODE,
            start_time=datetime.now(timezone.utc),
            end_time=datetime.now(timezone.utc) if hook_type == "post" else None,
            status=status,
            attributes=attributes,
            input_data=self._normalize_input(tool_name, tool_input),
            output_data=self._normalize_output(tool_output) if tool_output else None,
            events=[],
            error_message=hook_data.get("error"),
        )

    def _get_span_kind(self, tool_name: str) -> SpanKind:
        """Determine SpanKind from tool name."""
        # Direct match
        if tool_name in self.TOOL_KIND_MAP:
            return self.TOOL_KIND_MAP[tool_name]

        # MCP tools
        if tool_name.startswith("mcp__"):
            return SpanKind.TOOL

        # Default to TOOL
        return SpanKind.TOOL

    def _normalize_input(self, tool_name: str, tool_input: Any) -> dict:
        """Normalize tool input to dict format."""
        if isinstance(tool_input, dict):
            return tool_input
        elif isinstance(tool_input, str):
            return {"value": tool_input}
        elif tool_input is None:
            return {}
        else:
            return {"value": str(tool_input)}

    def _normalize_output(self, tool_output: Any) -> dict:
        """Normalize tool output to dict format."""
        if isinstance(tool_output, dict):
            return tool_output
        elif isinstance(tool_output, str):
            return {"value": tool_output}
        elif tool_output is None:
            return {}
        else:
            return {"value": str(tool_output)}

    def reset_session(self, session_id: str) -> None:
        """Reset trace tracking for a session.

        Called when a session ends to start fresh on next session.
        """
        self._session_traces.pop(session_id, None)

    def get_trace_id(self, session_id: str) -> Optional[str]:
        """Get the trace ID for a session."""
        return self._session_traces.get(session_id)
