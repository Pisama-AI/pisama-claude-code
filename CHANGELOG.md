# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.3] - 2026-06-02

### Added
- **`/pisama-diagnose` skill** for Claude Code and Codex. Turns an agent trace
  into a root-cause diagnosis via the public `/atif/analyze` endpoint. With a
  connected key it also renders a ready-to-paste CLAUDE.md/.cursorrules guardrail
  plus a verification command block (from `/healing/trigger/sync`), and can
  auto-apply the fix to a live n8n workflow via `--apply`. Installed to
  `~/.claude/skills` and `~/.codex/skills` by `pisama-cc install`.

### Fixed
- Point the `connect` default API URL and dashboard links at `api.pisama.ai`
  and `app.pisama.ai` (were the pre-rebrand `maotesting.com`).

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

[Unreleased]: https://github.com/tn-pisama/pisama-claude-code/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/tn-pisama/pisama-claude-code/compare/v0.3.5...v0.4.0
[0.3.5]: https://github.com/tn-pisama/pisama-claude-code/compare/v0.3.4...v0.3.5
[0.3.4]: https://github.com/tn-pisama/pisama-claude-code/compare/v0.3.3...v0.3.4
[0.3.3]: https://github.com/tn-pisama/pisama-claude-code/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/tn-pisama/pisama-claude-code/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/tn-pisama/pisama-claude-code/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/tn-pisama/pisama-claude-code/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/tn-pisama/pisama-claude-code/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/tn-pisama/pisama-claude-code/releases/tag/v0.1.0
