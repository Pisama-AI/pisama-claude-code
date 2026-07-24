"""Tests for PISAMA lite mode.

Tests the lightweight standalone detection system:
- LiteStorage: SQLite persistence for detections and scores
- LiteConfig: YAML-based configuration with defaults
- LiteRunner: Local detection runner for loop, overflow, and repetition
"""

import json

import pytest

from pisama_claude_code.lite import (
    LiteRunner,
    _extract_content,
    _extract_tokens,
    _hash_entry,
    _tokenize_text,
    _word_overlap,
)
from pisama_claude_code.lite_config import LiteConfig
from pisama_claude_code.lite_storage import (
    LiteDetectionRecord,
    LiteScoreRecord,
    LiteStorage,
)

# ===========================================================================
# LiteStorage
# ===========================================================================


class TestLiteStorage:
    def test_init_creates_db(self, tmp_path):
        LiteStorage(tmp_path / "test.db")
        assert (tmp_path / "test.db").exists()

    def test_init_creates_tables(self, tmp_path):
        import sqlite3

        LiteStorage(tmp_path / "test.db")
        conn = sqlite3.connect(str(tmp_path / "test.db"))
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        table_names = {t[0] for t in tables}
        assert "lite_detections" in table_names
        assert "lite_scores" in table_names
        assert "lite_sessions" in table_names
        conn.close()

    def test_store_and_get_detection(self, tmp_path):
        db = LiteStorage(tmp_path / "test.db")
        record = LiteDetectionRecord(
            id="d1",
            trace_id="t1",
            session_id="s1",
            detector_name="loop",
            detected=True,
            severity=60,
            confidence=0.8,
            method="hash",
            details={"info": "test"},
            evidence=[],
            created_at="2026-03-02T00:00:00Z",
        )
        db.store_detection(record)
        results = db.get_detections()
        assert len(results) >= 1
        assert results[0].detector_name == "loop"
        assert results[0].detected is True
        assert results[0].severity == 60
        assert results[0].confidence == 0.8
        assert results[0].details == {"info": "test"}

    def test_get_detections_filter_by_session(self, tmp_path):
        db = LiteStorage(tmp_path / "test.db")
        for i, sid in enumerate(["s1", "s2", "s1"]):
            db.store_detection(
                LiteDetectionRecord(
                    id=f"d{i}",
                    trace_id="t1",
                    session_id=sid,
                    detector_name="loop",
                    detected=True,
                    severity=50,
                    confidence=0.7,
                    method="hash",
                    details={},
                    evidence=[],
                    created_at=f"2026-03-02T00:0{i}:00Z",
                )
            )
        results = db.get_detections(session_id="s1")
        assert len(results) == 2
        assert all(r.session_id == "s1" for r in results)

    def test_get_detections_filter_by_detector(self, tmp_path):
        db = LiteStorage(tmp_path / "test.db")
        for i, det in enumerate(["loop", "overflow", "loop"]):
            db.store_detection(
                LiteDetectionRecord(
                    id=f"d{i}",
                    trace_id="t1",
                    session_id="s1",
                    detector_name=det,
                    detected=True,
                    severity=50,
                    confidence=0.7,
                    method="hash",
                    details={},
                    evidence=[],
                    created_at=f"2026-03-02T00:0{i}:00Z",
                )
            )
        results = db.get_detections(detector_name="overflow")
        assert len(results) == 1
        assert results[0].detector_name == "overflow"

    def test_get_detections_detected_only(self, tmp_path):
        db = LiteStorage(tmp_path / "test.db")
        db.store_detection(
            LiteDetectionRecord(
                id="d1",
                trace_id="t1",
                session_id="s1",
                detector_name="loop",
                detected=True,
                severity=60,
                confidence=0.8,
                method="hash",
                details={},
                evidence=[],
                created_at="2026-03-02T00:00:00Z",
            )
        )
        db.store_detection(
            LiteDetectionRecord(
                id="d2",
                trace_id="t1",
                session_id="s1",
                detector_name="overflow",
                detected=False,
                severity=0,
                confidence=0.0,
                method="count",
                details={},
                evidence=[],
                created_at="2026-03-02T00:01:00Z",
            )
        )
        results = db.get_detections(detected_only=True)
        assert len(results) == 1
        assert results[0].detected is True

    def test_store_detections_batch(self, tmp_path):
        db = LiteStorage(tmp_path / "test.db")
        records = [
            LiteDetectionRecord(
                id=f"d{i}",
                trace_id="t1",
                session_id="s1",
                detector_name="loop",
                detected=True,
                severity=50,
                confidence=0.7,
                method="hash",
                details={},
                evidence=[],
                created_at=f"2026-03-02T00:0{i}:00Z",
            )
            for i in range(5)
        ]
        db.store_detections_batch(records)
        results = db.get_detections()
        assert len(results) == 5

    def test_store_detections_batch_empty(self, tmp_path):
        db = LiteStorage(tmp_path / "test.db")
        db.store_detections_batch([])  # Should not raise
        results = db.get_detections()
        assert len(results) == 0

    def test_store_and_get_score(self, tmp_path):
        db = LiteStorage(tmp_path / "test.db")
        record = LiteScoreRecord(
            id="sc1",
            trace_id="t1",
            session_id="s1",
            score_type="quality",
            overall_score=0.85,
            dimension_scores={"goal": 0.9, "coherence": 0.8},
            summary="Good conversation",
            created_at="2026-03-02T00:00:00Z",
        )
        db.store_score(record)
        results = db.get_scores()
        assert len(results) == 1
        assert results[0].score_type == "quality"
        assert results[0].overall_score == 0.85
        assert results[0].dimension_scores["goal"] == 0.9

    def test_get_stats(self, tmp_path):
        db = LiteStorage(tmp_path / "test.db")
        db.store_detection(
            LiteDetectionRecord(
                id="d1",
                trace_id="t1",
                session_id="s1",
                detector_name="loop",
                detected=True,
                severity=60,
                confidence=0.8,
                method="hash",
                details={},
                evidence=[],
                created_at="2026-03-02T00:00:00Z",
            )
        )
        db.store_detection(
            LiteDetectionRecord(
                id="d2",
                trace_id="t1",
                session_id="s1",
                detector_name="overflow",
                detected=False,
                severity=0,
                confidence=0.0,
                method="count",
                details={},
                evidence=[],
                created_at="2026-03-02T00:01:00Z",
            )
        )
        stats = db.get_stats()
        assert stats["total_detections"] == 2
        assert stats["total_positive"] == 1
        assert stats["sessions"] >= 1
        assert "by_detector" in stats
        assert "loop" in stats["by_detector"]
        assert stats["by_detector"]["loop"]["detected"] == 1

    def test_get_stats_severity_buckets(self, tmp_path):
        db = LiteStorage(tmp_path / "test.db")
        for sev in [30, 50, 75]:  # low, medium, high
            db.store_detection(
                LiteDetectionRecord(
                    id=f"d{sev}",
                    trace_id="t1",
                    session_id="s1",
                    detector_name="loop",
                    detected=True,
                    severity=sev,
                    confidence=0.8,
                    method="hash",
                    details={},
                    evidence=[],
                    created_at="2026-03-02T00:00:00Z",
                )
            )
        stats = db.get_stats()
        assert stats["by_severity"]["low"] == 1
        assert stats["by_severity"]["medium"] == 1
        assert stats["by_severity"]["high"] == 1

    def test_export(self, tmp_path):
        db = LiteStorage(tmp_path / "test.db")
        db.store_detection(
            LiteDetectionRecord(
                id="d1",
                trace_id="t1",
                session_id="s1",
                detector_name="loop",
                detected=True,
                severity=60,
                confidence=0.8,
                method="hash",
                details={"key": "val"},
                evidence=["e1"],
                created_at="2026-03-02T00:00:00Z",
            )
        )
        # Non-detected records should NOT be exported
        db.store_detection(
            LiteDetectionRecord(
                id="d2",
                trace_id="t1",
                session_id="s1",
                detector_name="overflow",
                detected=False,
                severity=0,
                confidence=0.0,
                method="count",
                details={},
                evidence=[],
                created_at="2026-03-02T00:01:00Z",
            )
        )
        output = tmp_path / "export.json"
        count = db.export_for_platform(output)
        assert count == 1  # only positive detections
        assert output.exists()

        data = json.loads(output.read_text())
        assert data["format"] == "pisama-lite-export"
        assert data["version"] == "1.0"
        assert len(data["detections"]) == 1
        assert data["detections"][0]["detector_name"] == "loop"
        assert data["detections"][0]["detected"] is True

    def test_export_includes_scores(self, tmp_path):
        db = LiteStorage(tmp_path / "test.db")
        db.store_score(
            LiteScoreRecord(
                id="sc1",
                trace_id="t1",
                session_id="s1",
                score_type="quality",
                overall_score=0.85,
                dimension_scores={},
                summary="",
                created_at="2026-03-02T00:00:00Z",
            )
        )
        output = tmp_path / "export.json"
        db.export_for_platform(output)
        data = json.loads(output.read_text())
        assert "scores" in data
        assert len(data["scores"]) == 1

    def test_upsert_session_updates(self, tmp_path):
        db = LiteStorage(tmp_path / "test.db")
        db.store_detection(
            LiteDetectionRecord(
                id="d1",
                trace_id="t1",
                session_id="s1",
                detector_name="loop",
                detected=True,
                severity=60,
                confidence=0.8,
                method="hash",
                details={},
                evidence=[],
                created_at="2026-03-02T00:00:00Z",
            )
        )
        db.store_detection(
            LiteDetectionRecord(
                id="d2",
                trace_id="t2",
                session_id="s1",
                detector_name="loop",
                detected=True,
                severity=70,
                confidence=0.9,
                method="hash",
                details={},
                evidence=[],
                created_at="2026-03-02T01:00:00Z",
            )
        )
        stats = db.get_stats()
        assert stats["sessions"] == 1  # Same session, updated


