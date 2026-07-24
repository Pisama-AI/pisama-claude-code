"""Tests for the pisama-diagnose skill engine (skills/pisama-diagnose/diagnose.py).

The engine is a standalone stdlib script shipped beside the SKILL.md, so it is
loaded by path rather than imported as a package module. These tests exercise
the pure rendering/loading functions with realistic backend payload shapes (the
real DiagnosisResult and FixSuggestion.ide_patch contracts) — no network, no
mocked services.
"""

import importlib.util
import json
from pathlib import Path

import pytest

_ENGINE = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "pisama_claude_code"
    / "skills"
    / "pisama-diagnose"
    / "diagnose.py"
)


def _load_engine():
    spec = importlib.util.spec_from_file_location("pisama_diagnose_engine", _ENGINE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


diagnose = _load_engine()


# Real-shaped DiagnosisResult.to_dict() slice (see backend orchestrator).
DIAGNOSIS = {
    "trace_id": "trace-abc",
    "has_failures": True,
    "failure_count": 1,
    "primary_failure": {
        "category": "infinite_loop",
        "detected": True,
        "confidence": 0.82,
        "confidence_tier": "likely",
        "severity": "high",
        "title": "Agent repeats the same tool call",
        "description": "search_files was invoked identically 6 times.",
        "evidence": [{"span": "s3", "note": "identical args"}],
        "affected_spans": ["s3", "s4"],
        "mistake_step": 4,
        "mistake_agent": "researcher",
        "suggested_fix": "Add a retry limit so the loop terminates.",
    },
    "all_detections": [
        {
            "category": "infinite_loop",
            "confidence": 0.82,
            "severity": "high",
            "title": "Agent repeats the same tool call",
            "mistake_step": 4,
            "mistake_agent": "researcher",
            "affected_spans": ["s3"],
        }
    ],
    "self_healing_available": True,
    "detectors_run": ["infinite_loop", "overflow"],
    "root_cause_explanation": "No progress signal broke the loop.",
}

TRACE = {
    "trace_id": "trace-abc",
    "span_count": 7,
    "total_tokens": 1200,
    "source_format": "atif",
    "atif_schema_version": "ATIF-v1.7",
}

# Real-shaped FixSuggestion.ide_patch (see backend app/fixes/models.py).
IDE_PATCH = {
    "target_files": ["CLAUDE.md", ".cursorrules"],
    "apply_mode": "suggested",
    "framework": None,
    "instructions": (
        "<!-- Pisama fix · infinite_loop · retry_limit · fix_1 -->\n"
        "## Add retry limit to prevent infinite loops\n\n"
        "Add a maximum retry limit of 7 iterations.\n\n"
        "**Apply in `agent.py`:**\n\n"
        "```python\nMAX_RETRIES = 7\n```\n"
    ),
    "verification": (
        "# Verify the fix resolved the `infinite_loop` failure:\n"
        "# 2. Re-check with Pisama and confirm the detector clears (>= 0.30 drop)\n"
        "pytest -q\n"
    ),
}


class TestRenderIdePatch:
    def test_renders_apply_section_with_fenced_blocks(self):
        lines = diagnose._render_ide_patch(IDE_PATCH)
        text = "\n".join(lines)
        assert "## Apply this fix" in text
        # Instructions are wrapped in a 4-backtick fence so the inner
        # ```python snippet survives intact.
        assert "````markdown" in text
        assert "## Add retry limit to prevent infinite loops" in text
        assert "```python" in text  # inner snippet preserved
        assert "### Verify the fix" in text
        assert "pytest -q" in text

    def test_connector_mode_mentions_n8n_apply(self):
        patch = dict(IDE_PATCH, apply_mode="connector", framework="n8n")
        text = "\n".join(diagnose._render_ide_patch(patch))
        assert "n8n" in text and "--apply" in text

    def test_empty_patch_renders_nothing(self):
        assert diagnose._render_ide_patch(None) == []
        assert diagnose._render_ide_patch({"instructions": "", "verification": ""}) == []


class TestRenderReport:
    def test_includes_patch_when_present(self):
        report = diagnose.render_report(DIAGNOSIS, TRACE, ide_patch=IDE_PATCH)
        assert "# Pisama diagnosis" in report
        assert "## Primary failure" in report
        assert "agent `researcher`, step 4" in report  # localization
        assert "## Apply this fix" in report  # the patch section
        assert "## Suggested fix" in report  # still shows prose fix

    def test_falls_back_to_suggested_fix_without_patch(self):
        report = diagnose.render_report(DIAGNOSIS, TRACE, ide_patch=None)
        assert "## Suggested fix" in report
        assert "Add a retry limit so the loop terminates." in report
        assert "## Apply this fix" not in report  # no patch section

    def test_renders_clean_verdict(self):
        clean = {"has_failures": False, "all_detections": [], "detectors_run": ["x"]}
        report = diagnose.render_report(clean, TRACE)
        assert "no failures detected" in report.lower()


class TestHelpers:
    def test_localized_degrades_honestly(self):
        assert diagnose._localized({"mistake_agent": "a", "mistake_step": 2}) == "agent `a`, step 2"
        assert diagnose._localized({"mistake_step": 2}) == "step 2"
        assert diagnose._localized({"affected_spans": ["s1", "s2"]}) == "span `s1` (+1 more)"
        assert diagnose._localized({}) == "not localized to a single span"

    def test_conf_pct(self):
        assert diagnose._conf_pct(0.82) == "82%"
        assert diagnose._conf_pct(82) == "82%"
        assert diagnose._conf_pct(None) == "None"

    def test_load_trajectory_bare_and_enveloped(self, tmp_path):
        traj = {
            "schema_version": "ATIF-v1.7",
            "agent": {"name": "a", "version": "1"},
            "steps": [{"step_id": 1, "source": "user", "message": "hi"}],
        }
        bare = tmp_path / "bare.json"
        bare.write_text(json.dumps(traj))
        assert diagnose.load_trajectory(str(bare))["schema_version"] == "ATIF-v1.7"

        env = tmp_path / "env.json"
        env.write_text(json.dumps({"trajectory": traj, "apply_fix": False}))
        # Envelope is unwrapped to the inner trajectory.
        assert diagnose.load_trajectory(str(env))["agent"]["name"] == "a"

    def test_load_trajectory_rejects_non_object(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("[1, 2, 3]")
        with pytest.raises(SystemExit):
            diagnose.load_trajectory(str(bad))
