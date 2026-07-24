#!/usr/bin/env python3
"""PISAMA Claude Code Installer.

Installs trace capture hooks into ~/.claude/.
"""

import json
import shutil
import stat
import sys
from pathlib import Path
from typing import Dict

from pisama_claude_code.private_files import (
    ensure_private_dir,
    make_private,
    write_private_text,
)

HOOK_TEMPLATE = '''#!{python_path}
"""Auto-generated Pisama capture hook."""

from pisama_claude_code.hooks.capture_hook import main
main()
'''

FORWARD_TEMPLATE = '''#!{python_path}
"""Auto-generated Pisama forward hook."""

from pisama_claude_code.hooks.forward_hook import main
main()
'''


def _write_hook_entry(path: Path, template: str, python_path: str, force: bool):
    """Write an auto-generated hook entry-point script and make it executable."""
    if path.exists() and not force:
        print(f"Skipping {path.name} (exists, use --force to overwrite)")
        return
    path.write_text(template.format(python_path=python_path))
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"Installed {path.name}")


def install(force: bool = False, auto_config: bool = True):
    """Install PISAMA hooks to ~/.claude/hooks/.

    Args:
        force: Overwrite existing hooks if True
        auto_config: Automatically update settings.local.json (default True)
    """
    claude_dir = Path.home() / ".claude"
    hooks_dir = claude_dir / "hooks"
    pisama_dir = claude_dir / "pisama"

    # Ensure directories exist
    hooks_dir.mkdir(parents=True, exist_ok=True)
    ensure_private_dir(pisama_dir)
    ensure_private_dir(pisama_dir / "traces")

    # Use the Python executable that has pisama_claude_code installed
    python_path = sys.executable

    # Install hook entry-points: capture (PostToolUse) + forward (Stop)
    _write_hook_entry(hooks_dir / "pisama-capture.py", HOOK_TEMPLATE, python_path, force)
    _write_hook_entry(hooks_dir / "pisama-forward.py", FORWARD_TEMPLATE, python_path, force)

    # Install shell wrappers
    _install_shell_hooks(hooks_dir, force)

    # Install minimal config, preserving connection settings
    config_path = pisama_dir / "config.json"
    default_config = {}
    config_valid = True

    if config_path.exists():
        # Preserve existing config (especially connection settings)
        try:
            existing = json.loads(config_path.read_text())
            default_config = existing
        except json.JSONDecodeError:
            config_valid = False
            print("Warning: Existing Pisama config is invalid JSON; replacing it")

    if not config_path.exists() or not config_valid:
        write_private_text(config_path, json.dumps(default_config, indent=2))
        print("Installed default config")
    else:
        make_private(config_path)

    # Update settings.local.json
    settings_updated = _update_settings(claude_dir, hooks_dir, auto_config=auto_config)

    # Install the pisama-diagnose skill (Claude Code + Codex)
    skills_installed = install_skill(force=force)

    print("\nPisama installation complete!")
    print(f"Hooks installed to: {hooks_dir}")
    print(f"Traces will be stored in: {pisama_dir / 'traces'}")
    if skills_installed:
        print(f"Skill installed to: {', '.join(skills_installed)}")

    if settings_updated:
        print("\n✅ settings.local.json automatically configured")
        print("   Restart Claude Code for hooks to take effect")
    elif not auto_config:
        print("\nNote: --no-auto-config specified, manual configuration required")

    print("\nNext steps:")
    print("  1. Restart Claude Code")
    print("  2. Run 'pisama-cc verify' to confirm installation")
    print("  3. Diagnose a failing run: ask Claude to use the pisama-diagnose skill,")
    print("     or run 'python3 ~/.claude/skills/pisama-diagnose/diagnose.py <trace.json>'")
    print("  4. Run 'pisama-cc connect --api-key <key>' to unlock the apply-ready patch")
    print("\n" + "─" * 50)
    print("⭐ If this tool saves you time/money, consider starring:")
    print("   https://github.com/Pisama-AI/pisama-claude-code")