# ===========================================================================
# LiteConfig
# ===========================================================================


class TestLiteConfig:
    def test_defaults(self):
        config = LiteConfig()
        assert config.severity_threshold == 40
        assert config.loop_hash_threshold == 3
        assert config.overflow_token_limit == 128_000
        assert config.repetition_similarity_threshold == 0.7
        assert config.llm_judge_enabled is False
        assert config.enabled_detectors == []

    def test_save_and_load(self, tmp_path):
        config = LiteConfig(severity_threshold=60, loop_hash_threshold=5)
        config_path = tmp_path / "config.yaml"
        config.save(config_path)
        assert config_path.exists()

        loaded = LiteConfig.load(config_path)
        assert loaded.severity_threshold == 60
        assert loaded.loop_hash_threshold == 5

    def test_load_nonexistent_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            LiteConfig.load(tmp_path / "nonexistent.yaml")

    def test_to_dict_masks_secrets(self):
        config = LiteConfig(
            anthropic_api_key="secret-key-123",
            platform_api_key="platform-secret",
        )
        d = config.to_dict()
        assert d["anthropic_api_key"] == "***"
        assert d["platform_api_key"] == "***"

    def test_to_dict_shows_none_when_no_secrets(self):
        config = LiteConfig()
        d = config.to_dict()
        assert d["anthropic_api_key"] is None
        assert d["platform_api_key"] is None

    def test_save_includes_optional_secrets(self, tmp_path):
        config = LiteConfig(
            anthropic_api_key="my-key",
            platform_url="https://pisama.example.com",
            platform_api_key="plat-key",
        )
        config_path = tmp_path / "config.yaml"
        config.save(config_path)

        loaded = LiteConfig.load(config_path)
        assert loaded.anthropic_api_key == "my-key"
        assert loaded.platform_url == "https://pisama.example.com"
        assert loaded.platform_api_key == "plat-key"

    def test_save_excludes_unset_secrets(self, tmp_path):
        config = LiteConfig()
        config_path = tmp_path / "config.yaml"
        config.save(config_path)

        yaml_text = config_path.read_text()
        assert "anthropic_api_key" not in yaml_text

    def test_enabled_detectors_roundtrip(self, tmp_path):
        config = LiteConfig(enabled_detectors=["loop", "overflow"])
        config_path = tmp_path / "config.yaml"
        config.save(config_path)

        loaded = LiteConfig.load(config_path)
        assert loaded.enabled_detectors == ["loop", "overflow"]

    def test_load_empty_yaml(self, tmp_path):
        config_path = tmp_path / "empty.yaml"
        config_path.write_text("")
        config = LiteConfig.load(config_path)
        # Should return defaults
        assert config.severity_threshold == 40


