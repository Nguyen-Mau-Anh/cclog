"""
cclog.jsonl — Read token usage from Claude Code JSONL transcript files.

Claude Code writes one JSONL line per API response content block, so the same
message.id appears multiple times. We deduplicate by message.id before summing.
"""
from __future__ import annotations

import glob
import json
import os
from typing import NamedTuple, Optional


class TranscriptTokens(NamedTuple):
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    model: str               # last non-synthetic model seen, or "unknown"
    last_msg_id: Optional[str]  # last message.id processed


def find_transcript(session_id: str) -> Optional[str]:
    """Find the JSONL transcript for a session by session_id.

    Searches ~/.claude/projects/*/<session_id>.jsonl.
    Returns the first match, or None if not found.
    """
    pattern = os.path.expanduser(f"~/.claude/projects/*/{session_id}.jsonl")
    matches = glob.glob(pattern)
    return matches[0] if matches else None


def read_session_tokens(transcript_path: str) -> TranscriptTokens:
    """Read and sum all token usage from a Claude Code JSONL transcript.

    Deduplicates by message.id (same API response appears once per content block).
    Skips entries where model == "<synthetic>" (placeholder, not real API calls).
    Returns zeros gracefully on any file error.
    """
    seen: set[str] = set()
    total_input = 0
    total_output = 0
    total_cache_creation = 0
    total_cache_read = 0
    model = "unknown"
    last_msg_id: Optional[str] = None

    try:
        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if entry.get("type") != "assistant":
                    continue

                msg = entry.get("message") or {}
                if msg.get("model") == "<synthetic>":
                    continue

                msg_id = msg.get("id")
                if not msg_id or msg_id in seen:
                    continue
                seen.add(msg_id)

                usage = msg.get("usage") or {}
                total_input += usage.get("input_tokens", 0)
                total_output += usage.get("output_tokens", 0)
                total_cache_creation += usage.get("cache_creation_input_tokens", 0)
                total_cache_read += usage.get("cache_read_input_tokens", 0)

                if msg.get("model") and msg["model"] != "<synthetic>":
                    model = msg["model"]
                last_msg_id = msg_id

    except (OSError, IOError):
        pass

    return TranscriptTokens(
        input_tokens=total_input,
        output_tokens=total_output,
        cache_creation_tokens=total_cache_creation,
        cache_read_tokens=total_cache_read,
        model=model,
        last_msg_id=last_msg_id,
    )
