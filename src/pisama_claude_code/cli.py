"""PISAMA Claude Code CLI - Trace capture for Claude Code.

Command-line interface for capturing Claude Code traces and syncing
to the PISAMA platform for analysis and self-healing.

Quick Start (3 lines!):
    pip install pisama-claude-code    # Install package
    pisama-cc install                 # Install hooks + auto-config
    pisama-cc verify                  # Confirm everything works

Usage:
    pisama-cc install      Install hooks to ~/.claude/ (auto-updates settings)
    pisama-cc verify       Verify installation is working
    pisama-cc uninstall    Remove hooks
    pisama-cc status       Show current status (incl. token/cost totals)
    pisama-cc traces       View recent traces (-v for token usage)
    pisama-cc usage        Show token usage and cost breakdown
    pisama-cc demo         Run demo detection on sample traces
    pisama-cc export       Export traces to file (JSONL or OTEL format)
    pisama-cc export-otel  Export traces to OpenTelemetry collector
    pisama-cc connect      Connect to PISAMA platform
    pisama-cc sync         Sync traces to platform
    pisama-cc analyze      Analyze traces (requires platform)

Self-Healing:
    pisama-cc fix list                   List available fixes
    pisama-cc fix show FIX_ID            Show fix details
    pisama-cc fix apply FIX_ID           Apply a suggested fix

OTEL Export:
    pisama-cc export --format otel -o traces.json
    pisama-cc export-otel -e http://localhost:4318/v1/traces
"""

import click
import json
import gzip
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

try:
    import httpx
except ImportError:
    httpx = None


# Config paths
CLAUDE_DIR = Path.home() / ".claude"
CONFIG_DIR = CLAUDE_DIR / "pisama"
CONFIG_FILE = CONFIG_DIR / "config.json"
TRACES_DIR = CONFIG_DIR / "traces"
HOOKS_DIR = CLAUDE_DIR / "hooks"

# The Pisama v1 API is mounted under /api/v1. Auth is JWT: exchange the API key
# for a short-lived token, then send it as a Bearer credential.
API_PREFIX = "/api/v1"
DEFAULT_API_URL = "https://api.pisama.ai"


