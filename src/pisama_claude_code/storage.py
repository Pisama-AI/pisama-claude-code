"""Trace Storage for Claude Code.

Handles local storage of traces in SQLite and JSONL formats.
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from pisama_core.traces import Platform, Span, SpanKind, SpanStatus


class TraceStorage:
    """Local storage for Claude Code traces.

    Stores traces in:
    - SQLite database for fast querying
    - JSONL files (date-partitioned) for archival/export
    """

    def __init__(self, traces_dir: Path):
        self.traces_dir = traces_dir
        self.db_path = traces_dir / "pisama.db"

        # Ensure directory exists
        self.traces_dir.mkdir(parents=True, exist_ok=True)

        # Initialize database
        self._init_db()

    def _init_db(self) -> None:
        """Initialize SQLite database schema."""
        conn = sqlite3.connect(str(self.db_path))

        # Check if table exists with old schema
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='traces'"
        )
        table_exists = cursor.fetchone() is not None

        if table_exists:
            # Check if we need to migrate
            cursor = conn.execute("PRAGMA table_info(traces)")
            columns = {row[1] for row in cursor.fetchall()}

            if "span_id" not in columns:
                # Old schema - add new columns
                try:
                    conn.execute("ALTER TABLE traces ADD COLUMN span_id TEXT")
                    conn.execute("ALTER TABLE traces ADD COLUMN trace_id TEXT")
                    conn.execute("ALTER TABLE traces ADD COLUMN parent_id TEXT")
                    conn.execute("ALTER TABLE traces ADD COLUMN kind TEXT")
                    conn.execute("ALTER TABLE traces ADD COLUMN status TEXT")
                    conn.execute("ALTER TABLE traces ADD COLUMN attributes TEXT")
                    conn.execute("ALTER TABLE traces ADD COLUMN duration_ms INTEGER")
                    conn.execute("ALTER TABLE traces ADD COLUMN error TEXT")
                except sqlite3.OperationalError:
                    pass  # Column already exists
        else:
            # Create new table with full schema
            conn.execute("""
                CREATE TABLE IF NOT EXISTS traces (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    span_id TEXT,
                    trace_id TEXT,
                    parent_id TEXT,
                    session_id TEXT,
                    timestamp TEXT,
                    hook_type TEXT,
                    tool_name TEXT,
                    kind TEXT,
                    status TEXT,
                    tool_input TEXT,
                    tool_output TEXT,
                    attributes TEXT,
                    duration_ms INTEGER,
                    error TEXT,
                    working_dir TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

        # Create indexes (these are safe to run even if they exist)
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_session ON traces(session_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON traces(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tool ON traces(tool_name)")
        except sqlite3.OperationalError:
            pass

        # Try to create new indexes (may fail on old data)
        try:
            cursor = conn.execute("PRAGMA table_info(traces)")
            columns = {row[1] for row in cursor.fetchall()}
            if "span_id" in columns:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_span ON traces(span_id)")
            if "trace_id" in columns:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_trace ON traces(trace_id)")
        except sqlite3.OperationalError:
            pass

        conn.commit()
        conn.close()

    def store(self, span: Span, raw_data: Optional[dict] = None) -> None:
        """Store a span to database and JSONL.

        Args:
            span: Universal span to store
            raw_data: Optional raw hook data for archival
        """
        # Write to JSONL (date-partitioned)
        self._write_jsonl(span, raw_data)

        # Write to SQLite
        self._write_db(span)

    def _write_jsonl(self, span: Span, raw_data: Optional[dict] = None) -> None:
        """Write span to JSONL file."""
        date_str = span.start_time.strftime("%Y-%m-%d")
        jsonl_path = self.traces_dir / f"traces-{date_str}.jsonl"

        record = {
            "span_id": span.span_id,
            "trace_id": span.trace_id,
            "parent_id": span.parent_id,
            "name": span.name,
            "kind": span.kind.value,
            "platform": span.platform.value,
            "status": span.status.value,
            "start_time": span.start_time.isoformat(),
            "end_time": span.end_time.isoformat() if span.end_time else None,
            "attributes": span.attributes,
            "input_data": span.input_data,
            "output_data": span.output_data,
            "error": span.error_message,
            "raw": raw_data,
        }

        with open(jsonl_path, "a") as f:
            f.write(json.dumps(record) + "\n")

    def _write_db(self, span: Span) -> None:
        """Write span to SQLite database."""
        try:
            conn = sqlite3.connect(str(self.db_path))

            # Calculate duration if end_time available
            duration_ms = None
            if span.end_time:
                delta = span.end_time - span.start_time
                duration_ms = int(delta.total_seconds() * 1000)

            # Get session_id from attributes
            session_id = span.attributes.get("session_id") or span.trace_id
            hook_type = span.attributes.get("hook_type", "unknown")
            working_dir = span.attributes.get("working_dir", "")

            conn.execute("""
                INSERT OR REPLACE INTO traces (
                    span_id, trace_id, parent_id, session_id, timestamp,
                    hook_type, tool_name, kind, status, tool_input,
                    tool_output, attributes, duration_ms, error, working_dir
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                span.span_id,
                span.trace_id,
                span.parent_id,
                session_id,
                span.start_time.isoformat(),
                hook_type,
                span.name,
                span.kind.value,
                span.status.value,
                json.dumps(span.input_data) if span.input_data else None,
                json.dumps(span.output_data) if span.output_data else None,
                json.dumps(span.attributes),
                duration_ms,
                span.error_message,
                working_dir,
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            # Log but don't fail
            import sys
            print(f"PISAMA DB error: {e}", file=sys.stderr)

    def get_recent(self, limit: int = 10, session_id: Optional[str] = None) -> list[Span]:
        """Get recent spans from database.

        Args:
            limit: Maximum number of spans to return
            session_id: Optional filter by session

        Returns:
            List of spans, most recent first
        """
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row

            if session_id:
                cursor = conn.execute("""
                    SELECT * FROM traces
                    WHERE session_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (session_id, limit))
            else:
                cursor = conn.execute("""
                    SELECT * FROM traces
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (limit,))

            rows = cursor.fetchall()
            conn.close()

            return [self._row_to_span(row) for row in rows]
        except Exception:
            return []

    def get_tool_sequence(self, limit: int = 10, session_id: Optional[str] = None) -> list[str]:
        """Get recent tool names in sequence.

        Args:
            limit: Maximum number of tools to return
            session_id: Optional filter by session

        Returns:
            List of tool names, most recent first
        """
        try:
            conn = sqlite3.connect(str(self.db_path))

            if session_id:
                cursor = conn.execute("""
                    SELECT tool_name FROM traces
                    WHERE session_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (session_id, limit))
            else:
                cursor = conn.execute("""
                    SELECT tool_name FROM traces
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (limit,))

            rows = cursor.fetchall()
            conn.close()

            return [row[0] for row in rows]
        except Exception:
            return []

    def _row_to_span(self, row: sqlite3.Row) -> Span:
        """Convert database row to Span object."""

        # Parse kind
        try:
            kind = SpanKind(row["kind"])
        except ValueError:
            kind = SpanKind.TOOL

        # Parse status
        try:
            status = SpanStatus(row["status"])
        except ValueError:
            status = SpanStatus.OK

        # Parse timestamp
        start_time = datetime.fromisoformat(row["timestamp"])

        # Parse JSON fields
        input_data = json.loads(row["tool_input"]) if row["tool_input"] else {}
        output_data = json.loads(row["tool_output"]) if row["tool_output"] else {}
        attributes = json.loads(row["attributes"]) if row["attributes"] else {}

        return Span(
            span_id=row["span_id"],
            parent_id=row["parent_id"],
            trace_id=row["trace_id"],
            name=row["tool_name"],
            kind=kind,
            platform=Platform.CLAUDE_CODE,
            start_time=start_time,
            end_time=None,  # Not stored separately
            status=status,
            attributes=attributes,
            input_data=input_data,
            output_data=output_data,
            events=[],
            error_message=row["error"] if "error" in row.keys() else None,
        )

    def clear_session(self, session_id: str) -> int:
        """Clear traces for a session.

        Args:
            session_id: Session to clear

        Returns:
            Number of traces deleted
        """
        try:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.execute(
                "DELETE FROM traces WHERE session_id = ?",
                (session_id,)
            )
            count = cursor.rowcount
            conn.commit()
            conn.close()
            return count
        except Exception:
            return 0
