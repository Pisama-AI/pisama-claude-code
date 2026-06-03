#!/usr/bin/env python3
"""PISAMA Claude Code Installer.

Installs trace capture hooks into ~/.claude/.
"""

import json
import shutil
import stat
import sys
from pathlib import Path
from typing import Dict, Any


HOOK_TEMPLATE = '''#!{python_path}
"""Auto-generated PISAMA capture hook."""

from pisama_claude_code.hooks.capture_hook import main
main()
'''


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
    pisama_dir.mkdir(parents=True, exist_ok=True)
    (pisama_dir / "traces").mkdir(exist_ok=True)

    # Use the Python executable that has pisama_claude_code installed
    python_path = sys.executable

    # Install capture hook
    hook_path = hooks_dir / "pisama-capture.py"
    if hook_path.exists() and not force:
        print(f"Skipping pisama-capture.py (exists, use --force to overwrite)")
    else:
        content = HOOK_TEMPLATE.format(python_path=python_path)
        hook_path.write_text(content)
        hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        print("Installed pisama-capture.py")

    # Install shell wrappers
    _install_shell_hooks(hooks_dir, force)

    # Install minimal config, preserving connection settings
    config_path = pisama_dir / "config.json"
    default_config = {}

    if config_path.exists():
        # Preserve existing config (especially connection settings)
        try:
            existing = json.loads(config_path.read_text())
            default_config = existing
        except json.JSONDecodeError:
            pass

    if not config_path.exists():
        config_path.write_text(json.dumps(default_config, indent=2))
        print("Installed default config")

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
    print("   https://github.com/tn-pisama/pisama-claude-code")


