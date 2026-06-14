"""Tests for the full-fidelity logging proxy (proxy.py)."""

import asyncio
import json

import pytest

from pisama_claude_code import proxy


SSE = (
    b'event: message_start\n'
    b'data: {"type":"message_start","message":{"model":"claude-opus-4-8",'
    b'"usage":{"input_tokens":12,"cache_read_input_tokens":9000}}}\n\n'
    b'data: {"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}\n\n'
    b'data: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"Let me reason "}}\n\n'
    b'data: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"about this."}}\n\n'
    b'data: {"type":"content_block_start","index":1,"content_block":{"type":"text","text":""}}\n\n'
    b'data: {"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"The answer is 42."}}\n\n'
    b'data: {"type":"content_block_start","index":2,"content_block":{"type":"tool_use","id":"tu_1","name":"Bash"}}\n\n'
    b'data: {"type":"content_block_delta","index":2,"delta":{"type":"input_json_delta","partial_json":"{\\"command\\":"}}\n\n'
    b'data: {"type":"content_block_delta","index":2,"delta":{"type":"input_json_delta","partial_json":" \\"ls\\"}"}}\n\n'
    b'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"},"usage":{"output_tokens":57}}\n\n'
    b'data: [DONE]\n\n'
)


# --------------------------- pure parser tests ---------------------------- #

def test_reassemble_sse_recovers_reasoning_and_output():
    s = proxy.reassemble_sse(SSE)
    assert s["reasoning"] == "Let me reason about this."   # the whole point
    assert s["output"] == "The answer is 42."
    assert s["tool_calls"] == [{"name": "Bash", "input": {"command": "ls"}, "id": "tu_1"}]
    assert s["usage"]["input_tokens"] == 12
    assert s["usage"]["cache_read_input_tokens"] == 9000
    assert s["usage"]["output_tokens"] == 57
    assert s["model"] == "claude-opus-4-8"
    assert s["stop_reason"] == "tool_use"


def test_parse_request_finds_user_input_skipping_tool_results():
    body = json.dumps({
        "model": "claude-x",
        "system": [{"type": "text", "text": "You are helpful."}],
        "messages": [
            {"role": "user", "content": "hi there"},
            {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "ok"}]},
        ],
        "stream": True,
    }).encode()
    p = proxy.parse_request(body)
    assert p["user_input"] == "hi there"      # skips the tool_result-only last turn
    assert p["system"] == "You are helpful."
    assert p["stream"] is True


def test_parse_json_response_non_streaming():
    body = json.dumps({
        "model": "m",
        "content": [
            {"type": "thinking", "thinking": "hmm"},
            {"type": "text", "text": "done"},
            {"type": "tool_use", "id": "t", "name": "Read", "input": {"file_path": "a"}},
        ],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 3, "output_tokens": 4},
    }).encode()
    s = proxy.parse_json_response(body)
    assert s["reasoning"] == "hmm"
    assert s["output"] == "done"
    assert s["tool_calls"][0]["name"] == "Read"


def test_conversation_id_stable_across_turns():
    base = {"system": "S", "messages": [{"role": "user", "content": "first"}]}
    grown = {"system": "S", "messages": [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "..."},
        {"role": "user", "content": "second"},
    ]}
    assert proxy.conversation_id(base) == proxy.conversation_id(grown)


def test_build_record_shape():
    parsed = proxy.parse_request(json.dumps(
        {"model": "claude-opus-4-8", "system": "s", "messages": [{"role": "user", "content": "q"}]}
    ).encode())
    rec = proxy.build_record(parsed, proxy.reassemble_sse(SSE),
                             timestamp="2026-06-14T00:00:00+00:00", status=200, duration_ms=123)
    assert rec["reasoning"] == "Let me reason about this."
    assert rec["user_input"] == "q"
    assert rec["conversation_id"].startswith("cc-proxy-")
    assert rec["status"] == 200 and rec["duration_ms"] == 123


# ----------------------- always-on wiring (pure) -------------------------- #

def test_set_and_unset_base_url(tmp_path):
    sp = tmp_path / "settings.json"
    sp.write_text(json.dumps({"hooks": {"PostToolUse": []}}))  # preserve other keys
    url = proxy.set_base_url(8788, settings_path=sp)
    assert url == "http://127.0.0.1:8788"
    data = json.loads(sp.read_text())
    assert data["env"]["ANTHROPIC_BASE_URL"] == url
    assert "hooks" in data                      # untouched
    assert proxy.configured_base_url(settings_path=sp) == url
    assert proxy.unset_base_url(settings_path=sp) is True
    assert "ANTHROPIC_BASE_URL" not in json.loads(sp.read_text()).get("env", {})


