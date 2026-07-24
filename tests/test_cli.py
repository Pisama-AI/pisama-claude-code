"""Tests for PISAMA Claude Code CLI."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pisama_claude_code.cli import get_config, main, save_config


class TestConfig:
    """Tests for configuration management."""

    def test_get_config_missing_file(self, tmp_path):
        """Test get_config returns empty dict when file missing."""
        with patch("pisama_claude_code.cli.CONFIG_FILE", tmp_path / "missing.json"):
            config = get_config()
            assert config == {}

    def test_save_and_get_config(self, tmp_path):
        """Test saving and retrieving config."""
        config_file = tmp_path / "config.json"
        config_dir = tmp_path

        with patch("pisama_claude_code.cli.CONFIG_FILE", config_file):
            with patch("pisama_claude_code.cli.CONFIG_DIR", config_dir):
                save_config({"api_key": "test123", "auto_sync": True})

                loaded = get_config()
                assert loaded["api_key"] == "test123"
                assert loaded["auto_sync"] is True


class TestCLI:
    """Tests for CLI commands."""

    def test_version(self):
        """Test --version flag matches the package version."""
        from pisama_claude_code import __version__

        runner = CliRunner()
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert __version__ in result.output

    def test_help(self):
        """Test --help flag."""
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "Pisama Claude Code" in result.output
        assert "connect" in result.output
        assert "sync" in result.output
        assert "proxy" in result.output

    def test_status_not_connected(self, tmp_path):
        """Test status when not connected."""
        runner = CliRunner()
        config_file = tmp_path / "config.json"

        with patch("pisama_claude_code.cli.CONFIG_FILE", config_file):
            with patch("pisama_claude_code.cli.CONFIG_DIR", tmp_path):
                result = runner.invoke(main, ["status"])
                assert result.exit_code == 0
                # Should show not connected or similar

    def test_connect_saves_config(self, tmp_path):
        """Test connect command saves API key."""
        import httpx as real_httpx

        runner = CliRunner()
        config_file = tmp_path / "config.json"

        with patch("pisama_claude_code.cli.CONFIG_FILE", config_file):
            with patch("pisama_claude_code.cli.CONFIG_DIR", tmp_path):
                with patch("pisama_claude_code.cli.httpx") as mock_httpx:
                    # Mock ConnectError (offline mode) - use real exception class
                    mock_httpx.ConnectError = real_httpx.ConnectError
                    mock_httpx.get.side_effect = real_httpx.ConnectError("Connection failed")

                    result = runner.invoke(
                        main,
                        [
                            "connect",
                            "--api-key",
                            "pk_test_123",
                            "--api-url",
                            "http://localhost:8000",
                        ],
                    )

                    # Should save config even if connection fails
                    assert result.exit_code == 0
                    assert config_file.exists()
                    config = json.loads(config_file.read_text())
                    assert config["api_key"] == "pk_test_123"

    def test_sync_requires_connection(self, tmp_path):
        """Test sync fails when not connected."""
        runner = CliRunner()
        config_file = tmp_path / "config.json"

        with patch("pisama_claude_code.cli.CONFIG_FILE", config_file):
            result = runner.invoke(main, ["sync"])
            assert "Not connected" in result.output or "connect" in result.output.lower()

    def test_export_creates_file(self, tmp_path):
        """Test export creates output file."""
        runner = CliRunner()
        output_file = tmp_path / "export.jsonl"
        traces_dir = tmp_path / "traces"
        traces_dir.mkdir()

        # Create sample trace file
        trace_file = traces_dir / "traces-2026-01-01.jsonl"
        trace_file.write_text(
            json.dumps(
                {
                    "timestamp": "2026-01-01T00:00:00+00:00",
                    "tool_name": "Bash",
                    "hook_type": "PreToolUse",
                    "session_id": "test-session",
                    "tool_input": {"command": "echo hello"},
                }
            )
            + "\n"
        )

        with patch("pisama_claude_code.cli.TRACES_DIR", traces_dir):
            result = runner.invoke(main, ["export", "--last", "10", "-o", str(output_file)])

            assert output_file.exists()
            assert "Exported" in result.output


class TestPrivacy:
    """Tests for privacy and redaction."""

    def test_sanitize_input_redacts_secrets(self):
        """Test that sensitive fields are redacted."""
        from pisama_claude_code.cli import sanitize_input

        inp = {
            "command": "echo hello",
            "api_key": "sk-secret123",
            "password": "mypassword",
            "normal_field": "visible",
        }

        result = sanitize_input(inp)

        assert result["command"] == "echo hello"
        assert result["api_key"] == "[REDACTED]"
        assert result["password"] == "[REDACTED]"
        assert result["normal_field"] == "visible"

    def test_anonymize_path_replaces_home(self):
        """Test that home directory is anonymized."""
        from pisama_claude_code.cli import anonymize_path

        home = str(Path.home())
        path = f"{home}/projects/secret-project/file.py"

        result = anonymize_path(path)

        assert result == "~/projects/secret-project/file.py"
        assert home not in result

    def test_sanitize_input_preserves_full_content(self):
        """Full-content mode keeps moderately long values intact."""
        from pisama_claude_code.cli import sanitize_input

        inp = {"content": "x" * 1000}  # well under the cap
        result = sanitize_input(inp)

        assert result["content"] == "x" * 1000

    def test_sanitize_input_truncates_beyond_cap(self):
        """Only values larger than MAX_FIELD_CHARS are truncated."""
        from pisama_claude_code.cli import MAX_FIELD_CHARS, sanitize_input

        inp = {"content": "x" * (MAX_FIELD_CHARS + 500)}
        result = sanitize_input(inp)

        assert len(result["content"]) <= MAX_FIELD_CHARS + len("...[truncated]")
        assert "[truncated]" in result["content"]

    def test_scrub_secrets_patterns(self):
        """The built-in scrubber redacts common credential shapes."""
        from pisama_claude_code.cli import _scrub_secrets

        cases = [
            "sk-ant-api03-" + "A" * 40,
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.abcDEFghiJKLmnoPQR",
            "pisama_" + "B" * 40,
            "ghp_" + "C" * 36,
            "AKIA" + "D" * 16,
        ]
        for secret in cases:
            out = _scrub_secrets(f"value is {secret} end")
            assert secret not in out and "[REDACTED]" in out, secret

    def test_forward_payload_scrubs_content(self):
        """prepare_sync_payload scrubs secrets from forwarded content fields."""
        from pisama_claude_code.cli import prepare_sync_payload

        secret = "sk-ant-api03-" + "Z" * 40
        traces = [
            {
                "session_id": "s",
                "tool_name": "Bash",
                "user_input": f"my key {secret}",
                "reasoning": "none",
                "ai_output": "ok",
                "tool_input": {"command": f"export KEY={secret}"},
                "tool_output": f"printed {secret}",
            }
        ]
        payload = prepare_sync_payload(traces, include_outputs=True)
        blob = json.dumps(payload)
        assert secret not in blob and "[REDACTED]" in blob


class TestDetection:
    """Tests for local (offline) detection via _run_local_detection.

    _run_local_detection returns a LIST of detection dicts and currently covers
    tool-loop detection (identical consecutive calls)."""

    def test_empty_traces_no_detections(self):
        from pisama_claude_code.cli import _run_local_detection

        assert _run_local_detection([]) == []

    def test_finds_tool_loop(self):
        from pisama_claude_code.cli import _run_local_detection

        traces = [{"tool_name": "Bash", "tool_input": {"command": "ls"}} for _ in range(15)]
        results = _run_local_detection(traces)
        loops = [d for d in results if "loop" in d.get("type", "").lower()]
        assert loops, "expected a loop detection"
        assert loops[0]["details"]["repetition_count"] >= 3

    def test_no_loop_for_varied_calls(self):
        from pisama_claude_code.cli import _run_local_detection

        traces = [{"tool_name": "Bash", "tool_input": {"command": f"echo {i}"}} for i in range(15)]
        results = _run_local_detection(traces)
        assert not [d for d in results if "loop" in d.get("type", "").lower()]


class TestTokenizeConfig:
    """tokenize config must report the switches that actually exist (the
    PISAMA_TOKENIZATION env var, hardcoded fail-open), not the unread
    config.json tokenization block it used to display."""

    def test_reports_env_var_switch(self, monkeypatch):
        from pisama_claude_code.cli import tokenize_config

        monkeypatch.delenv("PISAMA_TOKENIZATION", raising=False)
        result = CliRunner().invoke(tokenize_config)
        assert result.exit_code == 0
        assert "PISAMA_TOKENIZATION" in result.output
        assert "Enabled: True" in result.output
        assert "Tokenized fields:" in result.output
        assert "tool_input" in result.output
        assert "Custom Patterns" not in result.output

    def test_disabled_via_env(self, monkeypatch):
        from pisama_claude_code.cli import tokenize_config

        monkeypatch.setenv("PISAMA_TOKENIZATION", "0")
        result = CliRunner().invoke(tokenize_config)
        assert result.exit_code == 0
        assert "Enabled: False" in result.output


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
