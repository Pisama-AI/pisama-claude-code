"""Pytest fixtures for pisama-claude-code tests."""

import tempfile
from pathlib import Path

import pytest
from pisama_core.traces import Platform, Span, SpanKind


@pytest.fixture
def temp_pisama_dir():
    """Create a temporary pisama directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        pisama_dir = Path(tmpdir) / "pisama"
        pisama_dir.mkdir()
        (pisama_dir / "traces").mkdir()
        yield pisama_dir


@pytest.fixture
def sample_hook_data():
    """Sample hook input data from Claude Code."""
    return {
        "tool_name": "Read",
        "tool_input": {
            "file_path": "/tmp/test.py",
        },
        "session_id": "session-123",
        "working_dir": "/home/user/project",
    }


@pytest.fixture
def sample_hook_data_bash():
    """Sample Bash tool hook data."""
    return {
        "tool_name": "Bash",
        "tool_input": {
            "command": "ls -la",
            "description": "List files",
        },
        "session_id": "session-123",
        "working_dir": "/home/user/project",
    }


@pytest.fixture
def sample_span():
    """Sample span for testing."""
    return Span(
        span_id="span-001",
        name="Read",
        kind=SpanKind.TOOL,
        platform=Platform.CLAUDE_CODE,
        attributes={
            "session_id": "session-123",
            "hook_type": "pre",
        },
        input_data={"file_path": "/tmp/test.py"},
    )


@pytest.fixture
def looping_hook_sequence():
    """Sequence of hook data representing a loop."""
    return [
        {
            "tool_name": "Read",
            "tool_input": {"file_path": "/tmp/same.py"},
            "session_id": "session-loop",
        }
        for _ in range(10)
    ]
