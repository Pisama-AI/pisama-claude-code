"""Standalone detection runner for lite mode.

Runs detection algorithms locally without the full backend.
No auth, no tenant isolation, no Postgres -- just SQLite and in-process detection.

Supported detectors:
- Loop detection: Track state hashes, flag if same hash appears 3+ times
- Overflow: Check if accumulated tokens exceed a configurable threshold
- Repetition: Check content similarity between consecutive entries via word overlap
"""

import hashlib
import json
import logging
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .lite_config import LiteConfig
from .lite_storage import LiteDetectionRecord, LiteStorage
from .private_files import ensure_private_dir

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    """Return current UTC timestamp as ISO string."""
    return datetime.now(timezone.utc).isoformat()


def _make_id() -> str:
    """Generate a short unique ID."""
    return uuid.uuid4().hex[:16]


def _hash_entry(entry: Dict[str, Any]) -> str:
    """Produce a deterministic hash of a trace entry for loop detection.

    Hashes tool_name + sorted tool_input so identical calls produce
    the same digest regardless of dict key order.
    """
    tool_name = entry.get("tool_name", entry.get("name", ""))
    tool_input = entry.get("tool_input", entry.get("input", entry.get("input_data", {})))

    canonical = json.dumps({"t": tool_name, "i": tool_input}, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:24]


def _tokenize_text(text: str) -> Set[str]:
    """Split text into a set of lowercased words for overlap comparison."""
    if not text:
        return set()
    # Strip punctuation, lowercase, split on whitespace
    cleaned = ""
    for ch in text:
        if ch.isalnum() or ch.isspace():
            cleaned += ch
        else:
            cleaned += " "
    return set(w for w in cleaned.lower().split() if len(w) > 1)


def _word_overlap(a: str, b: str) -> float:
    """Compute Jaccard similarity between word sets of two strings.

    Returns:
        Float in [0, 1]. 1.0 means identical word sets.
    """
    words_a = _tokenize_text(a)
    words_b = _tokenize_text(b)
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union)


def _extract_content(entry: Dict[str, Any]) -> str:
    """Extract the main textual content from a trace entry.

    Tries multiple common keys used in different trace formats.
    """
    # Try output fields first (most relevant for repetition)
    for key in ("tool_output", "output", "output_data", "response", "content", "text"):
        val = entry.get(key)
        if isinstance(val, str) and val.strip():
            return val
        if isinstance(val, dict):
            # Nested dict -- serialize for comparison
            return json.dumps(val, sort_keys=True)

    # Fall back to input
    for key in ("tool_input", "input", "input_data"):
        val = entry.get(key)
        if isinstance(val, str) and val.strip():
            return val
        if isinstance(val, dict):
            return json.dumps(val, sort_keys=True)

    return ""


def _extract_tokens(entry: Dict[str, Any]) -> int:
    """Extract token count from a trace entry.

    Looks for common token-count keys and sums input + output tokens.
    """
    total = 0

    # Direct token fields
    for key in ("input_tokens", "prompt_tokens"):
        val = entry.get(key)
        if isinstance(val, (int, float)):
            total += int(val)

    for key in ("output_tokens", "completion_tokens"):
        val = entry.get(key)
        if isinstance(val, (int, float)):
            total += int(val)

    # Nested usage dict (OpenAI-style)
    usage = entry.get("usage")
    if isinstance(usage, dict):
        total += int(usage.get("prompt_tokens", 0))
        total += int(usage.get("completion_tokens", 0))
        total += int(usage.get("total_tokens", 0))

    # Attributes may contain token info
    attrs = entry.get("attributes", {})
    if isinstance(attrs, dict):
        for key in ("gen_ai.usage.prompt_tokens", "gen_ai.usage.completion_tokens"):
            val = attrs.get(key)
            if isinstance(val, (int, float)):
                total += int(val)

    return total


