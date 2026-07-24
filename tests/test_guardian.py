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
