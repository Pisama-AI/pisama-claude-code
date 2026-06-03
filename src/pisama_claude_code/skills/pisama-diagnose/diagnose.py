#!/usr/bin/env python3
"""pisama-diagnose: turn an agent trace into a root-cause diagnosis.

Self-contained (standard library only). Talks to the live Pisama backend.

  Diagnose (public, no auth):
    POST /api/v1/atif/analyze
      Runs every applicable Pisama detector on an ATIF trajectory and
      returns the DiagnosisResult: root cause, the primary failure, which
      agent failed at which step, every detection, and the server's
      suggested fix for each one.

  Apply-ready patch (needs a key):
    POST /api/v1/healing/trigger/sync
      For the primary failure, returns a FixSuggestion whose `ide_patch`
      is a ready-to-paste CLAUDE.md/.cursorrules instruction block plus a
      verification command block. Rendered into the report under "Apply
      this fix" so your coding agent can drop it straight into the repo.

  Auto-apply (n8n-scoped):
    POST /api/v1/atif/analyze  with  apply_fix=true
      Generates the primary fix and applies it to a live framework entity
      through the unified AutoApplyService. Real-trace apply currently
      lands on n8n; pass --entity-id plus n8n credentials.

  Stored detection (needs a key):
    GET /api/v1/tenants/{tenant}/detections/{id}

Honesty guardrails are baked into the rendered report:
  * Step/agent localization is best-effort and is often unset. We always
    show the cited evidence so the developer can confirm.
  * Detector confidences are calibrated on an external trace corpus.
  * Live auto-apply lands on n8n only (the framework with verified
    real-trace apply). Other frameworks get the fix as a suggestion to
    apply by hand.

Usage:
  python3 diagnose.py TRACE.json                       # diagnose a trace
  cat trace.json | python3 diagnose.py -               # diagnose from stdin
  python3 diagnose.py TRACE.json --out diagnosis.md    # write a report file
  python3 diagnose.py TRACE.json --apply --entity-id WORKFLOW_ID \
      --n8n-url https://n8n.example.com --n8n-key KEY   # apply to n8n
  python3 diagnose.py --detection-id <uuid>            # diagnose a stored detection
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_API_URL = "https://api.pisama.ai"
# Reuse the pisama-claude-code config written by `pisama-cc connect`.
PISAMA_CONFIG = Path.home() / ".claude" / "pisama" / "config.json"
# Real-trace auto-apply is verified for this framework only.
REAL_APPLY_FRAMEWORKS = ("n8n",)


# ---------------------------------------------------------------------------
# Config + HTTP (stdlib only)
# ---------------------------------------------------------------------------

def load_config() -> Dict[str, Any]:
    """Read api_url / api_key / tenant_id from the pisama-cc config or env."""
    cfg: Dict[str, Any] = {}
    if PISAMA_CONFIG.exists():
        try:
            cfg = json.loads(PISAMA_CONFIG.read_text())
        except (json.JSONDecodeError, OSError):
            cfg = {}
    cfg.setdefault("api_url", os.getenv("PISAMA_API_URL", DEFAULT_API_URL))
    if os.getenv("PISAMA_API_KEY"):
        cfg["api_key"] = os.getenv("PISAMA_API_KEY")
    if os.getenv("PISAMA_TENANT_ID"):
        cfg["tenant_id"] = os.getenv("PISAMA_TENANT_ID")
    return cfg


def _request(method: str, url: str, body: Optional[dict], token: Optional[str],
             timeout: float) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:600]
        raise SystemExit(f"Pisama API {method} {url} failed: HTTP {e.code}\n{detail}")
    except urllib.error.URLError as e:
        raise SystemExit(f"Could not reach Pisama API at {url}: {e.reason}")


def post(url: str, body: dict, token: Optional[str] = None, timeout: float = 120) -> dict:
    return _request("POST", url, body, token, timeout)


def get(url: str, token: Optional[str] = None, timeout: float = 60) -> dict:
    return _request("GET", url, None, token, timeout)


# ---------------------------------------------------------------------------
# Trace loading
# ---------------------------------------------------------------------------

def load_trajectory(src: str) -> dict:
    """Load an ATIF trajectory from a file path or '-' (stdin).

    Accepts either a bare trajectory object or a request envelope that
    already wraps it under a ``trajectory`` key.
    """
    raw = sys.stdin.read() if src == "-" else Path(src).read_text()
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SystemExit(f"Trace is not valid JSON: {e}")
    if isinstance(obj, dict) and "trajectory" in obj and "schema_version" not in obj:
        obj = obj["trajectory"]
    if not isinstance(obj, dict):
        raise SystemExit("Trace must be a JSON object (an ATIF trajectory).")
    return obj


# ---------------------------------------------------------------------------
# Backend calls
# ---------------------------------------------------------------------------

def analyze_trajectory(trajectory: dict, api_url: str, *, apply_fix: bool = False,
                       framework: Optional[str] = None, entity_id: Optional[str] = None,
                       credentials: Optional[dict] = None) -> dict:
    body: Dict[str, Any] = {"trajectory": trajectory, "apply_fix": apply_fix}
    if framework:
        body["framework"] = framework
    if entity_id:
        body["entity_id"] = entity_id
    if credentials:
        body["credentials"] = credentials
    return post(f"{api_url.rstrip('/')}/api/v1/atif/analyze", body)


def fetch_detection(detection_id: str, api_url: str, tenant_id: str, token: str) -> dict:
    url = f"{api_url.rstrip('/')}/api/v1/tenants/{tenant_id}/detections/{detection_id}"
    return get(url, token=token)


def fetch_ide_patch(primary: Dict[str, Any], api_url: str, token: str) -> Optional[Dict[str, Any]]:
    """Ask the healing engine for an apply-ready fix for the primary failure.

    Calls the side-effect-free ``/healing/trigger/sync`` endpoint (which
    needs a key) and returns the FixSuggestion's ``ide_patch`` — a
    ready-to-paste CLAUDE.md/.cursorrules instruction block plus a
    verification command block. Best-effort: any failure returns None so
    the diagnosis still renders. Older backends that predate ide_patch
    return a fix without it; we return None in that case too.
    """
    category = primary.get("category")
    if not category:
        return None
    details: Dict[str, Any] = {
        "confidence": primary.get("confidence"),
        "severity": primary.get("severity"),
        "evidence": primary.get("evidence") or [],
        "affected_spans": primary.get("affected_spans") or [],
        "mistake_step": primary.get("mistake_step"),
        "mistake_agent": primary.get("mistake_agent"),
        "description": primary.get("description"),
    }
    body = {"detection_type": category, "details": details, "method": "atif"}
    url = f"{api_url.rstrip('/')}/api/v1/healing/trigger/sync"
    try:
        resp = post(url, body, token=token, timeout=60)
    except SystemExit:
        # _request raises SystemExit on HTTP/URL errors; downgrade to a
        # soft miss so a 401/404/older-backend never sinks the diagnosis.
        return None
    fix = (resp or {}).get("fix") or {}
    patch = fix.get("ide_patch")
    return patch if isinstance(patch, dict) and patch.get("instructions") else None


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def _conf_pct(value: Any) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{f * 100:.0f}%" if f <= 1.0 else f"{f:.0f}%"


def _localized(det: Dict[str, Any]) -> str:
    """Where the failure happened, honestly degraded when not localized."""
    agent = det.get("mistake_agent")
    step = det.get("mistake_step")
    spans = det.get("affected_spans") or []
    if agent and step is not None:
        return f"agent `{agent}`, step {step}"
    if agent:
        return f"agent `{agent}`"
    if step is not None:
        return f"step {step}"
    if spans:
        return f"span `{spans[0]}`" + (f" (+{len(spans) - 1} more)" if len(spans) > 1 else "")
    return "not localized to a single span"


def render_report(diagnosis: Dict[str, Any], trace: Dict[str, Any],
                  healing: Optional[Dict[str, Any]] = None,
                  ide_patch: Optional[Dict[str, Any]] = None) -> str:
    d = diagnosis
    detections: List[Dict[str, Any]] = d.get("all_detections") or []
    primary = d.get("primary_failure")
    failure_count = d.get("failure_count", 0)

    out: List[str] = ["# Pisama diagnosis", ""]
    meta = [
        f"trace `{trace.get('trace_id', d.get('trace_id', 'unknown'))}`",
        f"{trace.get('span_count', d.get('total_spans', '?'))} spans",
        f"{trace.get('total_tokens', d.get('total_tokens', '?'))} tokens",
    ]
    if trace.get("source_format"):
        meta.insert(1, str(trace["source_format"]))
    if trace.get("atif_schema_version"):
        meta.append(f"schema {trace['atif_schema_version']}")
    out.append(", ".join(meta))
    if d.get("analyzed_at"):
        out.append(f"analyzed {d['analyzed_at']}")
    out.append("")

    # Verdict
    if d.get("has_failures") and primary:
        out.append(f"**Verdict: {failure_count} failure(s) detected.**")
    elif d.get("abstained"):
        out.append("**Verdict: abstained.** The detectors were not confident enough "
                   "to call this trace.")
    else:
        out.append("**Verdict: no failures detected.**")
    run = len(d.get("detectors_run") or [])
    if run:
        out.append(f"\n_{run} detector(s) ran on this trace._")
    out.append("")

    # Root cause
    if d.get("root_cause_explanation"):
        out += ["## Root cause", "", str(d["root_cause_explanation"]).strip(), ""]

    # Primary failure
    if primary:
        out += ["## Primary failure", "",
                f"**{primary.get('title', primary.get('category', 'Failure'))}**", ""]
        out += [f"- {b}" for b in [
            f"Type: `{primary.get('category', 'unknown')}`",
            f"Severity: {primary.get('severity', 'unknown')}",
            f"Confidence: {_conf_pct(primary.get('confidence'))}"
            + (f" ({primary['confidence_tier']})" if primary.get("confidence_tier") else ""),
            f"Where it went wrong: {_localized(primary)}",
        ]]
        out += ["",
                "> Step and agent localization is best-effort and is often unset. "
                "Confirm against the evidence below before acting.", ""]
        evidence = primary.get("evidence") or []
        if evidence:
            ev_json = json.dumps(evidence[:4], indent=2)[:1800]
            out += ["Evidence:", "", "```json", ev_json, "```", ""]

    # All detections table
    if detections:
        out += [f"## All detections ({len(detections)})", "",
                "| Detector | Confidence | Severity | Where | Title |",
                "|---|---|---|---|---|"]
        for det in detections:
            out.append(
                f"| `{det.get('category', '?')}` "
                f"| {_conf_pct(det.get('confidence'))} "
                f"| {det.get('severity', '?')} "
                f"| {_localized(det)} "
                f"| {str(det.get('title', '')).replace('|', '/')[:60]} |"
            )
        out.append("")

    # Suggested fix (rendered from the diagnosis itself; no extra call)
    out += _render_fix_guidance(d, primary)

    # Apply-ready IDE patch (from /healing/trigger/sync, when a key is set)
    out += _render_ide_patch(ide_patch)

    # Auto-apply result (--apply)
    if healing is not None:
        out += _render_apply(healing)

    # Trajectory quality
    tscore, cscore = d.get("trajectory_score"), d.get("task_completion_score")
    if tscore is not None or cscore is not None:
        out += ["## Trajectory quality", ""]
        if tscore is not None:
            out.append(f"- Trajectory score: {tscore}")
        if cscore is not None:
            out.append(f"- Task completion score: {cscore}")
        out.append("")

    # Honest caveats
    out += ["## Notes", "",
            "- Detector confidences are calibrated on an external trace corpus. "
            "Per-detector F1 and coverage are published at docs.pisama.ai.",
            "- Live auto-apply lands on n8n workflows, the framework with verified "
            "real-trace apply. Other frameworks return the fix as a suggestion you "
            "apply by hand.",
            "", "_Generated by the pisama-diagnose skill._", ""]
    return "\n".join(out)


def _render_fix_guidance(d: Dict[str, Any], primary: Optional[Dict[str, Any]]) -> List[str]:
    """Build the suggested-fix section from the diagnosis payload."""
    preview = d.get("auto_fix_preview") or {}
    suggested = (primary or {}).get("suggested_fix") or preview.get("action")
    if not suggested and not preview:
        return []
    out = ["## Suggested fix", ""]
    if suggested:
        out.append(suggested)
        out.append("")
    if d.get("self_healing_available"):
        out.append("Pisama may be able to auto-apply this fix for n8n workflows. "
                   "Re-run with `--apply --entity-id <workflow_id>` plus your n8n credentials. "
                   "The apply step reports whether a fix generator matched this failure type.")
        out.append("")
    elif primary:
        out.append("No automated self-healing fix is registered for this failure type. "
                   "Use the suggestion above as a manual starting point.")
        out.append("")
    return out


def _render_ide_patch(patch: Optional[Dict[str, Any]]) -> List[str]:
    """Render the apply-ready CLAUDE.md/.cursorrules block + verification.

    `patch` is a FixSuggestion.ide_patch: {instructions, verification,
    target_files, apply_mode, framework}. The instruction block is fenced
    with four backticks so its own ```python snippet stays intact and your
    coding agent can lift the block verbatim into CLAUDE.md/.cursorrules.
    """
    if not patch:
        return []
    instructions = str(patch.get("instructions") or "").strip()
    verification = str(patch.get("verification") or "").strip()
    if not instructions and not verification:
        return []
    targets = patch.get("target_files") or ["CLAUDE.md", ".cursorrules"]
    out = ["## Apply this fix", "",
           "Drop this guardrail into your `" + "` or `".join(targets) + "` so your "
           "coding agent applies it on the next run:", ""]
    if instructions:
        out += ["````markdown", instructions, "````", ""]
    if verification:
        out += ["### Verify the fix", "", "```sh", verification, "```", ""]
    if patch.get("apply_mode") == "connector":
        out += ["_Pisama can also apply this directly to your n8n workflow; "
                "re-run with `--apply --entity-id <workflow_id>`._", ""]
    return out


def _render_apply(healing: Dict[str, Any]) -> List[str]:
    out = ["## Auto-apply result", ""]
    if healing.get("success"):
        out.append(f"Applied fix `{healing.get('fix_type', 'fix')}` to the target entity.")
        for key in ("healing_id", "fix_id", "backup_commit_sha", "applied_at", "successor_entity"):
            if healing.get(key):
                out.append(f"- {key}: {healing[key]}")
        if healing.get("rolled_back"):
            out.append("- rolled_back: true")
    else:
        out.append(f"No fix applied. {healing.get('error', 'See the diagnosis above.')}")
    out.append("")
    return out


def render_from_detection(det: Dict[str, Any]) -> str:
    """Render a report from a stored DetectionResponse (the --detection-id path)."""
    out = ["# Pisama diagnosis", "",
           f"detection `{det.get('id', 'unknown')}`, trace `{det.get('trace_id', 'unknown')}`", "",
           f"**Verdict: {det.get('detection_type', 'failure')} detected** "
           f"(confidence {det.get('confidence', '?')}%"
           + (f", {det['confidence_tier']}" if det.get("confidence_tier") else "") + ").", ""]
    if det.get("explanation"):
        out += ["## Root cause", "", str(det["explanation"]).strip(), ""]
    details = det.get("details") or {}
    if details.get("description"):
        out += ["## Detail", "", str(details["description"]).strip(), ""]
    if det.get("business_impact"):
        out += [f"Business impact: {det['business_impact']}", ""]
    fix = det.get("suggested_fix") or det.get("suggested_action") or details.get("suggested_fix")
    if fix:
        out += ["## Suggested fix", "", str(fix), ""]
    out += ["_Generated by the pisama-diagnose skill._", ""]
    return "\n".join(out)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pisama-diagnose",
        description="Diagnose an agent trace with the live Pisama backend.",
    )
    p.add_argument("trace", nargs="?", help="Path to an ATIF trajectory JSON, or '-' for stdin.")
    p.add_argument("--detection-id", help="Diagnose a stored Pisama detection by id (needs a key).")
    p.add_argument("--apply", action="store_true",
                   help="Apply the primary fix to a live n8n entity (needs --entity-id + creds).")
    p.add_argument("--framework", default="n8n", help="Framework for --apply (default: n8n).")
    p.add_argument("--entity-id", help="Framework entity id for --apply (e.g. an n8n workflow id).")
    p.add_argument("--n8n-url", help="n8n instance URL for --apply (or env PISAMA_N8N_URL).")
    p.add_argument("--n8n-key", help="n8n API key for --apply (or env PISAMA_N8N_KEY).")
    p.add_argument("--no-patch", action="store_true",
                   help="Skip the apply-ready CLAUDE.md/.cursorrules patch lookup (needs a key).")
    p.add_argument("--out", help="Write the report to this path (default: print to stdout).")
    p.add_argument("--api-url", help=f"Pisama API base URL (default: {DEFAULT_API_URL} or config).")
    p.add_argument("--json", action="store_true", help="Also print the raw API response to stderr.")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config()
    api_url = args.api_url or cfg.get("api_url", DEFAULT_API_URL)
    token = cfg.get("api_key")
    tenant_id = cfg.get("tenant_id")

    if not args.trace and not args.detection_id:
        build_parser().print_help()
        return 2

    # Path A: stored detection by id.
    if args.detection_id:
        if not (token and tenant_id):
            raise SystemExit("--detection-id needs a connected key. "
                             "Run: pisama-cc connect --api-key <key>")
        det = fetch_detection(args.detection_id, api_url, tenant_id, token)
        _emit(render_from_detection(det), args.out)
        return 0

    # Path B: analyze a trace.
    trajectory = load_trajectory(args.trace)

    apply_kwargs: Dict[str, Any] = {}
    if args.apply:
        if args.framework not in REAL_APPLY_FRAMEWORKS:
            verified = ", ".join(REAL_APPLY_FRAMEWORKS)
            print(f"Note: real-trace auto-apply is verified for {verified} only. "
                  f"'{args.framework}' will be attempted but may not apply.", file=sys.stderr)
        if not args.entity_id:
            raise SystemExit("--apply needs --entity-id (the framework entity to patch).")
        n8n_url = args.n8n_url or os.getenv("PISAMA_N8N_URL")
        n8n_key = args.n8n_key or os.getenv("PISAMA_N8N_KEY")
        creds: Dict[str, Any] = {}
        if n8n_url:
            creds["instance_url"] = n8n_url
        if n8n_key:
            creds["api_key"] = n8n_key
        apply_kwargs = dict(apply_fix=True, framework=args.framework,
                            entity_id=args.entity_id, credentials=creds)

    result = analyze_trajectory(trajectory, api_url, **apply_kwargs)
    if args.json:
        print(json.dumps(result, indent=2)[:4000], file=sys.stderr)

    diagnosis = result.get("diagnosis", {})

    # Apply-ready patch: only when a key is set and a primary failure exists.
    # Diagnosis itself is public; the patch lookup uses the healing endpoint.
    ide_patch = None
    primary = diagnosis.get("primary_failure")
    if primary and token and not args.no_patch:
        ide_patch = fetch_ide_patch(primary, api_url, token)

    report = render_report(diagnosis, result.get("trace", {}),
                           healing=result.get("healing"), ide_patch=ide_patch)
    _emit(report, args.out)
    return 0


def _emit(report: str, out: Optional[str]) -> None:
    if out:
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report)
        print(f"Wrote {out}")
    else:
        print(report)


if __name__ == "__main__":
    raise SystemExit(main())
