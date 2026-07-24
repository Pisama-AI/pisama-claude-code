#!/usr/bin/env python3
"""PISAMA Trace Capture Hook - Captures all Claude Code tool calls for forensics.

This hook runs on tool calls to capture trace data for analysis.
It stores traces in both SQLite (for querying) and JSONL (for archival).

Now includes:
- AI response capture from transcript
- Token usage tracking
- Cost calculation
- PII tokenization (optional, configurable)

Usage:
    Install in ~/.claude/hooks/ and configure in settings.local.json
"""

import json
import os
import sys
from datetime import date
from typing import Any

# PII Tokenization configuration
TOKENIZATION_ENABLED = os.environ.get("PISAMA_TOKENIZATION", "1") == "1"
# Fields to tokenize - now includes input, reasoning, output
TOKENIZATION_FIELDS = [
    "tool_input",
    "tool_output",
    "user_input",      # User's prompt/message
    "reasoning",       # Extended thinking content
    "ai_output",       # Assistant's text response
    "ai_response",     # Legacy field
]

# Anthropic API list pricing per 1M tokens, reviewed 2026-07-23.
MODEL_PRICING = {
    "opus-4": {"input": 5.00, "output": 25.00, "cache_read": 0.50, "cache_write": 6.25},
    "sonnet-5-promo": {"input": 2.00, "output": 10.00, "cache_read": 0.20, "cache_write": 2.50},
    "sonnet-5": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75},
    "sonnet-4": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75},
    "haiku-4": {"input": 1.00, "output": 5.00, "cache_read": 0.10, "cache_write": 1.25},
    "sonnet-3.5": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75},
    "haiku-3.5": {"input": 0.80, "output": 4.00, "cache_read": 0.08, "cache_write": 1.00},
    "opus-4.1": {"input": 15.00, "output": 75.00, "cache_read": 1.50, "cache_write": 18.75},
    "default": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75},
}

_MODEL_FAMILY_HINTS = (
    (("opus-4-8", "opus-4-7", "opus-4-6", "opus-4-5"), "opus-4"),
    (("opus-4-1",), "opus-4.1"),
    (("sonnet-4",), "sonnet-4"),
    (("haiku-4",), "haiku-4"),
    (("3-5-sonnet",), "sonnet-3.5"),
    (("3-5-haiku",), "haiku-3.5"),
)


def _pricing_for_model(model: str, today: date | None = None) -> dict[str, float]:
    """Resolve a Claude model ID or alias to its current list pricing."""
    normalized = model.lower()
    if "sonnet-5" in normalized:
        key = "sonnet-5-promo" if (today or date.today()) < date(2026, 9, 1) else "sonnet-5"
    else:
        key = next(
            (
                family
                for hints, family in _MODEL_FAMILY_HINTS
                if any(hint in normalized for hint in hints)
            ),
            "default",
        )
    return MODEL_PRICING[key]


def calculate_cost(model: str, usage: dict) -> float:
    """Calculate cost in USD from token usage."""
    pricing = _pricing_for_model(model)

    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    cache_read = usage.get("cache_read_input_tokens", 0)
    cache_create = usage.get("cache_creation_input_tokens", 0)

    cost = (
        (input_tokens / 1_000_000) * pricing["input"] +
        (output_tokens / 1_000_000) * pricing["output"] +
        (cache_read / 1_000_000) * pricing["cache_read"] +
        (cache_create / 1_000_000) * pricing["cache_write"]
    )
    return round(cost, 6)


def get_tokenizer(session_id: str) -> Any:
    """Get a Tokenizer instance for PII protection.

    Returns None if tokenization is disabled or unavailable.
    """
    if not TOKENIZATION_ENABLED:
        return None

    try:
        from pisama_core.tokenization import Tokenizer
        return Tokenizer(
            session_id=session_id,
            enabled=True,
            fail_open=True,  # Don't fail if tokenization has issues
        )
    except ImportError:
        return None
    except Exception:
        return None


