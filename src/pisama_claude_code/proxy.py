"""Logging reverse-proxy for full-fidelity Claude Code capture.

Claude Code strips extended-thinking text before anything touches disk, so the
transcript/hook capture can never see reasoning. The thinking text DOES exist in
the live API response stream. This proxy sits in front of api.anthropic.com
(via ANTHROPIC_BASE_URL), passes every request through transparently, and tees a
copy of the streamed response so it can reassemble the full turn INCLUDING the
reasoning, plus the exact request payload (system prompt, full message history,
tools) and token usage.

Design rules:
- The pass-through is the hot path of every Claude Code request. Capture is
  strictly best-effort and runs AFTER the client already has every byte, so a
  capture/forward failure can never slow or break a session.
- Pure parsing helpers (parse_request / reassemble_sse / build_record) need no
  network and are unit-tested directly.

Two ways to use it:
- Opt-in, per session:   pisama-cc proxy serve   then run Claude Code with
  ANTHROPIC_BASE_URL pointed at it for the session you want deep-captured.
- Always-on:             pisama-cc proxy install --always-on   wires
  ANTHROPIC_BASE_URL into ~/.claude/settings.json and installs a KeepAlive
  launchd agent so the proxy is always up (so it can't brick Claude Code).
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import aiohttp
    from aiohttp import web
except ImportError:  # the [proxy] extra is not installed
    aiohttp = None
    web = None


DEFAULT_PORT = 8788
DEFAULT_UPSTREAM = "https://api.anthropic.com"
PROXY_DIR = Path.home() / ".claude" / "pisama" / "proxy"

# Hop-by-hop headers that must not be forwarded verbatim.
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}


# --------------------------------------------------------------------------- #
# Pure parsing helpers (no network; unit-tested)
# --------------------------------------------------------------------------- #

def _system_text(system: Any) -> Optional[str]:
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        parts = [b.get("text", "") for b in system if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(p for p in parts if p) or None
    return None


def parse_request(body: bytes) -> Dict[str, Any]:
    """Extract model / system / messages / tools / last-user-text from a
    /v1/messages request body. Returns {} if the body is not JSON."""
    try:
        req = json.loads(body)
    except (ValueError, TypeError):
        return {}
    if not isinstance(req, dict):
        return {}

    messages = req.get("messages") or []
    user_input = None
    for m in reversed(messages):
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, str):
            user_input = c
            break
        if isinstance(c, list):
            has_tool_result = any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c)
            texts = [b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"]
            texts = [t for t in texts if t]
            if texts:
                user_input = "\n".join(texts)
                break
            if has_tool_result:
                continue  # tool-result-only turn, keep looking back for real input
    return {
        "model": req.get("model"),
        "system": _system_text(req.get("system")),
        "messages": messages,
        "tools": req.get("tools"),
        "user_input": user_input,
        "stream": bool(req.get("stream")),
    }


def reassemble_sse(raw: bytes) -> Dict[str, Any]:
    """Reassemble a streamed Anthropic Messages response into a turn summary.

    Recovers the reasoning (thinking_delta), output text, tool calls, model and
    token usage from the SSE event stream.
    """
    text_parts: List[str] = []
    thinking_parts: List[str] = []
    tool_calls: List[Dict[str, Any]] = []
    usage: Dict[str, Any] = {}
    model: Optional[str] = None
    stop_reason: Optional[str] = None
    blocks: Dict[int, Dict[str, Any]] = {}

    for line in raw.split(b"\n"):
        line = line.strip()
        if not line.startswith(b"data:"):
            continue
        data = line[len(b"data:"):].strip()
        if not data or data == b"[DONE]":
            continue
        try:
            ev = json.loads(data)
        except ValueError:
            continue
        etype = ev.get("type")

        if etype == "message_start":
            msg = ev.get("message", {}) or {}
            model = msg.get("model") or model
            if isinstance(msg.get("usage"), dict):
                usage.update(msg["usage"])
        elif etype == "content_block_start":
            idx = ev.get("index")
            cb = ev.get("content_block", {}) or {}
            blocks[idx] = {
                "type": cb.get("type"),
                "name": cb.get("name"),
                "id": cb.get("id"),
                "text": cb.get("text", "") or "",
                "thinking": cb.get("thinking", "") or "",
                "json": "",
            }
        elif etype == "content_block_delta":
            idx = ev.get("index")
            d = ev.get("delta", {}) or {}
            b = blocks.get(idx)
            if b is None:
                b = blocks[idx] = {"type": None, "text": "", "thinking": "", "json": ""}
            dt = d.get("type")
            if dt == "text_delta":
                b["text"] += d.get("text", "")
                b["type"] = b["type"] or "text"
            elif dt == "thinking_delta":
                b["thinking"] += d.get("thinking", "")
                b["type"] = b["type"] or "thinking"
            elif dt == "input_json_delta":
                b["json"] += d.get("partial_json", "")
                b["type"] = b["type"] or "tool_use"
        elif etype == "message_delta":
            d = ev.get("delta", {}) or {}
            if d.get("stop_reason"):
                stop_reason = d["stop_reason"]
            if isinstance(ev.get("usage"), dict):
                usage.update(ev["usage"])

    for idx in sorted(blocks):
        b = blocks[idx]
        if b["type"] == "text" and b["text"]:
            text_parts.append(b["text"])
        elif b["type"] == "thinking" and b["thinking"]:
            thinking_parts.append(b["thinking"])
        elif b["type"] == "tool_use":
            try:
                inp = json.loads(b["json"]) if b["json"] else {}
            except ValueError:
                inp = {"_raw": b["json"]}
            tool_calls.append({"name": b.get("name"), "input": inp, "id": b.get("id")})

    return {
        "model": model,
        "output": "\n\n".join(text_parts) or None,
        "reasoning": "\n\n".join(thinking_parts) or None,
        "tool_calls": tool_calls or None,
        "usage": usage,
        "stop_reason": stop_reason,
    }


def parse_json_response(body: bytes) -> Dict[str, Any]:
    """Reassemble a NON-streamed Messages response (a single JSON object)."""
    try:
        resp = json.loads(body)
    except (ValueError, TypeError):
        return {}
    if not isinstance(resp, dict):
        return {}
    text_parts, thinking_parts, tool_calls = [], [], []
    for b in resp.get("content", []) or []:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text" and b.get("text"):
            text_parts.append(b["text"])
        elif b.get("type") == "thinking" and b.get("thinking"):
            thinking_parts.append(b["thinking"])
        elif b.get("type") == "tool_use":
            tool_calls.append({"name": b.get("name"), "input": b.get("input"), "id": b.get("id")})
    return {
        "model": resp.get("model"),
        "output": "\n\n".join(text_parts) or None,
        "reasoning": "\n\n".join(thinking_parts) or None,
        "tool_calls": tool_calls or None,
        "usage": resp.get("usage", {}) or {},
        "stop_reason": resp.get("stop_reason"),
    }


def conversation_id(parsed: Dict[str, Any]) -> str:
    """Stable id for the conversation: hash of system + first user turn.

    The same across every API call of one Claude Code conversation."""
    first_user = ""
    for m in parsed.get("messages") or []:
        if isinstance(m, dict) and m.get("role") == "user":
            c = m.get("content")
            first_user = c if isinstance(c, str) else json.dumps(c, sort_keys=True)[:2000]
            break
    key = ((parsed.get("system") or "")[:500] + "||" + first_user[:2000]).encode()
    return "cc-proxy-" + hashlib.sha1(key).hexdigest()[:16]


def build_record(parsed_req: Dict[str, Any], summary: Dict[str, Any],
                 *, timestamp: str, status: int, duration_ms: Optional[int]) -> Dict[str, Any]:
    """Combine the parsed request + reassembled response into a stored record."""
    usage = summary.get("usage") or {}
    model = summary.get("model") or parsed_req.get("model")
    try:
        from pisama_claude_code.hooks.capture_hook import calculate_cost
        cost = calculate_cost(model, usage) if model and usage else 0.0
    except Exception:
        cost = 0.0
    return {
        "ts": timestamp,
        "conversation_id": conversation_id(parsed_req),
        "model": model,
        "system": parsed_req.get("system"),
        "input_messages": parsed_req.get("messages"),
        "user_input": parsed_req.get("user_input"),
        "reasoning": summary.get("reasoning"),
        "output": summary.get("output"),
        "tool_calls": summary.get("tool_calls"),
        "stop_reason": summary.get("stop_reason"),
        "usage": usage,
        "cost_usd": cost,
        "status": status,
        "duration_ms": duration_ms,
    }


# --------------------------------------------------------------------------- #
# Local storage + forwarding
# --------------------------------------------------------------------------- #

def store_record(record: Dict[str, Any], proxy_dir: Optional[Path] = None) -> None:
    proxy_dir = proxy_dir or PROXY_DIR
    proxy_dir.mkdir(parents=True, exist_ok=True)
    date_str = record.get("ts", "")[:10] or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = proxy_dir / f"calls-{date_str}.jsonl"
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")


def _records_for_conversation(conv_id: str, proxy_dir: Optional[Path] = None, max_files: int = 7) -> List[Dict[str, Any]]:
    proxy_dir = proxy_dir or PROXY_DIR
    out: List[Dict[str, Any]] = []
    if not proxy_dir.exists():
        return out
    for fp in sorted(proxy_dir.glob("calls-*.jsonl"), reverse=True)[:max_files]:
        try:
            for line in fp.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("conversation_id") == conv_id:
                    out.append(rec)
        except OSError:
            continue
    out.sort(key=lambda r: r.get("ts") or "")
    return out


def _record_to_trace(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Map a proxy call record to the backend Claude Code trace shape."""
    usage = rec.get("usage") or {}
    tool_calls = rec.get("tool_calls") or []
    tool_name = "assistant_turn"
    if tool_calls and tool_calls[0].get("name"):
        tool_name = tool_calls[0]["name"]
    return {
        "timestamp": rec.get("ts"),
        "tool_name": tool_name,
        "hook_type": "proxy",
        "session_id": rec.get("conversation_id"),
        "tool_input": {"tool_calls": tool_calls} if tool_calls else None,
        "user_input": rec.get("user_input"),
        "reasoning": rec.get("reasoning"),
        "ai_output": rec.get("output"),
        "model": rec.get("model"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cache_read_tokens": usage.get("cache_read_input_tokens"),
        "cost_usd": rec.get("cost_usd"),
    }


def forward_conversation(conv_id: str, proxy_dir: Optional[Path] = None) -> tuple:
    """Forward a whole conversation's calls (incl. reasoning) to Pisama.

    Returns (ok, message). Best-effort; safe to call repeatedly (the backend
    replaces the session on each ingest)."""
    try:
        import httpx

        from pisama_claude_code.cli import _cap_field, api_url, get_config, get_jwt
    except Exception as e:  # noqa: BLE001
        return False, f"forward unavailable: {e}"

    config = get_config()
    if not config.get("api_key") or not config.get("auto_sync", False):
        return False, "not connected / auto-sync off"

    token = get_jwt(config)
    if not token:
        return False, "auth failed"

    records = _records_for_conversation(conv_id, proxy_dir)
    if not records:
        return False, "no records"

    traces = []
    for rec in records:
        t = _record_to_trace(rec)
        for k in ("user_input", "reasoning", "ai_output"):
            if isinstance(t.get(k), str):
                t[k] = _cap_field(t[k])
        traces.append(t)

    payload = {
        "source": "claude-code-proxy",
        "version": "0.4.3",
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "trace_count": len(traces),
        "traces": traces,
    }
    try:
        resp = httpx.post(
            api_url(config, "/traces/claude-code/ingest"),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload, timeout=30,
        )
    except Exception as e:  # noqa: BLE001
        return False, f"network error: {e}"
    if resp.status_code in (200, 201, 202):
        return True, "ok"
    return False, f"{resp.status_code}: {resp.text[:160]}"


def emit_proxy_record(rec: Dict[str, Any]) -> tuple:
    """Forward ONE proxy call record (incl. reasoning) as a single appended span.

    The real-time proxy path: emit just this API call's span via the append-only
    ``/traces/ingest`` endpoint (idempotent on state_hash), instead of replaying
    the whole conversation to the batch endpoint on every call. Best-effort;
    returns ``(ok, message)`` and never raises into the request path.
    """
    try:
        from pisama_claude_code.cli import emit_span, get_config
    except Exception as e:  # noqa: BLE001
        return False, f"emit unavailable: {e}"
    config = get_config()
    if not config.get("api_key") or not config.get("auto_sync", False):
        return False, "not connected / auto-sync off"
    return emit_span(_record_to_trace(rec), config)


# --------------------------------------------------------------------------- #
# The streaming reverse proxy (aiohttp)
# --------------------------------------------------------------------------- #

def _forward_request_headers(headers) -> Dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP}