# ===========================================================================
# Lite helper functions
# ===========================================================================


class TestHashEntry:
    def test_deterministic(self):
        entry = {"tool_name": "search", "tool_input": {"q": "test"}}
        h1 = _hash_entry(entry)
        h2 = _hash_entry(entry)
        assert h1 == h2

    def test_different_inputs_different_hashes(self):
        e1 = {"tool_name": "search", "tool_input": {"q": "test1"}}
        e2 = {"tool_name": "search", "tool_input": {"q": "test2"}}
        assert _hash_entry(e1) != _hash_entry(e2)

    def test_key_order_irrelevant(self):
        e1 = {"tool_name": "search", "tool_input": {"a": 1, "b": 2}}
        e2 = {"tool_name": "search", "tool_input": {"b": 2, "a": 1}}
        assert _hash_entry(e1) == _hash_entry(e2)

    def test_handles_missing_keys(self):
        entry = {"other_key": "value"}
        h = _hash_entry(entry)
        assert isinstance(h, str) and len(h) == 24


class TestTokenizeText:
    def test_basic(self):
        words = _tokenize_text("Hello World")
        assert "hello" in words
        assert "world" in words

    def test_strips_punctuation(self):
        words = _tokenize_text("hello! world?")
        assert "hello" in words
        assert "world" in words

    def test_filters_single_chars(self):
        words = _tokenize_text("I am a test")
        assert "i" not in words  # single char
        assert "a" not in words  # single char
        assert "am" in words
        assert "test" in words

    def test_empty_string(self):
        assert _tokenize_text("") == set()