def tokenize_trace_data(
    trace: dict,
    session_id: str,
    fields: list[str] | None = None,
) -> dict:
    """Tokenize sensitive fields in trace data.

    Args:
        trace: The trace dictionary to tokenize.
        session_id: Session ID for token scoping.
        fields: Fields to tokenize (defaults to TOKENIZATION_FIELDS).

    Returns:
        Trace with PII tokenized (or original if tokenization unavailable).
    """
    if not TOKENIZATION_ENABLED:
        return trace

    tokenizer = get_tokenizer(session_id)
    if tokenizer is None:
        return trace

    fields = fields or TOKENIZATION_FIELDS
    result = trace.copy()

    try:
        for field in fields:
            if field in result and result[field]:
                value = result[field]
                if isinstance(value, str):
                    result[field] = tokenizer.tokenize_string(value)
                elif isinstance(value, dict):
                    result[field] = tokenizer.tokenize_dict(value)
        return result
    except Exception:
        # Fail open - return original trace if tokenization fails
        return trace
    finally:
        try:
            tokenizer.close()
        except Exception:
            pass


def extract_content_parts(content_blocks: list) -> dict:
    """Extract input, reasoning, and output from content blocks.

    Claude's response contains different block types:
    - type="thinking": Extended thinking/reasoning (antml:thinking blocks)
    - type="text": Regular text output
    - type="tool_use": Tool calls

    Returns:
        {
            "reasoning": {"content": str, "tokens": int},
            "output": {"content": str, "tokens": int},
            "tool_calls": [...]
        }
    """
    reasoning_parts = []
    output_parts = []
    tool_calls = []

    for block in content_blocks:
        if not isinstance(block, dict):
            continue

        block_type = block.get("type", "")

        if block_type == "thinking":
            # Extended thinking block
            thinking_text = block.get("thinking", "")
            if thinking_text:
                reasoning_parts.append(thinking_text)

        elif block_type == "text":
            # Regular text output
            text = block.get("text", "")
            if text:
                output_parts.append(text)

        elif block_type == "tool_use":
            # Tool call
            tool_calls.append({
                "id": block.get("id"),
                "name": block.get("name"),
                "input": block.get("input"),
            })

    return {
        "reasoning": {
            "content": "\n\n".join(reasoning_parts) if reasoning_parts else None,
            "block_count": len(reasoning_parts),
        },
        "output": {
            "content": "\n\n".join(output_parts) if output_parts else None,
            "block_count": len(output_parts),
        },
        "tool_calls": tool_calls if tool_calls else None,
    }


def _user_text_from_entry(entry: dict) -> "str | None":
    """Return the real user text from a transcript 'user' entry.

    Claude Code "user" entries are either actual user input or tool results /
    system interrupts. Returns the user's text, or None if the entry is a tool
    result / system message / interrupt (i.e. not real user input).
    """
    if not isinstance(entry, dict) or entry.get("type") not in ("user", "human"):
        return None
    msg = entry.get("message")
    if not isinstance(msg, dict):
        return None
    content = msg.get("content", [])

    if isinstance(content, str):
        if content.startswith("[Request") or content.startswith("[System"):
            return None
        return content or None

    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                btype = block.get("type", "")
                if btype == "tool_result":
                    return None  # tool result, not user input
                if btype == "text":
                    text = block.get("text", "")
                    if text and not text.startswith("[Request"):
                        parts.append(text)
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts) if parts else None
    return None


def get_last_user_message(transcript_path: str) -> dict:
    """Read transcript and get the last real user text message (input)."""
    try:
        from pathlib import Path
        transcript = Path(transcript_path)
        if not transcript.exists():
            return {}

        with open(transcript) as f:
            lines = f.readlines()[-800:]  # cover long turns (many tool calls)

        for line in reversed(lines):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            text = _user_text_from_entry(entry)
            if text is not None:
                return {"content": text, "role": "user"}
        return {}
    except Exception:
        return {}


