# pisama-diagnose (Codex)

Diagnose a failing AI agent run with Pisama and return an apply-ready fix.
Invoke by mentioning `$pisama-diagnose` when the user has an agent trace,
trajectory, eval, or run that failed or looks wrong.

The engine is a self-contained, standard-library Python script next to this
file: `diagnose.py`. It calls the live Pisama backend at `api.pisama.ai`. The
diagnosis call is public (no key). A key unlocks the apply-ready
CLAUDE.md/.cursorrules patch, stored-detection lookups, and n8n auto-apply.

## Run it

1. Resolve the script path (it sits beside this file):
   ```bash
   SKILL_DIR="$(dirname "$(find . ~/.codex/skills ~/.claude/skills -name diagnose.py -path '*pisama-diagnose*' 2>/dev/null | head -1)")"
   ```
2. Diagnose a trace and keep a report:
   ```bash
   python3 "$SKILL_DIR/diagnose.py" path/to/trace.json --out diagnosis.md
   ```
   - Pasted trace: `pbpaste | python3 "$SKILL_DIR/diagnose.py" - --out diagnosis.md`
   - Stored detection (needs a key): `python3 "$SKILL_DIR/diagnose.py" --detection-id <uuid>`
   - Local Pisama: add `--api-url http://localhost:8000`.
3. Read `diagnosis.md` back: verdict, root cause, which agent failed at which
   step, the suggested fix.

## Apply the fix

- If the report has an **"Apply this fix"** section, offer to append the fenced
  ` ````markdown ` block verbatim to the user's `CLAUDE.md` or `.cursorrules`
  (it carries a `<!-- Pisama fix ... -->` marker). Then run the **Verify the
  fix** commands; do not claim success until they pass.
- n8n auto-apply (n8n only): `python3 "$SKILL_DIR/diagnose.py" TRACE.json --apply
  --entity-id <workflow_id> --n8n-url <url> --n8n-key <key>`. Never invent an
  entity id or credentials; ask the user.

## Honesty (relay these)

- Step/agent localization is best-effort and often unset; confirm against the
  cited evidence, not a guessed step.
- Confidences are calibrated on an external corpus (per-detector F1 at
  docs.pisama.ai), not on the user's data.
- "No failures detected" means no detector fired, not that the run was perfect.
- Real-trace auto-apply is verified for n8n only; elsewhere the fix is a
  suggestion the user applies by hand.

Connect a key to unlock the patch and stored data:
`pip install pisama-claude-code && pisama-cc connect --api-key <key>`.
