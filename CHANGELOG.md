# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

- Make type checking blocking and fix all reported source errors.
- Add Python 3.13 and a non-regression coverage floor to CI.
- Tighten the optional `pisama-core` compatibility range.
- Add CodeQL and pull request dependency review.

## [0.6.4] - 2026-07-23

### Changed
- Updated cost estimation for the active Claude Opus 4, Sonnet 5, Sonnet 4,
  and Haiku 4 model families.
- Accounted for the distinct 5-minute cache-write rate instead of charging
  cache creation at the base input rate.
- Added automatic handling for the temporary Sonnet 5 introductory price,
  which ends September 1, 2026.

### Fixed
- Removed the aiohttp application-key warning from the optional reasoning
  proxy.
- Audited the vault and tokenize commands. The vault surface checked out
  almost entirely (every pisama-core class, method, stats key, the
  `[TYPE:sess:random]` format hint, the `pisama-core[tokenization]` extra,
  and the shared `~/.claude/pisama/vault.db` path are all real, verified
  against pisama-core 1.7.3), with one exception: both `vault status` and
  `tokenize config` displayed a `tokenization` block from config.json that
  nothing writes and nothing reads. The capture hook's real switch is the
  `PISAMA_TOKENIZATION` env var, fail-open is hardcoded, and the displayed
  `custom_patterns`/`exclusions` had zero consumers, so the commands could
  report "disabled" while every session kept tokenizing (and vice versa).
  Both now report the effective configuration: the env-var switch, the
  hardcoded fail-open, the vault path, the tokenized fields, and the live
  detection-pattern list.
- Audited the non-lite commands for hints pointing at things that do not
  exist:
  - `status` could never report a healthy install: its settings check looked
    for the stale `PreToolCall`/`PostToolCall` event names that the fixed
    installer explicitly strips (the real events are `PostToolUse` + `Stop`),
    and its file check demanded the legacy `pisama-pre.sh` while missing the
    `pisama-forward.py`/`pisama-forward.sh` pair the installer actually
    writes. Every healthy install was told "hooks not in settings" and
    prompted to reinstall in a loop. Both checks now mirror what `install`
    writes (and what `verify` checks).
  - `sync` printed "View at:" with an `app.pisama.ai` URL, and `analyze`
    pointed at `https://app.pisama.ai/settings/api` for API keys. The
    `app.pisama.ai` host does not resolve at all; the dashboard lives at
    `pisama.ai` and the API-keys page is `/settings/api-keys`. All three
    sites (including the analyze fallback dashboard URL) now point at real
    pages (`pisama.ai/traces`, `pisama.ai/settings/api-keys`,
    `pisama.ai/dashboard`).
  - The "Star us on GitHub" URL, README badges/links, and `pyproject.toml`
    project URLs still pointed at the pre-transfer `tn-pisama` org; now
    `Pisama-AI`. The README's framework table referenced a `mao-testing` SDK
    whose repo link 404s (pre-rebrand name); it now names the real
    `pisama-core` SDK and links the live integration docs. The pyproject
    Documentation URL now goes straight to `docs.pisama.ai/claude-code`.
- Audited every lite command for advertised behavior that did not exist:
  - `lite analyze --session-id` was accepted but silently ignored (the session
    id was always the trace file's stem). It is now passed through to the
    runner.
  - `lite init --severity-threshold` was saved to config and printed back, but
    nothing ever read it. Positive detections below the threshold are now
    dropped (not reported, not stored). No behavior change at the default of
    40: every lite detector's minimum positive severity is 48 or higher.
  - `lite dashboard --session` claimed to filter by session id but was never
    wired to anything; the dashboard is always global. The option is removed
    rather than half-implemented (aggregate stats have no per-session query),
    so passing it now errors instead of silently doing nothing.
  - The `lite` group help still advertised `export` as "Export results for
    platform import"; it now matches the corrected command ("Export results
    to a local JSON file").
- `lite export` no longer prints a curl example against `/v1/import/lite`,
  an endpoint that does not exist on the platform (and whose nearest real
  route, `POST /api/v1/import-jobs`, ingests raw trace files under JWT auth,
  not pre-computed lite detections). The post-export hint now points at the
  path that actually works: `pisama-cc connect` + `pisama-cc sync`, which
  uploads the captured sessions so the platform re-runs full detection. The
  misleading `lite init (set platform_url in config)` fallback is gone too;
  `lite init` has no such option.

## [0.6.3] - 2026-07-21

### Added
- Forward agent identity. Claude Code hook payloads carry `agent_id` and
  `agent_type` for tool calls made inside subagents (Task/workflow fan-outs);
  the capture hook now records them (plus a derived `is_sidechain`) and both
  forwarding paths carry them: the batch sync payload as top-level fields, and
  the real-time OTLP span as `gen_ai.agent.id` / `gen_ai.agent.name` plus the
  `gen_ai.state` channel. Without this the backend flattened parallel
  subagents into one sequential stream (false "Repeated Tool Calls" loops) and
  classified multi-agent sessions as single-agent chat, so the multi-agent
  detector family never ran on the Claude Code channel.

### Fixed
- `analyze` and the `fix list/show/apply` commands now request `/api/v1/...`
  paths via the shared `api_url()` helper. They previously hard-coded bare
  `/v1/...` paths, which the platform never serves (every router is mounted
  under `/api/v1`), so these commands always failed with 404 against
  production. The sync/auth/ingest wire layer was already correct.

## [0.6.2] - 2026-06-15

### Changed
- Per-field forward cap raised from 100 KB to **1 MB** (default), and made
  configurable via `PISAMA_CC_MAX_FIELD_CHARS`. Keeps large tool outputs intact
  while staying safely under the platform's 10 MB request-body limit; secrets are
  still scrubbed before any capping.

## [0.6.1] - 2026-06-14

### Fixed
- `emit_span()` now posts to the tenant-scoped ingest
  (`/api/v1/tenants/{tenant_id}/traces/ingest`, tenant from the JWT payload)
  instead of the bare `/api/v1/traces/ingest`. The "keyless" alias still requires
  a `?tenant_id=` query param server-side, so real-time forwarding 422'd against
  the live platform (the mocked unit tests didn't catch it; an end-to-end run
  against prod did). Verified live: ingest 202 + a `trace.span` event delivered
  over the `/traces/live` SSE stream.

## [0.6.0] - 2026-06-14

Real-time trace collection. Forwarding now streams one small appended span per
event instead of re-sending the whole session as a single large transaction —
the change that fixes long-session ingest stalls on the platform.

### Changed
- **Real-time forwarding (transport change).** When `auto_sync` is on, the
  PostToolUse hook now emits each tool call as a single appended span to the
  platform's idempotent `/traces/ingest` endpoint, and the reasoning proxy emits
  one span per API call (reasoning included). Previously both re-sent the entire
  session to the delete-and-replace `claude-code` batch endpoint, which became a
  single giant transaction on long sessions. The `Stop` hook is now a *reconcile*
  pass that re-emits only events a per-tool emit missed (tracked in the sync log),
  not a full batch upload. `pisama-cc sync` still uses the batch endpoint for
  explicit offline bulk uploads.
- `emit_span()` carries full content — including extended-thinking *reasoning* —
  in the `gen_ai.state` span attribute, which the platform stores verbatim;
  content is secret-scrubbed and size-capped before it leaves the machine.

### Notes
- Real-time platform-side *processing* (live partial-trace detection + a live
  dashboard SSE stream) is wired but ships off by default; a platform operator
  enables it per tenant via feature flags.

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