def get_last_assistant_message(transcript_path: str) -> dict:
    """Read transcript and get recent assistant messages with usage.

    Claude Code separates thinking, text, and tool_use into different messages,
    so we need to collect content from multiple recent assistant messages.

    Extracts:
    - model: The Claude model used
    - usage: Token counts (input, output, cache)
    - input: User's input message
    - reasoning: Extended thinking content (collected from recent messages)
    - output: Text response content (collected from recent messages)
    - tool_calls: Any tool calls made
    """
    try:
        from pathlib import Path
        transcript = Path(transcript_path)
        if not transcript.exists():
            return {}

        # Read a wide window so a long turn's opening thinking block (which can
        # sit many tool calls back) is still in view.
        with open(transcript) as f:
            lines = f.readlines()[-800:]

        # Collect every assistant content block in the CURRENT turn. Claude Code
        # emits thinking, text and tool_use as separate assistant entries, and a
        # turn ends (going backwards) at the real user text message.
        all_reasoning: list[str] = []
        all_output: list[str] = []
        all_tool_calls: list[dict[str, Any]] = []
        model = None
        usage: dict[str, Any] = {}
        stop_reason = None
        user_input = None
        asst_seen = 0

        for line in reversed(lines):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            if entry.get("type") == "assistant" and isinstance(entry.get("message"), dict):
                asst_seen += 1
                if asst_seen > 400:  # pathological-turn safety bound
                    break
                msg = entry["message"]
                if model is None:
                    model = msg.get("model")
                # Usage from the most recent assistant entry that carries it.
                if not usage and isinstance(msg.get("usage"), dict):
                    usage = msg["usage"]
                    stop_reason = msg.get("stop_reason")
                content = msg.get("content", [])
                if isinstance(content, list):
                    parts = extract_content_parts(content)
                    if parts.get("reasoning", {}).get("content"):
                        all_reasoning.append(parts["reasoning"]["content"])
                    if parts.get("output", {}).get("content"):
                        all_output.append(parts["output"]["content"])
                    if parts.get("tool_calls"):
                        all_tool_calls.extend(parts["tool_calls"])
                continue

            # A real user text message marks the start of this turn.
            text = _user_text_from_entry(entry)
            if text is not None:
                user_input = text
                break

        # If the turn boundary fell outside the window, fall back to a scan.
        if user_input is None:
            user_input = get_last_user_message(transcript_path).get("content")

        # Combine collected content (reverse to get chronological order)
        reasoning_text = "\n\n".join(reversed(all_reasoning)) if all_reasoning else None
        output_text = "\n\n".join(reversed(all_output)) if all_output else None

        return {
            "model": model,
            "usage": usage,
            "stop_reason": stop_reason,
            # Structured content
            "input": user_input,
            "reasoning": reasoning_text,
            "output": output_text,
            "tool_calls": all_tool_calls if all_tool_calls else None,
            # Legacy field for backward compatibility
            "content": [],
        }
    except Exception:
        return {}


def main():
    """Main hook entry point."""
    # Determine hook type from environment or argv
    hook_type = os.environ.get("PISAMA_HOOK_TYPE", "unknown")
    if len(sys.argv) > 1:
        hook_type = sys.argv[1]

    # Read hook input from stdin
    try:
        raw_input = sys.stdin.read()
        if raw_input.strip():
            hook_data = json.loads(raw_input)
        else:
            hook_data = {}
    except json.JSONDecodeError:
        hook_data = {"raw": raw_input}
    except Exception as e:
        hook_data = {"error": str(e)}

    try:
        # Capture the tool call plus the reconstructed turn (user prompt,
        # reasoning, AI output, tokens, cost) in the flat record shape the sync
        # pipeline and the backend ingest model expect.
        _capture(hook_data, hook_type)
    except Exception as e:
        # Never block the session on a capture error.
        print(f"Pisama capture error: {e}", file=sys.stderr)

    # Always exit successfully (don't block)
    sys.exit(0)