def _capture(parsed_req, body_resp, content_type, *, status, duration_ms, forward,
             path=None, content_encoding=None):
    """Best-effort: reassemble, store, and (optionally) schedule a forward.

    Never raises into the request path; callers wrap in try/except too."""
    raw = _maybe_decompress(bytes(body_resp), content_encoding)
    ct = (content_type or "").lower()
    if "text/event-stream" in ct:
        summary = reassemble_sse(raw)
    elif "application/json" in ct:
        summary = parse_json_response(raw)
    else:
        summary = {}
    record = build_record(
        parsed_req, summary,
        timestamp=datetime.now(timezone.utc).isoformat(),
        status=status, duration_ms=duration_ms,
    )
    record["path"] = path
    if status >= 400:
        try:
            record["error"] = raw.decode("utf-8", "replace")[:500]
        except Exception:
            record["error"] = None
    store_record(record)
    return record


def _maybe_decompress(raw: bytes, encoding: Optional[str]) -> bytes:
    """Decompress a teed body if it carries a Content-Encoding we understand.

    We force identity upstream, so this is a belt-and-suspenders fallback for
    gzip/deflate. Unknown encodings (br/zstd) are returned as-is (capture is
    best-effort and will simply find nothing to parse)."""
    enc = (encoding or "").lower().strip()
    try:
        if enc == "gzip":
            import gzip as _gz
            return _gz.decompress(raw)
        if enc == "deflate":
            import zlib
            try:
                return zlib.decompress(raw)
            except zlib.error:
                return zlib.decompress(raw, -zlib.MAX_WBITS)
    except Exception:
        return raw
    return raw


