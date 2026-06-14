"""Tests for hook registration in install.py (corrected event names + shape)."""

import json

import pisama_claude_code.install as install


def _settings(claude_dir):
    return json.loads((claude_dir / "settings.local.json").read_text())


def test_update_settings_writes_correct_shape(tmp_path):
    claude_dir = tmp_path / ".claude"
    hooks_dir = claude_dir / "hooks"
    hooks_dir.mkdir(parents=True)

    changed = install._update_settings(claude_dir, hooks_dir, auto_config=True)
    assert changed is True

    hooks = _settings(claude_dir)["hooks"]
    # Correct event names only - the broken installer used PreToolCall/PostToolCall.
    assert "PostToolUse" in hooks and "Stop" in hooks
    assert "PreToolCall" not in hooks and "PostToolCall" not in hooks

    post = hooks["PostToolUse"][0]
    assert post["matcher"] == "*"
    inner = post["hooks"][0]
    assert inner["type"] == "command"
    assert "pisama-post.sh" in inner["command"]
    assert inner["timeout"] == 10          # seconds, not ms
    assert inner["async"] is True

    stop_inner = hooks["Stop"][0]["hooks"][0]
    assert "pisama-forward.sh" in stop_inner["command"]
    assert stop_inner["async"] is True


def test_update_settings_idempotent(tmp_path):
    claude_dir = tmp_path / ".claude"
    hooks_dir = claude_dir / "hooks"
    hooks_dir.mkdir(parents=True)

    assert install._update_settings(claude_dir, hooks_dir) is True
    assert install._update_settings(claude_dir, hooks_dir) is False  # no-op second run

    hooks = _settings(claude_dir)["hooks"]
    assert len(hooks["PostToolUse"]) == 1
    assert len(hooks["Stop"]) == 1


def test_update_settings_migrates_stale_broken_entries(tmp_path):
    claude_dir = tmp_path / ".claude"
    hooks_dir = claude_dir / "hooks"
    hooks_dir.mkdir(parents=True)
    # Simulate the pre-fix broken install: wrong event names + flat shape.
    (claude_dir / "settings.local.json").write_text(json.dumps({
        "hooks": {
            "PreToolCall": [{"command": "~/.claude/hooks/pisama-pre.sh", "timeout": 2000}],
            "PostToolCall": [{"command": "~/.claude/hooks/pisama-post.sh", "timeout": 2000}],
        }
    }))

    install._update_settings(claude_dir, hooks_dir)

    hooks = _settings(claude_dir)["hooks"]
    assert "PreToolCall" not in hooks      # stale pisama-only group stripped
    assert "PostToolCall" not in hooks
    assert "PostToolUse" in hooks and "Stop" in hooks


def test_update_settings_preserves_foreign_hooks(tmp_path):
    claude_dir = tmp_path / ".claude"
    hooks_dir = claude_dir / "hooks"
    hooks_dir.mkdir(parents=True)
    (claude_dir / "settings.local.json").write_text(json.dumps({
        "hooks": {
            "PostToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "other-tool.sh"}]}
            ],
        }
    }))

    install._update_settings(claude_dir, hooks_dir)

    post = _settings(claude_dir)["hooks"]["PostToolUse"]
    cmds = [h["command"] for g in post for h in g.get("hooks", [])]
    assert "other-tool.sh" in cmds                      # foreign hook untouched
    assert any("pisama-post.sh" in c for c in cmds)     # pisama hook added


def test_full_install_then_verify(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    install.install(force=True, auto_config=True)

    assert install.verify() is True
    # Entry-points + wrappers exist.
    hooks_dir = tmp_path / ".claude" / "hooks"
    for f in ("pisama-capture.py", "pisama-forward.py", "pisama-post.sh", "pisama-forward.sh"):
        assert (hooks_dir / f).exists(), f


def test_uninstall_strips_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    install.install(force=True, auto_config=True)
    install.uninstall()

    settings_path = tmp_path / ".claude" / "settings.local.json"
    hooks = json.loads(settings_path.read_text()).get("hooks", {})
    # No pisama entries remain under any event.
    for groups in hooks.values():
        for g in groups:
            for h in g.get("hooks", []):
                assert "pisama" not in h.get("command", "")
