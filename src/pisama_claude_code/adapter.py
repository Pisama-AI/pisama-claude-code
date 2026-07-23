"""Claude Code Platform Adapter.

Implements PlatformAdapter for Claude Code, handling trace capture,
detection, and fix injection through Claude's hook and MCP systems.
"""

import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pisama_core.adapters import PlatformAdapter, InjectionResult, InjectionMethod
from pisama_core.injection import EnforcementLevel
from pisama_core.traces import Platform, Span, SpanKind, SpanStatus

from pisama_claude_code.trace_converter import TraceConverter
from pisama_claude_code.storage import TraceStorage


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
    - Blocking tool calls via the PreToolUse contract (exit code 2 / stdout
      JSON ``permissionDecision: "deny"``); exit code 1 does NOT block
    - Writing MCP resources for skill access
    """

    def __init__(
        self,
        pisama_dir: Optional[Path] = None,
        storage: Optional[TraceStorage] = None,
    ):
        self.pisama_dir = pisama_dir or (Path.home() / ".claude" / "pisama")
        self.traces_dir = self.pisama_dir / "traces"
        self.alert_path = Path("/tmp/pisama-alert.json")
        # Durable block/terminate store. The guardian hook runs as a FRESH
        # subprocess per tool call, so an in-memory set of blocked sessions is
        # empty on the next call — a TERMINATE never persists and the run
        # proceeds if detection doesn't re-fire. This on-disk store is what
        # makes BLOCK/TERMINATE durable across tool calls.
        self.blocked_store_path = self.pisama_dir / "blocked_sessions.json"

        self.converter = TraceConverter()
        self.storage = storage or TraceStorage(self.traces_dir)

        # In-memory mirror of the persisted blocked set (kept for parity with
        # get_state()); the persistent store is the source of truth.
        self._blocked_sessions: set[str] = set(self._load_blocked_store().keys())

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
                self.record_block(session_id, "block", severity, issues, message)

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
                self.record_block(session_id, "terminate", severity, issues, message)

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
        """Claude Code PreToolUse hooks can block (exit code 2 / deny JSON)."""
        return True

    def block_action(self, reason: str) -> bool:
        """Signal that the current tool call should be blocked.

        In Claude Code PreToolUse hooks, a block is signalled by exit code 2
        (stderr shown to Claude) or a stdout JSON
        ``hookSpecificOutput.permissionDecision: "deny"``. Exit code 1 is a
        NON-blocking error and does not prevent the tool. The guardian hook
        emits the correct signal via ``guardian_hook._emit_block``; this method
        records intent and returns True.

        Args:
            reason: Reason for blocking (surfaced to Claude by the hook)

        Returns:
            True (block intent recorded)
        """
        # The actual block signal is emitted by the hook entrypoint via
        # _emit_block(reason) -> stdout deny JSON + stderr reason + exit 2.
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
        """Check if a session is currently blocked (durable, cross-subprocess).

        Reads the on-disk store so a fresh guardian subprocess sees a block or
        terminate recorded by a prior tool call.

        Args:
            session_id: Session to check

        Returns:
            True if session is blocked or terminated
        """
        return session_id in self._load_blocked_store()

    def get_block_record(self, session_id: str) -> Optional[dict]:
        """Return the durable block record for a session, or None.

        The record carries ``level`` ("block" | "terminate"), ``severity``,
        ``issues``, ``message`` and ``timestamp``. Used by the guardian to
        re-block a session on a fresh tool call without re-running detection —
        the mechanism that makes TERMINATE actually end the run.
        """
        return self._load_blocked_store().get(session_id)

    def unblock_session(self, session_id: str) -> bool:
        """Unblock a session (clears the durable record).

        Called by the /pisama-intervene acknowledge flow once the user has
        reviewed a BLOCK. A TERMINATE is terminal but can still be cleared here
        when the user explicitly chooses to resume.

        Args:
            session_id: Session to unblock

        Returns:
            True if session was blocked and is now unblocked
        """
        store = self._load_blocked_store()
        self._blocked_sessions.discard(session_id)
        if session_id in store:
            del store[session_id]
            self._save_blocked_store(store)
            return True
        return False

    # -- durable block store --------------------------------------------------

    def record_block(
        self,
        session_id: str,
        level: str,
        severity: int,
        issues: list[str],
        message: str,
        block_count: int = 1,
    ) -> None:
        """Persist a durable block/terminate record for a session.

        Public so the guardian can persist a durable block keyed on its
        should_block DECISION rather than on the enforcement-level name — a
        sev 60-79 manual block maps to DIRECT, which would otherwise persist
        nothing and let the session resume on the next call.
        """
        store = self._load_blocked_store()
        store[session_id] = {
            "level": level,
            "severity": severity,
            "issues": list(issues or []),
            "message": message,
            "block_count": block_count,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._save_blocked_store(store)
        self._blocked_sessions.add(session_id)

    def escalate_block(
        self, session_id: str, terminate_after: int = 3
    ) -> Optional[dict]:
        """Increment a session's block count; escalate to terminate at the cap.

        Called on each re-block (a fresh tool call that hits the durable
        short-circuit): the agent kept issuing tool calls while blocked. After
        ``terminate_after`` re-blocks the record is upgraded to a durable
        ``terminate`` — this is how TERMINATE becomes reachable across
        subprocesses without the in-memory EnforcementEngine escalation (whose
        violation state resets every subprocess and is never fed).
        Returns the updated record, or None if the session has no record.
        """
        store = self._load_blocked_store()
        rec = store.get(session_id)
        if rec is None:
            return None
        rec["block_count"] = int(rec.get("block_count", 1)) + 1
        if rec.get("level") != "terminate" and rec["block_count"] >= terminate_after:
            rec["level"] = "terminate"
            rec["message"] = (
                "Session TERMINATED after repeated blocked tool calls "
                f"({rec['block_count']}). No further actions permitted; "
                "use /pisama-intervene to review."
            )
        store[session_id] = rec
        self._save_blocked_store(store)
        self._blocked_sessions.add(session_id)
        return rec

    def _load_blocked_store(self) -> dict[str, dict]:
        """Load the durable block store; tolerate a missing/corrupt file."""
        try:
            with open(self.blocked_store_path) as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def _save_blocked_store(self, store: dict[str, dict]) -> None:
        """Atomically write the durable block store."""
        self.pisama_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=str(self.pisama_dir), prefix=".blocked_sessions.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(store, f, indent=2)
            os.replace(tmp, self.blocked_store_path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass

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
        issue_text = "\n".join(f"  - {i}" for i in issues) if issues else "  - Critical pattern detected"
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
        issue_text = "\n".join(f"  - {i}" for i in issues) if issues else "  - Critical failure detected"
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