class TestWordOverlap:
    def test_identical_strings(self):
        sim = _word_overlap("hello world", "hello world")
        assert sim == 1.0

    def test_completely_different(self):
        sim = _word_overlap("hello world", "foo bar baz")
        assert sim == 0.0

    def test_partial_overlap(self):
        sim = _word_overlap("hello world foo", "hello world bar")
        assert 0.0 < sim < 1.0

    def test_empty_strings(self):
        assert _word_overlap("", "") == 0.0
        assert _word_overlap("hello", "") == 0.0
        assert _word_overlap("", "hello") == 0.0


class TestExtractContent:
    def test_extracts_from_tool_output(self):
        entry = {"tool_output": "some result"}
        assert _extract_content(entry) == "some result"

    def test_extracts_from_output(self):
        entry = {"output": "some output"}
        assert _extract_content(entry) == "some output"

    def test_extracts_from_content(self):
        entry = {"content": "some content"}
        assert _extract_content(entry) == "some content"

    def test_falls_back_to_input(self):
        entry = {"tool_input": "some input"}
        assert _extract_content(entry) == "some input"

    def test_empty_entry(self):
        assert _extract_content({}) == ""

    def test_serializes_dict_output(self):
        entry = {"output": {"key": "value"}}
        result = _extract_content(entry)
        assert "key" in result
        assert "value" in result


class TestExtractTokens:
    def test_extracts_from_direct_fields(self):
        entry = {"input_tokens": 100, "output_tokens": 200}
        assert _extract_tokens(entry) == 300

    def test_extracts_from_usage_dict(self):
        entry = {"usage": {"prompt_tokens": 50, "completion_tokens": 100}}
        assert _extract_tokens(entry) >= 150

    def test_extracts_from_attributes(self):
        entry = {
            "attributes": {
                "gen_ai.usage.prompt_tokens": 75,
                "gen_ai.usage.completion_tokens": 150,
            }
        }
        assert _extract_tokens(entry) >= 225

    def test_returns_zero_on_empty(self):
        assert _extract_tokens({}) == 0
        assert _extract_tokens({"other": "data"}) == 0


