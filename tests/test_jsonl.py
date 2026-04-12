"""Tests for cclog.jsonl — transcript finding and token reading."""
from __future__ import annotations

import json
import tempfile
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from cclog.jsonl import find_transcript, read_session_tokens, TranscriptTokens


# ---------------------------------------------------------------------------
# find_transcript
# ---------------------------------------------------------------------------

def test_find_transcript_returns_none_when_no_match():
    with patch("glob.glob", return_value=[]):
        result = find_transcript("abc123")
    assert result is None


def test_find_transcript_returns_path_when_match_exists():
    fake_path = "/home/user/.claude/projects/my-proj/abc123.jsonl"
    with patch("glob.glob", return_value=[fake_path]):
        result = find_transcript("abc123")
    assert result == fake_path


def test_find_transcript_returns_first_match_when_multiple():
    paths = [
        "/home/user/.claude/projects/proj-a/abc123.jsonl",
        "/home/user/.claude/projects/proj-b/abc123.jsonl",
    ]
    with patch("glob.glob", return_value=paths):
        result = find_transcript("abc123")
    assert result == paths[0]


# ---------------------------------------------------------------------------
# read_session_tokens — helpers
# ---------------------------------------------------------------------------

def _make_entry(msg_id: str, model: str, input_tokens: int, output_tokens: int,
                cache_creation: int = 0, cache_read: int = 0) -> dict:
    return {
        "type": "assistant",
        "message": {
            "id": msg_id,
            "model": model,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_creation_input_tokens": cache_creation,
                "cache_read_input_tokens": cache_read,
            },
        },
    }


def _write_jsonl(path: str, entries: list) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# read_session_tokens — tests
# ---------------------------------------------------------------------------

def test_read_session_tokens_empty_file():
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        # Empty file — nothing written
        result = read_session_tokens(tmp_path)
        assert result.input_tokens == 0
        assert result.output_tokens == 0
        assert result.cache_creation_tokens == 0
        assert result.cache_read_tokens == 0
        assert result.last_msg_id is None
    finally:
        os.unlink(tmp_path)


def test_read_session_tokens_no_assistant_entries():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tmp:
        tmp_path = tmp.name
        # Only non-assistant entries
        tmp.write(json.dumps({"type": "human", "text": "hello"}) + "\n")
        tmp.write(json.dumps({"type": "system", "content": "..."}) + "\n")
    try:
        result = read_session_tokens(tmp_path)
        assert result.last_msg_id is None
        assert result.input_tokens == 0
    finally:
        os.unlink(tmp_path)


def test_read_session_tokens_deduplicates_same_message_id():
    """Same message.id appearing multiple times should be counted once."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tmp:
        tmp_path = tmp.name
        entry = _make_entry("msg-001", "claude-sonnet-4-6", input_tokens=100, output_tokens=50)
        # Write the same entry three times (mimics multiple content blocks)
        for _ in range(3):
            tmp.write(json.dumps(entry) + "\n")
    try:
        result = read_session_tokens(tmp_path)
        assert result.input_tokens == 100   # counted once
        assert result.output_tokens == 50   # counted once
        assert result.last_msg_id == "msg-001"
    finally:
        os.unlink(tmp_path)


def test_read_session_tokens_skips_synthetic_model():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tmp:
        tmp_path = tmp.name
        synthetic = _make_entry("msg-syn", "<synthetic>", input_tokens=999, output_tokens=999)
        real = _make_entry("msg-real", "claude-sonnet-4-6", input_tokens=100, output_tokens=50)
        tmp.write(json.dumps(synthetic) + "\n")
        tmp.write(json.dumps(real) + "\n")
    try:
        result = read_session_tokens(tmp_path)
        assert result.input_tokens == 100   # synthetic not counted
        assert result.output_tokens == 50
        assert result.model == "claude-sonnet-4-6"
        assert result.last_msg_id == "msg-real"
    finally:
        os.unlink(tmp_path)


def test_read_session_tokens_sums_multiple_messages():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tmp:
        tmp_path = tmp.name
        entries = [
            _make_entry("msg-001", "claude-sonnet-4-6", input_tokens=100, output_tokens=50,
                        cache_creation=20, cache_read=10),
            _make_entry("msg-002", "claude-sonnet-4-6", input_tokens=200, output_tokens=80,
                        cache_creation=30, cache_read=15),
        ]
        for e in entries:
            tmp.write(json.dumps(e) + "\n")
    try:
        result = read_session_tokens(tmp_path)
        assert result.input_tokens == 300
        assert result.output_tokens == 130
        assert result.cache_creation_tokens == 50
        assert result.cache_read_tokens == 25
        assert result.model == "claude-sonnet-4-6"
        assert result.last_msg_id == "msg-002"
    finally:
        os.unlink(tmp_path)


def test_read_session_tokens_sets_last_msg_id_to_last_seen():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tmp:
        tmp_path = tmp.name
        for i in range(5):
            entry = _make_entry(f"msg-{i:03d}", "claude-sonnet-4-6",
                                input_tokens=10, output_tokens=5)
            tmp.write(json.dumps(entry) + "\n")
    try:
        result = read_session_tokens(tmp_path)
        assert result.last_msg_id == "msg-004"
    finally:
        os.unlink(tmp_path)


def test_read_session_tokens_nonexistent_file_returns_zeros():
    result = read_session_tokens("/nonexistent/path/transcript.jsonl")
    assert result.input_tokens == 0
    assert result.output_tokens == 0
    assert result.last_msg_id is None


def test_read_session_tokens_skips_malformed_json_lines():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tmp:
        tmp_path = tmp.name
        tmp.write("not valid json\n")
        entry = _make_entry("msg-001", "claude-sonnet-4-6", input_tokens=100, output_tokens=50)
        tmp.write(json.dumps(entry) + "\n")
    try:
        result = read_session_tokens(tmp_path)
        assert result.input_tokens == 100
        assert result.last_msg_id == "msg-001"
    finally:
        os.unlink(tmp_path)


def test_read_session_tokens_returns_transcript_tokens_namedtuple():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tmp:
        tmp_path = tmp.name
        entry = _make_entry("msg-001", "claude-sonnet-4-6", input_tokens=1, output_tokens=1)
        tmp.write(json.dumps(entry) + "\n")
    try:
        result = read_session_tokens(tmp_path)
        assert isinstance(result, TranscriptTokens)
    finally:
        os.unlink(tmp_path)


def test_read_session_tokens_model_defaults_to_unknown_with_no_entries():
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        result = read_session_tokens(tmp_path)
        assert result.model == "unknown"
    finally:
        os.unlink(tmp_path)
