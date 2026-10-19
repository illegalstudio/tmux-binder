from __future__ import annotations

import json
import os
import shlex
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from binder.installer import DEFAULT_PATHS, install, remove


class HookInstallation(unittest.TestCase):
    def test_claude_migrates_async_observers_and_installs_tool_start(self):
        with tempfile.TemporaryDirectory(prefix="binder-installer-") as directory:
            configured = {key: str(Path(directory) / key) for key in DEFAULT_PATHS}
            path = Path(configured["CLAUDE_SETTINGS"])
            foreign = {"type": "command", "command": "CJ_RUST_HOOK=1 cj hook claude", "async": True}
            old = {"type": "command", "command": "/plugin/binder/scripts/features/hooks/hook-claude.sh", "async": True}
            path.write_text(json.dumps({"hooks": {event: [{"hooks": [foreign, old]}] for event in ("UserPromptSubmit", "PostToolUse", "Stop")}}))
            with patch.dict(os.environ, {**configured, "PLUGIN_DST": "/plugin/binder"}):
                install(["claude"])
                first = path.read_text()
                install(["claude"])
            self.assertEqual(first, path.read_text())
            hooks = json.loads(first)["hooks"]
            self.assertIn("PreToolUse", hooks)
            for event, groups in hooks.items():
                handlers = [hook for group in groups for hook in group["hooks"]]
                owned = [hook for hook in handlers if hook["command"] == old["command"]]
                self.assertEqual(len(owned), 1, event)
                self.assertNotIn("async", owned[0], event)
                if event in ("UserPromptSubmit", "PostToolUse", "Stop"):
                    self.assertIn(foreign, handlers)

    def test_grok_installs_native_observers_and_preserves_foreign_hooks(self):
        with tempfile.TemporaryDirectory(prefix="binder-installer-") as directory:
            configured = {key: str(Path(directory) / key) for key in DEFAULT_PATHS}
            path = Path(configured["GROK_HOOKS"])
            foreign = {"type": "command", "command": "/other/grok-observer"}
            path.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [foreign]}]}}))
            with patch.dict(os.environ, {**configured, "PLUGIN_DST": "/plugin/binder"}):
                install(["grok"])
                first = path.read_text()
                install(["grok"])
                self.assertEqual(first, path.read_text())
                data = json.loads(first)["hooks"]
                self.assertNotIn("PreToolUse", data)
                self.assertIn("StopCancelled", data)
                self.assertEqual(data["Stop"][0]["hooks"][0], foreign)
                self.assertFalse(Path(configured["CLAUDE_SETTINGS"]).exists())
                remove(["grok"])
                self.assertEqual(json.loads(path.read_text())["hooks"]["Stop"][0]["hooks"], [foreign])

    def test_idempotent_install_remove_and_backups_preserve_other_hooks(self):
        with tempfile.TemporaryDirectory(prefix="binder-installer-") as directory:
            configured = {key: str(Path(directory) / (key + (".toml" if key == "CODEX_CONFIG" else ".json"))) for key in DEFAULT_PATHS}
            claude = Path(configured["CLAUDE_SETTINGS"])
            claude.write_text(json.dumps({"other": True, "hooks": {"Stop": [{"matcher": "", "hooks": [{"type": "command", "command": "/other/hook"}]}]}}))
            codex = Path(configured["CODEX_CONFIG"])
            codex.write_text("model = \"custom\"\n")
            with patch.dict(os.environ, {**configured, "PLUGIN_DST": "/plugin/binder"}):
                for _ in range(5):
                    install()
                data = json.loads(claude.read_text())
                commands = [item["command"] for rule in data["hooks"]["Stop"] for item in rule["hooks"]]
                self.assertEqual(commands.count("/plugin/binder/scripts/features/hooks/hook-claude.sh"), 1)
                self.assertIn("/other/hook", commands)
                self.assertEqual(len(list(claude.parent.glob(claude.name + ".bak-tmux-sidebar-*"))), 3)
                self.assertIn('model = "custom"', codex.read_text())
                self.assertNotIn("notify =", codex.read_text())
                native = json.loads(Path(configured["CODEX_HOOKS"]).read_text())
                self.assertEqual(len(native["hooks"]["UserPromptSubmit"][0]["hooks"]), 1)
                remove()
                data = json.loads(claude.read_text())
                self.assertTrue(data["other"])
                self.assertEqual(data["hooks"]["Stop"][0]["hooks"][0]["command"], "/other/hook")
                self.assertNotIn("/plugin/binder", codex.read_text())
                self.assertIn('model = "custom"', codex.read_text())
                self.assertFalse(list(Path(directory).glob("*.binder-new")))

    def test_existing_kiro_default_is_preserved(self):
        with tempfile.TemporaryDirectory(prefix="binder-installer-") as directory:
            configured = {key: str(Path(directory) / key) for key in DEFAULT_PATHS}
            Path(configured["KIRO_CLI_SETTINGS"]).write_text('{"chat":{"defaultAgent":"custom"}}')
            with patch.dict(os.environ, configured):
                install()
            self.assertEqual(json.loads(Path(configured["KIRO_CLI_SETTINGS"]).read_text())["chat"]["defaultAgent"], "custom")

    def test_migration_deduplicates_legacy_handlers_and_preserves_other_hooks(self):
        with tempfile.TemporaryDirectory(prefix="binder-installer-") as directory:
            configured = {key: str(Path(directory) / key) for key in DEFAULT_PATHS}
            foreign = {"type": "command", "command": "CJ_RUST_HOOK=1 cj hook claude", "timeout": 30}
            old = {"type": "command", "command": "bash ~/.tmux/plugins/tmux-pane-tree/examples/claude-hook.sh"}
            path = Path(configured["CLAUDE_SETTINGS"])
            path.write_text(json.dumps({"other": {"keep": True}, "hooks": {"Stop": [{"matcher": "", "hooks": [foreign, old, old]}]}}))
            path.chmod(0o600)
            with patch.dict(os.environ, {**configured, "PLUGIN_DST": str(Path(directory) / "plugin with spaces")}):
                install(["claude"])
                once = path.read_text()
                install(["claude"])
                self.assertEqual(path.read_text(), once)
                data = json.loads(once)
                handlers = data["hooks"]["Stop"][0]["hooks"]
                self.assertEqual(handlers[0], foreign)
                self.assertEqual(len(handlers), 2)
                self.assertEqual(shlex.split(handlers[1]["command"]), [str(Path(directory) / "plugin with spaces/scripts/features/hooks/hook-claude.sh")])
                self.assertTrue(data["other"]["keep"])
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertFalse(Path(configured["KIRO_AGENT"]).exists())
                self.assertFalse(Path(configured["CODEX_CONFIG"]).exists())
                remove(["claude"])
                self.assertEqual(json.loads(path.read_text())["hooks"]["Stop"][0]["hooks"], [foreign])

    def test_codex_native_hooks_preserve_notify_and_trust_state(self):
        with tempfile.TemporaryDirectory(prefix="binder-installer-") as directory:
            configured = {key: str(Path(directory) / key) for key in DEFAULT_PATHS}
            config = Path(configured["CODEX_CONFIG"])
            original = 'notify = ["other-notifier"]\n[features]\nhooks = true\n[hooks.state."existing:stop:0:0"]\ntrusted_hash = "existing-hash"\n'
            config.write_text(original)
            path = Path(configured["CODEX_HOOKS"])
            foreign = {"type": "command", "command": "CJ_RUST_HOOK=1 cj hook codex"}
            path.write_text(json.dumps({"description": "keep", "hooks": {"Stop": [{"hooks": [foreign]}]}}))
            with patch.dict(os.environ, {**configured, "PLUGIN_DST": "/plugin/binder"}):
                install(["codex"])
                install(["codex"])
                self.assertEqual(config.read_text(), original)
                data = json.loads(path.read_text())
                self.assertEqual(data["description"], "keep")
                self.assertEqual(data["hooks"]["Stop"][0]["hooks"][0], foreign)
                self.assertEqual(len(data["hooks"]["Stop"][0]["hooks"]), 2)
                remove(["codex"])
                self.assertEqual(config.read_text(), original)
                self.assertEqual(json.loads(path.read_text())["hooks"]["Stop"][0]["hooks"], [foreign])

    def test_codex_old_notify_is_removed_even_when_multiline_or_misplaced(self):
        for notify in ('notify = ["bash", "/old/tmux-pane-tree/scripts/features/hooks/hook-codex.sh"]\n', 'notify = [ # old callback\n "bash",\n "/old/tmux-binder/examples/codex-hook.sh",\n]\n'):
            for prefix in ('', '[notice]\n'):
                with self.subTest(notify=notify, prefix=prefix), tempfile.TemporaryDirectory(prefix="binder-installer-") as directory:
                    configured = {key: str(Path(directory) / key) for key in DEFAULT_PATHS}
                    config = Path(configured["CODEX_CONFIG"])
                    config.write_text(prefix + notify + 'keep = "same"\n')
                    with patch.dict(os.environ, {**configured, "PLUGIN_DST": "/plugin/binder"}):
                        install(["codex"])
                    self.assertEqual(config.read_text(), prefix + 'keep = "same"\n')

    def test_cursor_migration_keeps_observers_and_other_permission_hooks(self):
        with tempfile.TemporaryDirectory(prefix="binder-installer-") as directory:
            configured = {key: str(Path(directory) / key) for key in DEFAULT_PATHS}
            path = Path(configured["CURSOR_HOOKS"])
            foreign = {"command": "/other/permission-policy"}
            old = {"command": "bash /old/tmux-pane-tree/scripts/features/hooks/hook-cursor.sh"}
            path.write_text(json.dumps({"version": 1, "hooks": {"preToolUse": [foreign, old], "stop": [old, old]}}))
            with patch.dict(os.environ, {**configured, "PLUGIN_DST": "/plugin/binder"}):
                install(["cursor"])
                first = path.read_text()
                install(["cursor"])
            self.assertEqual(path.read_text(), first)
            hooks = json.loads(first)["hooks"]
            self.assertEqual(hooks["preToolUse"], [foreign])
            self.assertNotIn("subagentStart", hooks)
            self.assertEqual(len(hooks["stop"]), 1)
            self.assertIn("postToolUseFailure", hooks)

    def test_invalid_config_is_rejected_without_replacing_existing_settings(self):
        with tempfile.TemporaryDirectory(prefix="binder-installer-") as directory:
            configured = {key: str(Path(directory) / key) for key in DEFAULT_PATHS}
            claude = Path(configured["CLAUDE_SETTINGS"])
            claude.write_text('{"keep":true}')
            Path(configured["CODEX_HOOKS"]).write_text('{"hooks":[]}')
            with patch.dict(os.environ, configured), self.assertRaises(ValueError):
                install(["claude", "codex"])
            self.assertEqual(claude.read_text(), '{"keep":true}')

    def test_atomic_write_preserves_symlinks_and_target_permissions(self):
        with tempfile.TemporaryDirectory(prefix="binder-installer-") as directory:
            configured = {key: str(Path(directory) / key) for key in DEFAULT_PATHS}
            target = Path(directory) / "settings-source.json"
            target.write_text('{"keep":true}')
            target.chmod(0o600)
            path = Path(configured["CLAUDE_SETTINGS"])
            path.symlink_to(target)
            with patch.dict(os.environ, {**configured, "PLUGIN_DST": "/plugin/binder"}):
                install(["claude"])
            self.assertTrue(path.is_symlink())
            self.assertTrue(json.loads(target.read_text())["keep"])
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
