"""
cclog.tokens — Three-tier token counting.

Tier 1 (api):       payload["usage"]["input_tokens"] is present.
Tier 2 (tiktoken):  tiktoken package installed; BPE-encode the JSON text.
Tier 3 (heuristic): (len(input_json) + len(output_json)) // 4.

net_input = max(0, gross_input - prev_gross_input)
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict

try:
    import tiktoken as _tiktoken
    _TIKTOKEN_AVAILABLE = True
    _ENCODER = _tiktoken.get_encoding("cl100k_base")
except Exception:
    _TIKTOKEN_AVAILABLE = False
    _ENCODER = None


@dataclass
class TokenResult:
    gross_input: int
    gross_output: int
    net_input: int
    counted_by: str  # "api" | "tiktoken" | "heuristic"


def _serialize(obj: Any) -> str:
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    return json.dumps(obj, ensure_ascii=False)


def _net(gross: int, prev: int) -> int:
    return max(0, gross - prev)


def count_tokens(payload: Dict[str, Any], prev_gross_input: int) -> TokenResult:
    usage = payload.get("usage") or {}

    # Tier 1: API-provided counts
    if "input_tokens" in usage:
        gross_in = int(usage.get("input_tokens") or 0)
        gross_out = int(usage.get("output_tokens") or 0)
        return TokenResult(
            gross_input=gross_in,
            gross_output=gross_out,
            net_input=_net(gross_in, prev_gross_input),
            counted_by="api",
        )

    input_text = _serialize(payload.get("tool_input"))
    output_text = _serialize(payload.get("tool_response"))

    # Tier 2: tiktoken BPE
    if _TIKTOKEN_AVAILABLE and _ENCODER is not None:
        gross_in = len(_ENCODER.encode(input_text))
        gross_out = len(_ENCODER.encode(output_text))
        return TokenResult(
            gross_input=gross_in,
            gross_output=gross_out,
            net_input=_net(gross_in, prev_gross_input),
            counted_by="tiktoken",
        )

    # Tier 3: character heuristic
    input_json = _serialize(payload.get("tool_input"))
    output_json = _serialize(payload.get("tool_response"))
    gross_in = (len(input_json) + len(output_json)) // 4
    return TokenResult(
        gross_input=gross_in,
        gross_output=0,
        net_input=_net(gross_in, prev_gross_input),
        counted_by="heuristic",
    )
