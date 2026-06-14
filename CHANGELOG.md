# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.0] - 2026-06-14

This release makes trace capture and forwarding actually work end to end (the
prior installs registered hooks under invalid event names and never fired), adds
full-fidelity reasoning capture, and tightens the privacy defaults.

### Added
- **Reasoning capture via an opt-in proxy** (`pisama-cc proxy`). A local logging
  reverse-proxy (`ANTHROPIC_BASE_URL`) reassembles the full turn — including
  extended-thinking *reasoning*, which Claude Code never writes to disk — plus the
  exact request payload and token usage, from the live API response stream.
  - `pisama-cc proxy serve` — opt-in, per session.
  - `pisama-cc proxy install --always-on` — always-on, auto-started (macOS launchd).
  - `pisama-cc proxy status` / `uninstall`.
  - ⚠️ Experimental. Defaults to API-key usage. **Subscription (OAuth) users:** the
    proxy handles your account token — ToS-sensitive; use knowingly.
- **Built-in secret scrubbing**, on by default with no extras: credential-shaped
  strings (API keys, JWTs, cloud tokens) are redacted from any content before it
  is forwarded. The optional `[core]` extra adds pisama-core's reversible vault.
- **Auto-forward on session end** (`Stop` hook), idempotent per session.
- **`/pisama-diagnose` skill** for Claude Code and Codex (first PyPI release).
  Turns an agent trace into a root-cause diagnosis via `/atif/analyze`; with a
  connected key it renders a ready-to-paste CLAUDE.md/.cursorrules guardrail and
  can auto-apply a fix to a live n8n workflow. Installed by `pisama-cc install`.

### Changed
- **Forwarding is now opt-in**: `connect` defaults to `--no-auto-sync`. Nothing
  leaves your machine until you run `pisama-cc sync` or reconnect with
  `--auto-sync`.
- Full-content capture: user prompt, reasoning, AI output, tool I/O, tokens, cost.

### Fixed
- **Hooks now actually fire.** Register `PostToolUse` (capture) + `Stop` (forward)
  with the correct nested settings shape and second-based timeouts (were the
  invalid `PreToolCall`/`PostToolCall` events with ms timeouts). Installs from the
  broken layout are migrated automatically.
- **Capture records real content.** Read the tool result from
  `tool_result`/`tool_response`, capture the user prompt and AI output reliably
  across the whole turn (the prior parser missed both).
- **Forwarding authenticates correctly.** Exchange the API key for a JWT and post
  to `/api/v1/...` (was sending the raw key to a `/v1/...` path → 401/404).

## [0.4.3] - 2026-04-19

### Fixed
- Normalize "PISAMA" → "Pisama" in user-facing install/verify/uninstall output (environment variable names remain uppercase).
- Update project metadata: description, maintainer email, and homepage URLs now use the canonical "Pisama" brand and `pisama.ai` domain.

## [0.4.0] - 2026-01-05

### Added
- **OpenTelemetry export** - Export traces to any OTEL-compatible backend
  - `export-otel` command for direct export to OTEL collectors (Jaeger, Honeycomb, Datadog)
  - `export --format otel` for OTEL JSON file export
  - GenAI semantic conventions for token usage and model attributes
  - Optional `[otel]` dependency: `pip install pisama-claude-code[otel]`

### Changed
- Updated description to reflect role in broader Pisama platform

## [0.3.5] - 2025-01-05

### Fixed
- Correctly extract user input from transcript (skip tool_result blocks)
- Collect reasoning/output from multiple assistant messages (Claude Code splits them)
- Look back 200 lines to find user prompts

## [0.3.4] - 2025-01-05

### Fixed
- Version string in CLI now matches package version

## [0.3.3] - 2025-01-04

### Added
- Full content capture for input, reasoning (extended thinking), and output
- PII tokenization for captured content (via pisama-core)
- `--content` flag to show input/reasoning/output in `traces` command
- `--reasoning` flag to show reasoning (thinking) content only
- Export and sync now include user_input, reasoning, ai_output fields

### Changed
- Improved failure detection accuracy with full context capture
- Database schema updated with new content columns

## [0.3.2] - 2025-01-04

### Added
- Token usage tracking with input, output, and cache read tokens
- Cost calculation per trace and session totals
- Model-aware pricing (Opus 4.5, Sonnet 4, Haiku 3.5)
- `pisama-cc usage` command with `--by-model` and `--by-tool` grouping
- Export to gzip format with `--compress` flag

### Changed
- Improved status output with token and cost summaries
- Better formatting for large numbers in CLI output

### Fixed
- Cache token counting for long sessions

## [0.3.1] - 2025-01-03

### Added
- Verbose trace output with `-v` flag
- Filter traces by tool type

### Fixed
- SQLite connection handling in async contexts

## [0.3.0] - 2025-01-02

### Added
- SQLite storage backend for local traces
- JSONL export format
- `pisama-cc export` command
- Automatic secret redaction (API keys, passwords, tokens)
- File path anonymization (home directory replacement)

### Changed
- Migrated from file-based to SQLite storage
- Improved hook installation reliability

## [0.2.0] - 2024-12-30

### Added
- Platform sync functionality (`pisama-cc sync`)
- `pisama-cc connect` for platform authentication
- `pisama-cc analyze` for remote failure detection

### Changed
- Restructured CLI with subcommands

## [0.1.0] - 2024-12-28

### Added
- Initial release
- Hook-based trace capture for Claude Code
- `pisama-cc install` and `pisama-cc uninstall` commands
- `pisama-cc status` command
- `pisama-cc traces` command for viewing recent traces
- Support for Bash, Read, Write, Edit, Grep, Glob tools
- Local storage in `~/.claude/pisama/traces/`

[Unreleased]: https://github.com/tn-pisama/pisama-claude-code/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/tn-pisama/pisama-claude-code/compare/v0.4.3...v0.5.0
[0.4.3]: https://github.com/tn-pisama/pisama-claude-code/compare/v0.4.0...v0.4.3
[0.4.0]: https://github.com/tn-pisama/pisama-claude-code/compare/v0.3.5...v0.4.0
[0.3.5]: https://github.com/tn-pisama/pisama-claude-code/compare/v0.3.4...v0.3.5
[0.3.4]: https://github.com/tn-pisama/pisama-claude-code/compare/v0.3.3...v0.3.4
[0.3.3]: https://github.com/tn-pisama/pisama-claude-code/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/tn-pisama/pisama-claude-code/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/tn-pisama/pisama-claude-code/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/tn-pisama/pisama-claude-code/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/tn-pisama/pisama-claude-code/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/tn-pisama/pisama-claude-code/releases/tag/v0.1.0
