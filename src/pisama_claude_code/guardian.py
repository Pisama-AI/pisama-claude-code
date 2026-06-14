"""PISAMA Guardian for Claude Code.

Provides real-time detection and intervention using pisama-core.
This is the main integration point for Claude Code hooks.
"""

import json
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pisama_core.audit import AuditLogger
from pisama_core.detection import DetectionOrchestrator
from pisama_core.healing import HealingEngine
from pisama_core.injection import EnforcementEngine, EnforcementLevel
from pisama_core.scoring import ScoringEngine
from pisama_core.traces import Trace

from pisama_claude_code.adapter import ClaudeCodeAdapter


@dataclass
class GuardianConfig:
    """Configuration for the Guardian."""

    enabled: bool = True
    mode: str = "manual"  # manual, auto, report
    severity_threshold: int = 40
    auto_fix_types: list[str] = None
    blocked_fixes: list[str] = None
    max_auto_fixes: int = 10
    cooldown_seconds: int = 30
    pattern_window: int = 10

    def __post_init__(self):
        if self.auto_fix_types is None:
            self.auto_fix_types = ["break_loop", "add_delay", "switch_strategy"]
        if self.blocked_fixes is None:
            self.blocked_fixes = ["delete_file", "git_push", "external_api"]

    @classmethod
    def from_dict(cls, data: dict) -> "GuardianConfig":
        """Create config from dict."""
        self_healing = data.get("self_healing", {})
        monitoring = data.get("monitoring", {})
        return cls(
            enabled=self_healing.get("enabled", True),
            mode=self_healing.get("mode", "manual"),
            severity_threshold=self_healing.get("severity_threshold", 40),
            auto_fix_types=self_healing.get("auto_fix_types"),
            blocked_fixes=self_healing.get("blocked_fixes"),
            max_auto_fixes=self_healing.get("max_auto_fixes", 10),
            cooldown_seconds=self_healing.get("cooldown_seconds", 30),
            pattern_window=monitoring.get("pattern_window", 10),
        )


@dataclass
class GuardianResult:
    """Result of guardian analysis."""

    should_block: bool = False
    severity: int = 0
    issues: list[str] = None
    recommendation: Optional[str] = None
    action_taken: str = "allowed"
    message: Optional[str] = None

    def __post_init__(self):
        if self.issues is None:
            self.issues = []


