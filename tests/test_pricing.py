"""Tests for cclog.pricing — cost calculation and YAML override."""
import pytest
from cclog.pricing import get_cost_usd, PRICE_TABLE, _load_yaml_overrides


def test_price_table_has_claude_3_5_sonnet():
    assert "claude-3-5-sonnet" in PRICE_TABLE

def test_price_table_has_claude_3_5_haiku():
    assert "claude-3-5-haiku" in PRICE_TABLE

def test_price_table_has_claude_opus_4():
    assert "claude-opus-4" in PRICE_TABLE

def test_price_table_entry_structure():
    for model, prices in PRICE_TABLE.items():
        assert "input" in prices
        assert "output" in prices
        assert prices["input"] >= 0
        assert prices["output"] >= 0

def test_get_cost_usd_known_model():
    # claude-3-5-sonnet: $3/M input, $15/M output
    cost = get_cost_usd("claude-3-5-sonnet", input_tok=1_000_000, output_tok=1_000_000)
    assert abs(cost - 18.0) < 0.001

def test_get_cost_usd_haiku():
    # claude-3-5-haiku: $0.80/M input, $4/M output
    cost = get_cost_usd("claude-3-5-haiku", input_tok=1_000_000, output_tok=1_000_000)
    assert abs(cost - 4.80) < 0.001

def test_get_cost_usd_zero_tokens():
    cost = get_cost_usd("claude-3-5-sonnet", input_tok=0, output_tok=0)
    assert cost == 0.0

def test_get_cost_usd_unknown_model_returns_none():
    result = get_cost_usd("some-future-model-xyz", input_tok=100, output_tok=50)
    assert result is None

def test_get_cost_usd_small_count():
    # 1000 input tokens of claude-3-5-sonnet at $3/M = $0.003
    cost = get_cost_usd("claude-3-5-sonnet", input_tok=1000, output_tok=0)
    assert abs(cost - 0.003) < 1e-6

def test_load_yaml_overrides_missing_file():
    result = _load_yaml_overrides("/nonexistent/path/pricing.yaml")
    assert result == {}

def test_load_yaml_overrides_malformed(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(":::not valid yaml:::")
    assert _load_yaml_overrides(str(bad)) == {}

def test_load_yaml_overrides_valid(tmp_path):
    f = tmp_path / "pricing.yaml"
    f.write_text("my-model:\n  input: 1.0\n  output: 2.0\n")
    result = _load_yaml_overrides(str(f))
    assert "my-model" in result
    assert result["my-model"]["input"] == 1.0

def test_get_cost_usd_with_yaml_override(tmp_path):
    f = tmp_path / "pricing.yaml"
    f.write_text("claude-3-5-sonnet:\n  input: 0.0\n  output: 0.0\n")
    cost = get_cost_usd("claude-3-5-sonnet", input_tok=1_000_000, output_tok=1_000_000, yaml_path=str(f))
    assert cost == 0.0
