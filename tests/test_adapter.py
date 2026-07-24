"""Tests for pisama_claude_code.adapter module."""

from pisama_core.injection import EnforcementLevel
from pisama_core.traces import Platform, SpanKind

from pisama_claude_code.adapter import ClaudeCodeAdapter, ClaudeCodeContext


class TestClaudeCodeContext:
    """Tests for ClaudeCodeContext."""

    def test_create_context(self):
        """Test creating context."""
        ctx = ClaudeCodeContext(
            session_id="session-1",
            working_dir="/home/user/project",
        )
        assert ctx.session_id == "session-1"
        assert ctx.hook_type == "pre"  # default


class TestClaudeCodeAdapter:
    """Tests for ClaudeCodeAdapter."""

    def test_create_adapter(self, temp_pisama_dir):
        """Test creating adapter."""
        adapter = ClaudeCodeAdapter(pisama_dir=temp_pisama_dir)
        assert adapter.platform_name == Platform.CLAUDE_CODE

    def test_capture_span(self, temp_pisama_dir, sample_hook_data):
        """Test converting hook data to span."""
        adapter = ClaudeCodeAdapter(pisama_dir=temp_pisama_dir)
        span = adapter.capture_span(sample_hook_data)

        assert span.name == "Read"
        assert span.kind == SpanKind.TOOL
        assert span.platform == Platform.CLAUDE_CODE
        assert span.input_data is not None

    def test_capture_span_bash(self, temp_pisama_dir, sample_hook_data_bash):
        """Test converting Bash hook data to span."""
        adapter = ClaudeCodeAdapter(pisama_dir=temp_pisama_dir)
        span = adapter.capture_span(sample_hook_data_bash)

        assert span.name == "Bash"
        assert span.kind == SpanKind.TOOL

    def test_store_span(self, temp_pisama_dir, sample_span):
        """Test storing span."""
        adapter = ClaudeCodeAdapter(pisama_dir=temp_pisama_dir)
        adapter.store_span(sample_span)

        # Verify stored
        recent = adapter.get_recent_spans(limit=5)
        assert len(recent) >= 1

    def test_inject_fix_suggest(self, temp_pisama_dir, capsys):
        """Test injecting suggestion-level fix."""
        adapter = ClaudeCodeAdapter(pisama_dir=temp_pisama_dir)

        result = adapter.inject_fix(
            directive="Consider a different approach",
            level=EnforcementLevel.SUGGEST,
            session_id="session-1",
            metadata={"severity": 30, "issues": ["Minor pattern detected"]},
        )

        assert result.success is True
        assert result.blocked is False

        # Check stderr output
        captured = capsys.readouterr()
        assert "PISAMA" in captured.err
        assert "30" in captured.err

    def test_inject_fix_direct(self, temp_pisama_dir, capsys):
        """Test injecting direct-level fix."""
        adapter = ClaudeCodeAdapter(pisama_dir=temp_pisama_dir)

        result = adapter.inject_fix(
            directive="Apply this fix",
            level=EnforcementLevel.DIRECT,
            session_id="session-1",
            metadata={"severity": 50, "issues": ["Pattern detected"]},
        )

        assert result.success is True
        assert result.blocked is False

        # Check stderr output
        captured = capsys.readouterr()
        assert "Guardian" in captured.err or "PISAMA" in captured.err

        # Check alert file created
        assert adapter.alert_path.exists()

    def test_inject_fix_block(self, temp_pisama_dir, capsys):
        """Test injecting block-level fix."""
        adapter = ClaudeCodeAdapter(pisama_dir=temp_pisama_dir)

        result = adapter.inject_fix(
            directive="Stop immediately",
            level=EnforcementLevel.BLOCK,
            session_id="session-1",
            metadata={"severity": 70, "issues": ["Critical issue"]},
        )

        assert result.success is True
        assert result.blocked is True

        # Check stderr output
        captured = capsys.readouterr()
        assert "BLOCKED" in captured.err

    def test_inject_fix_terminate(self, temp_pisama_dir, capsys):
        """Test injecting terminate-level fix."""
        adapter = ClaudeCodeAdapter(pisama_dir=temp_pisama_dir)

        result = adapter.inject_fix(
            directive="Session terminated",
            level=EnforcementLevel.TERMINATE,
            session_id="session-1",
            metadata={"severity": 90, "issues": ["Fatal error"]},
        )

        assert result.success is True
        assert result.blocked is True

        # Check stderr output
        captured = capsys.readouterr()
        assert "TERMINATED" in captured.err

    def test_can_block(self, temp_pisama_dir):
        """Test that adapter can block."""
        adapter = ClaudeCodeAdapter(pisama_dir=temp_pisama_dir)
        assert adapter.can_block() is True

    def test_block_action(self, temp_pisama_dir):
        """Test block action."""
        adapter = ClaudeCodeAdapter(pisama_dir=temp_pisama_dir)
        result = adapter.block_action("Test reason")
        assert result is True

    def test_session_blocking(self, temp_pisama_dir):
        """Test session blocking tracking."""
        adapter = ClaudeCodeAdapter(pisama_dir=temp_pisama_dir)

        # Initially not blocked
        assert adapter.is_session_blocked("session-1") is False

        # Block via inject
        adapter.inject_fix(
            directive="Block",
            level=EnforcementLevel.BLOCK,
            session_id="session-1",
        )

        # Now blocked
        assert adapter.is_session_blocked("session-1") is True

        # Unblock
        result = adapter.unblock_session("session-1")
        assert result is True
        assert adapter.is_session_blocked("session-1") is False

    def test_get_state(self, temp_pisama_dir):
        """Test getting platform state."""
        adapter = ClaudeCodeAdapter(pisama_dir=temp_pisama_dir)

        state = adapter.get_state()
        assert state["platform"] == "claude_code"
        assert "recent_tools" in state
        assert "blocked_sessions" in state

    def test_get_supported_injection_methods(self, temp_pisama_dir):
        """Test getting supported injection methods."""
        adapter = ClaudeCodeAdapter(pisama_dir=temp_pisama_dir)

        methods = adapter.get_supported_injection_methods()
        assert len(methods) >= 1
