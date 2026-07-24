#!/usr/bin/env python3
"""PISAMA Guardian Hook - Real-time detection and intervention for Claude Code.

This hook runs BEFORE each tool call and can:
1. Detect problematic patterns in real-time using pisama-core
2. BLOCK tool calls by returning exit code 1
3. Write alerts for the guardian skill to handle
4. Apply auto-fixes when configured

Usage:
    Install in ~/.claude/hooks/ and configure in settings.local.json
"""

import json
import os
import sys


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
            sys.exit(1)
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
        with sqlite3.connect(str(db_path)) as conn:
            cursor = conn.execute(
                """
                SELECT tool_name FROM traces
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT 10
                """,
                (session_id,),
            )
            recent_tools = [row[0] for row in cursor.fetchall()]

        # Add current tool
        current_tool = hook_data.get("tool_name", hook_data.get("tool", "unknown"))
        tool_sequence = [current_tool] + recent_tools

        # Basic loop detection
        if len(tool_sequence) >= 5:
            max_consecutive = 1
            current_consecutive = 1
            for i in range(1, len(tool_sequence)):
                if tool_sequence[i] == tool_sequence[i - 1]:
                    current_consecutive += 1
                    max_consecutive = max(max_consecutive, current_consecutive)
                else:
                    current_consecutive = 1

            if max_consecutive >= 5:
                print(
                    f"PISAMA: Loop detected ({current_tool} repeated {max_consecutive}x)",
                    file=sys.stderr,
                )
                sys.exit(1)

    except Exception:
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
