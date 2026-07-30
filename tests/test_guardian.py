"""Tests for pisama_claude_code.guardian module."""

import json

import pytest

from pisama_claude_code.guardian import Guardian, GuardianConfig, GuardianResult


class TestGuardianConfig:
    """Tests for GuardianConfig."""

    def test_create_default_config(self):
        """Test default config values."""
        config = GuardianConfig()
        assert config.enabled is True
        assert config.mode == "manual"
        assert config.severity_threshold == 40
        assert "break_loop" in config.auto_fix_types
        assert "delete_file" in config.blocked_fixes

    def test_create_custom_config(self):
        """Test custom config values."""
        config = GuardianConfig(
            enabled=True,
            mode="auto",
            severity_threshold=50,
            auto_fix_types=["break_loop"],
            max_auto_fixes=5,
        )
        assert config.mode == "auto"
        assert config.severity_threshold == 50
        assert config.max_auto_fixes == 5

    def test_config_from_dict(self):
        """Test creating config from dict."""
        data = {
            "self_healing": {
                "enabled": True,
                "mode": "report",
                "severity_threshold": 60,
            },
            "monitoring": {
                "pattern_window": 15,
            },
        }
        config = GuardianConfig.from_dict(data)
        assert config.mode == "report"
        assert config.severity_threshold == 60
        assert config.pattern_window == 15


class TestGuardianResult:
    """Tests for GuardianResult."""

    def test_create_result(self):
        """Test creating result."""
        result = GuardianResult(
            should_block=False,
            severity=40,
            issues=["Loop detected"],
            recommendation="break_loop",
        )
        assert result.should_block is False
        assert result.severity == 40
        assert len(result.issues) == 1

    def test_result_defaults(self):
        """Test result default values."""
        result = GuardianResult()
        assert result.should_block is False
        assert result.severity == 0
        assert result.issues == []
        assert result.action_taken == "allowed"


class TestGuardian:
    """Tests for Guardian."""

    def test_create_guardian(self, temp_pisama_dir):
        """Test creating guardian."""
        guardian = Guardian(pisama_dir=temp_pisama_dir)
        assert guardian.config is not None
        assert guardian.config.enabled is True

    def test_create_guardian_with_config(self, temp_pisama_dir):
        """Test creating guardian with custom config."""
        config = GuardianConfig(mode="auto", severity_threshold=50)
        guardian = Guardian(config=config, pisama_dir=temp_pisama_dir)
        assert guardian.config.mode == "auto"
        assert guardian.config.severity_threshold == 50

    def test_load_config_from_file(self, temp_pisama_dir):
        """Test loading config from file."""
        config_path = temp_pisama_dir / "config.json"
        config_data = {
            "self_healing": {
                "enabled": True,
                "mode": "report",
                "severity_threshold": 55,
            },
            "monitoring": {
                "pattern_window": 20,
            },
        }
        with open(config_path, "w") as f:
            json.dump(config_data, f)

        guardian = Guardian(pisama_dir=temp_pisama_dir)
        assert guardian.config.mode == "report"
        assert guardian.config.severity_threshold == 55

    @pytest.mark.asyncio
    async def test_analyze_disabled(self, temp_pisama_dir):
        """Test analyze when guardian is disabled."""
        config = GuardianConfig(enabled=False)
        guardian = Guardian(config=config, pisama_dir=temp_pisama_dir)

        result = await guardian.analyze({"tool_name": "Read"})
        assert result.action_taken == "disabled"

    @pytest.mark.asyncio
    async def test_analyze_simple_tool(self, temp_pisama_dir, sample_hook_data):
        """Test analyzing a simple tool call."""
        guardian = Guardian(pisama_dir=temp_pisama_dir)

        result = await guardian.analyze(sample_hook_data)
        # First call should not detect issues
        assert result.should_block is False

    @pytest.mark.asyncio
    async def test_analyze_report_mode(self, temp_pisama_dir, sample_hook_data, capsys):
        """Test analyze in report mode."""
        config = GuardianConfig(mode="report", severity_threshold=1)
        guardian = Guardian(config=config, pisama_dir=temp_pisama_dir)

        # Simulate issue by lowering threshold
        result = await guardian.analyze(sample_hook_data)

        # In report mode, should never block
        assert result.should_block is False

    def test_guardian_config_path(self, temp_pisama_dir):
        """Test guardian uses correct config path."""
        guardian = Guardian(pisama_dir=temp_pisama_dir)
        expected_path = temp_pisama_dir / "config.json"
        assert guardian.config_path == expected_path


class TestGuardianPisamaCoreContract:
    """Contract tests against the real pisama-core API.

    These exist because pisama-core 1.8.2 added a `py.typed` marker. Before that
    mypy treated the whole package as `Any`, so `guardian.py` could call
    `DetectionResult.evidence.get(...)` and `HealingPlan.fixes` and pass
    `(str, dict)` to `AuditLogger.log` without anything complaining, at type-check
    time or in the test suite. Those lines were all uncovered, so the first sign
    of trouble would have been an AttributeError in a user's session.

    No mocks: these assert against the real objects, so if pisama-core changes
    these shapes again, this fails here rather than in production.
    """

    def test_detection_result_evidence_is_a_list_not_a_mapping(self):
        """`evidence` is list[Evidence]. Calling .get() on it raises."""
        from pisama_core.detection.result import DetectionResult, Evidence

        result = DetectionResult(
            detector_name="loop",
            detected=True,
            evidence=[Evidence(description="repeated Read on the same path")],
        )
        assert isinstance(result.evidence, list)
        assert not hasattr(result.evidence, "get")
        # This is the extraction guardian.py performs, and it must yield plain
        # strings because `issues` is declared list[str] downstream.
        issues = [ev.description for ev in result.evidence]
        assert issues == ["repeated Read on the same path"]
        assert all(isinstance(i, str) for i in issues)

    def test_healing_plan_exposes_primary_fix_not_fixes(self):
        """HealingPlan has primary_fix/fallback_fixes. `plan.fixes` raises."""
        from pisama_core.healing import HealingPlan

        fields = set(getattr(HealingPlan, "__annotations__", {}))
        assert "primary_fix" in fields
        assert "fixes" not in fields

    def test_audit_logger_log_takes_an_event_type_and_session_id(self, tmp_path):
        """log() is (AuditEventType, session_id, details=...), not (str, dict)."""
        from pisama_core.audit import AuditEventType, AuditLogger

        logger = AuditLogger(log_dir=tmp_path)
        event = logger.log(
            AuditEventType.ISSUE_DETECTED,
            "session-abc",
            details={"severity": 42, "issues": ["loop"], "event": "warning"},
            severity=42,
        )
        assert event.session_id == "session-abc"

    def test_every_event_type_guardian_uses_exists(self):
        """Guardian maps its legacy event strings onto these members."""
        from pisama_core.audit import AuditEventType

        for name in (
            "ISSUE_DETECTED",
            "FIX_APPLIED",
            "DIRECTIVE_ISSUED",
            "FIX_RECOMMENDED",
        ):
            assert hasattr(AuditEventType, name), f"AuditEventType.{name} is gone"
