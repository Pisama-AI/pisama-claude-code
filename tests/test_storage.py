"""Tests for pisama_claude_code.storage module."""

import json

from pisama_core.traces import Platform, Span, SpanKind

from pisama_claude_code.storage import TraceStorage


class TestTraceStorage:
    """Tests for TraceStorage."""

    def test_create_storage(self, temp_pisama_dir):
        """Test creating storage initializes database."""
        traces_dir = temp_pisama_dir / "traces"
        storage = TraceStorage(traces_dir)

        assert storage.db_path.exists()

    def test_store_span(self, temp_pisama_dir, sample_span):
        """Test storing a span."""
        traces_dir = temp_pisama_dir / "traces"
        storage = TraceStorage(traces_dir)

        storage.store(sample_span)

        # Check JSONL file was created
        date_str = sample_span.start_time.strftime("%Y-%m-%d")
        jsonl_path = traces_dir / f"traces-{date_str}.jsonl"
        assert jsonl_path.exists()

        # Check content
        with open(jsonl_path) as f:
            record = json.loads(f.readline())
            assert record["name"] == "Read"
            assert record["span_id"] == "span-001"

    def test_store_multiple_spans(self, temp_pisama_dir):
        """Test storing multiple spans."""
        traces_dir = temp_pisama_dir / "traces"
        storage = TraceStorage(traces_dir)

        for i in range(5):
            span = Span(
                span_id=f"span-{i:03d}",
                name=f"Tool-{i}",
                kind=SpanKind.TOOL,
                platform=Platform.CLAUDE_CODE,
            )
            storage.store(span)

        # Check database has all spans
        recent = storage.get_recent(limit=10)
        assert len(recent) == 5

    def test_get_recent(self, temp_pisama_dir):
        """Test getting recent spans."""
        traces_dir = temp_pisama_dir / "traces"
        storage = TraceStorage(traces_dir)

        # Store some spans
        for i in range(10):
            span = Span(
                span_id=f"span-{i:03d}",
                name=f"Tool-{i}",
                kind=SpanKind.TOOL,
                platform=Platform.CLAUDE_CODE,
            )
            storage.store(span)

        # Get recent (should be limited)
        recent = storage.get_recent(limit=5)
        assert len(recent) == 5

    def test_get_recent_by_session(self, temp_pisama_dir):
        """Test getting recent spans filtered by session."""
        traces_dir = temp_pisama_dir / "traces"
        storage = TraceStorage(traces_dir)

        # Store spans for different sessions
        for i in range(5):
            span = Span(
                span_id=f"span-a-{i}",
                name="Read",
                kind=SpanKind.TOOL,
                platform=Platform.CLAUDE_CODE,
                attributes={"session_id": "session-A"},
            )
            storage.store(span)

        for i in range(3):
            span = Span(
                span_id=f"span-b-{i}",
                name="Edit",
                kind=SpanKind.TOOL,
                platform=Platform.CLAUDE_CODE,
                attributes={"session_id": "session-B"},
            )
            storage.store(span)

        # Filter by session
        session_a = storage.get_recent(limit=10, session_id="session-A")
        assert len(session_a) == 5

        session_b = storage.get_recent(limit=10, session_id="session-B")
        assert len(session_b) == 3

    def test_get_tool_sequence(self, temp_pisama_dir):
        """Test getting tool name sequence."""
        traces_dir = temp_pisama_dir / "traces"
        storage = TraceStorage(traces_dir)

        tools = ["Read", "Edit", "Bash", "Read", "Write"]
        for i, tool in enumerate(tools):
            span = Span(
                span_id=f"span-{i}",
                name=tool,
                kind=SpanKind.TOOL,
                platform=Platform.CLAUDE_CODE,
            )
            storage.store(span)

        sequence = storage.get_tool_sequence(limit=10)
        # Most recent first, so reversed
        assert len(sequence) == 5
        assert "Read" in sequence
        assert "Edit" in sequence

    def test_clear_session(self, temp_pisama_dir):
        """Test clearing session traces."""
        traces_dir = temp_pisama_dir / "traces"
        storage = TraceStorage(traces_dir)

        # Store some spans
        for i in range(5):
            span = Span(
                span_id=f"span-{i}",
                name="Read",
                kind=SpanKind.TOOL,
                platform=Platform.CLAUDE_CODE,
                attributes={"session_id": "session-to-clear"},
            )
            storage.store(span)

        # Clear session
        count = storage.clear_session("session-to-clear")
        assert count == 5

        # Verify cleared
        remaining = storage.get_recent(limit=10, session_id="session-to-clear")
        assert len(remaining) == 0

    def test_store_with_raw_data(self, temp_pisama_dir, sample_span):
        """Test storing span with raw hook data."""
        traces_dir = temp_pisama_dir / "traces"
        storage = TraceStorage(traces_dir)

        raw_data = {
            "tool_name": "Read",
            "extra_field": "some_value",
        }
        storage.store(sample_span, raw_data)

        # Check JSONL includes raw data
        date_str = sample_span.start_time.strftime("%Y-%m-%d")
        jsonl_path = traces_dir / f"traces-{date_str}.jsonl"

        with open(jsonl_path) as f:
            record = json.loads(f.readline())
            assert record["raw"] == raw_data

    def test_empty_database(self, temp_pisama_dir):
        """Test queries on empty database."""
        traces_dir = temp_pisama_dir / "traces"
        storage = TraceStorage(traces_dir)

        recent = storage.get_recent(limit=10)
        assert recent == []

        sequence = storage.get_tool_sequence(limit=10)
        assert sequence == []