def _install_shell_hooks(hooks_dir: Path, force: bool):
    """Install shell wrapper hooks (capture on PostToolUse, forward on Stop)."""
    # Capture wrapper. Records the tool call + surrounding turn to the local
    # store. Registered async in settings so it stays off the critical path.
    post_script = """#!/bin/bash
# Pisama capture hook - record the tool call + surrounding turn locally.
PISAMA_HOOK_TYPE=post ~/.claude/hooks/pisama-capture.py
"""
    post_path = hooks_dir / "pisama-post.sh"
    if not post_path.exists() or force:
        post_path.write_text(post_script)
        post_path.chmod(post_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        print("Installed pisama-post.sh")

    # Forward wrapper. Flushes unsynced traces to Pisama in a DETACHED background
    # process, so a slow or failed network call can never block or delay the
    # session, even if the harness does not honor the "async" hook flag. We read
    # the hook payload off stdin first, then hand it to the background process.
    forward_script = """#!/bin/bash
# Pisama forward hook - flush unsynced traces to the Pisama platform.
input=$(cat)
echo "$input" | nohup ~/.claude/hooks/pisama-forward.py >/dev/null 2>&1 &
exit 0
"""
    forward_path = hooks_dir / "pisama-forward.sh"
    if not forward_path.exists() or force:
        forward_path.write_text(forward_script)
        forward_path.chmod(forward_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        print("Installed pisama-forward.sh")


# Canonical Pisama hook entries. Claude Code expects, under each event name, a
# list of matcher-groups; each group has a nested "hooks" list of command
# entries. Timeouts are in SECONDS. async=true keeps the hooks off the
# interactive critical path. PostToolUse captures every tool call; Stop flushes
# the turn's traces to the platform.
PISAMA_HOOK_EVENTS = {
    "PostToolUse": {
        "matcher": "*",
        "hooks": [
            {
                "type": "command",
                "command": "~/.claude/hooks/pisama-post.sh",
                "timeout": 10,
                "async": True,
            }
        ],
    },
    "Stop": {
        "matcher": "*",
        "hooks": [
            {
                "type": "command",
                "command": "~/.claude/hooks/pisama-forward.sh",
                "timeout": 30,
                "async": True,
            }
        ],
    },
}

# Event names written by the pre-fix installer (wrong names + flat shape). Any
# pisama entries left under these never fired; strip them on (re)install.
_STALE_EVENTS = ("PreToolCall", "PostToolCall")


def _strip_pisama(groups: list) -> list:
    """Drop entries referencing a pisama hook, handling both the old flat shape
    ({"command": ...}) and the correct nested shape ({"hooks": [{"command": ...}]})."""
    out = []
    for g in groups:
        if not isinstance(g, dict):
            out.append(g)
            continue
        if "pisama" in str(g.get("command", "")):
            continue  # old flat pisama entry
        inner = g.get("hooks")
        if isinstance(inner, list):
            kept = [h for h in inner if "pisama" not in str(h.get("command", ""))]
            if not kept:
                continue  # group held only pisama hooks
            g = {**g, "hooks": kept}
        out.append(g)
    return out


def _update_settings(claude_dir: Path, hooks_dir: Path, auto_config: bool = True) -> bool:
    """Reconcile settings.local.json so PostToolUse + Stop carry the canonical
    Pisama hooks (and stale pre-fix entries are removed).

    Returns True if settings were modified, False otherwise.
    """
    settings_path = claude_dir / "settings.local.json"
    backup_path = claude_dir / "settings.local.json.pisama-backup"

    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
        except json.JSONDecodeError:
            print("Warning: Existing settings.local.json has invalid JSON, creating new")
            settings = {}
    else:
        settings = {}

    hooks = settings.get("hooks", {})
    modified = False

    # Remove stale entries left by the broken installer.
    for event in _STALE_EVENTS:
        grp = hooks.get(event)
        if not isinstance(grp, list):
            continue
        cleaned = _strip_pisama(grp)
        if cleaned != grp:
            modified = True
            if cleaned:
                hooks[event] = cleaned
            else:
                hooks.pop(event, None)

    # Reconcile each target event to exactly one canonical pisama entry.
    for event, group in PISAMA_HOOK_EVENTS.items():
        existing = hooks.get(event)
        existing = existing if isinstance(existing, list) else []
        reconciled = _strip_pisama(existing) + [group]
        if reconciled != existing:
            modified = True
        hooks[event] = reconciled

    if not modified:
        print("Pisama hooks already configured in settings.local.json")
        return False

    if not auto_config:
        print("\nNote: Add the following to your settings.local.json hooks:")
        print(json.dumps({"hooks": hooks}, indent=2))
        return False

    if settings_path.exists():
        shutil.copy2(settings_path, backup_path)
        print(f"Backed up settings to {backup_path.name}")

    settings["hooks"] = hooks
    settings_path.write_text(json.dumps(settings, indent=2))
    return True


def _bundled_skill_file(name: str) -> "str | None":
    """Read a bundled pisama-diagnose asset by filename, or None if missing.

    Works both from an installed wheel (importlib.resources) and from an
    editable/source checkout (read next to this file).
    """
    try:
        from importlib.resources import files

        base = files("pisama_claude_code") / "skills" / "pisama-diagnose"
        return (base / name).read_text()
    except Exception:
        local = Path(__file__).parent / "skills" / "pisama-diagnose" / name
        if local.exists():
            return local.read_text()
        return None


def install_skill(force: bool = False) -> list:
    """Install the pisama-diagnose skill for Claude Code and Codex.

    Drops SKILL.md + diagnose.py into ~/.claude/skills/pisama-diagnose/ and
    ~/.codex/skills/pisama-diagnose/. The engine is self-contained (stdlib
    only) and calls the live Pisama backend. Returns the directories written.
    """
    engine = _bundled_skill_file("diagnose.py")
    claude_doc = _bundled_skill_file("SKILL.md")
    codex_doc = _bundled_skill_file("SKILL.codex.md") or claude_doc
    if engine is None or claude_doc is None:
        print("Skipping skill install (bundled skill assets not found)")
        return []
    assert codex_doc is not None

    targets = [
        (Path.home() / ".claude" / "skills" / "pisama-diagnose", claude_doc),
        (Path.home() / ".codex" / "skills" / "pisama-diagnose", codex_doc),
    ]
    installed = []
    for skill_dir, doc in targets:
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_path = skill_dir / "SKILL.md"
        engine_path = skill_dir / "diagnose.py"
        if skill_path.exists() and not force:
            print(f"Skipping {skill_dir} (exists, use --force to overwrite)")
            continue
        skill_path.write_text(doc)
        engine_path.write_text(engine)
        engine_path.chmod(engine_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        installed.append(str(skill_dir))
        print(f"Installed pisama-diagnose skill to {skill_dir}")
    return installed


def uninstall():
    """Uninstall PISAMA hooks from ~/.claude/hooks/."""
    claude_dir = Path.home() / ".claude"
    hooks_dir = claude_dir / "hooks"

    hooks = [
        "pisama-capture.py",
        "pisama-forward.py",
        "pisama-pre.sh",  # legacy
        "pisama-post.sh",
        "pisama-forward.sh",
    ]

    for filename in hooks:
        hook_path = hooks_dir / filename
        if hook_path.exists():
            hook_path.unlink()
            print(f"Removed {filename}")

    # Strip Pisama hook entries from settings.local.json (all event names).
    settings_path = claude_dir / "settings.local.json"
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
            settings_hooks = settings.get("hooks", {})
            changed = False
            for event in list(settings_hooks.keys()):
                grp = settings_hooks[event]
                if not isinstance(grp, list):
                    continue
                cleaned = _strip_pisama(grp)
                if cleaned != grp:
                    changed = True
                    if cleaned:
                        settings_hooks[event] = cleaned
                    else:
                        settings_hooks.pop(event, None)
            if changed:
                settings["hooks"] = settings_hooks
                settings_path.write_text(json.dumps(settings, indent=2))
                print("Removed Pisama hooks from settings.local.json")
        except json.JSONDecodeError:
            pass

    # Remove the pisama-diagnose skill (both IDEs).
    for skill_dir in (
        Path.home() / ".claude" / "skills" / "pisama-diagnose",
        Path.home() / ".codex" / "skills" / "pisama-diagnose",
    ):
        if skill_dir.exists():
            for child in skill_dir.iterdir():
                child.unlink()
            skill_dir.rmdir()
            print(f"Removed skill {skill_dir}")

    print("\nPisama hooks uninstalled.")
    print("Note: Config and traces in ~/.claude/pisama/ were preserved.")


def verify() -> bool:
    """Verify PISAMA installation is working.

    Checks:
    - Hooks directory exists
    - Hook files exist and are executable
    - settings.local.json has PISAMA hooks configured

    Returns:
        True if all checks pass, False otherwise
    """
    claude_dir = Path.home() / ".claude"
    hooks_dir = claude_dir / "hooks"
    settings_path = claude_dir / "settings.local.json"

    capture_entry = hooks_dir / "pisama-capture.py"
    forward_entry = hooks_dir / "pisama-forward.py"
    post_wrapper = hooks_dir / "pisama-post.sh"
    forward_wrapper = hooks_dir / "pisama-forward.sh"

    def _executable(p: Path) -> bool:
        return p.exists() and bool(p.stat().st_mode & stat.S_IXUSR)

    def _event_has_pisama(hooks: dict, event: str) -> bool:
        for g in hooks.get(event, []):
            if not isinstance(g, dict):
                continue
            for h in g.get("hooks", []):
                if "pisama" in str(h.get("command", "")):
                    return True
        return False

    checks: Dict[str, bool] = {
        "hooks_directory": hooks_dir.exists() and hooks_dir.is_dir(),
        "capture_entry": capture_entry.exists(),
        "forward_entry": forward_entry.exists(),
        "post_wrapper_executable": _executable(post_wrapper),
        "forward_wrapper_executable": _executable(forward_wrapper),
        "settings_file": settings_path.exists(),
        "posttooluse_configured": False,
        "stop_configured": False,
    }

    if settings_path.exists():
        try:
            hooks = json.loads(settings_path.read_text()).get("hooks", {})
            checks["posttooluse_configured"] = _event_has_pisama(hooks, "PostToolUse")
            checks["stop_configured"] = _event_has_pisama(hooks, "Stop")
        except json.JSONDecodeError:
            pass

    print("\nPisama Installation Verification")
    print("=" * 40)

    all_passed = True
    for check, passed in checks.items():
        icon = "✅" if passed else "❌"
        print(f"  {icon} {check.replace('_', ' ').title()}")
        if not passed:
            all_passed = False
    print("=" * 40)

    # Live smoke check (informational): how many traces captured recently.
    recent = _recent_trace_count(claude_dir)
    if recent is not None:
        print(f"  ℹ️  {recent} trace(s) captured in the last 24h")
        if all_passed and recent == 0:
            print("     (Run some tool calls in a Claude Code session, then re-check.)")

    if all_passed:
        print("✅ All checks passed! Pisama is ready.")
        print("\nRun 'pisama-cc status' to see current state.")
    else:
        print("❌ Some checks failed.")
        print("\nTo fix, run: pisama-cc install --force  (then restart Claude Code)")

    return all_passed


def _recent_trace_count(claude_dir: Path) -> "int | None":
    """Count traces captured in the last 24h from the local JSONL store.

    Returns None if the trace store is absent (nothing captured yet)."""
    from datetime import datetime, timedelta, timezone

    traces_dir = claude_dir / "pisama" / "traces"
    if not traces_dir.exists():
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    count = 0
    found_any = False
    for jsonl in traces_dir.glob("traces-*.jsonl"):
        found_any = True
        try:
            for line in jsonl.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = rec.get("timestamp") or rec.get("time")
                if not ts:
                    count += 1  # undated row still counts as captured
                    continue
                try:
                    when = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=timezone.utc)
                    if when >= cutoff:
                        count += 1
                except ValueError:
                    count += 1
        except OSError:
            continue
    return count if found_any else None


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="PISAMA Claude Code Installer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  pisama-install              Install hooks with auto-config
  pisama-install --verify     Verify installation status
  pisama-install --uninstall  Remove hooks
  pisama-install --no-auto-config  Install without modifying settings
""",
    )
    parser.add_argument("--force", "-f", action="store_true", help="Overwrite existing hooks")
    parser.add_argument("--uninstall", "-u", action="store_true", help="Uninstall hooks")
    parser.add_argument("--verify", "-v", action="store_true", help="Verify installation status")
    parser.add_argument(
        "--no-auto-config", action="store_true", help="Don't auto-update settings.local.json"
    )

    args = parser.parse_args()

    if args.verify:
        success = verify()
        sys.exit(0 if success else 1)
    elif args.uninstall:
        uninstall()
    else:
        install(force=args.force, auto_config=not args.no_auto_config)


if __name__ == "__main__":
    main()