def make_app(upstream: str = DEFAULT_UPSTREAM, forward: bool = True):
    if web is None:
        raise RuntimeError("aiohttp is required: pip install 'pisama-claude-code[proxy]'")
    session_key = web.AppKey("session", aiohttp.ClientSession)

    async def handler(request: "web.Request") -> "web.StreamResponse":
        import time as _time
        if request.path == "/__pisama/health":
            return web.json_response({"ok": True, "service": "pisama-cc-proxy", "upstream": upstream})
        body = await request.read()
        url = upstream.rstrip("/") + request.rel_url.raw_path_qs
        fwd_headers = _forward_request_headers(request.headers)
        # Force an uncompressed response so the teed copy is parseable. The client
        # always accepts identity, and on localhost<->API the bandwidth cost is nil.
        fwd_headers = {k: v for k, v in fwd_headers.items() if k.lower() != "accept-encoding"}
        fwd_headers["Accept-Encoding"] = "identity"
        is_messages = request.method == "POST" and request.path.endswith("/v1/messages")
        parsed_req = parse_request(body) if is_messages else {}
        started = _time.monotonic()

        session = request.app[session_key]
        try:
            upstream_resp = await session.request(
                request.method, url, headers=fwd_headers, data=body or None,
            )
        except Exception as e:  # noqa: BLE001
            return web.Response(status=502, text=f"pisama proxy upstream error: {e}")

        resp_headers = {k: v for k, v in upstream_resp.headers.items() if k.lower() not in _HOP_BY_HOP}
        client_resp = web.StreamResponse(status=upstream_resp.status, headers=resp_headers)
        await client_resp.prepare(request)

        captured = bytearray() if is_messages else None
        try:
            async for chunk in upstream_resp.content.iter_any():
                await client_resp.write(chunk)          # client first, always
                if captured is not None and len(captured) < 8_000_000:
                    captured.extend(chunk)              # tee a bounded copy
        finally:
            await client_resp.write_eof()
            upstream_resp.release()

        # Capture is fully off the hot path now: the client already has every byte.
        if captured is not None and parsed_req:
            try:
                record = _capture(
                    parsed_req, captured, upstream_resp.headers.get("Content-Type"),
                    status=upstream_resp.status,
                    duration_ms=int((_time.monotonic() - started) * 1000),
                    forward=forward,
                    path=request.path,
                    content_encoding=upstream_resp.headers.get("Content-Encoding"),
                )
                if record and forward:
                    import asyncio
                    asyncio.create_task(_emit_record_async(record))
            except Exception as e:  # noqa: BLE001
                print(f"pisama proxy capture error: {e}", file=sys.stderr)

        return client_resp

    async def _emit_record_async(record: Dict[str, Any]):
        try:
            import asyncio
            await asyncio.to_thread(emit_proxy_record, record)
        except Exception as e:  # noqa: BLE001
            print(f"pisama proxy forward error: {e}", file=sys.stderr)

    async def _on_startup(app):
        app[session_key] = aiohttp.ClientSession(auto_decompress=False)

    async def _on_cleanup(app):
        await app[session_key].close()

    app = web.Application(client_max_size=1024 ** 3)
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    app.router.add_route("*", "/{tail:.*}", handler)
    return app


