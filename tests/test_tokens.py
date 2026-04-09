"""Tests for cclog.tokens — three-tier token counting."""
import json
import pytest

from cclog.tokens import TokenResult, count_tokens


def _payload(input_tokens=None, output_tokens=None, tool_input=None, tool_response=None):
    p = {}
    if tool_input is not None:
        p["tool_input"] = tool_input
    if tool_response is not None:
        p["tool_response"] = tool_response
    if input_tokens is not None or output_tokens is not None:
        p["usage"] = {}
        if input_tokens is not None:
            p["usage"]["input_tokens"] = input_tokens
        if output_tokens is not None:
            p["usage"]["output_tokens"] = output_tokens
    return p


# --- Tier 1: API counts ---

def test_api_tier_uses_usage_field():
    result = count_tokens(_payload(input_tokens=300, output_tokens=50), prev_gross_input=0)
    assert result.counted_by == "api"

def test_api_tier_gross_input():
    result = count_tokens(_payload(input_tokens=300, output_tokens=50), prev_gross_input=0)
    assert result.gross_input == 300

def test_api_tier_gross_output():
    result = count_tokens(_payload(input_tokens=300, output_tokens=50), prev_gross_input=0)
    assert result.gross_output == 50

def test_api_tier_net_input_first_event():
    result = count_tokens(_payload(input_tokens=300, output_tokens=50), prev_gross_input=0)
    assert result.net_input == 300

def test_api_tier_net_input_delta():
    result = count_tokens(_payload(input_tokens=500, output_tokens=80), prev_gross_input=300)
    assert result.net_input == 200

def test_api_tier_net_input_never_negative():
    result = count_tokens(_payload(input_tokens=200, output_tokens=10), prev_gross_input=300)
    assert result.net_input == 0

def test_api_tier_partial_usage_only_input():
    result = count_tokens(_payload(input_tokens=100), prev_gross_input=0)
    assert result.counted_by == "api"
    assert result.gross_output == 0

def test_api_tier_partial_usage_only_output():
    """Payload with only output_tokens (no input_tokens) falls through to heuristic/tiktoken tier."""
    payload = _payload(output_tokens=20)
    result = count_tokens(payload, prev_gross_input=0)
    # Without input_tokens in usage, tier 1 is not triggered
    assert result.counted_by in ("tiktoken", "heuristic")


# --- Tier 2: tiktoken (skip if not installed) ---

@pytest.mark.skipif(
    pytest.importorskip.__module__ and not __import__("importlib.util", fromlist=["find_spec"]).find_spec("tiktoken"),
    reason="tiktoken not installed",
)
class TestTiktokenTier:
    def test_tiktoken_tier_used_when_no_usage(self):
        pytest.importorskip("tiktoken", reason="tiktoken not installed")
        payload = _payload(
            tool_input={"command": "ls -la"},
            tool_response={"output": "total 8\n-rw-r--r-- 1 user user 123 Apr 9 file.txt\n"},
        )
        result = count_tokens(payload, prev_gross_input=0)
        assert result.counted_by == "tiktoken"

    def test_tiktoken_tier_gross_input_positive(self):
        pytest.importorskip("tiktoken", reason="tiktoken not installed")
        payload = _payload(tool_input={"command": "cat README.md"}, tool_response={"output": "# Hello\n"})
        result = count_tokens(payload, prev_gross_input=0)
        assert result.gross_input > 0

    def test_tiktoken_tier_net_delta(self):
        pytest.importorskip("tiktoken", reason="tiktoken not installed")
        payload = _payload(tool_input={"command": "echo hi"}, tool_response={"output": "hi\n"})
        result = count_tokens(payload, prev_gross_input=5)
        assert result.net_input == max(0, result.gross_input - 5)


# --- Tier 3: heuristic ---

def test_heuristic_tier_no_usage_no_tiktoken(monkeypatch):
    import cclog.tokens as tok_mod
    monkeypatch.setattr(tok_mod, "_TIKTOKEN_AVAILABLE", False)
    payload = _payload(tool_input={"command": "echo hello"}, tool_response={"output": "hello\n"})
    result = count_tokens(payload, prev_gross_input=0)
    assert result.counted_by == "heuristic"

def test_heuristic_tier_formula(monkeypatch):
    import cclog.tokens as tok_mod
    monkeypatch.setattr(tok_mod, "_TIKTOKEN_AVAILABLE", False)
    input_text = "A" * 40
    output_text = "B" * 20
    payload = {"tool_input": {"command": input_text}, "tool_response": {"output": output_text}}
    result = count_tokens(payload, prev_gross_input=0)
    input_json = json.dumps({"command": input_text})
    output_json = json.dumps({"output": output_text})
    assert result.gross_input == len(input_json) // 4
    assert result.gross_output == len(output_json) // 4  # add this assertion

def test_heuristic_tier_empty_payload(monkeypatch):
    import cclog.tokens as tok_mod
    monkeypatch.setattr(tok_mod, "_TIKTOKEN_AVAILABLE", False)
    result = count_tokens({}, prev_gross_input=0)
    assert result.counted_by == "heuristic"
    assert result.gross_input == 0
    assert result.gross_output == 0
    assert result.net_input == 0


# --- TokenResult dataclass ---

def test_token_result_fields():
    tr = TokenResult(gross_input=100, gross_output=20, net_input=60, counted_by="api")
    assert tr.gross_input == 100
    assert tr.gross_output == 20
    assert tr.net_input == 60
    assert tr.counted_by == "api"
