"""cclog.pricing — static token cost table with optional YAML override."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

# Prices in USD per 1M tokens (input, output)
PRICE_TABLE: Dict[str, Dict[str, float]] = {
    "claude-3-5-sonnet":     {"input": 3.0,   "output": 15.0},
    "claude-3-5-haiku":      {"input": 0.80,  "output": 4.0},
    "claude-opus-4":         {"input": 15.0,  "output": 75.0},
    "claude-sonnet-4-6":     {"input": 3.0,   "output": 15.0},
    "claude-haiku-4-5":      {"input": 0.80,  "output": 4.0},
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