# ===========================================================================
# LiteRunner - Loop detection
# ===========================================================================


class TestLiteRunnerLoops:
    def test_detects_consecutive_loops(self, tmp_path):
        db_path = tmp_path / "test.db"
        config = LiteConfig(db_path=db_path)
        runner = LiteRunner(config)

        entries = [
            {"tool_name": "search", "tool_input": {"q": "test"}},
            {"tool_name": "search", "tool_input": {"q": "test"}},
            {"tool_name": "search", "tool_input": {"q": "test"}},
            {"tool_name": "search", "tool_input": {"q": "test"}},
        ]
        result = runner.analyze_data(entries, session_id="test-session")
        assert result["detection_count"] >= 1
        loop_detections = [
            d for d in result["detections"] if d["detector"] == "loop" and d["detected"]
        ]
        assert len(loop_detections) >= 1

    def test_no_loop_with_varied_input(self, tmp_path):
        db_path = tmp_path / "test.db"
        config = LiteConfig(db_path=db_path)
        runner = LiteRunner(config)

        entries = [{"tool_name": "search", "tool_input": {"q": f"query-{i}"}} for i in range(5)]
        result = runner.analyze_data(entries, session_id="test-session")
        loop_detections = [
            d for d in result["detections"] if d["detector"] == "loop" and d["detected"]
        ]
        assert len(loop_detections) == 0

    def test_respects_hash_threshold(self, tmp_path):
        db_path = tmp_path / "test.db"
        config = LiteConfig(db_path=db_path, loop_hash_threshold=5)
        runner = LiteRunner(config)

        # Only 4 repeats, threshold is 5 -- should not detect
        entries = [{"tool_name": "search", "tool_input": {"q": "test"}} for _ in range(4)]
        result = runner.analyze_data(entries, session_id="test-session")
        loop_detections = [
            d for d in result["detections"] if d["detector"] == "loop" and d["detected"]
        ]
        assert len(loop_detections) == 0

    def test_too_few_entries(self, tmp_path):
        db_path = tmp_path / "test.db"
        config = LiteConfig(db_path=db_path)
        runner = LiteRunner(config)

        entries = [
            {"tool_name": "search", "tool_input": {"q": "test"}}
            for _ in range(2)  # fewer than threshold (3)
        ]
        result = runner.analyze_data(entries, session_id="s")
        loop_detections = [
            d for d in result["detections"] if d["detector"] == "loop" and d["detected"]
        ]
        assert len(loop_detections) == 0


# ===========================================================================
# LiteRunner - Overflow detection
# ===========================================================================


class TestLiteRunnerOverflow:
    def test_detects_overflow(self, tmp_path):
        db_path = tmp_path / "test.db"
        config = LiteConfig(db_path=db_path, overflow_token_limit=100)
        runner = LiteRunner(config)

        entries = [
            {"tool_name": "llm", "input_tokens": 60, "output_tokens": 60},
        ]
        result = runner.analyze_data(entries, session_id="s")
        overflow_detections = [
            d for d in result["detections"] if d["detector"] == "overflow" and d["detected"]
        ]
        assert len(overflow_detections) >= 1

    def test_no_overflow_within_limit(self, tmp_path):
        db_path = tmp_path / "test.db"
        config = LiteConfig(db_path=db_path, overflow_token_limit=1000)
        runner = LiteRunner(config)

        entries = [
            {"tool_name": "llm", "input_tokens": 50, "output_tokens": 50},
        ]
        result = runner.analyze_data(entries, session_id="s")
        overflow_detections = [
            d for d in result["detections"] if d["detector"] == "overflow" and d["detected"]
        ]
        assert len(overflow_detections) == 0


# ===========================================================================
# LiteRunner - Repetition detection
# ===========================================================================


