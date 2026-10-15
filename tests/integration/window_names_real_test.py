from __future__ import annotations

import shlex
import sys
import tempfile
import unittest
from pathlib import Path

import live_preview_real_test
from coordinator_test import Harness
from live_preview_real_test import Terminal


class WindowNames(unittest.TestCase):
    def test_custom_formats_and_inheritance_survive_open_configuration_and_close(self):
        with tempfile.TemporaryDirectory(prefix="binder-names-") as directory:
            server = Harness(Path(directory))
            try:
                custom = 'tag, } "#{?pane_in_mode,copy,#{pane_current_command}}" 界'
                server.t("set-option", "-w", "-t", server.window, "automatic-rename-format", custom)
                server.t("set-option", "-w", "-t", server.window, "automatic-rename", "on")
                server.wait(lambda: server.t("display-message", "-pt", server.main, "#{window_name}"), 'tag, } "sleep" 界')
                other = server.t("new-window", "-dP", "-F", "#{pane_id}", "-t", "work", "-n", "other", "tail -f /dev/null")
                window = server.t("display-message", "-pt", other, "#{window_id}")
                server.wait(lambda: server.t("display-message", "-pt", other, "#{pane_current_command}"), "tail")
                server.t("set-option", "-w", "-t", window, "automatic-rename", "on")
                server.open()
                server.t("select-pane", "-t", server.sidebar(server.window))
                server.api("configure")
                self.assertEqual(server.t("display-message", "-pt", server.main, "#{window_name}"), 'tag, } "sleep" 界')
                changed = "global:#{pane_current_command}"
                server.t("set-option", "-gw", "automatic-rename-format", changed)
                server.wait(lambda: server.t("show-options", "-wqv", "-t", window, "@tmux_pane_tree_rename_format"), changed)
                self.assertEqual(server.t("show-options", "-wqv", "-t", server.window, "@tmux_pane_tree_rename_format"), custom)
                server.t("select-pane", "-t", server.sidebar(window))
                server.t("respawn-pane", "-k", "-t", other, "sleep 86400")
                server.wait(lambda: server.t("display-message", "-pt", other, "#{pane_current_command}"), "sleep")
                server.t("select-pane", "-t", other)
                server.wait(lambda: server.t("display-message", "-pt", other, "#{window_name}"), "global:sleep")
                server.api("close")
                self.assertEqual(server.t("show-options", "-wqv", "-t", server.window, "automatic-rename-format"), custom)
                self.assertEqual(server.t("show-options", "-wqv", "-t", window, "automatic-rename-format"), "")
                self.assertEqual(server.t("show-options", "-gwv", "automatic-rename-format"), changed)
                self.assertEqual(server.t("show-options", "-wqv", "-t", window, "@tmux_pane_tree_rename_format"), "")
            finally:
                server.close()

    def test_existing_sidebar_names_are_repaired_without_overriding_automatic_rename(self):
        with tempfile.TemporaryDirectory(prefix="binder-names-") as directory:
            server = Harness(Path(directory))
            try:
                server.api("shutdown")
                server.wait(lambda: server.identity.endpoint.exists(), False)
                server.t("set-option", "-gw", "automatic-rename-format", "##{literal} #{pane_current_command}")
                command = shlex.join([sys.executable, "-c", "import time; time.sleep(86400)"])
                sidebar = server.t("split-window", "-hP", "-F", "#{pane_id}", "-t", server.main, command)
                server.t("set-option", "-p", "-t", sidebar, "@tmux_sidebar_owned", "1")
                server.wait(lambda: server.t("display-message", "-pt", sidebar, "#{pane_current_command}"), "python3")
                server.t("set-option", "-wu", "-t", server.window, "automatic-rename")
                server.wait(lambda: server.t("display-message", "-pt", sidebar, "#{window_name}"), "#{literal} python3")
                server.t("set-option", "-g", "@tmux_sidebar_enabled", "1")
                server.api("configure")
                server.wait(lambda: server.t("display-message", "-pt", server.main, "#{window_name}"), "#{literal} sleep")
                self.assertEqual(server.t("show-options", "-wqv", "-t", server.window, "automatic-rename"), "")
                self.assertEqual(server.t("display-message", "-pt", server.main, "#{automatic-rename}"), "1")
                server.api("close")
                self.assertEqual(server.t("show-options", "-wqv", "-t", server.window, "automatic-rename-format"), "")
            finally:
                server.close()

    def test_preview_keeps_automatic_and_manual_window_names(self):
        with tempfile.TemporaryDirectory(prefix="binder-names-") as directory:
            server = Harness(Path(directory))
            terminal = None
            try:
                other = server.t("new-session", "-dP", "-F", "#{pane_id}", "-s", "ops", "-n", "logs", "tail -f /dev/null")
                other_window = server.t("display-message", "-pt", other, "#{window_id}")
                manual = server.t("new-window", "-dP", "-F", "#{window_id}", "-t", "work", "-n", "My Python3|界", "sleep 86400")
                server.wait(lambda: server.t("display-message", "-pt", other, "#{pane_current_command}"), "tail")
                server.t("set-option", "-w", "-t", server.window, "automatic-rename", "on")
                server.t("set-option", "-w", "-t", other_window, "automatic-rename", "on")
                server.t("set-option", "-g", "@tmux_pane_tree_session_order", "work,ops")
                server.wait(lambda: server.t("display-message", "-pt", server.main, "#{window_name}"), "sleep")
                server.wait(lambda: server.t("display-message", "-pt", other, "#{window_name}"), "tail")
                terminal = Terminal(server)
                live_preview_real_test.LivePreview().open(server, terminal)
                terminal.drain(.7)
                self.assertEqual(server.t("display-message", "-pt", server.main, "#{window_name}"), "sleep")
                terminal.keys("G")
                terminal.wait(lambda: terminal.location(), ["ops", other_window, server.sidebar(other_window)])
                terminal.drain(.7)
                self.assertEqual(server.t("display-message", "-pt", other, "#{window_name}"), "tail")
                self.assertEqual(server.t("display-message", "-pt", manual, "#{window_name}"), "My Python3|界")
                server.api("close")
                self.assertEqual(server.t("show-options", "-wqv", "-t", server.window, "automatic-rename"), "on")
                self.assertEqual(server.t("show-options", "-wqv", "-t", manual, "automatic-rename"), "off")
            finally:
                try:
                    if terminal is not None:
                        terminal.close()
                finally:
                    server.close()


if __name__ == "__main__":
    unittest.main()
