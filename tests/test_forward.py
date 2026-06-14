"""Tests for the platform wire layer + Stop forward hook."""

import base64
import io
import json
import sys
from unittest.mock import MagicMock, patch

from pisama_claude_code import cli
from pisama_claude_code.hooks import forward_hook


def _jwt(exp, tenant="t-1"):
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": exp, "tenant_id": tenant}).encode()
    ).decode().rstrip("=")
    return f"h.{payload}.s"


def _record(session_id, tool, output, ts):
    return json.dumps({
        "session_id": session_id,
        "timestamp": ts,
        "hook_type": "post",
        "tool_name": tool,
        "tool_input": {"command": "x"},
        "tool_output": output,
        "user_input": "hi",
        "ai_output": "ok",
        "model": "claude",
        "usage": {"input_tokens": 1, "output_tokens": 2},
        "cost_usd": 0.0,
    })


def test_api_url_builds_prefix():
    assert cli.api_url(
        {"api_url": "http://localhost:8000"}, "/traces/claude-code/ingest"
    ) == "http://localhost:8000/api/v1/traces/claude-code/ingest"
    # tolerates a trailing /api and trailing slash
    assert cli.api_url(
        {"api_url": "https://api.pisama.ai/api/"}, "/auth/token"
    ) == "https://api.pisama.ai/api/v1/auth/token"


def test_get_jwt_exchanges_and_caches(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path)
    token = _jwt(99999999999)
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"access_token": token}

    with patch.object(cli, "httpx") as mock_httpx:
        mock_httpx.post.return_value = resp
        config = {"api_key": "pisama_x", "api_url": "http://localhost:8000"}

        assert cli.get_jwt(config) == token
        assert config["tenant_id"] == "t-1"
        # Verify it hit the token endpoint with the right body.
        url = mock_httpx.post.call_args.args[0]
        assert url.endswith("/api/v1/auth/token")
        assert mock_httpx.post.call_args.kwargs["json"] == {
            "api_key": "pisama_x", "scope": "ingest"
        }

        # Cached: a second call must not re-exchange.
        mock_httpx.post.reset_mock()
        assert cli.get_jwt(config) == token
        mock_httpx.post.assert_not_called()


def test_get_jwt_returns_none_on_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path)
    with patch.object(cli, "httpx") as mock_httpx:
        mock_httpx.post.return_value = MagicMock(status_code=401)
        assert cli.get_jwt({"api_key": "bad", "api_url": "http://x"}) is None


def test_push_sessions_forwards_complete_session(tmp_path, monkeypatch):
    traces_dir = tmp_path / "traces"
    traces_dir.mkdir()
    (traces_dir / "traces-2026-01-01.jsonl").write_text(
        _record("s1", "Bash", "OUT1", "2026-01-01T00:00:01+00:00") + "\n"
        + _record("s1", "Read", "OUT2", "2026-01-01T00:00:02+00:00") + "\n"
        + _record("s2", "Bash", "OUT3", "2026-01-01T00:00:03+00:00") + "\n"
    )
    monkeypatch.setattr(cli, "TRACES_DIR", traces_dir)
    monkeypatch.setattr(cli, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path)

    token = _jwt(99999999999)
    captured = {}

    def fake_post(url, **kw):
        if url.endswith("/auth/token"):
            r = MagicMock(status_code=200)
            r.json.return_value = {"access_token": token}
            return r
        captured["url"] = url
        captured["json"] = kw.get("json")
        captured["headers"] = kw.get("headers")
        r = MagicMock(status_code=202)
        r.json.return_value = {"traces_stored": 2}
        return r

    with patch.object(cli, "httpx") as mock_httpx:
        mock_httpx.post.side_effect = fake_post
        ok, msg, result = cli.push_sessions(
            {"api_key": "pisama_x", "api_url": "http://localhost:8000"},
            ["s1"],
            include_outputs=True,
        )

    assert ok, msg
    assert captured["url"].endswith("/api/v1/traces/claude-code/ingest")
    assert captured["headers"]["Authorization"] == f"Bearer {token}"
    sent = captured["json"]["traces"]
    # Only s1, and BOTH of its traces (complete session, not a slice).
    assert {t["session_id"] for t in sent} == {"s1"}
    assert len(sent) == 2
    # Full content: tool outputs are forwarded.
    assert {t.get("tool_output") for t in sent} == {"OUT1", "OUT2"}


def test_forward_hook_noops_when_not_connected(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"session_id": "s1"})))
    with patch.object(cli, "httpx") as mock_httpx:
        forward_hook.main()
        mock_httpx.post.assert_not_called()


def test_forward_hook_forwards_when_connected(tmp_path, monkeypatch):
    traces_dir = tmp_path / "traces"
    traces_dir.mkdir()
    (traces_dir / "traces-2026-01-01.jsonl").write_text(
        _record("s1", "Bash", "OUT1", "2026-01-01T00:00:01+00:00") + "\n"
    )
    monkeypatch.setattr(cli, "TRACES_DIR", traces_dir)
    monkeypatch.setattr(cli, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path)
    cli.save_config({
        "api_key": "pisama_x",
        "api_url": "http://localhost:8000",
        "auto_sync": True,
    })
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"session_id": "s1"})))

    token = _jwt(99999999999)
    posted = []

    def fake_post(url, **kw):
        posted.append(url)
        r = MagicMock(status_code=200 if url.endswith("/auth/token") else 202)
        r.json.return_value = (
            {"access_token": token} if url.endswith("/auth/token")
            else {"traces_stored": 1}
        )
        return r

    with patch.object(cli, "httpx") as mock_httpx:
        mock_httpx.post.side_effect = fake_post
        forward_hook.main()

    assert any(u.endswith("/api/v1/traces/claude-code/ingest") for u in posted)