def run_proxy(port: int = DEFAULT_PORT, upstream: str = DEFAULT_UPSTREAM, forward: bool = True) -> None:
    if web is None:
        raise RuntimeError("aiohttp is required: pip install 'pisama-claude-code[proxy]'")
    app = make_app(upstream=upstream, forward=forward)
    print(f"Pisama proxy listening on http://127.0.0.1:{port} -> {upstream}")
    print(f"Captures: {PROXY_DIR}  (forward={'on' if forward else 'off'})")
    web.run_app(app, host="127.0.0.1", port=port, print=None)


# --------------------------------------------------------------------------- #
# Always-on wiring: settings.json env + launchd (macOS) auto-start
# --------------------------------------------------------------------------- #

SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
LAUNCHD_LABEL = "ai.pisama.cc-proxy"
LAUNCHD_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def set_base_url(port: int, settings_path: Optional[Path] = None) -> str:
    """Point Claude Code at the proxy by writing env.ANTHROPIC_BASE_URL."""
    settings_path = settings_path or SETTINGS_PATH
    url = f"http://127.0.0.1:{port}"
    settings: Dict[str, Any] = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
        except ValueError:
            settings = {}
    env = settings.get("env") or {}
    env["ANTHROPIC_BASE_URL"] = url
    settings["env"] = env
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings, indent=2))
    return url


