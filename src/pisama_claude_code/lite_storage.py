"""Lite mode storage using SQLite for standalone detection and scoring.

Provides local persistence for detection results and quality scores
without requiring PostgreSQL or a platform backend. All data stays
on the developer's machine in a single SQLite file.
"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pisama_claude_code.private_files import ensure_private_dir, make_private


@dataclass
class LiteDetectionRecord:
    """A single detection result stored locally."""

    id: str
    trace_id: str
    session_id: str
    detector_name: str
    detected: bool
    severity: int
    confidence: float
    method: str
    details: Dict[str, Any]
    evidence: List[Any]
    created_at: str


@dataclass
class LiteScoreRecord:
    """A quality score computed locally."""

    id: str
    trace_id: str
    session_id: str
    score_type: str
    overall_score: float
    dimension_scores: Dict[str, Any]
    summary: str
    created_at: str


class LiteStorage:
    """Extended SQLite storage for lite mode.

    Manages three tables:
    - lite_detections: individual detection results
    - lite_scores: quality score records
    - lite_sessions: session metadata for quick lookups
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        ensure_private_dir(db_path.parent)
        self._init_schema()
        make_private(db_path)

    def _get_conn(self) -> sqlite3.Connection:
        """Get a new connection with row_factory set."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        """Create tables if they do not exist."""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lite_detections (
                id TEXT PRIMARY KEY,
                trace_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                detector_name TEXT NOT NULL,
                detected INTEGER NOT NULL DEFAULT 0,
                severity INTEGER NOT NULL DEFAULT 0,
                confidence REAL NOT NULL DEFAULT 0.0,
                method TEXT NOT NULL DEFAULT '',
                details TEXT NOT NULL DEFAULT '{}',
                evidence TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lite_scores (
                id TEXT PRIMARY KEY,
                trace_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                score_type TEXT NOT NULL,
                overall_score REAL NOT NULL DEFAULT 0.0,
                dimension_scores TEXT NOT NULL DEFAULT '{}',
                summary TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lite_sessions (
                session_id TEXT PRIMARY KEY,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                trace_count INTEGER NOT NULL DEFAULT 0,
                detection_count INTEGER NOT NULL DEFAULT 0
            )
        """)

        # Indexes for common queries
        conn.execute("CREATE INDEX IF NOT EXISTS idx_det_session ON lite_detections(session_id)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_det_detector ON lite_detections(detector_name)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_det_created ON lite_detections(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_score_session ON lite_scores(session_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_score_created ON lite_scores(created_at)")
        conn.commit()
        conn.close()

    def store_detection(self, record: LiteDetectionRecord) -> None:
        """Store a single detection result.

        Args:
            record: Detection record to persist.
        """
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO lite_detections
                    (id, trace_id, session_id, detector_name, detected,
                     severity, confidence, method, details, evidence, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.trace_id,
                    record.session_id,
                    record.detector_name,
                    1 if record.detected else 0,
                    record.severity,
                    record.confidence,
                    record.method,
                    json.dumps(record.details),
                    json.dumps(record.evidence),
                    record.created_at,
                ),
            )
            # Update session metadata
            self._upsert_session(conn, record.session_id, record.created_at, is_detection=True)
            conn.commit()
        finally:
            conn.close()

    def store_detections_batch(self, records: List[LiteDetectionRecord]) -> None:
        """Store multiple detection results in a single transaction.

        Args:
            records: List of detection records to persist.
        """
        if not records:
            return

        conn = sqlite3.connect(str(self.db_path))
        try:
            for record in records:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO lite_detections
                        (id, trace_id, session_id, detector_name, detected,
                         severity, confidence, method, details, evidence, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.id,
                        record.trace_id,
                        record.session_id,
                        record.detector_name,
                        1 if record.detected else 0,
                        record.severity,
                        record.confidence,
                        record.method,
                        json.dumps(record.details),
                        json.dumps(record.evidence),
                        record.created_at,
                    ),
                )

            # Update session metadata for all unique sessions in batch
            sessions_seen: Dict[str, str] = {}
            for record in records:
                existing = sessions_seen.get(record.session_id)
                if existing is None or record.created_at > existing:
                    sessions_seen[record.session_id] = record.created_at

            for session_id, latest_ts in sessions_seen.items():
                self._upsert_session(conn, session_id, latest_ts, is_detection=True)

            conn.commit()
        finally:
            conn.close()

    def store_score(self, record: LiteScoreRecord) -> None:
        """Store a quality score.

        Args:
            record: Score record to persist.
        """
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO lite_scores
                    (id, trace_id, session_id, score_type, overall_score,
                     dimension_scores, summary, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.trace_id,
                    record.session_id,
                    record.score_type,
                    record.overall_score,
                    json.dumps(record.dimension_scores),
                    record.summary,
                    record.created_at,
                ),
            )
            self._upsert_session(conn, record.session_id, record.created_at, is_detection=False)
            conn.commit()
        finally:
            conn.close()

    def get_detections(
        self,
        session_id: Optional[str] = None,
        detector_name: Optional[str] = None,
        detected_only: bool = False,
        limit: int = 50,
    ) -> List[LiteDetectionRecord]:
        """Query stored detections.

        Args:
            session_id: Filter by session.
            detector_name: Filter by detector type.
            detected_only: If True, only return records where detected=True.
            limit: Max records to return.

        Returns:
            List of LiteDetectionRecord, most recent first.
        """
        conn = self._get_conn()
        try:
            clauses: List[str] = []
            params: List[Any] = []

            if session_id:
                clauses.append("session_id = ?")
                params.append(session_id)
            if detector_name:
                clauses.append("detector_name = ?")
                params.append(detector_name)
            if detected_only:
                clauses.append("detected = 1")

            where = ""
            if clauses:
                where = "WHERE " + " AND ".join(clauses)

            query = f"""
                SELECT * FROM lite_detections
                {where}
                ORDER BY created_at DESC
                LIMIT ?
            """
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            return [self._row_to_detection(row) for row in rows]
        finally:
            conn.close()

    def get_scores(
        self,
        session_id: Optional[str] = None,
        score_type: Optional[str] = None,
        limit: int = 50,
    ) -> List[LiteScoreRecord]:
        """Query stored scores.

        Args:
            session_id: Filter by session.
            score_type: Filter by score type.
            limit: Max records to return.

        Returns:
            List of LiteScoreRecord, most recent first.
        """
        conn = self._get_conn()
        try:
            clauses: List[str] = []
            params: List[Any] = []

            if session_id:
                clauses.append("session_id = ?")
                params.append(session_id)
            if score_type:
                clauses.append("score_type = ?")
                params.append(score_type)

            where = ""
            if clauses:
                where = "WHERE " + " AND ".join(clauses)

            query = f"""
                SELECT * FROM lite_scores
                {where}
                ORDER BY created_at DESC
                LIMIT ?
            """
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            return [self._row_to_score(row) for row in rows]
        finally:
            conn.close()

    def get_stats(self) -> Dict[str, Any]:
        """Get aggregate statistics across all stored data.

        Returns:
            Dict with keys: total_detections, total_positive, total_scores,
            by_detector, by_severity, sessions.
        """
        conn = self._get_conn()
        try:
            # Total detections
            total = conn.execute("SELECT COUNT(*) FROM lite_detections").fetchone()[0]
            positive = conn.execute(
                "SELECT COUNT(*) FROM lite_detections WHERE detected = 1"
            ).fetchone()[0]

            # By detector
            by_detector: Dict[str, Dict[str, int]] = {}
            rows = conn.execute("""
                SELECT detector_name, detected, COUNT(*) as cnt
                FROM lite_detections
                GROUP BY detector_name, detected
            """).fetchall()
            for row in rows:
                name = row["detector_name"]
                if name not in by_detector:
                    by_detector[name] = {"total": 0, "detected": 0}
                by_detector[name]["total"] += row["cnt"]
                if row["detected"]:
                    by_detector[name]["detected"] += row["cnt"]

            # By severity (buckets: low <40, medium 40-69, high 70+)
            by_severity = {"low": 0, "medium": 0, "high": 0}
            rows = conn.execute("""
                SELECT severity, COUNT(*) as cnt
                FROM lite_detections
                WHERE detected = 1
                GROUP BY severity
            """).fetchall()
            for row in rows:
                sev = row["severity"]
                if sev >= 70:
                    by_severity["high"] += row["cnt"]
                elif sev >= 40:
                    by_severity["medium"] += row["cnt"]
                else:
                    by_severity["low"] += row["cnt"]

            # Scores
            total_scores = conn.execute("SELECT COUNT(*) FROM lite_scores").fetchone()[0]

            # Sessions
            sessions = conn.execute("SELECT COUNT(*) FROM lite_sessions").fetchone()[0]

            return {
                "total_detections": total,
                "total_positive": positive,
                "total_scores": total_scores,
                "sessions": sessions,
                "by_detector": by_detector,
                "by_severity": by_severity,
            }
        finally:
            conn.close()

    def export_for_platform(self, output_path: Path) -> int:
        """Export all positive detections as JSON for platform import.

        Args:
            output_path: Path to write the JSON export file.

        Returns:
            Number of records exported.
        """
        conn = self._get_conn()
        try:
            rows = conn.execute("""
                SELECT * FROM lite_detections
                WHERE detected = 1
                ORDER BY created_at ASC
            """).fetchall()

            records = []
            for row in rows:
                records.append(
                    {
                        "id": row["id"],
                        "trace_id": row["trace_id"],
                        "session_id": row["session_id"],
                        "detector_name": row["detector_name"],
                        "detected": True,
                        "severity": row["severity"],
                        "confidence": row["confidence"],
                        "method": row["method"],
                        "details": json.loads(row["details"]),
                        "evidence": json.loads(row["evidence"]),
                        "created_at": row["created_at"],
                    }
                )

            # Include scores
            score_rows = conn.execute("""
                SELECT * FROM lite_scores
                ORDER BY created_at ASC
            """).fetchall()

            scores = []
            for row in score_rows:
                scores.append(
                    {
                        "id": row["id"],
                        "trace_id": row["trace_id"],
                        "session_id": row["session_id"],
                        "score_type": row["score_type"],
                        "overall_score": row["overall_score"],
                        "dimension_scores": json.loads(row["dimension_scores"]),
                        "summary": row["summary"],
                        "created_at": row["created_at"],
                    }
                )

            export_data = {
                "format": "pisama-lite-export",
                "version": "1.0",
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "detections": records,
                "scores": scores,
            }

            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(export_data, indent=2))

            return len(records)
        finally:
            conn.close()

    def _upsert_session(
        self, conn: sqlite3.Connection, session_id: str, timestamp: str, is_detection: bool
    ) -> None:
        """Insert or update session metadata."""
        existing = conn.execute(
            "SELECT * FROM lite_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()

        if existing is None:
            conn.execute(
                """
                INSERT INTO lite_sessions (session_id, first_seen, last_seen, trace_count, detection_count)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, timestamp, timestamp, 1, 1 if is_detection else 0),
            )
        else:
            conn.execute(
                """
                UPDATE lite_sessions
                SET last_seen = MAX(last_seen, ?),
                    trace_count = trace_count + 1,
                    detection_count = detection_count + ?
                WHERE session_id = ?
                """,
                (timestamp, 1 if is_detection else 0, session_id),
            )

    def _row_to_detection(self, row: sqlite3.Row) -> LiteDetectionRecord:
        """Convert a database row to a LiteDetectionRecord."""
        return LiteDetectionRecord(
            id=row["id"],
            trace_id=row["trace_id"],
            session_id=row["session_id"],
            detector_name=row["detector_name"],
            detected=bool(row["detected"]),
            severity=row["severity"],
            confidence=row["confidence"],
            method=row["method"],
            details=json.loads(row["details"]),
            evidence=json.loads(row["evidence"]),
            created_at=row["created_at"],
        )

    def _row_to_score(self, row: sqlite3.Row) -> LiteScoreRecord:
        """Convert a database row to a LiteScoreRecord."""
        return LiteScoreRecord(
            id=row["id"],
            trace_id=row["trace_id"],
            session_id=row["session_id"],
            score_type=row["score_type"],
            overall_score=row["overall_score"],
            dimension_scores=json.loads(row["dimension_scores"]),
            summary=row["summary"],
            created_at=row["created_at"],
        )
