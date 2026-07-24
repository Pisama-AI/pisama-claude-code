"""Claude Code Platform Adapter.

Implements PlatformAdapter for Claude Code, handling trace capture,
detection, and fix injection through Claude's hook and MCP systems.
"""

import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pisama_core.adapters import InjectionMethod, InjectionResult, PlatformAdapter
from pisama_core.injection import EnforcementLevel
from pisama_core.traces import Platform, Span

from pisama_claude_code.storage import TraceStorage
from pisama_claude_code.trace_converter import TraceConverter


@dataclass
class ClaudeCodeContext:
    """Context for Claude Code session."""

    session_id: str
    working_dir: str
    hook_type: str = "pre"
    config: dict = field(default_factory=dict)


class ClaudeCodeAdapter(PlatformAdapter):
    """Platform adapter for Claude Code.

    Provides integration between Claude Code's hook system and PISAMA core.
    Handles:
    - Converting hook data to universal Span format
    - Injecting fixes via stderr (visible to Claude)
    - Blocking tool calls by returning exit code 1
    - Writing MCP resources for skill access
    """

    def __init__(
        self,
        pisama_dir: Optional[Path] = None,
        storage: Optional[TraceStorage] = None,
    ):
        self.pisama_dir = pisama_dir or (Path.home() / ".claude" / "pisama")
        self.traces_dir = self.pisama_dir / "traces"
        self.alert_path = Path(tempfile.gettempdir()) / "pisama-alert.json"

        self.converter = TraceConverter()
        self.storage = storage or TraceStorage(self.traces_dir)

        self._blocked_sessions: set[str] = set()

    @property
    def platform_name(self) -> Platform:
        """Return platform identifier."""
        return Platform.CLAUDE_CODE

    def capture_span(self, raw_data: Any) -> Span:
        """Convert Claude Code hook data to universal Span.

        Args:
            raw_data: Hook input data (dict with tool_name, tool_input, etc.)

        Returns:
            Universal Span object
        """
        return self.converter.to_span(raw_data)

    def store_span(self, span: Span, raw_data: Optional[dict] = None) -> None:
        """Store span to local database and JSONL.

        Args:
            span: Universal span to store
            raw_data: Optional raw hook data
        """
        self.storage.store(span, raw_data)

    def inject_fix(
        self,
        directive: str,
        level: EnforcementLevel,
        session_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> InjectionResult:
        """Inject a fix directive to Claude Code.

        For Claude Code, we inject via stderr which is visible to the agent.
        Higher enforcement levels can also write MCP resources or block.

        Args:
            directive: The fix directive to inject
            level: Enforcement level (SUGGEST, DIRECT, BLOCK, TERMINATE)
            session_id: Optional session ID for tracking
            metadata: Optional metadata (severity, issues, etc.)

        Returns:
            InjectionResult indicating success and method used
        """
        metadata = metadata or {}
        severity = metadata.get("severity", 50)
        issues = metadata.get("issues", [])
        recommendation = metadata.get("recommendation", "break_loop")

        if level == EnforcementLevel.SUGGEST:
            # Soft suggestion via stderr
            message = self._format_suggestion(severity, issues, directive)
            print(message, file=sys.stderr)
            return InjectionResult(
                success=True,
                method=InjectionMethod.STDERR,
                message=message,
            )

        elif level == EnforcementLevel.DIRECT:
            # Direct instruction via stderr
            message = self._format_direct(severity, issues, directive)
            print(message, file=sys.stderr)

            # Also write MCP resource for skill access
            self._write_alert(
                session_id=session_id,
                severity=severity,
                issues=issues,
                recommendation=recommendation,
            )

            return InjectionResult(
                success=True,
                method=InjectionMethod.STDERR,
                message=message,
            )

        elif level == EnforcementLevel.BLOCK:
            # Block with message and MCP alert
            message = self._format_block(severity, issues, directive)
            print(message, file=sys.stderr)

            self._write_alert(
                session_id=session_id,
                severity=severity,
                issues=issues,
                recommendation=recommendation,
            )

            if session_id:
                self._blocked_sessions.add(session_id)

            return InjectionResult(
                success=True,
                method=InjectionMethod.STDERR,
                message=message,
                blocked=True,
            )

        elif level == EnforcementLevel.TERMINATE:
            # Terminate message and block
            message = self._format_terminate(severity, issues)
            print(message, file=sys.stderr)

            if session_id:
                self._blocked_sessions.add(session_id)

            return InjectionResult(
                success=True,
                method=InjectionMethod.STDERR,
                message=message,
                blocked=True,
            )

        return InjectionResult(
            success=False,
            method=InjectionMethod.STDERR,
            error="Unknown enforcement level",
        )

    def can_block(self) -> bool:
        """Claude Code hooks can block by returning exit code 1."""
        return True

    def block_action(self, reason: str) -> bool:
        """Block current action by exiting with code 1.

        In Claude Code hooks, exiting with code 1 prevents the tool call.

        Args:
            reason: Reason for blocking (logged but not used in exit)

        Returns:
            True (always succeeds if we're in a hook context)
        """
        # The actual exit happens after this returns
        # Caller should call sys.exit(1) after this
        return True

    def get_supported_injection_methods(self) -> list[InjectionMethod]:
        """Get supported injection methods for Claude Code.

        Returns:
            List of injection methods (stderr + resource/MCP)
        """
        return [InjectionMethod.STDERR, InjectionMethod.RESOURCE]

    def get_state(self) -> dict[str, Any]:
        """Get current platform state.

        Returns:
            State dict with session info and recent traces
        """
        recent = self.storage.get_tool_sequence(limit=10)
        return {
            "platform": "claude_code",
            "recent_tools": recent,
            "blocked_sessions": list(self._blocked_sessions),
        }

    def get_recent_spans(self, limit: int = 10) -> list[Span]:
        """Get recent spans from storage.

        Args:
            limit: Maximum number of spans to return

        Returns:
            List of recent spans, most recent first
        """
        return self.storage.get_recent(limit)

    def is_session_blocked(self, session_id: str) -> bool:
        """Check if a session is currently blocked.

        Args:
            session_id: Session to check

        Returns:
            True if session is blocked
        """
        return session_id in self._blocked_sessions

    def unblock_session(self, session_id: str) -> bool:
        """Unblock a session.

        Args:
            session_id: Session to unblock

        Returns:
            True if session was blocked and is now unblocked
        """
        if session_id in self._blocked_sessions:
            self._blocked_sessions.discard(session_id)
            return True
        return False

    def _format_suggestion(self, severity: int, issues: list[str], directive: str) -> str:
        """Format a suggestion message."""
        issue_text = "\n".join(f"  - {i}" for i in issues) if issues else "  - Pattern detected"
        return f"""
[PISAMA Observation]
Severity: {severity}/100
{issue_text}

Suggestion: {directive}
"""

    def _format_direct(self, severity: int, issues: list[str], directive: str) -> str:
        """Format a direct instruction message."""
        issue_text = "\n".join(f"  - {i}" for i in issues) if issues else "  - Pattern detected"
        return f"""
[PISAMA Guardian Alert]
Severity: {severity}/100
Issues:
{issue_text}

DIRECTIVE: {directive}

Use /pisama-intervene to review and decide how to proceed.
"""

    def _format_block(self, severity: int, issues: list[str], directive: str) -> str:
        """Format a blocking message."""
        issue_text = (
            "\n".join(f"  - {i}" for i in issues) if issues else "  - Critical pattern detected"
        )
        return f"""
[PISAMA BLOCKED]
Severity: {severity}/100 (Critical)
Issues:
{issue_text}

This action has been BLOCKED.

REQUIRED ACTION: {directive}

You must use /pisama-intervene to acknowledge and apply the fix before continuing.
"""

    def _format_terminate(self, severity: int, issues: list[str]) -> str:
        """Format a termination message."""
        issue_text = (
            "\n".join(f"  - {i}" for i in issues) if issues else "  - Critical failure detected"
        )
        return f"""
[PISAMA TERMINATED]
Severity: {severity}/100 (Critical)
Issues:
{issue_text}

Session has been TERMINATED due to repeated violations.

The user has been notified. No further actions will be permitted.
"""

    def _write_alert(
        self,
        session_id: Optional[str],
        severity: int,
        issues: list[str],
        recommendation: str,
    ) -> None:
        """Write alert file for MCP/skill access."""
        import json

        alert = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id or "unknown",
            "severity": severity,
            "issues": issues,
            "recommendation": recommendation,
        }

        with open(self.alert_path, "w") as f:
            json.dump(alert, f, indent=2)