def unset_base_url(settings_path: Optional[Path] = None) -> bool:
    """Remove env.ANTHROPIC_BASE_URL (revert always-on routing)."""
    settings_path = settings_path or SETTINGS_PATH
    if not settings_path.exists():
        return False
    try:
        settings = json.loads(settings_path.read_text())
    except ValueError:
        return False
    env = settings.get("env") or {}
    if "ANTHROPIC_BASE_URL" not in env:
        return False
    del env["ANTHROPIC_BASE_URL"]
    if env:
        settings["env"] = env
    else:
        settings.pop("env", None)
    settings_path.write_text(json.dumps(settings, indent=2))
    return True


def configured_base_url(settings_path: Optional[Path] = None) -> Optional[str]:
    settings_path = settings_path or SETTINGS_PATH
    if not settings_path.exists():
        return None
    try:
        return (json.loads(settings_path.read_text()).get("env") or {}).get("ANTHROPIC_BASE_URL")
    except ValueError:
        return None


SHELL_PROFILE = Path.home() / ".zshrc"
SHELL_MARKER_BEGIN = "# >>> pisama-cc proxy >>>"
SHELL_MARKER_END = "# <<< pisama-cc proxy <<<"


def _strip_shell_block(text: str) -> str:
    import re
    pattern = re.escape(SHELL_MARKER_BEGIN) + r".*?" + re.escape(SHELL_MARKER_END)
    return re.sub(r"\n*" + pattern + r"\n*", "\n", text, flags=re.S)


def add_shell_export(port: int, profile: Optional[Path] = None) -> Path:
    """Export ANTHROPIC_BASE_URL from the shell profile (the mechanism Claude
    Code actually honors; settings.json env is ignored for base-url routing).
    New shells pick it up. Idempotent via a marker block."""
    profile = profile or SHELL_PROFILE
    url = f"http://127.0.0.1:{port}"
    block = f"{SHELL_MARKER_BEGIN}\nexport ANTHROPIC_BASE_URL={url}  # route Claude Code through Pisama capture\n{SHELL_MARKER_END}\n"
    text = profile.read_text() if profile.exists() else ""
    text = _strip_shell_block(text).rstrip("\n")
    profile.write_text((text + "\n\n" if text else "") + block)
    return profile


def remove_shell_export(profile: Optional[Path] = None) -> bool:
    profile = profile or SHELL_PROFILE
    if not profile.exists():
        return False
    text = profile.read_text()
    new = _strip_shell_block(text)
    if new != text:
        profile.write_text(new)
        return True
    return False


def launchd_plist_xml(port: int) -> str:
    pisama_cc = str(Path(sys.executable).with_name("pisama-cc"))
    log = str(PROXY_DIR / "proxy.log")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LAUNCHD_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{pisama_cc}</string>
    <string>proxy</string>
    <string>serve</string>
    <string>--port</string>
    <string>{port}</string>
    <string>--no-print-env</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict>
</plist>
"""