class Guardian:
    """PISAMA Guardian for Claude Code.

    Provides real-time detection and intervention by:
    1. Converting hook data to spans
    2. Running detection using pisama-core
    3. Applying healing based on configuration
    4. Injecting fixes via the Claude Code adapter
    """

    def __init__(
        self,
        config: Optional[GuardianConfig] = None,
        pisama_dir: Optional[Path] = None,
    ):
        self.pisama_dir = pisama_dir or (Path.home() / ".claude" / "pisama")
        self.config_path = self.pisama_dir / "config.json"

        # Load config
        if config:
            self.config = config
        else:
            self.config = self._load_config()

        # Initialize components
        self.adapter = ClaudeCodeAdapter(pisama_dir=self.pisama_dir)
        self.audit = AuditLogger(self.pisama_dir / "audit_log.jsonl")

        # Core engines
        self.detector_orchestrator = DetectionOrchestrator()
        self.scoring_engine = ScoringEngine()
        self.healing_engine = HealingEngine()
        self.enforcement_engine = EnforcementEngine()

        # Session tracking
        self._fix_counts: dict[str, int] = {}

    def _load_config(self) -> GuardianConfig:
        """Load configuration from file."""
        if self.config_path.exists():
            try:
                with open(self.config_path) as f:
                    data = json.load(f)
                return GuardianConfig.from_dict(data)
            except Exception:
                pass
        return GuardianConfig()

    async def analyze(
        self,
        hook_data: dict[str, Any],
        session_id: Optional[str] = None,
    ) -> GuardianResult:
        """Analyze a tool call and determine action.

        Args:
            hook_data: Raw hook input data
            session_id: Optional session ID

        Returns:
            GuardianResult with action to take
        """
        if not self.config.enabled:
            return GuardianResult(action_taken="disabled")

        # Get session ID
        session_id = session_id or hook_data.get("session_id") or os.environ.get("CLAUDE_SESSION_ID", "unknown")

        # Convert to span
        span = self.adapter.capture_span(hook_data)

        # Store the span
        self.adapter.store_span(span, hook_data)

        # Build trace from recent spans
        recent_spans = self.adapter.get_recent_spans(self.config.pattern_window)
        # Include current span in the trace if not already present
        all_spans = recent_spans if span in recent_spans else [span] + recent_spans
        trace = Trace(
            trace_id=span.trace_id or session_id,
            spans=all_spans,
        )

        # Run detection
        analysis = await self.detector_orchestrator.analyze(trace)
        detection_results = analysis.detection_results

        # Calculate severity
        if detection_results:
            severity = self.scoring_engine.calculate_severity(detection_results)
            issues = []
            for result in detection_results:
                if result.detected:
                    issues.extend(result.evidence.get("issues", []))
        else:
            severity = 0
            issues = []

        # Check threshold
        if severity < self.config.severity_threshold:
            # Below threshold - log warning if close
            if severity >= self.config.severity_threshold - 10 and issues:
                self.audit.log("warning", {
                    "severity": severity,
                    "issues": issues,
                    "action": "allowed",
                    "session_id": session_id,
                })
            return GuardianResult(
                severity=severity,
                issues=issues,
                action_taken="allowed",
            )

        # Above threshold - determine action based on mode
        recommendation = self._get_recommendation(detection_results)

        if self.config.mode == "report":
            return self._handle_report_mode(severity, issues, recommendation, session_id)
        elif self.config.mode == "auto":
            return await self._handle_auto_mode(severity, issues, recommendation, session_id, trace, detection_results)
        else:  # manual
            return self._handle_manual_mode(severity, issues, recommendation, session_id)

    def _get_recommendation(self, detection_results: list) -> str:
        """Determine recommended fix based on detection results."""
        for result in detection_results:
            if result.detected and result.detector_name == "loop":
                if result.severity >= 60:
                    return "break_loop"
                else:
                    return "switch_strategy"
            elif result.detected and result.detector_name == "repetition":
                return "break_loop"
            elif result.detected and result.detector_name == "coordination":
                return "escalate"
        return "break_loop"  # Default

    def _handle_report_mode(
        self,
        severity: int,
        issues: list[str],
        recommendation: str,
        session_id: str,
    ) -> GuardianResult:
        """Handle report mode - log but don't block."""
        self.audit.log("report", {
            "severity": severity,
            "issues": issues,
            "recommendation": recommendation,
            "action": "logged_only",
            "session_id": session_id,
        })

        # Print warning to stderr
        print(f"PISAMA: Detected issue (severity {severity}) - {issues[0] if issues else 'unknown'}", file=sys.stderr)

        return GuardianResult(
            severity=severity,
            issues=issues,
            recommendation=recommendation,
            action_taken="logged_only",
        )

    async def _handle_auto_mode(
        self,
        severity: int,
        issues: list[str],
        recommendation: str,
        session_id: str,
        trace: Trace,
        detection_results: list,
    ) -> GuardianResult:
        """Handle auto mode - apply fixes automatically."""
        # Check if fix type is approved
        if recommendation not in self.config.auto_fix_types:
            # Escalate to manual
            return self._escalate_to_manual(severity, issues, recommendation, session_id)

        # Check fix count limit
        fix_count = self._fix_counts.get(session_id, 0)
        if fix_count >= self.config.max_auto_fixes:
            # Too many fixes - escalate
            return self._escalate_to_manual(
                severity, issues, recommendation, session_id,
                reason="max_auto_fixes_reached"
            )

        # Apply fix using healing engine
        for result in detection_results:
            if result.detected:
                plan = self.healing_engine.analyze(result)
                if plan.fixes:
                    # For now, just use the recommendation
                    break

        # Determine enforcement level
        level = self.enforcement_engine.get_level(severity, session_id)

        # Inject fix
        self.adapter.inject_fix(
            directive=f"Apply fix: {recommendation}",
            level=level,
            session_id=session_id,
            metadata={
                "severity": severity,
                "issues": issues,
                "recommendation": recommendation,
            },
        )

        # Log
        self.audit.log("auto_heal", {
            "severity": severity,
            "issues": issues,
            "fix_applied": recommendation,
            "action": "auto_fixed",
            "session_id": session_id,
        })

        # Track fix count
        self._fix_counts[session_id] = fix_count + 1

        # Determine if we should block
        should_block = recommendation == "break_loop" and severity >= 60

        return GuardianResult(
            should_block=should_block,
            severity=severity,
            issues=issues,
            recommendation=recommendation,
            action_taken="auto_fixed",
            message=f"Auto-healing applied: {recommendation}",
        )

    def _handle_manual_mode(
        self,
        severity: int,
        issues: list[str],
        recommendation: str,
        session_id: str,
    ) -> GuardianResult:
        """Handle manual mode - alert and optionally block."""
        # Write alert for skill access
        self._write_alert(session_id, severity, issues, recommendation)

        # Determine enforcement level
        level = self.enforcement_engine.get_level(severity, session_id)

        # Inject directive
        self.adapter.inject_fix(
            directive=f"Recommended action: {recommendation}",
            level=level,
            session_id=session_id,
            metadata={
                "severity": severity,
                "issues": issues,
                "recommendation": recommendation,
            },
        )

        # Log
        self.audit.log("intervention", {
            "severity": severity,
            "issues": issues,
            "recommendation": recommendation,
            "action": "blocked_for_approval" if severity >= 60 else "warning",
            "session_id": session_id,
        })

        # Block critical issues
        should_block = severity >= 60

        return GuardianResult(
            should_block=should_block,
            severity=severity,
            issues=issues,
            recommendation=recommendation,
            action_taken="blocked_for_approval" if should_block else "warning",
            message="Use /pisama-intervene to review and decide how to proceed.",
        )

    def _escalate_to_manual(
        self,
        severity: int,
        issues: list[str],
        recommendation: str,
        session_id: str,
        reason: str = "fix_type_not_approved",
    ) -> GuardianResult:
        """Escalate from auto to manual mode."""
        self._write_alert(session_id, severity, issues, recommendation)

        self.audit.log("escalate", {
            "severity": severity,
            "issues": issues,
            "recommendation": recommendation,
            "reason": reason,
            "action": "escalated_to_manual",
            "session_id": session_id,
        })

        # Block and wait for user
        self.adapter.inject_fix(
            directive=f"Escalated: {recommendation} (requires approval)",
            level=EnforcementLevel.BLOCK,
            session_id=session_id,
            metadata={
                "severity": severity,
                "issues": issues,
                "recommendation": recommendation,
            },
        )

        return GuardianResult(
            should_block=True,
            severity=severity,
            issues=issues,
            recommendation=recommendation,
            action_taken="escalated_to_manual",
            message=f"Escalated due to: {reason}",
        )

    def _write_alert(
        self,
        session_id: str,
        severity: int,
        issues: list[str],
        recommendation: str,
    ) -> None:
        """Write alert file for MCP/skill access."""
        alert_path = Path(tempfile.gettempdir()) / "pisama-alert.json"
        recent_spans = self.adapter.get_recent_spans(10)

        alert = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "pattern": {
                "type": recommendation,
                "sequence": [s.name for s in recent_spans[:10]],
                "occurrences": len(recent_spans),
            },
            "severity": severity,
            "issues": issues,
            "recommendation": recommendation,
        }

        with open(alert_path, "w") as f:
            json.dump(alert, f, indent=2)


# Synchronous wrapper for hook usage
def analyze_sync(hook_data: dict, session_id: Optional[str] = None) -> GuardianResult:
    """Synchronous wrapper for Guardian.analyze().

    Used by the hook script which runs synchronously.
    """
    import asyncio

    guardian = Guardian()
    return asyncio.run(guardian.analyze(hook_data, session_id))