class TestLiteRunnerRepetition:
    def test_detects_repetition(self, tmp_path):
        db_path = tmp_path / "test.db"
        config = LiteConfig(db_path=db_path, repetition_similarity_threshold=0.5)
        runner = LiteRunner(config)

        # Same output repeated 4 times in a row
        entries = [
            {"tool_name": "llm", "output": "The answer is forty two and that is final"},
            {"tool_name": "llm", "output": "The answer is forty two and that is final"},
            {"tool_name": "llm", "output": "The answer is forty two and that is final"},
            {"tool_name": "llm", "output": "The answer is forty two and that is final"},
        ]
        result = runner.analyze_data(entries, session_id="s")
        rep_detections = [
            d for d in result["detections"] if d["detector"] == "repetition" and d["detected"]
        ]
        assert len(rep_detections) >= 1

    def test_no_repetition_with_varied_content(self, tmp_path):
        db_path = tmp_path / "test.db"
        config = LiteConfig(db_path=db_path)
        runner = LiteRunner(config)

        entries = [
            {"tool_name": "llm", "output": "Apples are red fruits"},
            {"tool_name": "llm", "output": "Bananas are yellow curved fruits"},
            {"tool_name": "llm", "output": "Cherries are small and sweet"},
            {"tool_name": "llm", "output": "Dates are palm tree fruits"},
        ]
        result = runner.analyze_data(entries, session_id="s")
        rep_detections = [
            d for d in result["detections"] if d["detector"] == "repetition" and d["detected"]
        ]
        assert len(rep_detections) == 0


# ===========================================================================
# LiteRunner - General
# ===========================================================================


