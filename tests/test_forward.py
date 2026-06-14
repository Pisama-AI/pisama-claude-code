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


def test_emit_span_appends_single_otlp_span_with_reasoning(tmp_path, monkeypatch):
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
        r = MagicMock(status_code=202)
        r.json.return_value = {"accepted": 1}
        return r

    with patch.object(cli, "httpx") as mock_httpx:
        mock_httpx.post.side_effect = fake_post
        ok, msg = cli.emit_span(
            {
                "session_id": "s1",
                "timestamp": "2026-01-01T00:00:01+00:00",
                "hook_type": "post",
                "tool_name": "Bash",
                "tool_input": {"command": "ls"},
                "tool_output": "out",
                "model": "claude",
                "input_tokens": 1,
                "output_tokens": 2,
                "user_input": "hi",
                "reasoning": "let me think about this",
                "ai_output": "done",
            },
            {"api_key": "pisama_x", "api_url": "http://localhost:8000", "auto_sync": True},
        )

    assert ok, msg
    # Tenant-scoped append endpoint (tenant from the JWT payload), NOT the batch
    # claude-code endpoint and NOT the bare keyless path (which needs ?tenant_id).
    assert captured["url"].endswith("/api/v1/tenants/t-1/traces/ingest")
    span = captured["json"]["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    state_attr = next(a for a in span["attributes"] if a["key"] == "gen_ai.state")
    state = json.loads(state_attr["value"]["stringValue"])
    # Reasoning survives in the lossless state channel.
    assert state["reasoning"] == "let me think about this"
    assert state["user_input"] == "hi"
    # Marked synced so the Stop reconcile won't re-emit it.
    assert ("s1", "2026-01-01T00:00:01+00:00") in cli._synced_keys()


def test_emit_span_noops_when_auto_sync_off():
    ok, msg = cli.emit_span(
        {"session_id": "s1", "timestamp": "t"},
        {"api_key": "pisama_x", "auto_sync": False},
    )
    assert not ok
    assert "auto-sync" in msg


def test_forward_command_toggles_auto_sync(tmp_path, monkeypatch):
    from click.testing import CliRunner
    monkeypatch.setattr(cli, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path)
    cli.save_config({"api_key": "pisama_x", "api_url": "https://api.pisama.ai", "auto_sync": False})

    r = CliRunner().invoke(cli.main, ["forward", "on"])
    assert r.exit_code == 0, r.output
    assert cli.get_config()["auto_sync"] is True

    r = CliRunner().invoke(cli.main, ["forward", "off"])
    assert r.exit_code == 0, r.output
    assert cli.get_config()["auto_sync"] is False


def test_forward_command_requires_connection(tmp_path, monkeypatch):
    from click.testing import CliRunner
    monkeypatch.setattr(cli, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path)
    r = CliRunner().invoke(cli.main, ["forward", "on"])
    assert r.exit_code == 0
    assert "Not connected" in r.output
    assert cli.get_config().get("auto_sync") in (None, False)


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
        posted.append((url, kw.get("json")))
        r = MagicMock(status_code=200 if url.endswith("/auth/token") else 202)
        r.json.return_value = (
            {"access_token": token} if url.endswith("/auth/token")
            else {"accepted": 1}
        )
        return r

    with patch.object(cli, "httpx") as mock_httpx:
        mock_httpx.post.side_effect = fake_post
        forward_hook.main()

    # Real-time reconcile now appends single OTLP spans to the tenant-scoped
    # ingest, NOT the old delete-and-replace claude-code batch endpoint.
    ingests = [(u, j) for (u, j) in posted if u.endswith("/traces/ingest")]
    assert ingests, posted
    assert all("/tenants/" in u for (u, _) in ingests), ingests
    assert not any(u.endswith("/traces/claude-code/ingest") for (u, _) in posted)
    # OTLP shape + reasoning/content rides in gen_ai.state.
    _, payload = ingests[0]
    span = payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    attr_keys = {a["key"] for a in span["attributes"]}
    assert "gen_ai.state" in attr_keys