def test_shell_export_add_remove_idempotent(tmp_path):
    prof = tmp_path / ".zshrc"
    prof.write_text("export PATH=/usr/bin\n")

    proxy.add_shell_export(8788, profile=prof)
    txt = prof.read_text()
    assert "export ANTHROPIC_BASE_URL=http://127.0.0.1:8788" in txt
    assert proxy.SHELL_MARKER_BEGIN in txt
    assert "export PATH=/usr/bin" in txt              # pre-existing content preserved

    # re-install updates value without duplicating the block
    proxy.add_shell_export(8799, profile=prof)
    txt = prof.read_text()
    assert txt.count(proxy.SHELL_MARKER_BEGIN) == 1
    assert "8799" in txt and "8788" not in txt

    assert proxy.remove_shell_export(profile=prof) is True
    txt = prof.read_text()
    assert proxy.SHELL_MARKER_BEGIN not in txt
    assert "export PATH=/usr/bin" in txt              # still preserved
    assert proxy.remove_shell_export(profile=prof) is False  # nothing left to remove


def test_launchd_plist_has_keepalive_and_serve_args():
    xml = proxy.launchd_plist_xml(8788)
    assert "ai.pisama.cc-proxy" in xml
    assert "<key>KeepAlive</key><true/>" in xml
    assert "proxy" in xml and "serve" in xml and "8788" in xml


# --------------------------- CLI smoke tests ------------------------------ #

def test_cli_proxy_install_print_only():
    from click.testing import CliRunner
    from pisama_claude_code.cli import main
    r = CliRunner().invoke(main, ["proxy", "install"])
    assert r.exit_code == 0
    assert "Opt-in" in r.output and "always-on" in r.output.lower()


def test_cli_proxy_status_runs(monkeypatch, tmp_path):
    from click.testing import CliRunner
    from pisama_claude_code.cli import main
    monkeypatch.setattr(proxy, "PROXY_DIR", tmp_path)
    monkeypatch.setattr(proxy, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(proxy, "SHELL_PROFILE", tmp_path / ".zshrc")  # never read real profile
    r = CliRunner().invoke(main, ["proxy", "status", "--port", "1"])  # nothing on :1
    assert r.exit_code == 0
    assert "Proxy:" in r.output and "Routing:" in r.output


def test_cli_proxy_uninstall_hermetic(monkeypatch, tmp_path):
    from click.testing import CliRunner
    from pisama_claude_code.cli import main
    # Fully sandbox every path uninstall touches - must NOT mutate real ~/.zshrc etc.
    monkeypatch.setattr(proxy, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(proxy, "LAUNCHD_PLIST", tmp_path / "agent.plist")
    monkeypatch.setattr(proxy, "SHELL_PROFILE", tmp_path / ".zshrc")
    r = CliRunner().invoke(main, ["proxy", "uninstall"])
    assert r.exit_code == 0
    assert "disabled" in r.output.lower()


# -------------------- integration: real streaming proxy ------------------- #

@pytest.mark.asyncio
async def test_proxy_passthrough_and_capture(tmp_path, monkeypatch):
    """End to end: a request through the proxy returns upstream bytes intact AND
    a capture record (with reasoning) is written - no Anthropic creds needed."""
    aiohttp = pytest.importorskip("aiohttp")
    from aiohttp import web
    from aiohttp.test_utils import TestServer

    monkeypatch.setattr(proxy, "PROXY_DIR", tmp_path)

    seen_upstream = {}

    async def upstream_handler(request):
        seen_upstream["path"] = request.path
        seen_upstream["api_key"] = request.headers.get("x-api-key")
        seen_upstream["body"] = await request.read()
        resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        # stream in two chunks to exercise the tee
        await resp.write(SSE[: len(SSE) // 2])
        await resp.write(SSE[len(SSE) // 2:])
        await resp.write_eof()
        return resp

    up = web.Application()
    up.router.add_route("*", "/{tail:.*}", upstream_handler)
    up_server = TestServer(up)
    await up_server.start_server()
    upstream_url = f"http://127.0.0.1:{up_server.port}"

    app = proxy.make_app(upstream=upstream_url, forward=False)
    proxy_server = TestServer(app)
    await proxy_server.start_server()
    purl = f"http://127.0.0.1:{proxy_server.port}"

    req_body = json.dumps({
        "model": "claude-opus-4-8",
        "system": "sys",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
    }).encode()

    try:
        async with aiohttp.ClientSession() as s:
            # health endpoint short-circuits (no upstream call)
            async with s.get(purl + "/__pisama/health") as h:
                assert h.status == 200 and (await h.json())["ok"] is True
            # real request flows through to the mock upstream
            async with s.post(purl + "/v1/messages", data=req_body,
                              headers={"content-type": "application/json", "x-api-key": "sk-test"}) as r:
                got = await r.read()

        assert got == SSE                                   # byte-for-byte passthrough
        assert seen_upstream["path"].endswith("/v1/messages")
        assert seen_upstream["api_key"] == "sk-test"        # auth forwarded untouched

        # capture happens just after write_eof; poll briefly for the file
        rec = None
        for _ in range(100):
            files = list(tmp_path.glob("calls-*.jsonl"))
            if files and files[0].read_text().strip():
                rec = json.loads(files[0].read_text().splitlines()[-1])
                break
            await asyncio.sleep(0.01)
        assert rec is not None, "no capture record written"
        assert rec["reasoning"] == "Let me reason about this."
        assert rec["output"] == "The answer is 42."
        assert rec["user_input"] == "hello"
        assert rec["usage"]["cache_read_input_tokens"] == 9000
    finally:
        await proxy_server.close()
        await up_server.close()
