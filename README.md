# pisama-claude-code

> Lightweight trace capture for Claude Code sessions with token usage and cost tracking.

[![PyPI version](https://img.shields.io/pypi/v/pisama-claude-code.svg)](https://pypi.org/project/pisama-claude-code/)
[![GitHub stars](https://img.shields.io/github/stars/Pisama-AI/pisama-claude-code?style=social)](https://github.com/Pisama-AI/pisama-claude-code)
[![Python versions](https://img.shields.io/pypi/pyversions/pisama-claude-code.svg)](https://pypi.org/project/pisama-claude-code/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![CI](https://github.com/Pisama-AI/pisama-claude-code/actions/workflows/ci.yml/badge.svg)](https://github.com/Pisama-AI/pisama-claude-code/actions/workflows/ci.yml)
[![Downloads](https://static.pepy.tech/badge/pisama-claude-code)](https://pepy.tech/project/pisama-claude-code)
[![Downloads/month](https://img.shields.io/pypi/dm/pisama-claude-code)](https://pypistats.org/packages/pisama-claude-code)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

## Demo

![pisama-claude-code demo](assets/demo.gif)

## Why Pisama?

When working with Claude Code, have you ever wondered:

- **How much did that session cost?** Track token usage and costs in real-time
- **What tools were called?** See every Bash, Read, Write, and Edit operation
- **Why did it fail?** Capture traces for debugging and forensics
- **Can I export my sessions?** JSONL export for analysis or compliance

**pisama-claude-code** captures everything Claude Code does, locally and privately.

```
┌─────────────────────┐         ┌─────────────────────┐
│   Claude Code       │         │   Pisama Platform   │
│   + pisama-cc       │ ──────▶ │   (optional)        │
│   (capture)         │  sync   │   - detection       │
└─────────────────────┘         │   - self-healing    │
        │                       └─────────────────────┘
        │
        ▼
   ~/.claude/pisama/traces/
   (local storage)
```

## Installation

```bash
pip install pisama-claude-code
```

**Requirements:** Python 3.10+ and [Claude Code CLI](https://claude.ai/code)

## Quick Start

```bash
# 1. Install capture hooks
pisama-cc install

# 2. Use Claude Code normally - traces are captured automatically

# 3. View your session data
pisama-cc status        # Summary with token totals and cost
pisama-cc traces        # Recent tool calls
pisama-cc usage         # Detailed breakdown
```

## Features

### Token & Cost Tracking

```bash
$ pisama-cc usage --by-model --by-tool

📊 Token Usage Summary (last 100 traces)
==================================================
Input tokens:           10,234
Output tokens:          85,421
Cache read tokens:   1,234,567
Total cost:        $    52.34

📈 By Model:
--------------------------------------------------
  claude-opus-4-5-20251101            $52.34

🔧 By Tool:
--------------------------------------------------
  Bash                   45 calls  $25.12
  Read                   30 calls  $15.34
  Write                  20 calls  $8.45
  Edit                   5 calls   $3.43
```

### Session Status

```bash
$ pisama-cc status

📊 Pisama Status
========================================

🔧 Hook Installation:
   ✅ pisama-capture.py
   ✅ pisama-post.sh
   ✅ pisama-forward.py
   ✅ pisama-forward.sh
   All hooks installed

📁 Local Traces: 1,400
   Input tokens:  9,580
   Output tokens: 79,569
   Total cost:    $43.22
```

### Export & Analysis

```bash
# Export to JSONL
pisama-cc export -o traces.jsonl

# Export compressed
pisama-cc export -o traces.jsonl.gz --compress

# Export to OpenTelemetry format
pisama-cc export --format otel -o traces-otel.json

# Filter by date range
pisama-cc traces --since 2025-01-01 --until 2025-01-04
```

### OpenTelemetry Integration

Export traces to any OTEL-compatible backend (Jaeger, Honeycomb, Datadog, etc.):

```bash
# Install OTEL support
pip install pisama-claude-code[otel]

# Export to local Jaeger
pisama-cc export-otel -e http://localhost:4318/v1/traces

# Export to Honeycomb
pisama-cc export-otel -e https://api.honeycomb.io/v1/traces \
    -H "x-honeycomb-team=YOUR_API_KEY"

# Export to file in OTEL format
pisama-cc export --format otel -o traces.json
```

OTEL export uses [GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/) for token usage, costs, and model attributes.

## CLI Reference

| Command | Description |
|---------|-------------|
| `pisama-cc install` | Install capture hooks to `~/.claude/hooks/` |
| `pisama-cc uninstall` | Remove hooks |
| `pisama-cc status` | Show status, token totals, and cost |
| `pisama-cc traces` | View recent traces (`-v` for verbose, `-c` for content) |
| `pisama-cc usage` | Token usage breakdown (`--by-model`, `--by-tool`) |
| `pisama-cc export` | Export to JSONL or OTEL (`--format otel`, `--compress`) |
| `pisama-cc export-otel` | Export to OpenTelemetry collector (`-e ENDPOINT`) |
| `pisama-cc connect` | Connect to Pisama platform (forwarding is opt-in; add `--auto-sync` to forward every session) |
| `pisama-cc sync` | Upload traces to platform |
| `pisama-cc analyze` | Run failure detection (requires platform) |
| `pisama-cc proxy serve` | Run the opt-in reasoning proxy for a session (experimental) |
| `pisama-cc proxy install --always-on` | Always-on reasoning capture (macOS launchd) |
| `pisama-cc proxy status` / `uninstall` | Proxy health / teardown |
| `pisama-cc vault status` | Show PII tokenization vault status (`[core]`) |

## Cost Estimates

Cost estimates use Anthropic's first-party API list prices per 1M tokens.
The current active model families are:

| Model family | Input | Output | Cache write | Cache read |
|--------------|-------|--------|-------------|------------|
| Claude Opus 4.5 to 4.8 | $5.00 | $25.00 | $6.25 | $0.50 |
| Claude Sonnet 5 | $2.00 | $10.00 | $2.50 | $0.20 |
| Claude Sonnet 4.5 to 4.6 | $3.00 | $15.00 | $3.75 | $0.30 |
| Claude Haiku 4.5 | $1.00 | $5.00 | $1.25 | $0.10 |

Sonnet 5 introductory pricing is applied through August 31, 2026, then
automatically changes to $3 input, $15 output, $3.75 cache write, and $0.30
cache read. Provider-specific discounts, batch pricing, long-context premiums,
and regional pricing are not included. Check
[Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing)
for the authoritative rates.

## Privacy & Security

- **Local-first**: all traces are stored in `~/.claude/pisama/traces/`.
- **Private on disk**: trace, proxy, config, sync-log, and lite-mode files use
  user-only permissions on POSIX systems. Configuration writes are atomic.
- **No hidden raw copy**: captured hook fields are stored once. Sensitive tool
  input is no longer duplicated in an untokenized `raw` payload.
- **Forwarding is opt-in**: `connect` defaults to no auto-sync. Nothing leaves
  your machine until you run `pisama-cc sync` or reconnect with `--auto-sync`.
- **Secrets scrubbed by default**: credential-shaped strings (API keys, JWTs,
  cloud tokens) are redacted from content before forwarding — no extras needed.
  The optional `[core]` extra adds pisama-core's reversible keychain vault.
- **Paths anonymized**: home-directory paths are replaced with `~`.
- **Reasoning proxy is experimental + opt-in**: `pisama-cc proxy` routes API
  traffic through a local logging proxy to recover extended-thinking. It defaults
  to API-key usage. **Subscription (OAuth) users**: it handles your account token,
  which is ToS-sensitive — use only knowingly.

See [SECURITY.md](SECURITY.md) for our security policy.

## Configuration

After installation, the hooks are automatically configured. To customize, edit `~/.claude/settings.local.json`:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "*",
        "hooks": [
          { "type": "command", "command": "~/.claude/hooks/pisama-post.sh", "timeout": 10, "async": true }
        ]
      }
    ],
    "Stop": [
      {
        "matcher": "*",
        "hooks": [
          { "type": "command", "command": "~/.claude/hooks/pisama-forward.sh", "timeout": 30, "async": true }
        ]
      }
    ]
  }
}
```

## Platform Integration (Optional)

For advanced features like failure detection and self-healing, connect to the Pisama platform:

```bash
pisama-cc connect --api-key <key>  # Authenticate
pisama-cc sync                     # Upload traces
pisama-cc analyze                  # Run detection
```

Platform features:
- 25 MAST failure mode detection
- AI-powered fix suggestions
- Self-healing automation
- Visual dashboard

## Part of the Pisama Platform

`pisama-claude-code` is the Claude Code integration for the broader **Pisama** multi-agent failure detection platform, which supports multiple agent frameworks:

| Framework | Package | Status |
|-----------|---------|--------|
| Claude Code | `pisama-claude-code` | Stable |
| LangChain/LangGraph | `pisama-core` SDK | Available |
| CrewAI | `pisama-core` SDK | Available |
| AutoGen | `pisama-core` SDK | Available |
| n8n | `pisama-core` SDK | Available |

For other frameworks, see the [Pisama integration docs](https://docs.pisama.ai/integrations).

## Contributing

We welcome contributions! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

```bash
# Development setup
git clone https://github.com/Pisama-AI/pisama-claude-code.git
cd pisama-claude-code
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Run tests
pytest --cov=pisama_claude_code --cov-fail-under=60

# Run the same quality gates as CI
ruff check src/ tests/
ruff format --check src/ tests/
mypy src/pisama_claude_code/
xenon --max-absolute D --max-modules C --max-average B src/pisama_claude_code
pylint --disable=all --enable=duplicate-code --min-similarity-lines=12 src/pisama_claude_code
```

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for release history.

## License

MIT License - see [LICENSE](LICENSE) for details.

## Links

- [Documentation](https://docs.pisama.ai/claude-code)
- [Pisama Platform](https://pisama.ai)
- [Issue Tracker](https://github.com/Pisama-AI/pisama-claude-code/issues)
- [Discussions](https://github.com/Pisama-AI/pisama-claude-code/discussions)

---

<p align="center">
  Made with ❤️ for the Claude Code community
</p>