def _capture(hook_data: dict, hook_type: str) -> None:
    """Capture a tool call plus the reconstructed turn to the local store.

    Writes a flat record (JSONL + SQLite) carrying tool I/O, the user prompt,
    the assistant's reasoning and output, the model, token usage and cost. This
    is the shape that normalize_trace / prepare_sync_payload and the backend
    Claude Code ingest model consume.
    """
    import sqlite3
    from datetime import datetime, timezone
    from pathlib import Path

    traces_dir = Path.home() / ".claude" / "pisama" / "traces"
    db_path = traces_dir / "pisama.db"

    # Ensure directory exists
    traces_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).isoformat()
    session_id = hook_data.get("session_id", os.environ.get("CLAUDE_SESSION_ID", "unknown"))
    tool_name = hook_data.get("tool_name", hook_data.get("tool", "unknown"))
    tool_input = hook_data.get("tool_input", hook_data.get("input", {}))
    # Claude Code PostToolUse passes the result under tool_result (current);
    # older builds used tool_response. Accept both, then legacy tool_output.
    tool_output = hook_data.get("tool_result")
    if tool_output is None:
        tool_output = hook_data.get("tool_response")
    if tool_output is None:
        tool_output = hook_data.get("tool_output")
    working_dir = hook_data.get("cwd") or hook_data.get("working_dir") or os.getcwd()

    # Agent identity: Claude Code sets agent_id/agent_type on hook payloads
    # only when the tool call runs inside a subagent (Task/workflow fan-out).
    # Absence means the main-loop agent. Forwarding this is what lets the
    # backend keep parallel subagents apart instead of flattening a fan-out
    # into one sequential stream.
    agent_id = hook_data.get("agent_id")
    agent_type = hook_data.get("agent_type")
    is_sidechain = bool(agent_id)

    # Get AI response and token usage from transcript (PostToolUse only)
    model = None
    usage = {}
    cost = 0.0
    user_input = None
    reasoning = None
    ai_output = None
    ai_response = None  # Legacy field

    transcript_path = hook_data.get("transcript_path")
    if transcript_path and hook_type in ("post", "PostToolUse"):
        assistant_msg = get_last_assistant_message(transcript_path)
        if assistant_msg:
            model = assistant_msg.get("model")
            usage = assistant_msg.get("usage", {})
            cost = calculate_cost(model, usage) if model and usage else 0.0

            # New structured fields
            user_input = assistant_msg.get("input")
            reasoning = assistant_msg.get("reasoning")
            ai_output = assistant_msg.get("output")

            # Legacy ai_response for backward compatibility (truncated)
            if ai_output:
                ai_response = ai_output[:500] if len(ai_output) > 500 else ai_output

    # Write to JSONL
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    jsonl_path = traces_dir / f"traces-{date_str}.jsonl"

    trace = {
        "session_id": session_id,
        "timestamp": timestamp,
        "hook_type": hook_type,
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_output": tool_output,
        "working_dir": working_dir,
        # Agent identity (None/False for main-loop calls)
        "agent_id": agent_id,
        "agent_type": agent_type,
        "is_sidechain": is_sidechain,
        # Model and usage
        "model": model,
        "usage": usage,
        "cost_usd": cost,
        # NEW: Structured input/reasoning/output
        "user_input": user_input,
        "reasoning": reasoning,
        "ai_output": ai_output,
        # Legacy field (truncated for backward compat)
        "ai_response": ai_response,
        "raw": hook_data,
    }

    # Tokenize PII before storage (PostToolUse only to avoid double-tokenization)
    if hook_type in ("post", "PostToolUse"):
        trace = tokenize_trace_data(trace, session_id)

    with open(jsonl_path, "a") as f:
        f.write(json.dumps(trace) + "\n")

    # Write to SQLite
    try:
        conn = sqlite3.connect(str(db_path))
        # Updated schema with input/reasoning/output columns
        conn.execute("""
            CREATE TABLE IF NOT EXISTS traces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                timestamp TEXT,
                hook_type TEXT,
                tool_name TEXT,
                tool_input TEXT,
                tool_output TEXT,
                working_dir TEXT,
                model TEXT,
                input_tokens INTEGER,
                output_tokens INTEGER,
                cache_read_tokens INTEGER,
                cost_usd REAL,
                user_input TEXT,
                reasoning TEXT,
                ai_output TEXT,
                ai_response TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_session ON traces(session_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tool ON traces(tool_name)")

        # Add columns if they don't exist (migration for existing DBs)
        for col, col_type in [
            ("model", "TEXT"),
            ("input_tokens", "INTEGER"),
            ("output_tokens", "INTEGER"),
            ("cache_read_tokens", "INTEGER"),
            ("cost_usd", "REAL"),
            ("user_input", "TEXT"),
            ("reasoning", "TEXT"),
            ("ai_output", "TEXT"),
            ("ai_response", "TEXT"),
        ]:
            try:
                conn.execute(f"ALTER TABLE traces ADD COLUMN {col} {col_type}")
            except sqlite3.OperationalError:
                pass  # Column already exists

        # Use tokenized values from trace dict
        conn.execute("""
            INSERT INTO traces (
                session_id, timestamp, hook_type, tool_name, tool_input, tool_output,
                working_dir, model, input_tokens, output_tokens, cache_read_tokens, cost_usd,
                user_input, reasoning, ai_output, ai_response
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            session_id,
            timestamp,
            hook_type,
            tool_name,
            json.dumps(trace.get("tool_input")) if trace.get("tool_input") else None,
            json.dumps(trace.get("tool_output")) if trace.get("tool_output") else None,
            working_dir,
            model,
            usage.get("input_tokens"),
            usage.get("output_tokens"),
            usage.get("cache_read_input_tokens"),
            cost,
            trace.get("user_input"),
            trace.get("reasoning"),
            trace.get("ai_output"),
            trace.get("ai_response"),
        ))
        conn.commit()
        conn.close()
    except Exception:
        pass

    # Real-time forward: stream THIS event as a single appended span so a long
    # session never becomes one giant batch transaction. Opt-in (auto_sync) and
    # best-effort — runs in an async hook, never blocks or breaks the session.
    if hook_type in ("post", "PostToolUse"):
        try:
            from pisama_claude_code.cli import emit_span, get_config

            cfg = get_config()
            excluded = cfg.get("forward_exclude_sessions") or []
            if cfg.get("api_key") and cfg.get("auto_sync", False) and session_id not in excluded:
                emit_span(
                    {
                        "session_id": session_id,
                        "timestamp": timestamp,
                        "hook_type": hook_type,
                        "tool_name": tool_name,
                        "tool_input": trace.get("tool_input"),
                        "tool_output": trace.get("tool_output"),
                        "working_dir": working_dir,
                        "agent_id": agent_id,
                        "agent_type": agent_type,
                        "is_sidechain": is_sidechain,
                        "model": model,
                        "input_tokens": usage.get("input_tokens") or 0,
                        "output_tokens": usage.get("output_tokens") or 0,
                        "cache_read_tokens": usage.get("cache_read_input_tokens") or 0,
                        "cost_usd": cost,
                        "user_input": trace.get("user_input"),
                        "reasoning": trace.get("reasoning"),
                        "ai_output": trace.get("ai_output"),
                    },
                    cfg,
                )
        except Exception:
            pass  # forwarding must never disrupt the session


# Backwards-compatible alias: this used to be the import-failure fallback path,
# it is now the primary capture path.
_fallback_capture = _capture


if __name__ == "__main__":
    main()
