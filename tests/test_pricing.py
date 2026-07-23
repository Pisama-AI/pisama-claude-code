"""Tests for model-aware Claude cost estimation."""

from datetime import date

from pisama_claude_code.hooks.capture_hook import _pricing_for_model, calculate_cost


def test_active_model_families_resolve_to_current_rates():
    assert _pricing_for_model("claude-opus-4-8")["input"] == 5.00
    assert _pricing_for_model("claude-sonnet-4-6")["output"] == 15.00
    assert _pricing_for_model("claude-haiku-4-5-20251001")["cache_read"] == 0.10


def test_sonnet_5_price_changes_after_introductory_period():
    assert _pricing_for_model("claude-sonnet-5", date(2026, 8, 31))["input"] == 2.00
    assert _pricing_for_model("claude-sonnet-5", date(2026, 9, 1))["input"] == 3.00


def test_cost_uses_cache_write_and_cache_read_rates():
    usage = {
        "input_tokens": 1_000_000,
        "output_tokens": 1_000_000,
        "cache_creation_input_tokens": 1_000_000,
        "cache_read_input_tokens": 1_000_000,
    }
    assert calculate_cost("claude-opus-4-8", usage) == 36.75


def test_unknown_models_use_conservative_sonnet_fallback():
    assert _pricing_for_model("claude-future-model") == {
        "input": 3.00,
        "output": 15.00,
        "cache_read": 0.30,
        "cache_write": 3.75,
    }