class TestLiteRunnerGeneral:
    def test_analyze_data_returns_expected_keys(self, tmp_path):
        db_path = tmp_path / "test.db"
        config = LiteConfig(db_path=db_path)
        runner = LiteRunner(config)

        entries = [{"tool_name": "read", "tool_input": {"file": "a.py"}}]
        result = runner.analyze_data(entries, session_id="s")
        assert "session_id" in result
        assert "trace_id" in result
        assert "entries_count" in result
        assert "detections" in result
        assert "detection_count" in result
        assert "detectors_run" in result

    def test_analyze_data_runs_all_detectors(self, tmp_path):
        db_path = tmp_path / "test.db"
        config = LiteConfig(db_path=db_path)
        runner = LiteRunner(config)

        entries = [
            {"tool_name": "read", "tool_input": {"file": "a.py"}, "input_tokens": 50},
            {"tool_name": "write", "tool_input": {"file": "b.py"}, "output_tokens": 50},
        ]
        result = runner.analyze_data(entries, session_id="s")
        assert "loop" in result["detectors_run"]
        assert "overflow" in result["detectors_run"]
        assert "repetition" in result["detectors_run"]

    def test_enabled_detectors_filter(self, tmp_path):
        db_path = tmp_path / "test.db"
        config = LiteConfig(db_path=db_path, enabled_detectors=["loop"])
        runner = LiteRunner(config)

        entries = [{"tool_name": "read", "tool_input": {"file": "a.py"}}]
        result = runner.analyze_data(entries, session_id="s")
        assert "loop" in result["detectors_run"]
        assert "overflow" not in result["detectors_run"]
        assert "repetition" not in result["detectors_run"]

    def test_analyze_trace_file(self, tmp_path):
        db_path = tmp_path / "test.db"
        config = LiteConfig(db_path=db_path)
        runner = LiteRunner(config)

        trace_file = tmp_path / "trace.json"
        trace_file.write_text(
            json.dumps(
                [
                    {"tool_name": "read", "tool_input": {"file": "a.py"}, "input_tokens": 50},
                    {"tool_name": "write", "tool_input": {"file": "b.py"}, "output_tokens": 50},
                ]
            )
        )
        result = runner.analyze_trace_file(trace_file)
        assert "detection_count" in result
        assert result["entries_count"] == 2

    def test_analyze_trace_file_jsonl(self, tmp_path):
        db_path = tmp_path / "test.db"
        config = LiteConfig(db_path=db_path)
        runner = LiteRunner(config)

        trace_file = tmp_path / "trace.jsonl"
        lines = [
            json.dumps({"tool_name": "read", "tool_input": {"file": "a.py"}}),
            json.dumps({"tool_name": "write", "tool_input": {"file": "b.py"}}),
        ]
        trace_file.write_text("\n".join(lines))
        result = runner.analyze_trace_file(trace_file)
        assert result["entries_count"] == 2

    def test_analyze_trace_file_custom_session_id(self, tmp_path):
        db_path = tmp_path / "test.db"
        config = LiteConfig(db_path=db_path)
        runner = LiteRunner(config)

        trace_file = tmp_path / "trace.json"
        trace_file.write_text(json.dumps([{"tool_name": "read", "tool_input": {"file": "a.py"}}]))
        result = runner.analyze_trace_file(trace_file, session_id="my-session")
        assert result["session_id"] == "my-session"
        # Default falls back to the file stem
        result = runner.analyze_trace_file(trace_file)
        assert result["session_id"] == "trace"

    def test_severity_threshold_filters_positives(self, tmp_path):
        # 4 identical consecutive calls -> loop positive with severity 60
        entries = [{"tool_name": "search", "tool_input": {"q": "test"}} for _ in range(4)]

        config = LiteConfig(db_path=tmp_path / "low.db", severity_threshold=40)
        assert LiteRunner(config).analyze_data(entries, session_id="s")["detection_count"] > 0

        config = LiteConfig(db_path=tmp_path / "high.db", severity_threshold=90)
        runner = LiteRunner(config)
        result = runner.analyze_data(entries, session_id="s")
        assert result["detection_count"] == 0
        assert runner.storage.get_detections(session_id="s", detected_only=True) == []

    def test_analyze_trace_file_not_found(self, tmp_path):
        db_path = tmp_path / "test.db"
        config = LiteConfig(db_path=db_path)
        runner = LiteRunner(config)

        with pytest.raises(FileNotFoundError):
            runner.analyze_trace_file(tmp_path / "nonexistent.json")

    def test_analyze_trace_file_empty(self, tmp_path):
        db_path = tmp_path / "test.db"
        config = LiteConfig(db_path=db_path)
        runner = LiteRunner(config)

        trace_file = tmp_path / "empty.json"
        trace_file.write_text("")
        result = runner.analyze_trace_file(trace_file)
        assert result["entries_count"] == 0
        assert result["detection_count"] == 0

    def test_analyze_stores_results(self, tmp_path):
        db_path = tmp_path / "test.db"
        config = LiteConfig(db_path=db_path)
        runner = LiteRunner(config)

        entries = [
            {"tool_name": "search", "tool_input": {"q": "test"}},
            {"tool_name": "search", "tool_input": {"q": "test"}},
            {"tool_name": "search", "tool_input": {"q": "test"}},
        ]
        runner.analyze_data(entries, session_id="s1")

        # Verify results were persisted to storage
        stored = runner.storage.get_detections(session_id="s1")
        assert len(stored) >= 1

    def test_get_dashboard_data(self, tmp_path):
        db_path = tmp_path / "test.db"
        config = LiteConfig(db_path=db_path)
        runner = LiteRunner(config)

        entries = [
            {"tool_name": "search", "tool_input": {"q": "test"}},
            {"tool_name": "search", "tool_input": {"q": "test"}},
            {"tool_name": "search", "tool_input": {"q": "test"}},
        ]
        runner.analyze_data(entries, session_id="s1")

        dashboard = runner.get_dashboard_data()
        assert "stats" in dashboard
        assert "recent_detections" in dashboard
        assert "config" in dashboard
        assert "db_path" in dashboard

    def test_export_results(self, tmp_path):
        db_path = tmp_path / "test.db"
        config = LiteConfig(db_path=db_path)
        runner = LiteRunner(config)

        entries = [
            {"tool_name": "search", "tool_input": {"q": "test"}},
            {"tool_name": "search", "tool_input": {"q": "test"}},
            {"tool_name": "search", "tool_input": {"q": "test"}},
        ]
        runner.analyze_data(entries, session_id="s1")

        output = tmp_path / "export.json"
        count = runner.export_results(output)
        assert count >= 0
        assert output.exists()

    def test_export_unsupported_format_raises(self, tmp_path):
        db_path = tmp_path / "test.db"
        config = LiteConfig(db_path=db_path)
        runner = LiteRunner(config)

        with pytest.raises(ValueError, match="Unsupported export format"):
            runner.export_results(tmp_path / "export.csv", format="csv")
