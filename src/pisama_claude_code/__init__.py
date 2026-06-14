"""Pisama Claude Code Integration - Trace capture, failure detection, and self-healing."""

__version__ = "0.5.0"

# Lazy imports to avoid loading everything at startup
def install(force: bool = False):
    """Install Pisama hooks to ~/.claude/hooks/."""
    from .install import install as _install
    return _install(force=force)


def uninstall():
    """Remove Pisama hooks from ~/.claude/hooks/."""
    from .install import uninstall as _uninstall
    return _uninstall()


__all__ = ["install", "uninstall", "__version__"]
