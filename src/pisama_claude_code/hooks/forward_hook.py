"""Stop-hook entry point: reconcile the current Claude Code session to Pisama.

Invoked by ~/.claude/hooks/pisama-forward.sh, which runs this DETACHED in the
background so a slow or failed network call can never block or delay the
session. Reads the Stop hook payload from stdin to learn the session_id.

Real-time forwarding happens per tool call (the PostToolUse capture hook emits
one appended span each). This Stop hook is now a RECONCILE pass, not the primary
forward: it re-emits, via the same idempotent append endpoint, only the events
that a per-tool emit missed (tracked in the sync log). No more giant
delete-and-replace batch — that single large transaction is what destabilized
production on long sessions.

No-ops silently unless the user has connected (api_key present) and auto-sync is
enabled.
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
        from pisama_claude_code.cli import (
            _all_traces,
            _synced_keys,
            emit_span,
            get_config,
        )

        config = get_config()
        # Capture-only unless the user opted into forwarding.
        if not config.get("api_key") or not config.get("auto_sync", False):
            return

        sessions = [session_id] if session_id else _recent_session_ids(_all_traces())
        wanted = {s for s in sessions if s}
        if not wanted:
            return

        # Reconcile: re-emit only the session's events not already forwarded, as
        # single appended spans (idempotent on the backend). O(unsent), no batch.
        already = _synced_keys()
        traces = sorted(_all_traces(), key=lambda t: t.get("timestamp") or "")
        failures = 0
        for t in traces:
            if t.get("session_id") not in wanted:
                continue
            if (t.get("session_id"), t.get("timestamp")) in already:
                continue
            ok, msg = emit_span(t, config)
            if not ok:
                failures += 1
        if failures:
            print(f"Pisama reconcile: {failures} span(s) failed to forward", file=sys.stderr)
    except Exception as e:
        # Forwarding must never disrupt the session.
        print(f"Pisama forward error: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
