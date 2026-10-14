from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from binder.installer import DEFAULT_PATHS, install, remove


class HookInstallation(unittest.TestCase):
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
                self.assertEqual(codex.read_text().count("notify ="), 1)
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
