"""Stop-hook entry point: forward the current Claude Code session to Pisama.

Invoked by ~/.claude/hooks/pisama-forward.sh, which runs this DETACHED in the
background so a slow or failed network call can never block or delay the
session. Reads the Stop hook payload from stdin to learn the session_id, then
forwards that session's complete set of captured traces to the platform.

No-ops silently unless the user has connected (api_key present) and auto-sync is
enabled. The platform ingest replaces a session wholesale on each upload, so
re-forwarding the growing session every turn is safe and idempotent.
"""

import json
import sys


def _read_session_id() -> "str | None":
    """Extract session_id from the hook payload on stdin (best-effort)."""
    try:
        raw = sys.stdin.read()
        if raw.strip():
            return json.loads(raw).get("session_id")
    except Exception:
        pass
    return None


def _recent_session_ids(traces, limit: int = 1) -> list:
    """Fallback when stdin lacks a session_id: the most recent session(s)."""
    seen: list = []
    for t in sorted(traces, key=lambda x: x.get("timestamp") or "", reverse=True):
        sid = t.get("session_id")
        if sid and sid not in seen:
            seen.append(sid)
        if len(seen) >= limit:
            break
    return seen


def main() -> None:
    session_id = _read_session_id()

    try:
        from pisama_claude_code.cli import get_config, push_sessions, _all_traces

        config = get_config()
        # Capture-only unless the user opted into forwarding.
        if not config.get("api_key") or not config.get("auto_sync", False):
            return

        sessions = [session_id] if session_id else _recent_session_ids(_all_traces())
        sessions = [s for s in sessions if s]
        if not sessions:
            return

        # Full content by default (per the connect-time choice); bounded per field.
        full_content = config.get("forward_full_content", True)
        ok, msg, _ = push_sessions(config, sessions, include_outputs=full_content)
        if not ok:
            print(f"Pisama forward: {msg}", file=sys.stderr)
    except Exception as e:
        # Forwarding must never disrupt the session.
        print(f"Pisama forward error: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
