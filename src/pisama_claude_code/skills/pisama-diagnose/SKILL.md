---
name: pisama-diagnose
description: |
  Diagnose a failing AI agent run with Pisama. Turns a trace into a root-cause
  report: what failed, which agent failed at which step, every detection with a
  calibrated confidence, and an apply-ready fix. With a connected key it returns
  a ready-to-paste CLAUDE.md/.cursorrules guardrail plus the commands to verify
  it, and can auto-apply the fix to a live n8n workflow.
  Use when an agent trajectory, trace, eval, or run looks wrong and you want a
  root-cause diagnosis without leaving your editor. Accepts an ATIF/trajectory
  JSON file, a pasted trace, or a Pisama detection id.
allowed-tools: Bash, Read, Edit, Write
---

# Pisama Diagnose

You are diagnosing a failing AI agent run using the live Pisama backend. Pisama
runs failure detectors over the trace, localizes the primary failure, explains
the root cause, and returns a fix. With a connected key it also returns an
apply-ready CLAUDE.md/.cursorrules guardrail and (for n8n) can apply the fix
directly. Your job is to run the bundled engine, read the report back to the
user clearly, and offer to apply the guardrail.

The engine is a self-contained, standard-library Python script next to this file:
`diagnose.py`. It needs Python 3.8+ and network access to `api.pisama.ai`. The
diagnosis call is public, so **no API key is required to diagnose**. A key
unlocks the apply-ready patch and stored-detection lookups.

## When to use

- The user has an agent trace, trajectory, or eval that failed or looks wrong.
- The user asks "why did my agent fail / loop / give up / hallucinate here?"
- The user wants a root-cause report or a fix they can apply.

## Inputs the engine accepts

1. An **ATIF / trajectory JSON file** (the common case). ATIF is Pisama's trace
   interchange format; exporters exist for LangGraph, n8n, Dify, OpenClaw, and
   raw OTEL. If the user has a different trace shape, ask them to export ATIF or
   point you at the trajectory JSON.
2. A **pasted trace** on stdin (`-`).
3. A **Pisama detection id** (`--detection-id`), which fetches a stored
   detection. This path needs a connected key (`pisama-cc connect`).

## Flow

1. Find the script. It sits beside this SKILL.md. Resolve its path, for example:
   ```bash
   SKILL_DIR="$(dirname "$(find . ~/.claude/skills ~/.codex/skills -name diagnose.py -path '*pisama-diagnose*' 2>/dev/null | head -1)")"
   ```
   If the user installed via `pisama-cc install`, it is at
   `~/.claude/skills/pisama-diagnose/diagnose.py`.

2. Run the diagnosis and write a report file the user can keep:
   ```bash
   python3 "$SKILL_DIR/diagnose.py" path/to/trace.json --out diagnosis.md
   ```
   To diagnose a pasted trace: `pbpaste | python3 "$SKILL_DIR/diagnose.py" - --out diagnosis.md`

3. Read `diagnosis.md` and summarize for the user: the verdict, the root cause,
   which agent failed at which step, and the suggested fix. Then open the file.

4. If the report has an **"Apply this fix"** section, offer to apply it (see
   "Applying the guardrail"). Offer the n8n auto-apply step only when it fits
   (see "Applying a fix to n8n").

## Interpreting the report (read these caveats out loud)

- **Localization is best-effort.** Pisama reports which agent failed at which
  step at detection time, but step/agent localization is often unset and is not
  highly accurate. Always confirm the call against the cited evidence in the
  report. When the report says "not localized to a single span," lean on the
  evidence block and the affected span id, not a guessed step number.
- **Confidences are external-corpus calibrated.** Detector confidence and the
  per-detector F1 quoted at docs.pisama.ai are measured on an external trace
  corpus, not on the user's own data. Treat them as priors, not guarantees.
- **A clean verdict is not a proof of correctness.** "No failures detected" means
  no detector fired, not that the run was perfect.

## Applying the guardrail (CLAUDE.md / .cursorrules)

When a key is connected, the report includes an **"Apply this fix"** section: a
fenced ` ````markdown ` block (a heading, the rule, and a code snippet) plus a
verification block. This is a durable instruction for the user's coding agent.

- Offer to add the block to the user's `CLAUDE.md` or `.cursorrules`. With the
  user's go-ahead, append it verbatim (it carries a `<!-- Pisama fix ... -->`
  marker so re-runs are easy to spot). Use Edit on an existing file, or Write a
  new one.
- Then show the **Verify the fix** commands from the report and offer to run
  them. Do not claim the fix works until verification passes (the bar is the
  same one Pisama's inline verification uses: the detector clears, or its
  confidence drops by at least 0.30).
- If there is no "Apply this fix" section, the user has not connected a key, or
  the backend returned no structured fix. Connect with
  `pisama-cc connect --api-key <key>` to unlock it, or use the "Suggested fix"
  text as a manual starting point.

## Applying a fix to n8n

Real-trace auto-apply is verified for **n8n only**. The apply step generates the
primary fix and applies it to a live n8n workflow through Pisama's
AutoApplyService, with a git-style backup so it can roll back.

```bash
python3 "$SKILL_DIR/diagnose.py" path/to/trace.json --apply \
  --entity-id <n8n_workflow_id> \
  --n8n-url https://your-n8n.example.com --n8n-key <n8n_api_key>
```

Rules:
- Only offer `--apply` for n8n. For other frameworks, present the suggested fix
  and the CLAUDE.md/.cursorrules guardrail, and let the user apply it. Do not
  imply auto-apply works elsewhere.
- Never invent an `--entity-id` or credentials. Ask the user for the n8n
  workflow id and API key, or skip the apply step.
- The report's "Auto-apply result" section states the ground truth (applied, or
  why not). Read it back verbatim. If it says no fix generator matched the
  failure type, that is honest output, not an error.

## Connecting a key (unlocks the patch, stored data, and auto-apply)

```bash
pip install pisama-claude-code   # ships the pisama-cc CLI and this skill
pisama-cc connect --api-key <your-pisama-key>
```

The apply-ready patch, `--detection-id`, and any tenant-scoped lookup read the
key and tenant from `~/.claude/pisama/config.json`. Diagnosis from a trace file
needs none of this.

## Notes for the agent

- Prefer `--out diagnosis.md` so the user keeps an artifact, then open it.
- Pass `--api-url http://localhost:8000` if the user runs Pisama locally.
- If the trace is not ATIF and cannot be parsed, the engine surfaces the parser
  error. Relay it and ask for an ATIF export rather than guessing a conversion.