def _install_shell_hooks(hooks_dir: Path, force: bool):
    """Install shell wrapper hooks."""
    # Pre-hook shell script
    pre_script = '''#!/bin/bash
# PISAMA Pre-hook - capture tool calls
PISAMA_HOOK_TYPE=pre ~/.claude/hooks/pisama-capture.py
'''

    pre_path = hooks_dir / "pisama-pre.sh"
    if not pre_path.exists() or force:
        pre_path.write_text(pre_script)
        pre_path.chmod(pre_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        print("Installed pisama-pre.sh")

    # Post-hook shell script
    post_script = '''#!/bin/bash
# PISAMA Post-hook - capture tool results
PISAMA_HOOK_TYPE=post ~/.claude/hooks/pisama-capture.py
'''

    post_path = hooks_dir / "pisama-post.sh"
    if not post_path.exists() or force:
        post_path.write_text(post_script)
        post_path.chmod(post_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        print("Installed pisama-post.sh")


def _update_settings(claude_dir: Path, hooks_dir: Path, auto_config: bool = True) -> bool:
    """Update settings.local.json with hook configuration.

    Args:
        claude_dir: Path to ~/.claude
        hooks_dir: Path to hooks directory
        auto_config: If True, actually modify the file. If False, just print instructions.

    Returns:
        True if settings were modified, False otherwise
    """
    settings_path = claude_dir / "settings.local.json"
    backup_path = claude_dir / "settings.local.json.pisama-backup"

    # Load existing settings
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
        except json.JSONDecodeError:
            print("Warning: Existing settings.local.json has invalid JSON, creating new")
            settings = {}
    else:
        settings = {}

    # Define PISAMA hook configuration
    pisama_pre_hook = {
        "command": "~/.claude/hooks/pisama-pre.sh",
        "timeout": 2000
    }
    pisama_post_hook = {
        "command": "~/.claude/hooks/pisama-post.sh",
        "timeout": 2000
    }

    # Get existing hooks
    hooks = settings.get("hooks", {})
    modified = False

    # Check if PISAMA hooks already exist
    def _has_pisama_hook(hook_list: list) -> bool:
        return any("pisama" in h.get("command", "") for h in hook_list)

    # Add PreToolCall hook if not present
    if "PreToolCall" not in hooks:
        hooks["PreToolCall"] = []
    if not _has_pisama_hook(hooks["PreToolCall"]):
        hooks["PreToolCall"].append(pisama_pre_hook)
        modified = True

    # Add PostToolCall hook if not present
    if "PostToolCall" not in hooks:
        hooks["PostToolCall"] = []
    if not _has_pisama_hook(hooks["PostToolCall"]):
        hooks["PostToolCall"].append(pisama_post_hook)
        modified = True

    if not modified:
        print("PISAMA hooks already configured in settings.local.json")
        return False

    if not auto_config:
        # Print instructions instead of modifying
        print("\nNote: Add the following to your settings.local.json hooks:")
        print(json.dumps({"hooks": hooks}, indent=2))
        return False

    # Create backup before modifying
    if settings_path.exists():
        shutil.copy2(settings_path, backup_path)
        print(f"Backed up settings to {backup_path.name}")

    # Update settings and write
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
    hooks_dir = Path.home() / ".claude" / "hooks"

    hooks = [
        "pisama-capture.py",
        "pisama-pre.sh",
        "pisama-post.sh",
    ]

    for filename in hooks:
        hook_path = hooks_dir / filename
        if hook_path.exists():
            hook_path.unlink()
            print(f"Removed {filename}")

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

    checks: Dict[str, bool] = {
        "hooks_directory": False,
        "capture_hook": False,
        "pre_hook": False,
        "post_hook": False,
        "pre_hook_executable": False,
        "post_hook_executable": False,
        "settings_file": False,
        "hooks_configured": False,
    }

    # Check hooks directory
    checks["hooks_directory"] = hooks_dir.exists() and hooks_dir.is_dir()

    # Check hook files exist
    capture_path = hooks_dir / "pisama-capture.py"
    pre_path = hooks_dir / "pisama-pre.sh"
    post_path = hooks_dir / "pisama-post.sh"

    checks["capture_hook"] = capture_path.exists()
    checks["pre_hook"] = pre_path.exists()
    checks["post_hook"] = post_path.exists()

    # Check hooks are executable
    if checks["pre_hook"]:
        checks["pre_hook_executable"] = bool(pre_path.stat().st_mode & stat.S_IXUSR)
    if checks["post_hook"]:
        checks["post_hook_executable"] = bool(post_path.stat().st_mode & stat.S_IXUSR)

    # Check settings file exists
    checks["settings_file"] = settings_path.exists()

    # Check hooks are configured in settings
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
            hooks = settings.get("hooks", {})

            pre_configured = any(
                "pisama" in h.get("command", "")
                for h in hooks.get("PreToolCall", [])
            )
            post_configured = any(
                "pisama" in h.get("command", "")
                for h in hooks.get("PostToolCall", [])
            )
            checks["hooks_configured"] = pre_configured and post_configured
        except json.JSONDecodeError:
            pass

    # Print results
    print("\nPISAMA Installation Verification")
    print("=" * 40)

    all_passed = True
    for check, passed in checks.items():
        icon = "✅" if passed else "❌"
        label = check.replace("_", " ").title()
        print(f"  {icon} {label}")
        if not passed:
            all_passed = False

    print("=" * 40)

    if all_passed:
        print("✅ All checks passed! PISAMA is ready.")
        print("\nRun 'pisama-cc status' to see current state.")
    else:
        print("❌ Some checks failed.")
        print("\nTo fix, run: pisama-cc install")

    return all_passed


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
"""
    )
    parser.add_argument("--force", "-f", action="store_true",
                       help="Overwrite existing hooks")
    parser.add_argument("--uninstall", "-u", action="store_true",
                       help="Uninstall hooks")
    parser.add_argument("--verify", "-v", action="store_true",
                       help="Verify installation status")
    parser.add_argument("--no-auto-config", action="store_true",
                       help="Don't auto-update settings.local.json")

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