def get_config() -> dict:
    """Load PISAMA config."""
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def save_config(config: dict):
    """Save PISAMA config."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2))


def api_url(config: dict, path: str) -> str:
    """Build a full API URL for `path` (e.g. '/traces/claude-code/ingest').

    Tolerates an api_url that already includes a trailing '/api'."""
    base = (config.get("api_url") or DEFAULT_API_URL).rstrip("/")
    if base.endswith("/api"):
        base = base[: -len("/api")]
    return f"{base}{API_PREFIX}{path}"


_jwt_cache: dict = {}


def _bearer(config: dict) -> str:
    """Return the Bearer token for API calls.

    The Pisama API authenticates ingest/detection endpoints with a short-lived
    JWT, not the raw ``pisama_...`` API key (those endpoints decode a JWT). So
    exchange the stored api_key for a JWT via ``/auth/token`` once per process.
    If the configured value is already a JWT (legacy configs), use it as-is; on
    any exchange failure fall back to the raw key (older self-hosted backends
    accepted it directly).
    """
    key = config.get("api_key", "") or ""
    if key.startswith("eyJ"):  # already a JWT
        return key
    if key in _jwt_cache:
        return _jwt_cache[key]
    if httpx is not None:
        try:
            resp = httpx.post(
                api_url(config, "/auth/token"),
                json={"api_key": key},
                timeout=15,
            )
            if resp.status_code == 200:
                tok = resp.json().get("access_token", "")
                if tok:
                    _jwt_cache[key] = tok
                    return tok
        except Exception:
            pass
    return key


@click.group()
@click.version_option(version="0.4.3")
def main():
    """Pisama Claude Code - Trace capture and sync."""
    pass


GITHUB_URL = "https://github.com/tn-pisama/pisama-claude-code"


@main.command()
@click.option("--force", "-f", is_flag=True, help="Overwrite existing hooks")
@click.option("--no-auto-config", is_flag=True, help="Don't auto-update settings.local.json")
def install(force: bool, no_auto_config: bool):
    """Install PISAMA hooks to ~/.claude/hooks/.

    By default, automatically updates settings.local.json to enable hooks.
    Use --no-auto-config to skip automatic configuration.
    """
    from pisama_claude_code.install import install as do_install
    do_install(force=force, auto_config=not no_auto_config)


@main.command()
def verify():
    """Verify PISAMA installation is working correctly.

    Checks:
    - Hook files exist and are executable
    - settings.local.json has PISAMA hooks configured
    - All required directories exist

    Exit code 0 if all checks pass, 1 otherwise.
    """
    from pisama_claude_code.install import verify as do_verify
    import sys
    success = do_verify()
    sys.exit(0 if success else 1)


@main.command()
def uninstall():
    """Remove PISAMA hooks from ~/.claude/hooks/."""
    from pisama_claude_code.install import uninstall as do_uninstall
    do_uninstall()


@main.command()
@click.option("--verbose", "-v", is_flag=True, help="Show detailed detection info")
def demo(verbose: bool):
    """Run a demo detection on sample traces.

    This demonstrates PISAMA's failure detection capabilities without
    requiring platform connection. Uses bundled sample traces that
    showcase common agent failures.

    Example:
        pisama-cc demo           # Quick demo
        pisama-cc demo -v        # Verbose with details
    """
    import importlib.resources

    click.echo("")
    click.echo("🚀 PISAMA Demo - Instant Failure Detection")
    click.echo("=" * 55)
    click.echo("")

    # Load sample trace from package
    try:
        # Python 3.9+ compatible way to get package resources
        try:
            from importlib.resources import files
            sample_path = files("pisama_claude_code.sample_traces").joinpath("demo_loop.jsonl")
            sample_content = sample_path.read_text()
        except (ImportError, TypeError):
            # Fallback for older Python
            import pkg_resources
            sample_content = pkg_resources.resource_string(
                "pisama_claude_code", "sample_traces/demo_loop.jsonl"
            ).decode("utf-8")
    except Exception as e:
        click.echo(f"❌ Could not load sample traces: {e}")
        click.echo("   Try reinstalling: pip install --upgrade pisama-claude-code")
        return

    # Parse traces
    traces = []
    for line in sample_content.strip().split("\n"):
        if line.strip():
            try:
                traces.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not traces:
        click.echo("❌ No valid traces in sample file")
        return

    click.echo(f"📥 Loaded {len(traces)} sample tool calls")
    click.echo("")

    # Run local loop detection
    click.echo("🔍 Analyzing for failures...")
    click.echo("")

    detections = _run_local_detection(traces)

    if not detections:
        click.echo("✅ No failures detected in sample trace")
        click.echo("")
        click.echo("Try running on your own traces:")
        click.echo("   pisama-cc traces     # View captured traces")
        click.echo("   pisama-cc analyze    # Analyze with platform (requires connection)")
        return

    # Display detections with explanations
    for i, detection in enumerate(detections, 1):
        severity = detection.get("severity", 50)
        if severity >= 70:
            icon = "🔴"
        elif severity >= 40:
            icon = "🟡"
        else:
            icon = "🟢"

        click.echo(f"{icon} Detection #{i}: {detection.get('type', 'Unknown Failure')}")
        click.echo(f"   Confidence: {detection.get('confidence', 0)}%")
        click.echo("")

        # Plain English explanation
        explanation = detection.get("explanation", "")
        if explanation:
            click.echo(f"   📝 {explanation}")
            click.echo("")

        # Business impact
        impact = detection.get("business_impact", "")
        if impact:
            click.echo(f"   ⚠️  Impact: {impact}")

        # Suggested action
        action = detection.get("suggested_action", "")
        if action:
            click.echo(f"   💡 Fix: {action}")

        click.echo("")

        # Verbose details
        if verbose and detection.get("details"):
            click.echo("   Technical Details:")
            for key, value in detection.get("details", {}).items():
                click.echo(f"      {key}: {value}")
            click.echo("")

    # Celebration and next steps
    click.echo("=" * 55)
    click.echo("🎉 First detection complete!")
    click.echo("")
    click.echo("📋 Next Steps:")
    click.echo("   1. Install hooks to capture your traces:")
    click.echo("      pisama-cc install")
    click.echo("")
    click.echo("   2. Use Claude Code normally - traces are captured automatically")
    click.echo("")
    click.echo("   3. View your traces:")
    click.echo("      pisama-cc traces")
    click.echo("")
    click.echo("   4. Connect to platform for advanced analysis:")
    click.echo("      pisama-cc connect --api-key <your-key>")
    click.echo("")
    click.echo(f"Star us on GitHub: {GITHUB_URL}")
    click.echo("")


def _run_local_detection(traces: list) -> list:
    """Run basic local detection without platform connection.

    Currently supports:
    - Loop detection (exact message repetition)
    - Tool loop detection (same tool repeated)
    """
    detections = []

    # Check for loop detection
    if len(traces) >= 3:
        # Check for exact message loops
        tool_calls = [(t.get("tool_name", ""), json.dumps(t.get("tool_input", {}), sort_keys=True)) for t in traces]

        # Find repeated sequences
        consecutive_same = 1
        max_consecutive = 1
        repeated_tool = None
        repeated_input = None

        for i in range(1, len(tool_calls)):
            if tool_calls[i] == tool_calls[i - 1]:
                consecutive_same += 1
                if consecutive_same > max_consecutive:
                    max_consecutive = consecutive_same
                    repeated_tool = tool_calls[i][0]
                    repeated_input = tool_calls[i][1]
            else:
                consecutive_same = 1

        # Report loop if 3+ consecutive identical calls
        if max_consecutive >= 3:
            confidence = min(95, 70 + (max_consecutive - 3) * 5)
            severity = min(80, 40 + max_consecutive * 5)

            detections.append({
                "type": "Tool Loop Detected",
                "confidence": confidence,
                "severity": severity,
                "explanation": f"Your agent called '{repeated_tool}' {max_consecutive} times in a row with identical parameters. This indicates the agent is stuck repeating the same action without making progress.",
                "business_impact": "This wastes compute resources and API credits. The agent will likely time out without completing the task.",
                "suggested_action": f"Add a retry limit (recommend max 3 attempts) for '{repeated_tool}'. Consider caching results to avoid duplicate calls.",
                "details": {
                    "tool_name": repeated_tool,
                    "repetition_count": max_consecutive,
                    "detection_method": "exact_hash_match",
                },
            })

    # Check for semantic loops (same tool, different params but same pattern)
    tool_sequence = [t.get("tool_name", "") for t in traces]
    tool_counts = {}
    for tool in tool_sequence:
        tool_counts[tool] = tool_counts.get(tool, 0) + 1

    for tool, count in tool_counts.items():
        if count >= 4 and count / len(traces) > 0.6:
            # High concentration of one tool
            detections.append({
                "type": "Tool Overuse Pattern",
                "confidence": 75,
                "severity": 45,
                "explanation": f"'{tool}' was called {count} times ({int(count/len(traces)*100)}% of all calls). This suggests the agent may be over-relying on one approach.",
                "business_impact": "The agent might be missing more effective strategies or tools for the task.",
                "suggested_action": f"Review why '{tool}' is being called so frequently. Consider adding alternative approaches or result caching.",
                "details": {
                    "tool_name": tool,
                    "call_count": count,
                    "percentage": f"{int(count/len(traces)*100)}%",
                },
            })

    return detections


@main.command()
@click.option("--last", default=20, help="Number of traces to show")
@click.option("--tool", help="Filter by tool name")
@click.option("--session", help="Filter by session ID")
@click.option("--verbose", "-v", is_flag=True, help="Show token usage and cost")
@click.option("--content", "-c", is_flag=True, help="Show input/reasoning/output content")
@click.option("--reasoning", "-r", is_flag=True, help="Show reasoning (thinking) content only")
def traces(last: int, tool: Optional[str], session: Optional[str], verbose: bool, content: bool, reasoning: bool):
    """View recent traces."""
    all_traces = load_recent_traces(last * 3)  # Load extra for filtering

    # Apply filters
    if tool:
        all_traces = [t for t in all_traces if t.get("tool_name") == tool]
    if session:
        all_traces = [t for t in all_traces if t.get("session_id") == session]

    traces_to_show = all_traces[-last:]

    if not traces_to_show:
        click.echo("No traces found")
        return

    # Calculate totals
    total_input = sum(t.get("input_tokens", 0) for t in traces_to_show)
    total_output = sum(t.get("output_tokens", 0) for t in traces_to_show)
    total_cost = sum(t.get("cost_usd", 0) for t in traces_to_show)

    click.echo(f"📋 Recent Traces ({len(traces_to_show)} shown)")
    click.echo("=" * 70)

    for t in traces_to_show:
        ts = t.get("timestamp", "")[:19]
        tool_name = t.get("tool_name", "?")[:12].ljust(12)
        sess = t.get("session_id", "?")[:8]
        hook = t.get("hook_type", "?")[:4]

        if verbose:
            inp = t.get("input_tokens", 0)
            out = t.get("output_tokens", 0)
            cost = t.get("cost_usd", 0)
            tokens = f"{inp:>6}i {out:>5}o" if inp or out else "       -      "
            cost_str = f"${cost:.4f}" if cost else "  -    "
            click.echo(f"{ts} | {tool_name} | {sess} | {hook} | {tokens} | {cost_str}")
        else:
            click.echo(f"{ts} | {tool_name} | {sess} | {hook}")

        # Show content if requested
        if content or reasoning:
            user_input = t.get("user_input")
            reasoning_content = t.get("reasoning")
            ai_output = t.get("ai_output")

            if content and user_input:
                click.echo(f"  📥 INPUT: {_truncate(user_input, 200)}")
            if reasoning_content:
                click.echo(f"  🧠 REASONING: {_truncate(reasoning_content, 300)}")
            if content and ai_output:
                click.echo(f"  📤 OUTPUT: {_truncate(ai_output, 200)}")
            if content or reasoning:
                click.echo("")  # Blank line between traces

    # Show totals if we have usage data
    if verbose and (total_input or total_output or total_cost):
        click.echo("─" * 70)
        click.echo(f"Totals: {total_input:,} input + {total_output:,} output tokens = ${total_cost:.4f}")


def _truncate(text: str, max_len: int = 200) -> str:
    """Truncate text for display."""
    if not text:
        return ""
    text = text.replace("\n", " ").strip()
    if len(text) > max_len:
        return text[:max_len] + "..."
    return text


@main.command()
@click.option("--api-key", required=True, help="Your Pisama API key")
@click.option("--api-url", "api_url_opt", default=DEFAULT_API_URL, help="API base URL")
@click.option("--auto-sync/--no-auto-sync", default=True, help="Enable auto-sync")
def connect(api_key: str, api_url_opt: str, auto_sync: bool):
    """Connect to PISAMA platform."""
    if httpx is None:
        click.echo("❌ httpx required. Run: pip install httpx")
        return

    click.echo("🔗 Connecting to PISAMA platform...")

    # Validate API key
    try:
        response = httpx.get(
            api_url({"api_url": api_url_opt}, "/health"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        if response.status_code == 401:
            click.echo("❌ Invalid API key")
            return
    except httpx.ConnectError:
        click.echo(f"⚠️  Could not reach {api_url_opt} - saving config for later")

    # Save config
    config = get_config()
    config["api_key"] = api_key
    config["api_url"] = api_url_opt
    config["auto_sync"] = auto_sync
    config["connected_at"] = datetime.now(timezone.utc).isoformat()
    save_config(config)

    click.echo("✅ Connected to PISAMA platform")
    click.echo(f"   API URL: {api_url_opt}")
    click.echo(f"   Auto-sync: {'enabled' if auto_sync else 'disabled'}")

    click.echo("\n📡 You can now:")
    click.echo("   pisama-cc sync      - Upload traces to platform")
    click.echo("   pisama-cc analyze   - Run failure detection")


@main.command()
@click.option("--last", default=100, help="Number of recent traces to sync")
@click.option("--include-outputs/--no-outputs", default=False, help="Include tool outputs")
def sync(last: int, include_outputs: bool):
    """Sync traces to PISAMA platform."""
    if httpx is None:
        click.echo("❌ httpx required. Run: pip install httpx")
        return

    config = get_config()

    if not config.get("api_key"):
        click.echo("❌ Not connected. Run 'pisama-cc connect --api-key <key>' first")
        return

    click.echo(f"📤 Syncing last {last} traces...")

    # Load traces
    traces_list = load_recent_traces(last)
    if not traces_list:
        click.echo("No traces found to sync")
        return

    # Prepare payload (redact sensitive data)
    payload = prepare_sync_payload(traces_list, include_outputs)

    click.echo(f"   Found {len(traces_list)} traces")
    click.echo(f"   Payload size: {len(json.dumps(payload)) // 1024} KB")

    # Upload
    try:
        response = httpx.post(
            api_url(config, "/traces/claude-code/ingest"),
            headers={
                "Authorization": f"Bearer {_bearer(config)}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )

        if response.status_code in (200, 201, 202):
            result = response.json()
            click.echo(f"✅ Synced {result.get('traces_received', len(traces_list))} traces")
            click.echo(f"   View at: {config['api_url'].replace('api.', 'app.')}/traces")

            # Mark as synced
            mark_synced(traces_list)
        else:
            click.echo(f"❌ Sync failed: {response.status_code}")
            click.echo(f"   {response.text[:200]}")
    except httpx.ConnectError:
        click.echo(f"❌ Could not connect to {config['api_url']}")
        click.echo("   Traces saved locally, will retry on next sync")


@main.command()
@click.option("--last", default=50, help="Number of recent traces to analyze")
def analyze(last: int):
    """Analyze traces for failures (requires platform connection)."""
    if httpx is None:
        click.echo("❌ httpx required. Run: pip install httpx")
        return

    config = get_config()

    if not config.get("api_key"):
        click.echo("❌ Analysis requires platform connection")
        click.echo("")
        click.echo("Connect to get:")
        click.echo("   • Failure detection (28 MAST modes)")
        click.echo("   • Severity scores & explanations")
        click.echo("   • Fix suggestions")
        click.echo("   • Self-healing capabilities")
        click.echo("")
        click.echo("Run: pisama-cc connect --api-key <your-key>")
        click.echo("Get your key at: https://app.pisama.ai/settings/api")
        return

    click.echo(f"🔍 Analyzing last {last} traces...")

    # Load and sync traces first
    traces_list = load_recent_traces(last)
    if not traces_list:
        click.echo("No traces found")
        return

    # Send to platform for analysis
    try:
        payload = prepare_sync_payload(traces_list, include_outputs=False)
        response = httpx.post(
            api_url(config, "/traces/claude-code/analyze"),
            headers={
                "Authorization": f"Bearer {_bearer(config)}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=60,
        )

        if response.status_code == 200:
            results = response.json()
            display_analysis_results(results)
        else:
            click.echo(f"❌ Analysis failed: {response.status_code}")
            click.echo(f"   {response.text[:200]}")
    except httpx.ConnectError:
        click.echo(f"❌ Could not connect to {config['api_url']}")


def display_analysis_results(results: dict):
    """Display analysis results from platform."""
    detections = results.get("detections", [])
    trace_count = results.get("trace_count", 0)

    click.echo(f"\n📊 Analysis Results ({trace_count} traces)")
    click.echo("=" * 50)

    if not detections:
        click.echo("✅ No issues detected")
        return

    for d in detections:
        severity = d.get("severity", 0)
        if severity >= 70:
            icon = "🔴"
        elif severity >= 40:
            icon = "🟡"
        else:
            icon = "🟢"

        click.echo(f"\n{icon} {d.get('type', 'Unknown')} (severity: {severity}/100)")
        click.echo(f"   {d.get('explanation', '')}")
        if d.get("fix"):
            click.echo(f"   💡 Fix: {d['fix']}")

    # Summary
    click.echo("\n" + "─" * 50)
    click.echo(f"Found {len(detections)} issue(s)")
    click.echo(f"View details at: {results.get('dashboard_url', 'https://app.pisama.ai')}")


# =============================================================================
# FIX COMMANDS - Self-Healing Fix Application
# =============================================================================

@main.group()
def fix():
    """Self-healing fix commands."""
    pass


@fix.command("list")
@click.option("--detection-id", help="Show fixes for specific detection")
def fix_list(detection_id: Optional[str]):
    """List available fixes for detected issues."""
    if httpx is None:
        click.echo("❌ httpx required. Run: pip install httpx")
        return

    config = get_config()

    if not config.get("api_key"):
        click.echo("❌ Fix listing requires platform connection")
        click.echo("   Run: pisama-cc connect --api-key <your-key>")
        return

    click.echo("🔧 Available Fixes")
    click.echo("=" * 60)

    try:
        # Get recent detections with fixes
        endpoint = api_url(config, "/detections")
        if detection_id:
            endpoint = api_url(config, f"/detections/{detection_id}/fixes")

        response = httpx.get(
            endpoint,
            headers={"Authorization": f"Bearer {_bearer(config)}"},
            timeout=30,
        )

        if response.status_code == 200:
            data = response.json()
            if detection_id:
                # Single detection fixes
                suggestions = data.get("suggestions", [])
                if suggestions:
                    for i, fix_data in enumerate(suggestions, 1):
                        click.echo(f"\n{i}. {fix_data.get('title', 'Unnamed Fix')}")
                        click.echo(f"   ID: {fix_data.get('id', 'unknown')}")
                        click.echo(f"   Type: {fix_data.get('fix_type', 'unknown')}")
                        click.echo(f"   Confidence: {fix_data.get('confidence', 'unknown')}")
                        click.echo(f"   {fix_data.get('description', '')[:100]}")
                else:
                    click.echo("No fixes available for this detection")
            else:
                # List detections
                detections = data if isinstance(data, list) else data.get("detections", [])
                for d in detections[:10]:
                    click.echo(f"\n• {d.get('detection_type', 'unknown')} [{d.get('id', '')[:8]}]")
                    click.echo(f"  Confidence: {d.get('confidence', 0)}%")
                    click.echo(f"  Run: pisama-cc fix list --detection-id {d.get('id', '')}")
        else:
            click.echo(f"❌ Failed to fetch fixes: {response.status_code}")
    except httpx.ConnectError:
        click.echo(f"❌ Could not connect to {config['api_url']}")


@fix.command("show")
@click.argument("fix_id")
@click.option("--detection-id", required=True, help="Detection ID for the fix")
def fix_show(fix_id: str, detection_id: str):
    """Show detailed fix information with code changes."""
    if httpx is None:
        click.echo("❌ httpx required. Run: pip install httpx")
        return

    config = get_config()

    if not config.get("api_key"):
        click.echo("❌ Fix viewing requires platform connection")
        click.echo("   Run: pisama-cc connect --api-key <your-key>")
        return

    try:
        response = httpx.get(
            api_url(config, f"/detections/{detection_id}/fixes"),
            headers={"Authorization": f"Bearer {_bearer(config)}"},
            timeout=30,
        )

        if response.status_code == 200:
            data = response.json()
            suggestions = data.get("suggestions", [])

            # Find the specific fix
            fix_data = None
            for s in suggestions:
                if s.get("id") == fix_id:
                    fix_data = s
                    break

            if not fix_data:
                click.echo(f"❌ Fix {fix_id} not found")
                return

            click.echo("🔧 Fix Details")
            click.echo("=" * 60)
            click.echo(f"\nTitle: {fix_data.get('title', 'Unnamed')}")
            click.echo(f"Type: {fix_data.get('fix_type', 'unknown')}")
            click.echo(f"Confidence: {fix_data.get('confidence', 'unknown')}")

            if fix_data.get("breaking_changes"):
                click.echo("\n⚠️  WARNING: This fix may introduce breaking changes")

            if fix_data.get("requires_testing"):
                click.echo("ℹ️  This fix requires testing after application")

            click.echo(f"\nDescription:\n{fix_data.get('description', 'No description')}")
            click.echo(f"\nRationale:\n{fix_data.get('rationale', 'No rationale')}")

            # Show code changes
            code_changes = fix_data.get("code_changes", [])
            if code_changes:
                click.echo("\n" + "─" * 60)
                click.echo("Code Changes:")
                for change in code_changes:
                    click.echo(f"\n📄 {change.get('file_path', 'unknown')} ({change.get('language', '')})")
                    if change.get("description"):
                        click.echo(f"   {change['description']}")
                    if change.get("diff"):
                        click.echo("\n" + change["diff"])
                    elif change.get("suggested_code"):
                        click.echo("\nSuggested code:")
                        click.echo(change["suggested_code"])

            click.echo("\n" + "─" * 60)
            click.echo(f"To apply: pisama-cc fix apply {fix_id} --detection-id {detection_id}")
        else:
            click.echo(f"❌ Failed to fetch fix details: {response.status_code}")
    except httpx.ConnectError:
        click.echo(f"❌ Could not connect to {config['api_url']}")


@fix.command("apply")
@click.argument("fix_id")
@click.option("--detection-id", required=True, help="Detection ID for the fix")
@click.option("--dry-run", is_flag=True, help="Show what would be applied without making changes")
@click.option("--force", "-f", is_flag=True, help="Apply without confirmation")
def fix_apply(fix_id: str, detection_id: str, dry_run: bool, force: bool):
    """Apply a suggested fix for a detected issue.

    This records the fix as applied and shows the code changes to implement.
    The actual code changes need to be applied manually or via your IDE.

    Examples:

        # View a fix before applying
        pisama-cc fix show FIX_ID --detection-id DETECTION_ID

        # Apply a fix
        pisama-cc fix apply FIX_ID --detection-id DETECTION_ID

        # Dry run to see what would happen
        pisama-cc fix apply FIX_ID --detection-id DETECTION_ID --dry-run
    """
    if httpx is None:
        click.echo("❌ httpx required. Run: pip install httpx")
        return

    config = get_config()

    if not config.get("api_key"):
        click.echo("❌ Fix application requires platform connection")
        click.echo("   Run: pisama-cc connect --api-key <your-key>")
        return

    try:
        # First, get the fix details
        response = httpx.get(
            api_url(config, f"/detections/{detection_id}/fixes"),
            headers={"Authorization": f"Bearer {_bearer(config)}"},
            timeout=30,
        )

        if response.status_code != 200:
            click.echo(f"❌ Failed to fetch fix: {response.status_code}")
            return

        data = response.json()
        suggestions = data.get("suggestions", [])

        # Find the specific fix
        fix_data = None
        for s in suggestions:
            if s.get("id") == fix_id:
                fix_data = s
                break

        if not fix_data:
            click.echo(f"❌ Fix {fix_id} not found")
            return

        # Display fix summary
        click.echo("🔧 Applying Fix")
        click.echo("=" * 60)
        click.echo(f"\nTitle: {fix_data.get('title', 'Unnamed')}")
        click.echo(f"Type: {fix_data.get('fix_type', 'unknown')}")
        click.echo(f"Detection: {detection_id[:8]}...")

        if fix_data.get("breaking_changes"):
            click.echo("\n⚠️  WARNING: This fix may introduce breaking changes!")

        # Show code changes
        code_changes = fix_data.get("code_changes", [])
        if code_changes:
            click.echo("\nCode changes to apply:")
            for change in code_changes:
                click.echo(f"\n📄 {change.get('file_path', 'unknown')}")
                if change.get("diff"):
                    click.echo(change["diff"][:500])
                    if len(change.get("diff", "")) > 500:
                        click.echo("... (truncated, run 'fix show' to see full diff)")

        if dry_run:
            click.echo("\n" + "─" * 60)
            click.echo("🔍 DRY RUN - No changes made")
            click.echo("Remove --dry-run to apply this fix")
            return

        # Confirm application
        if not force:
            if not click.confirm("\nApply this fix?"):
                click.echo("Cancelled")
                return

        # Apply the fix via API
        apply_response = httpx.post(
            api_url(config, f"/detections/{detection_id}/fixes/{fix_id}/apply"),
            headers={"Authorization": f"Bearer {_bearer(config)}"},
            timeout=30,
        )

        if apply_response.status_code in (200, 201):
            result = apply_response.json()
            click.echo("\n" + "=" * 60)
            click.echo("✅ Fix recorded as applied!")
            click.echo(f"\n{result.get('message', 'Fix applied successfully.')}")

            if result.get("rollback_available"):
                click.echo("\nRollback is available if needed.")

            # Show next steps
            click.echo("\n📋 Next Steps:")
            click.echo("1. Review the code changes above")
            click.echo("2. Apply the changes to your codebase")
            click.echo("3. Test the affected code paths")
            click.echo("4. Commit the changes")
        else:
            click.echo(f"\n❌ Failed to apply fix: {apply_response.status_code}")
            click.echo(f"   {apply_response.text[:200]}")

    except httpx.ConnectError:
        click.echo(f"❌ Could not connect to {config['api_url']}")
    except Exception as e:
        click.echo(f"❌ Error: {e}")


@main.command()
def status():
    """Show PISAMA installation and connection status."""
    config_data = get_config()

    click.echo("📊 PISAMA Status")
    click.echo("=" * 40)

    # Check hook installation
    click.echo("\n🔧 Hook Installation:")
    hook_files = [
        "pisama-capture.py",
        "pisama-pre.sh",
        "pisama-post.sh",
    ]
    hooks_installed = 0
    for hf in hook_files:
        hook_path = HOOKS_DIR / hf
        if hook_path.exists():
            hooks_installed += 1
            click.echo(f"   ✅ {hf}")
        else:
            click.echo(f"   ❌ {hf} (missing)")

    if hooks_installed == len(hook_files):
        click.echo("   All hooks installed")
    elif hooks_installed == 0:
        click.echo("   Run 'pisama-cc install' to install hooks")
    else:
        click.echo("   Run 'pisama-cc install --force' to reinstall")

    # Check platform connection
    click.echo("\n🔗 Platform Connection:")
    if config_data.get("api_key"):
        click.echo(f"   ✅ Connected to: {config_data.get('api_url', 'unknown')}")
        connected_at = config_data.get("connected_at", "")
        if connected_at:
            click.echo(f"   Connected at: {connected_at[:19]}")
        click.echo(f"   Auto-sync: {'enabled' if config_data.get('auto_sync') else 'disabled'}")
    else:
        click.echo("   ❌ Not connected")
        click.echo("   Run 'pisama-cc connect --api-key <key>'")

    # Count local traces and calculate totals
    trace_count = 0
    total_input_tokens = 0
    total_output_tokens = 0
    total_cost = 0.0
    models_used = set()

    if TRACES_DIR.exists():
        for tf in TRACES_DIR.glob("traces-*.jsonl"):
            with open(tf) as f:
                for line in f:
                    trace_count += 1
                    try:
                        t = json.loads(line)
                        usage = t.get("usage", {})
                        if usage:
                            total_input_tokens += usage.get("input_tokens", 0)
                            total_output_tokens += usage.get("output_tokens", 0)
                        total_cost += t.get("cost_usd", 0) or 0
                        if t.get("model"):
                            models_used.add(t.get("model"))
                    except json.JSONDecodeError:
                        pass

    click.echo(f"\n📁 Local Traces: {trace_count}")
    if total_input_tokens or total_output_tokens:
        click.echo(f"   Input tokens:  {total_input_tokens:,}")
        click.echo(f"   Output tokens: {total_output_tokens:,}")
        click.echo(f"   Total cost:    ${total_cost:.4f}")
    if models_used:
        click.echo(f"   Models: {', '.join(sorted(models_used))}")

    # Check Claude Code settings for hook integration
    settings_file = Path.home() / ".claude" / "settings.local.json"
    if not settings_file.exists():
        settings_file = Path.home() / ".claude" / "settings.json"

    click.echo("\n📝 Claude Code Settings:")
    if settings_file.exists():
        try:
            settings = json.loads(settings_file.read_text())
            hooks = settings.get("hooks", {})
            pre_hooks = hooks.get("PreToolCall", [])
            post_hooks = hooks.get("PostToolCall", [])

            pisama_in_pre = any("pisama" in str(h).lower() for h in pre_hooks)
            pisama_in_post = any("pisama" in str(h).lower() for h in post_hooks)

            if pisama_in_pre and pisama_in_post:
                click.echo("   ✅ PISAMA hooks configured in settings")
            elif pisama_in_pre or pisama_in_post:
                click.echo("   ⚠️  PISAMA hooks partially configured")
            else:
                click.echo("   ❌ PISAMA hooks not in settings")
                click.echo("   Add hooks to settings.local.json (see 'pisama-cc install' output)")
        except json.JSONDecodeError:
            click.echo("   ⚠️  Could not parse settings file")
    else:
        click.echo("   ❌ No settings file found")

    # Star reminder
    click.echo("")
    click.echo(f"Star us on GitHub: {GITHUB_URL}")


@main.command()
@click.option("--last", default=100, help="Number of traces to analyze")
@click.option("--by-model", is_flag=True, help="Group by model")
@click.option("--by-tool", is_flag=True, help="Group by tool")
def usage(last: int, by_model: bool, by_tool: bool):
    """Show token usage and cost breakdown."""
    traces_list = load_recent_traces(last)

    if not traces_list:
        click.echo("No traces found")
        return

    # Calculate totals
    total_input = sum(t.get("input_tokens", 0) for t in traces_list)
    total_output = sum(t.get("output_tokens", 0) for t in traces_list)
    total_cache = sum(t.get("cache_read_tokens", 0) for t in traces_list)
    total_cost = sum(t.get("cost_usd", 0) for t in traces_list)

    click.echo(f"📊 Token Usage Summary (last {len(traces_list)} traces)")
    click.echo("=" * 50)
    click.echo(f"Input tokens:      {total_input:>12,}")
    click.echo(f"Output tokens:     {total_output:>12,}")
    click.echo(f"Cache read tokens: {total_cache:>12,}")
    click.echo(f"Total tokens:      {total_input + total_output:>12,}")
    click.echo(f"Total cost:        ${total_cost:>11.4f}")

    if by_model:
        click.echo("\n📈 By Model:")
        click.echo("-" * 50)
        model_stats = {}
        for t in traces_list:
            model = t.get("model") or "unknown"
            if model not in model_stats:
                model_stats[model] = {"input": 0, "output": 0, "cost": 0}
            model_stats[model]["input"] += t.get("input_tokens", 0)
            model_stats[model]["output"] += t.get("output_tokens", 0)
            model_stats[model]["cost"] += t.get("cost_usd", 0)

        for model, stats in sorted(model_stats.items(), key=lambda x: -x[1]["cost"]):
            if stats["cost"] > 0:
                click.echo(f"  {model[:35]:<35} ${stats['cost']:.4f}")

    if by_tool:
        click.echo("\n🔧 By Tool:")
        click.echo("-" * 50)
        tool_stats = {}
        for t in traces_list:
            tool = t.get("tool_name") or "unknown"
            if tool not in tool_stats:
                tool_stats[tool] = {"count": 0, "cost": 0}
            tool_stats[tool]["count"] += 1
            tool_stats[tool]["cost"] += t.get("cost_usd", 0)

        for tool, stats in sorted(tool_stats.items(), key=lambda x: -x[1]["count"]):
            click.echo(f"  {tool:<20} {stats['count']:>5} calls  ${stats['cost']:.4f}")


@main.command()
@click.option("--last", default=50, help="Number of traces to export")
@click.option("--output", "-o", default="traces-export.jsonl", help="Output file")
@click.option("--compress", is_flag=True, help="Gzip compress output")
@click.option("--format", "fmt", type=click.Choice(["jsonl", "otel"]), default="jsonl", help="Export format")
def export(last: int, output: str, compress: bool, fmt: str):
    """Export traces to a file (JSONL or OTEL format)."""
    traces_list = load_recent_traces(last)

    if not traces_list:
        click.echo("No traces to export")
        return

    # Handle OTEL format
    if fmt == "otel":
        from pisama_claude_code.otel_export import export_to_otel_file

        # Adjust default output for OTEL
        if output == "traces-export.jsonl":
            output = "traces-export-otel.json"

        result = export_to_otel_file(traces_list, output)
        click.echo(f"✅ Exported {result['spans_created']} spans to {output} (OTEL format)")
        click.echo(f"   Sessions: {result['sessions_exported']}")
        return

    # Prepare JSONL export (redact sensitive data)
    export_data = []
    for t in traces_list:
        clean = {
            "timestamp": t.get("timestamp"),
            "tool_name": t.get("tool_name"),
            "hook_type": t.get("hook_type"),
            "session_id": t.get("session_id"),
            # Model and token usage
            "model": t.get("model"),
            "input_tokens": t.get("input_tokens"),
            "output_tokens": t.get("output_tokens"),
            "cache_read_tokens": t.get("cache_read_tokens"),
            "cost_usd": t.get("cost_usd"),
            # Input/Reasoning/Output content (PII already tokenized by capture hook)
            "user_input": t.get("user_input"),
            "reasoning": t.get("reasoning"),
            "ai_output": t.get("ai_output"),
        }
        # Include sanitized tool input
        inp = t.get("tool_input", {})
        if isinstance(inp, dict):
            clean["tool_input"] = sanitize_input(inp)
        export_data.append(clean)

    output_path = Path(output)

    # Ensure parent directory exists
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        click.echo(f"❌ Cannot create directory: {e}")
        return

    if compress or output.endswith(".gz"):
        if not output.endswith(".gz"):
            output_path = Path(output + ".gz")
        with gzip.open(output_path, "wt") as f:
            for trace in export_data:
                f.write(json.dumps(trace) + "\n")
    else:
        with open(output_path, "w") as f:
            for trace in export_data:
                f.write(json.dumps(trace) + "\n")

    size_kb = output_path.stat().st_size // 1024
    click.echo(f"✅ Exported {len(export_data)} traces to {output_path} ({size_kb} KB)")


@main.command("export-otel")
@click.option("--last", default=50, help="Number of traces to export")
@click.option("--endpoint", "-e", required=True, help="OTEL collector endpoint (e.g., http://localhost:4318/v1/traces)")
@click.option("--service-name", "-s", default="claude-code", help="Service name for spans")
@click.option("--header", "-H", multiple=True, help="Header in 'key=value' format (can be repeated)")
def export_otel(last: int, endpoint: str, service_name: str, header: tuple):
    """Export traces to an OpenTelemetry collector.

    Examples:

      # Local Jaeger
      pisama-cc export-otel -e http://localhost:4318/v1/traces

      # Honeycomb
      pisama-cc export-otel -e https://api.honeycomb.io/v1/traces \\
          -H "x-honeycomb-team=YOUR_API_KEY"

      # Datadog (via OTEL collector)
      pisama-cc export-otel -e http://localhost:4318/v1/traces \\
          -s my-claude-agent
    """
    from pisama_claude_code.otel_export import is_otel_available, export_traces_to_otel

    if not is_otel_available():
        click.echo("❌ OpenTelemetry not installed")
        click.echo("   Run: pip install pisama-claude-code[otel]")
        return

    traces_list = load_recent_traces(last)

    if not traces_list:
        click.echo("No traces to export")
        return

    # Parse headers
    headers = {}
    for h in header:
        if "=" in h:
            key, value = h.split("=", 1)
            headers[key.strip()] = value.strip()

    click.echo(f"📤 Exporting {len(traces_list)} traces to OTEL collector...")
    click.echo(f"   Endpoint: {endpoint}")
    click.echo(f"   Service: {service_name}")

    try:
        result = export_traces_to_otel(
            traces=traces_list,
            endpoint=endpoint,
            service_name=service_name,
            headers=headers if headers else None,
        )

        if result["success"]:
            click.echo(f"✅ Exported {result['spans_created']} spans")
            click.echo(f"   Sessions: {result['sessions_exported']}")
            click.echo(f"   Traces: {result['traces_processed']}")
        else:
            click.echo("❌ Export failed")
    except Exception as e:
        click.echo(f"❌ Export failed: {e}")


# Helper functions

def normalize_trace(t: dict) -> dict:
    """Normalize trace to consistent format (handles old and new formats)."""
    # New format uses 'name', old uses 'tool_name'
    tool_name = t.get("tool_name") or t.get("name")

    # New format uses 'start_time', old uses 'timestamp'
    timestamp = t.get("timestamp") or t.get("start_time")

    # New format puts hook_type in attributes, old has it at top level
    attrs = t.get("attributes", {})
    hook_type = t.get("hook_type") or attrs.get("hook_type", "")

    # Normalize hook_type (pre -> PreToolUse, post -> PostToolUse)
    if hook_type == "pre":
        hook_type = "PreToolUse"
    elif hook_type == "post":
        hook_type = "PostToolUse"

    # New format uses 'input_data', old uses 'tool_input'
    tool_input = t.get("tool_input") or t.get("input_data", {})

    # Session ID might be in different places
    session_id = t.get("session_id") or t.get("trace_id", "")[:8]

    # Extract usage data (new fields)
    usage = t.get("usage", {})
    input_tokens = usage.get("input_tokens", 0) if usage else 0
    output_tokens = usage.get("output_tokens", 0) if usage else 0
    cache_read = usage.get("cache_read_input_tokens", 0) if usage else 0

    return {
        "tool_name": tool_name,
        "timestamp": timestamp,
        "hook_type": hook_type,
        "session_id": session_id,
        "tool_input": tool_input,
        "working_dir": t.get("working_dir") or attrs.get("working_dir", ""),
        # Model and token usage
        "model": t.get("model"),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read,
        "cost_usd": t.get("cost_usd", 0.0),
        # Input/Reasoning/Output content
        "user_input": t.get("user_input"),
        "reasoning": t.get("reasoning"),
        "ai_output": t.get("ai_output"),
        "ai_response": t.get("ai_response"),  # Legacy field
        "_raw": t,
    }


def load_recent_traces(n: int) -> list:
    """Load recent traces from local storage."""
    if n <= 0:
        return []

    traces = []
    if not TRACES_DIR.exists():
        return traces

    for tf in sorted(TRACES_DIR.glob("traces-*.jsonl"), reverse=True):
        try:
            with open(tf) as f:
                for line in f:
                    try:
                        raw = json.loads(line)
                        traces.append(normalize_trace(raw))
                    except json.JSONDecodeError:
                        continue  # Skip invalid JSON lines
        except OSError:
            continue  # Skip files that can't be read
        if len(traces) >= n:
            break

    return traces[-n:]


def prepare_sync_payload(traces: list, include_outputs: bool) -> dict:
    """Prepare traces for sync, redacting sensitive data."""
    clean_traces = []

    for t in traces:
        clean = {
            "timestamp": t.get("timestamp"),
            "tool_name": t.get("tool_name"),
            "hook_type": t.get("hook_type"),
            "session_id": t.get("session_id"),
            "working_dir": anonymize_path(t.get("working_dir", "")),
            # Model and token usage
            "model": t.get("model"),
            "input_tokens": t.get("input_tokens"),
            "output_tokens": t.get("output_tokens"),
            "cost_usd": t.get("cost_usd"),
            # Input/Reasoning/Output content (PII already tokenized)
            "user_input": t.get("user_input"),
            "reasoning": t.get("reasoning"),
            "ai_output": t.get("ai_output"),
        }

        # Sanitize tool input
        inp = t.get("tool_input", {})
        if isinstance(inp, dict):
            clean["tool_input"] = sanitize_input(inp)

        # Optionally include tool output
        if include_outputs:
            out = t.get("tool_output")
            if out and len(str(out)) < 1000:
                clean["tool_output"] = out

        clean_traces.append(clean)

    return {
        "source": "claude-code",
        "version": "0.3.5",
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "trace_count": len(clean_traces),
        "traces": clean_traces,
    }


def sanitize_input(inp: dict) -> dict:
    """Remove sensitive data from tool input."""
    clean = {}
    sensitive_keys = {"api_key", "password", "secret", "token", "credential"}

    for k, v in inp.items():
        # Skip sensitive keys
        if any(s in k.lower() for s in sensitive_keys):
            clean[k] = "[REDACTED]"
        # Anonymize file paths
        elif k in ("file_path", "path"):
            clean[k] = anonymize_path(str(v))
        # Truncate large values
        elif isinstance(v, str) and len(v) > 500:
            clean[k] = v[:500] + "...[truncated]"
        else:
            clean[k] = v

    return clean


def anonymize_path(path: str) -> str:
    """Anonymize user-specific parts of paths."""
    if not path:
        return path
    # Replace home directory with ~
    home = str(Path.home())
    return path.replace(home, "~")


def mark_synced(traces: list):
    """Mark traces as synced (for deduplication)."""
    sync_log = CONFIG_DIR / "sync_log.jsonl"
    with open(sync_log, "a") as f:
        for t in traces:
            f.write(json.dumps({
                "synced_at": datetime.now(timezone.utc).isoformat(),
                "timestamp": t.get("timestamp"),
                "session_id": t.get("session_id"),
            }) + "\n")


# =============================================================================
# VAULT COMMANDS - PII Tokenization Vault Management
# =============================================================================

@main.group()
def vault():
    """Manage the PII tokenization vault."""
    pass


@vault.command("status")
def vault_status():
    """Show vault status and statistics."""
    try:
        from pisama_core.tokenization import TokenVault, KeychainManager
    except ImportError:
        click.echo("❌ pisama-core not installed or tokenization not available")
        click.echo("   Install with: pip install pisama-core[tokenization]")
        return

    vault_path = CONFIG_DIR / "vault.db"

    click.echo("🔐 PISAMA Vault Status")
    click.echo("=" * 50)

    # Check keychain
    click.echo("\n🔑 Encryption Key:")
    try:
        keychain = KeychainManager(allow_file_fallback=True)
        status = keychain.get_status()
        if status["available"]:
            click.echo(f"   Backend: {status['backend']}")
            if status["key_exists"]:
                click.echo("   ✅ Key stored")
                if not status["is_secure"]:
                    click.echo("   ⚠️  Using file fallback (less secure)")
            else:
                click.echo("   ❌ No key found")
                click.echo("   Key will be auto-generated on first use")
        else:
            click.echo("   ❌ Keychain not available")
    except Exception as e:
        click.echo(f"   ❌ Error: {e}")

    # Check vault database
    click.echo("\n💾 Vault Database:")
    if vault_path.exists():
        size_kb = vault_path.stat().st_size // 1024
        click.echo(f"   Path: {vault_path}")
        click.echo(f"   Size: {size_kb} KB")

        try:
            token_vault = TokenVault(vault_path)
            token_vault.initialize()
            stats = token_vault.get_stats()

            click.echo(f"   Total tokens: {stats.get('total_tokens', 0):,}")
            click.echo(f"   Sessions: {stats.get('unique_sessions', 0)}")

            by_type = stats.get("by_type", {})
            if by_type:
                click.echo("   By type:")
                for pii_type, count in sorted(by_type.items()):
                    click.echo(f"      {pii_type}: {count:,}")

            if not stats.get("encryption_available"):
                click.echo("   ⚠️  Encryption not available (install cryptography)")

            token_vault.close()
        except Exception as e:
            click.echo(f"   ❌ Error reading vault: {e}")
    else:
        click.echo(f"   Path: {vault_path}")
        click.echo("   Status: Not created yet")
        click.echo("   Will be created on first tokenization")

    # Tokenization config
    click.echo("\n⚙️  Configuration:")
    config_data = get_config()
    tokenization = config_data.get("tokenization", {})
    enabled = tokenization.get("enabled", True)
    click.echo(f"   Tokenization: {'enabled' if enabled else 'disabled'}")


@vault.command("lookup")
@click.argument("token")
@click.option("--reason", "-r", required=True, help="Reason for lookup (audit log)")
@click.option("--ticket", "-t", help="Incident ticket reference")
def vault_lookup(token: str, reason: str, ticket: Optional[str]):
    """Look up a token to reveal original value (with audit logging)."""
    try:
        from pisama_core.tokenization import TokenVault, KeychainManager, TokenParser
    except ImportError:
        click.echo("❌ pisama-core not installed")
        return

    vault_path = CONFIG_DIR / "vault.db"

    if not vault_path.exists():
        click.echo("❌ Vault not found")
        return

    # Validate token format
    parser = TokenParser()
    if not parser.is_valid_token(token):
        click.echo(f"❌ Invalid token format: {token}")
        click.echo("   Expected: [TYPE:sess:random]")
        return

    # Get encryption key
    keychain = KeychainManager(allow_file_fallback=True)
    key = keychain.get_key()

    if key is None:
        click.echo("❌ No encryption key found")
        return

    # Lookup in vault
    token_vault = TokenVault(vault_path)
    token_vault.initialize()

    try:
        original = token_vault.retrieve(token, key)

        if original:
            click.echo(f"Token: {token}")
            click.echo(f"Value: {original}")

            # Get token info
            info = token_vault.get_token_info(token)
            if info:
                click.echo(f"Type: {info.pii_type}")
                click.echo(f"Session: {info.session_id}")
                click.echo(f"Created: {info.created_at}")

            # Log to audit
            _log_vault_access(token, reason, ticket, "lookup")
            click.echo(f"\n✅ Access logged (reason: {reason})")
        else:
            click.echo(f"❌ Token not found: {token}")
    finally:
        token_vault.close()


@vault.command("delete")
@click.option("--token", help="Delete specific token")
@click.option("--session", help="Delete all tokens for session")
@click.option("--value-hash", help="Delete by value hash (GDPR erasure)")
@click.option("--reason", "-r", required=True, help="Reason for deletion")
@click.option("--confirm", is_flag=True, help="Skip confirmation prompt")
def vault_delete(
    token: Optional[str],
    session: Optional[str],
    value_hash: Optional[str],
    reason: str,
    confirm: bool,
):
    """Delete tokens from vault (GDPR erasure support)."""
    try:
        from pisama_core.tokenization import TokenVault
    except ImportError:
        click.echo("❌ pisama-core not installed")
        return

    if not any([token, session, value_hash]):
        click.echo("❌ Specify --token, --session, or --value-hash")
        return

    vault_path = CONFIG_DIR / "vault.db"
    if not vault_path.exists():
        click.echo("❌ Vault not found")
        return

    token_vault = TokenVault(vault_path)
    token_vault.initialize()

    try:
        if token:
            if not confirm:
                click.confirm(f"Delete token {token}?", abort=True)
            deleted = token_vault.delete_token(token)
            if deleted:
                click.echo(f"✅ Deleted token: {token}")
                _log_vault_access(token, reason, None, "delete")
            else:
                click.echo(f"❌ Token not found: {token}")

        elif session:
            tokens = token_vault.list_session_tokens(session)
            if not tokens:
                click.echo(f"No tokens found for session: {session}")
                return

            if not confirm:
                click.confirm(f"Delete {len(tokens)} tokens for session {session}?", abort=True)

            count = token_vault.delete_session(session)
            click.echo(f"✅ Deleted {count} tokens for session {session}")
            _log_vault_access(f"session:{session}", reason, None, "delete_session")

        elif value_hash:
            if not confirm:
                click.confirm(f"Delete all tokens with value hash {value_hash[:16]}...?", abort=True)

            count = token_vault.delete_by_value_hash(value_hash)
            if count > 0:
                click.echo(f"✅ Deleted {count} tokens (GDPR erasure)")
                _log_vault_access(f"hash:{value_hash[:16]}", reason, None, "gdpr_erasure")
            else:
                click.echo("No matching tokens found")

    finally:
        token_vault.close()


@vault.command("health")
def vault_health():
    """Check vault health and integrity."""
    try:
        from pisama_core.tokenization import TokenVault, KeychainManager
    except ImportError:
        click.echo("❌ pisama-core not installed")
        return

    vault_path = CONFIG_DIR / "vault.db"

    click.echo("🏥 Vault Health Check")
    click.echo("=" * 50)

    issues = []

    # Check vault file
    click.echo("\n1. Vault Database:")
    if vault_path.exists():
        click.echo("   ✅ Database exists")
        size = vault_path.stat().st_size
        if size > 100 * 1024 * 1024:  # 100MB
            click.echo(f"   ⚠️  Large database ({size // 1024 // 1024} MB)")
            issues.append("Consider running 'pisama-cc vault vacuum'")
    else:
        click.echo("   ⚠️  Database not created yet")

    # Check encryption key
    click.echo("\n2. Encryption Key:")
    try:
        keychain = KeychainManager(allow_file_fallback=True)
        if keychain.key_exists():
            click.echo("   ✅ Key available")
            status = keychain.get_status()
            if not status["is_secure"]:
                click.echo("   ⚠️  Using file fallback (less secure)")
                issues.append("Consider enabling OS keychain for better security")
        else:
            click.echo("   ⚠️  No key (will be created on first use)")
    except Exception as e:
        click.echo(f"   ❌ Error: {e}")
        issues.append(f"Keychain error: {e}")

    # Check database integrity
    click.echo("\n3. Database Integrity:")
    if vault_path.exists():
        try:
            import sqlite3
            conn = sqlite3.connect(str(vault_path))
            result = conn.execute("PRAGMA integrity_check").fetchone()
            if result[0] == "ok":
                click.echo("   ✅ Integrity check passed")
            else:
                click.echo(f"   ❌ Integrity issue: {result[0]}")
                issues.append("Database integrity issue detected")
            conn.close()
        except Exception as e:
            click.echo(f"   ❌ Error: {e}")
            issues.append(f"Database error: {e}")
    else:
        click.echo("   ⚠️  No database to check")

    # Summary
    click.echo("\n" + "=" * 50)
    if issues:
        click.echo(f"⚠️  {len(issues)} issue(s) found:")
        for issue in issues:
            click.echo(f"   • {issue}")
    else:
        click.echo("✅ All checks passed")


@vault.command("backup")
@click.option("--output", "-o", help="Output path (default: vault-backup-{date}.db)")
def vault_backup(output: Optional[str]):
    """Create a backup of the vault database."""
    vault_path = CONFIG_DIR / "vault.db"

    if not vault_path.exists():
        click.echo("❌ No vault to backup")
        return

    # Generate backup path
    if output:
        backup_path = Path(output)
    else:
        date_str = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = CONFIG_DIR / f"vault-backup-{date_str}.db"

    # Copy vault
    import shutil
    try:
        shutil.copy2(vault_path, backup_path)
        size_kb = backup_path.stat().st_size // 1024
        click.echo(f"✅ Backup created: {backup_path} ({size_kb} KB)")
    except Exception as e:
        click.echo(f"❌ Backup failed: {e}")


@vault.command("restore")
@click.argument("backup_path")
@click.option("--confirm", is_flag=True, help="Skip confirmation")
def vault_restore(backup_path: str, confirm: bool):
    """Restore vault from a backup."""
    backup = Path(backup_path)

    if not backup.exists():
        click.echo(f"❌ Backup not found: {backup_path}")
        return

    vault_path = CONFIG_DIR / "vault.db"

    if vault_path.exists() and not confirm:
        click.confirm("This will overwrite the current vault. Continue?", abort=True)

    import shutil
    try:
        shutil.copy2(backup, vault_path)
        click.echo(f"✅ Vault restored from {backup_path}")
    except Exception as e:
        click.echo(f"❌ Restore failed: {e}")


@vault.command("vacuum")
def vault_vacuum():
    """Compact the vault database to reclaim space."""
    try:
        from pisama_core.tokenization import TokenVault
    except ImportError:
        click.echo("❌ pisama-core not installed")
        return

    vault_path = CONFIG_DIR / "vault.db"

    if not vault_path.exists():
        click.echo("❌ No vault to vacuum")
        return

    size_before = vault_path.stat().st_size

    token_vault = TokenVault(vault_path)
    token_vault.initialize()
    token_vault.vacuum()
    token_vault.close()

    size_after = vault_path.stat().st_size
    saved = size_before - size_after

    click.echo(f"✅ Vault compacted")
    click.echo(f"   Before: {size_before // 1024} KB")
    click.echo(f"   After:  {size_after // 1024} KB")
    click.echo(f"   Saved:  {saved // 1024} KB")


def _log_vault_access(target: str, reason: str, ticket: Optional[str], action: str):
    """Log vault access to audit log."""
    import os

    audit_log = CONFIG_DIR / "audit_log.jsonl"
    audit_log.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "target": target,
        "reason": reason,
        "ticket": ticket,
        "principal": os.environ.get("USER", "unknown"),
    }

    with open(audit_log, "a") as f:
        f.write(json.dumps(entry) + "\n")


# =============================================================================
# TOKENIZE COMMAND - Manual tokenization testing
# =============================================================================

@main.group()
def tokenize():
    """PII tokenization tools."""
    pass


@tokenize.command("test")
@click.argument("text")
@click.option("--patterns", help="Comma-separated list of patterns to use")
def tokenize_test(text: str, patterns: Optional[str]):
    """Test PII detection on a string."""
    try:
        from pisama_core.tokenization import PIIDetector
    except ImportError:
        click.echo("❌ pisama-core not installed")
        return

    detector = PIIDetector()

    # Filter patterns if specified
    if patterns:
        pattern_list = [p.strip() for p in patterns.split(",")]
        for name in list(detector.patterns.keys()):
            if name not in pattern_list:
                detector.disable_pattern(name)

    matches = detector.detect(text)

    click.echo("PII Detection Results")
    click.echo("=" * 50)
    click.echo(f"\nInput: \"{text}\"")

    if matches:
        click.echo("\nDetected:")
        for m in matches:
            click.echo(f"  [{m.pii_type}] \"{m.value}\" at position {m.start}-{m.end}")
    else:
        click.echo("\nNo PII detected")


@tokenize.command("config")
def tokenize_config():
    """Show tokenization configuration."""
    config_data = get_config()
    tokenization = config_data.get("tokenization", {})

    click.echo("⚙️  Tokenization Configuration")
    click.echo("=" * 50)
    click.echo(f"Enabled: {tokenization.get('enabled', True)}")
    click.echo(f"Fail-open: {tokenization.get('fail_open', True)}")

    custom = tokenization.get("custom_patterns", {})
    if custom:
        click.echo("\nCustom Patterns:")
        for name, pattern in custom.items():
            if not name.startswith("_"):
                click.echo(f"  {name}: {pattern}")

    exclusions = tokenization.get("exclusions", [])
    if exclusions:
        click.echo("\nExclusions:")
        for e in exclusions:
            click.echo(f"  {e}")


if __name__ == "__main__":
    main()
