"""Tests for PISAMA Claude Code CLI."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
from click.testing import CliRunner

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pisama_claude_code.cli import main, get_config, save_config


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
        """Test --version flag."""
        runner = CliRunner()
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "0.4.3" in result.output

    def test_help(self):
        """Test --help flag."""
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "Pisama Claude Code" in result.output
        assert "connect" in result.output
        assert "sync" in result.output
        assert "analyze" in result.output
    
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

                    result = runner.invoke(main, [
                        "connect",
                        "--api-key", "pk_test_123",
                        "--api-url", "http://localhost:8000"
                    ])

                    # Should save config even if connection fails
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
        trace_file.write_text(json.dumps({
            "timestamp": "2026-01-01T00:00:00+00:00",
            "tool_name": "Bash",
            "hook_type": "PreToolUse",
            "session_id": "test-session",
            "tool_input": {"command": "echo hello"}
        }) + "\n")
        
        with patch("pisama_claude_code.cli.TRACES_DIR", traces_dir):
            result = runner.invoke(main, [
                "export",
                "--last", "10",
                "-o", str(output_file)
            ])
            
            assert output_file.exists()
            assert "Exported" in result.output


class TestApiPaths:
    """Pin the full request paths the CLI hits — the backend mounts everything
    under /api/v1, so a bare /v1 path 404s in production."""

    def test_api_url_builds_api_v1_path(self):
        from pisama_claude_code.cli import api_url

        assert (
            api_url({"api_url": "https://api.pisama.ai"}, "/auth/token")
            == "https://api.pisama.ai/api/v1/auth/token"
        )
        # Trailing slash and an explicit /api suffix must not double the prefix
        assert (
            api_url({"api_url": "https://api.pisama.ai/"}, "/health")
            == "https://api.pisama.ai/api/v1/health"
        )
        assert (
            api_url({"api_url": "https://api.pisama.ai/api"}, "/health")
            == "https://api.pisama.ai/api/v1/health"
        )
        # Missing api_url falls back to the production default
        assert api_url({}, "/health") == "https://api.pisama.ai/api/v1/health"

    def test_sync_posts_to_api_v1_paths(self, tmp_path):
        """sync must exchange the key at POST /api/v1/auth/token and upload to
        POST /api/v1/traces/claude-code/ingest."""
        import httpx as real_httpx

        runner = CliRunner()
        config_file = tmp_path / "config.json"
        traces_dir = tmp_path / "traces"
        traces_dir.mkdir()
        (traces_dir / "traces-2026-01-01.jsonl").write_text(json.dumps({
            "timestamp": "2026-01-01T00:00:00+00:00",
            "tool_name": "Bash",
            "hook_type": "PreToolUse",
            "session_id": "test-session",
            "tool_input": {"command": "echo hello"},
        }) + "\n")

        posted = []

        def fake_post(url, **kwargs):
            posted.append((url, kwargs))
            resp = MagicMock()
            resp.status_code = 200
            if url.endswith("/auth/token"):
                resp.json.return_value = {"access_token": "eyJ.test.jwt"}
            else:
                resp.json.return_value = {"traces_received": 1}
            return resp

        with patch("pisama_claude_code.cli.CONFIG_FILE", config_file), \
             patch("pisama_claude_code.cli.CONFIG_DIR", tmp_path), \
             patch("pisama_claude_code.cli.TRACES_DIR", traces_dir), \
             patch("pisama_claude_code.cli._jwt_cache", {}), \
             patch("pisama_claude_code.cli.httpx") as mock_httpx:
            save_config({"api_key": "pisama_testkey", "api_url": "https://api.pisama.ai"})
            mock_httpx.ConnectError = real_httpx.ConnectError
            mock_httpx.post.side_effect = fake_post

            result = runner.invoke(main, ["sync"])

        assert result.exit_code == 0, result.output
        assert [url for url, _ in posted] == [
            "https://api.pisama.ai/api/v1/auth/token",
            "https://api.pisama.ai/api/v1/traces/claude-code/ingest",
        ]
        # The ingest call must carry the exchanged JWT, not the raw API key
        ingest_headers = posted[1][1]["headers"]
        assert ingest_headers["Authorization"] == "Bearer eyJ.test.jwt"
        assert "Synced" in result.output


class TestPrivacy:
    """Tests for privacy and redaction."""
    
    def test_sanitize_input_redacts_secrets(self):
        """Test that sensitive fields are redacted."""
        from pisama_claude_code.cli import sanitize_input
        
        inp = {
            "command": "echo hello",
            "api_key": "sk-secret123",
            "password": "mypassword",
            "normal_field": "visible"
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
    
    def test_sanitize_input_truncates_long_values(self):
        """Test that long values are truncated."""
        from pisama_claude_code.cli import sanitize_input
        
        inp = {
            "content": "x" * 1000  # Very long content
        }
        
        result = sanitize_input(inp)
        
        assert len(result["content"]) < 600
        assert "[truncated]" in result["content"]


class TestDetection:
    """Tests for failure detection."""

    def test_run_detection_empty_traces(self):
        """Test detection with no traces."""
        from pisama_claude_code.cli import _run_local_detection

        assert _run_local_detection([]) == []

    def test_run_detection_finds_loops(self):
        """Test detection finds consecutive repeated calls."""
        from pisama_claude_code.cli import _run_local_detection

        # Create 15 consecutive identical Bash calls
        traces = [{"tool_name": "Bash", "tool_input": {}} for _ in range(15)]

        detections = _run_local_detection(traces)

        assert any(d["type"] == "Tool Loop Detected" for d in detections)


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
