"""cclog.pricing — static token cost table with optional YAML override."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

# Prices in USD per 1M tokens (input, output, cache_write, cache_read)
PRICE_TABLE: Dict[str, Dict[str, float]] = {
    "claude-3-5-sonnet":  {"input": 3.0,   "output": 15.0,  "cache_write": 3.75,   "cache_read": 0.30},
    "claude-3-5-haiku":   {"input": 0.80,  "output": 4.0,   "cache_write": 1.00,   "cache_read": 0.08},
    "claude-opus-4":      {"input": 15.0,  "output": 75.0,  "cache_write": 18.75,  "cache_read": 1.50},
    "claude-opus-4-6":    {"input": 15.0,  "output": 75.0,  "cache_write": 18.75,  "cache_read": 1.50},
    "claude-sonnet-4-6":  {"input": 3.0,   "output": 15.0,  "cache_write": 3.75,   "cache_read": 0.30},
    "claude-haiku-4-5":   {"input": 0.80,  "output": 4.0,   "cache_write": 1.00,   "cache_read": 0.08},
}

_DEFAULT_YAML = Path.home() / ".cclog" / "pricing.yaml"


def _load_yaml_overrides(path: str) -> Dict[str, Dict[str, float]]:
    """Load pricing overrides from a YAML file. Returns {} on any error."""
    try:
        import yaml  # type: ignore
        with open(path) as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            return {}
        validated = {}
        for model, prices in data.items():
            if (
                isinstance(prices, dict)
                and "input" in prices
                and "output" in prices
                and isinstance(prices["input"], (int, float))
                and isinstance(prices["output"], (int, float))
            ):
                validated[model] = prices
        return validated
    except Exception:
        return {}


def get_cost_usd(
    model: str,
    input_tok: int,
    output_tok: int,
    yaml_path: Optional[str] = None,
) -> Optional[float]:
    """Return estimated cost in USD, or None if the model is unknown."""
    input_tok = max(0, input_tok)   # clamp negative values to 0
    output_tok = max(0, output_tok)  # clamp negative values to 0
    table = dict(PRICE_TABLE)
    override_path = yaml_path or os.environ.get("CCLOG_PRICING_YAML") or str(_DEFAULT_YAML)
    overrides = _load_yaml_overrides(override_path)
    table.update(overrides)

    prices = table.get(model)
    if prices is None:
        return None

    return (input_tok * prices["input"] + output_tok * prices["output"]) / 1_000_000


def get_session_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
    yaml_path: Optional[str] = None,
) -> Optional[float]:
    """Return total cost in USD including cache token pricing.

    Uses cache_write / cache_read fields from price table.
    Falls back to 1.25× / 0.10× input price if not explicitly set.
    Returns None if the model is unknown.
    """
    input_tokens = max(0, input_tokens)
    output_tokens = max(0, output_tokens)
    cache_creation_tokens = max(0, cache_creation_tokens)
    cache_read_tokens = max(0, cache_read_tokens)

    table = dict(PRICE_TABLE)
    override_path = yaml_path or os.environ.get("CCLOG_PRICING_YAML") or str(_DEFAULT_YAML)
    overrides = _load_yaml_overrides(override_path)
    table.update(overrides)

    prices = table.get(model)
    if prices is None:
        return None

    cost = (
        input_tokens * prices["input"] / 1_000_000
        + output_tokens * prices["output"] / 1_000_000
        + cache_creation_tokens * prices.get("cache_write", prices["input"] * 1.25) / 1_000_000
        + cache_read_tokens * prices.get("cache_read", prices["input"] * 0.10) / 1_000_000
    )
    return round(cost, 6)
