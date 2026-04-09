# cclog

Claude Code Token Audit Logger — tracks token usage and cost across all Claude Code sessions.

## Status

Under active development. See [token-tracker](https://github.com/Nguyen-Mau-Anh/token-tracker) for research and design documentation.

## Quick start

```bash
pipx install cclog
```

Configure hooks in `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse":  [{"matcher": "", "hooks": [{"type": "command", "command": "python -m cclog.hook pre"}]}],
    "PostToolUse": [{"matcher": "", "hooks": [{"type": "command", "command": "python -m cclog.hook post"}]}]
  }
}
```

Then run `cclog start` to begin monitoring.
