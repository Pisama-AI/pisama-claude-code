#!/usr/bin/env python3
"""PISAMA Guardian Hook - Real-time detection and intervention for Claude Code.

This hook runs BEFORE each tool call and can:
1. Detect problematic patterns in real-time using pisama-core
2. BLOCK tool calls using the Claude Code PreToolUse blocking contract
3. Write alerts for the guardian skill to handle
4. Apply auto-fixes when configured

Blocking contract (why not ``sys.exit(1)``):
    Claude Code treats exit code **2** as the PreToolUse blocking signal
    (stderr is fed back to Claude as the reason), or a stdout JSON
    ``hookSpecificOutput.permissionDecision: "deny"`` on exit 0. Exit code 1
    is a NON-blocking error — the tool proceeds. So a hook that "blocks" via
    ``sys.exit(1)`` is a silent no-op. ``_emit_block`` below emits BOTH signals
    (JSON deny on stdout + reason on stderr + exit 2) so the block is honored
    across Claude Code versions.

Usage:
    Install in ~/.claude/hooks/ and configure in settings.local.json
"""

import json
import os
import sys

# Claude Code PreToolUse blocking exit code. Exit 2 = block (stderr -> Claude);
# exit 1 = non-blocking error (tool proceeds). See module docstring.
BLOCK_EXIT_CODE = 2


def _emit_block(reason: str) -> None:
    """Signal a PreToolUse block that Claude Code actually honors.

    Emits the structured deny decision on stdout (for versions that parse
    ``hookSpecificOutput.permissionDecision``) AND the reason on stderr with
    exit code 2 (the broadly-honored blocking signal). The two agree, so any
    version blocks. Never returns — exits the process.
    """
    reason = (reason or "Blocked by PISAMA detection").strip()
    decision = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    try:
        print(json.dumps(decision))
    except Exception:
        pass
    print(f"PISAMA: {reason}", file=sys.stderr)
    sys.exit(BLOCK_EXIT_CODE)


def main():
    """Main hook entry point."""
    # Read hook input from stdin
    try:
        raw_input = sys.stdin.read()
        if raw_input.strip():
            hook_data = json.loads(raw_input)
        else:
            hook_data = {}
    except json.JSONDecodeError:
        hook_data = {}
    except Exception:
        hook_data = {}

    # Get session ID
    session_id = hook_data.get("session_id") or os.environ.get("CLAUDE_SESSION_ID", "unknown")

    try:
        # Import and run guardian analysis
        from pisama_claude_code.guardian import analyze_sync

        result = analyze_sync(hook_data, session_id)

        # Check if we should block
        if result.should_block:
            reason = result.message or (
                f"severity {result.severity}/100: " + "; ".join(result.issues)
                if result.issues else f"severity {result.severity}/100"
            )
            _emit_block(reason)
        else:
            sys.exit(0)

    except ImportError:
        # Fall back to minimal detection if pisama_claude_code not installed
        _fallback_detection(hook_data, session_id)
    except Exception as e:
        # Log error but don't block
        print(f"PISAMA hook error: {e}", file=sys.stderr)
        sys.exit(0)


def _fallback_detection(hook_data: dict, session_id: str) -> None:
    """Fallback detection when pisama_claude_code is not installed.

    Provides basic loop detection without the full pisama-core stack.
    """
    import sqlite3
    from pathlib import Path

    db_path = Path.home() / ".claude" / "pisama" / "traces" / "pisama.db"

    if not db_path.exists():
        sys.exit(0)

    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.execute("""
            SELECT tool_name FROM traces
            ORDER BY created_at DESC
            LIMIT 10
        """)
        recent_tools = [row[0] for row in cursor.fetchall()]
        conn.close()

        # Add current tool
        current_tool = hook_data.get("tool_name", hook_data.get("tool", "unknown"))
        tool_sequence = [current_tool] + recent_tools

        # Basic loop detection
        if len(tool_sequence) >= 5:
            max_consecutive = 1
            current_consecutive = 1
            for i in range(1, len(tool_sequence)):
                if tool_sequence[i] == tool_sequence[i-1]:
                    current_consecutive += 1
                    max_consecutive = max(max_consecutive, current_consecutive)
                else:
                    current_consecutive = 1

            if max_consecutive >= 5:
                _emit_block(
                    f"Loop detected ({current_tool} repeated {max_consecutive}x)"
                )

    except Exception:
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