class LiteRunner:
    """Standalone detection runner for single-developer use.

    Runs simple but effective detectors locally and stores results
    in SQLite via LiteStorage. No network access required.
    """

    def __init__(self, config: Optional[LiteConfig] = None):
        self.config = config or LiteConfig.load_or_default()
        ensure_private_dir(self.config.traces_dir)
        self.storage = LiteStorage(self.config.db_path)

    def _detector_enabled(self, name: str) -> bool:
        """Check if a detector is enabled in config."""
        if not self.config.enabled_detectors:
            return True  # empty list = all enabled
        return name in self.config.enabled_detectors

    def analyze_trace_file(
        self, trace_path: Path, session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Run detectors on a trace file (JSON or JSONL).

        Args:
            trace_path: Path to a .json or .jsonl trace file.
            session_id: Session identifier; defaults to the file stem.

        Returns:
            Summary dict with keys: session_id, file, entries_count,
            detections, detection_count, detectors_run.
        """
        if not trace_path.exists():
            raise FileNotFoundError(f"Trace file not found: {trace_path}")

        raw = trace_path.read_text()
        entries: List[Dict[str, Any]] = []

        # Try JSON array first
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                entries = parsed
            elif isinstance(parsed, dict):
                # Single object -- wrap in list
                entries = [parsed]
        except json.JSONDecodeError:
            pass

        # If not a JSON array, try JSONL
        if not entries:
            for line in raw.strip().split("\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.debug("Skipping invalid JSON line: %s", line[:80])

        if not entries:
            return {
                "session_id": "",
                "file": str(trace_path),
                "entries_count": 0,
                "detections": [],
                "detection_count": 0,
                "detectors_run": [],
            }

        return self.analyze_data(entries, session_id=session_id or str(trace_path.stem))

    def analyze_data(self, entries: List[Dict[str, Any]], session_id: str = "") -> Dict[str, Any]:
        """Run detectors on in-memory data.

        Args:
            entries: List of trace entry dicts.
            session_id: Optional session identifier.

        Returns:
            Summary dict with detection results.
        """
        if not session_id:
            session_id = _make_id()

        trace_id = _make_id()
        all_detections: List[LiteDetectionRecord] = []
        detectors_run: List[str] = []

        # Loop detection
        if self._detector_enabled("loop"):
            detectors_run.append("loop")
            all_detections.extend(self._detect_loops(entries, trace_id, session_id))

        # Overflow detection
        if self._detector_enabled("overflow"):
            detectors_run.append("overflow")
            all_detections.extend(self._detect_overflow(entries, trace_id, session_id))

        # Repetition detection
        if self._detector_enabled("repetition"):
            detectors_run.append("repetition")
            all_detections.extend(self._detect_repetition(entries, trace_id, session_id))

        # Drop positives below the configured severity floor (negatives are kept
        # so the dashboard can still show coverage)
        all_detections = [
            d
            for d in all_detections
            if not d.detected or d.severity >= self.config.severity_threshold
        ]

        # Store results
        if all_detections:
            self.storage.store_detections_batch(all_detections)

        # Build summary
        positive = [d for d in all_detections if d.detected]
        return {
            "session_id": session_id,
            "trace_id": trace_id,
            "entries_count": len(entries),
            "detections": [
                {
                    "id": d.id,
                    "detector": d.detector_name,
                    "detected": d.detected,
                    "severity": d.severity,
                    "confidence": d.confidence,
                    "method": d.method,
                    "details": d.details,
                }
                for d in all_detections
            ],
            "detection_count": len(positive),
            "detectors_run": detectors_run,
        }

    # -------------------------------------------------------------------------
    # Detectors
    # -------------------------------------------------------------------------

    def _detect_loops(
        self,
        entries: List[Dict[str, Any]],
        trace_id: str,
        session_id: str,
    ) -> List[LiteDetectionRecord]:
        """Detect loops via state hash comparison.

        Tracks the hash of each entry (tool_name + tool_input). If the same
        hash appears N or more times consecutively (default 3), it is flagged
        as a loop.

        Also detects non-consecutive repeated hashes that exceed the threshold.
        """
        threshold = self.config.loop_hash_threshold
        records: List[LiteDetectionRecord] = []

        if len(entries) < threshold:
            return records

        hashes = [_hash_entry(e) for e in entries]

        # --- Consecutive loop detection ---
        run_start = 0
        run_hash = hashes[0]
        run_len = 1

        for i in range(1, len(hashes)):
            if hashes[i] == run_hash:
                run_len += 1
            else:
                if run_len >= threshold:
                    # Found a consecutive loop
                    tool_name = entries[run_start].get(
                        "tool_name", entries[run_start].get("name", "unknown")
                    )
                    confidence = min(0.99, 0.70 + (run_len - threshold) * 0.05)
                    severity = min(80, 40 + run_len * 5)

                    records.append(
                        LiteDetectionRecord(
                            id=_make_id(),
                            trace_id=trace_id,
                            session_id=session_id,
                            detector_name="loop",
                            detected=True,
                            severity=severity,
                            confidence=confidence,
                            method="consecutive_hash",
                            details={
                                "hash": run_hash,
                                "consecutive_count": run_len,
                                "start_index": run_start,
                                "end_index": run_start + run_len - 1,
                                "tool_name": tool_name,
                            },
                            evidence=[
                                {
                                    "index": j,
                                    "tool_name": entries[j].get("tool_name", ""),
                                }
                                for j in range(run_start, min(run_start + run_len, run_start + 5))
                            ],
                            created_at=_now_iso(),
                        )
                    )

                run_start = i
                run_hash = hashes[i]
                run_len = 1

        # Check final run
        if run_len >= threshold:
            tool_name = entries[run_start].get(
                "tool_name", entries[run_start].get("name", "unknown")
            )
            confidence = min(0.99, 0.70 + (run_len - threshold) * 0.05)
            severity = min(80, 40 + run_len * 5)

            records.append(
                LiteDetectionRecord(
                    id=_make_id(),
                    trace_id=trace_id,
                    session_id=session_id,
                    detector_name="loop",
                    detected=True,
                    severity=severity,
                    confidence=confidence,
                    method="consecutive_hash",
                    details={
                        "hash": run_hash,
                        "consecutive_count": run_len,
                        "start_index": run_start,
                        "end_index": run_start + run_len - 1,
                        "tool_name": tool_name,
                    },
                    evidence=[
                        {
                            "index": j,
                            "tool_name": entries[j].get("tool_name", ""),
                        }
                        for j in range(run_start, min(run_start + run_len, run_start + 5))
                    ],
                    created_at=_now_iso(),
                )
            )

        # --- Non-consecutive frequency detection ---
        hash_counts: Counter = Counter(hashes)
        for h, count in hash_counts.items():
            if count >= threshold * 2:
                # Only flag if not already caught by consecutive detection
                already_caught = any(r.details.get("hash") == h for r in records)
                if already_caught:
                    continue

                # Find first occurrence for context
                first_idx = hashes.index(h)
                tool_name = entries[first_idx].get(
                    "tool_name", entries[first_idx].get("name", "unknown")
                )
                confidence = min(0.90, 0.50 + (count - threshold) * 0.05)
                severity = min(70, 30 + count * 3)

                records.append(
                    LiteDetectionRecord(
                        id=_make_id(),
                        trace_id=trace_id,
                        session_id=session_id,
                        detector_name="loop",
                        detected=True,
                        severity=severity,
                        confidence=confidence,
                        method="frequency_hash",
                        details={
                            "hash": h,
                            "total_count": count,
                            "tool_name": tool_name,
                            "total_entries": len(entries),
                            "frequency_pct": round(count / len(entries) * 100, 1),
                        },
                        evidence=[
                            {"index": i, "tool_name": entries[i].get("tool_name", "")}
                            for i, eh in enumerate(hashes)
                            if eh == h
                        ][:5],
                        created_at=_now_iso(),
                    )
                )

        return records

    def _detect_overflow(
        self,
        entries: List[Dict[str, Any]],
        trace_id: str,
        session_id: str,
    ) -> List[LiteDetectionRecord]:
        """Detect token overflow by accumulating token counts.

        Walks through entries in order, summing token counts. If the
        cumulative total exceeds the configured limit, flags overflow.
        """
        limit = self.config.overflow_token_limit
        records: List[LiteDetectionRecord] = []

        cumulative = 0
        overflow_index: Optional[int] = None

        for i, entry in enumerate(entries):
            tokens = _extract_tokens(entry)
            cumulative += tokens
            if cumulative > limit and overflow_index is None:
                overflow_index = i

        if overflow_index is not None:
            overshoot = cumulative - limit
            pct_over = round(overshoot / limit * 100, 1)
            severity = min(90, 50 + min(40, int(pct_over / 5)))
            confidence = min(0.95, 0.60 + min(0.35, pct_over / 200))

            records.append(
                LiteDetectionRecord(
                    id=_make_id(),
                    trace_id=trace_id,
                    session_id=session_id,
                    detector_name="overflow",
                    detected=True,
                    severity=severity,
                    confidence=confidence,
                    method="cumulative_token_count",
                    details={
                        "total_tokens": cumulative,
                        "limit": limit,
                        "overshoot": overshoot,
                        "pct_over": pct_over,
                        "overflow_at_entry": overflow_index,
                        "total_entries": len(entries),
                    },
                    evidence=[
                        {
                            "index": overflow_index,
                            "cumulative_tokens_at_overflow": limit + overshoot
                            if overflow_index == len(entries) - 1
                            else "exceeded_mid_trace",
                        }
                    ],
                    created_at=_now_iso(),
                )
            )

        # Even if no overflow, if we have token data, record a negative detection
        # so the dashboard can show coverage
        if overflow_index is None and cumulative > 0:
            records.append(
                LiteDetectionRecord(
                    id=_make_id(),
                    trace_id=trace_id,
                    session_id=session_id,
                    detector_name="overflow",
                    detected=False,
                    severity=0,
                    confidence=0.0,
                    method="cumulative_token_count",
                    details={
                        "total_tokens": cumulative,
                        "limit": limit,
                        "headroom": limit - cumulative,
                        "pct_used": round(cumulative / limit * 100, 1),
                    },
                    evidence=[],
                    created_at=_now_iso(),
                )
            )

        return records

    def _detect_repetition(
        self,
        entries: List[Dict[str, Any]],
        trace_id: str,
        session_id: str,
    ) -> List[LiteDetectionRecord]:
        """Detect content repetition between consecutive entries.

        Compares each pair of consecutive entries using word-overlap
        (Jaccard similarity). If similarity exceeds the configured
        threshold, flags as repetition.

        Only fires if 3+ consecutive pairs exceed the threshold,
        to avoid false positives from normal tool reuse.
        """
        threshold = self.config.repetition_similarity_threshold
        records: List[LiteDetectionRecord] = []

        if len(entries) < 2:
            return records

        contents = [_extract_content(e) for e in entries]

        # Compute pairwise similarity for consecutive entries
        similarities: List[float] = []
        for i in range(len(contents) - 1):
            sim = _word_overlap(contents[i], contents[i + 1])
            similarities.append(sim)

        # Find runs of high-similarity consecutive pairs
        run_start: Optional[int] = None
        run_length = 0

        for i, sim in enumerate(similarities):
            if sim >= threshold:
                if run_start is None:
                    run_start = i
                    run_length = 1
                else:
                    run_length += 1
            else:
                if run_start is not None and run_length >= 2:
                    # 2+ consecutive high-similarity pairs = 3+ similar entries
                    avg_sim = sum(similarities[run_start : run_start + run_length]) / run_length
                    severity = min(70, 30 + run_length * 8)
                    confidence = min(0.90, avg_sim)

                    records.append(
                        LiteDetectionRecord(
                            id=_make_id(),
                            trace_id=trace_id,
                            session_id=session_id,
                            detector_name="repetition",
                            detected=True,
                            severity=severity,
                            confidence=confidence,
                            method="word_overlap",
                            details={
                                "start_index": run_start,
                                "end_index": run_start + run_length,
                                "consecutive_similar_pairs": run_length,
                                "avg_similarity": round(avg_sim, 3),
                                "threshold": threshold,
                            },
                            evidence=[
                                {
                                    "pair": (j, j + 1),
                                    "similarity": round(similarities[j], 3),
                                    "tool_a": entries[j].get("tool_name", ""),
                                    "tool_b": entries[j + 1].get("tool_name", ""),
                                }
                                for j in range(run_start, run_start + run_length)
                            ][:5],
                            created_at=_now_iso(),
                        )
                    )

                run_start = None
                run_length = 0

        # Check final run
        if run_start is not None and run_length >= 2:
            avg_sim = sum(similarities[run_start : run_start + run_length]) / run_length
            severity = min(70, 30 + run_length * 8)
            confidence = min(0.90, avg_sim)

            records.append(
                LiteDetectionRecord(
                    id=_make_id(),
                    trace_id=trace_id,
                    session_id=session_id,
                    detector_name="repetition",
                    detected=True,
                    severity=severity,
                    confidence=confidence,
                    method="word_overlap",
                    details={
                        "start_index": run_start,
                        "end_index": run_start + run_length,
                        "consecutive_similar_pairs": run_length,
                        "avg_similarity": round(avg_sim, 3),
                        "threshold": threshold,
                    },
                    evidence=[
                        {
                            "pair": (j, j + 1),
                            "similarity": round(similarities[j], 3),
                            "tool_a": entries[j].get("tool_name", ""),
                            "tool_b": entries[j + 1].get("tool_name", ""),
                        }
                        for j in range(run_start, run_start + run_length)
                    ][:5],
                    created_at=_now_iso(),
                )
            )

        return records

    # -------------------------------------------------------------------------
    # Dashboard and export helpers
    # -------------------------------------------------------------------------

    def get_dashboard_data(self) -> Dict[str, Any]:
        """Summary data for terminal dashboard.

        Returns:
            Dict with keys: stats, recent_detections, config.
        """
        stats = self.storage.get_stats()
        recent = self.storage.get_detections(detected_only=True, limit=10)

        return {
            "stats": stats,
            "recent_detections": [
                {
                    "id": d.id,
                    "detector": d.detector_name,
                    "severity": d.severity,
                    "confidence": d.confidence,
                    "method": d.method,
                    "session_id": d.session_id[:12],
                    "created_at": d.created_at,
                    "details": d.details,
                }
                for d in recent
            ],
            "config": self.config.to_dict(),
            "db_path": str(self.config.db_path),
        }

    def export_results(self, output_path: Path, format: str = "json") -> int:
        """Export results for platform import.

        Args:
            output_path: Where to write the export file.
            format: Export format. Currently only "json" is supported.

        Returns:
            Number of detection records exported.
        """
        if format != "json":
            raise ValueError(f"Unsupported export format: {format}. Use 'json'.")

        return self.storage.export_for_platform(output_path)
