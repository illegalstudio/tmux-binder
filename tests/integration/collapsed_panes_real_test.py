from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from coordinator_test import Harness
from live_preview_real_test import Terminal


class CollapsedPanes(unittest.TestCase):
    def test_vertical_navigation_skips_session_titles(self):
        with tempfile.TemporaryDirectory(prefix="binder-navigation-") as directory:
            server = Harness(Path(directory))
            terminal = None
            try:
                other = server.t("new-session", "-dP", "-F", "#{pane_id}", "-s", "ops", "-n", "logs", "sleep 86400")
                window = server.t("display-message", "-pt", other, "#{window_id}")
                server.t("set-option", "-g", "@tmux_pane_tree_session_order", "work,ops")
                terminal = Terminal(server)
                terminal.wait(lambda: server.t("display-message", "-pt", server.main, "#{session_attached}"), "1")
                terminal.keys("\x02t")
                terminal.wait(lambda: bool(server.sidebar(server.window)))
                terminal.keys("\x02T")
                terminal.wait(lambda: terminal.location()[2], server.sidebar(server.window))

                def selected():
                    return next((line for line in terminal.frame().splitlines() if line.startswith("▶")), "")

                terminal.wait(lambda: "editor" in selected())
                terminal.keys("j")
                terminal.wait(lambda: terminal.location(), ["ops", window, server.sidebar(window)])
                terminal.wait(lambda: "logs" in selected())
                terminal.keys("\x1bOA")
                terminal.wait(lambda: terminal.location(), ["work", server.window, server.sidebar(server.window)])
                terminal.wait(lambda: "editor" in selected())
                terminal.keys("\x1bOB")
                terminal.wait(lambda: "logs" in selected())
                terminal.keys("k")
                terminal.wait(lambda: "editor" in selected())
                terminal.keys("G")
                terminal.wait(lambda: "logs" in selected())
                terminal.keys("+")
                terminal.wait(lambda: "sleep" in terminal.frame())
                terminal.keys("j")
                terminal.wait(lambda: "sleep" in selected())
                terminal.keys("k")
                terminal.wait(lambda: "logs" in selected())
                terminal.keys("gg")
                terminal.wait(lambda: "editor" in selected())
                self.assertIn("work", terminal.frame())
                self.assertIn("ops", terminal.frame())
            finally:
                if terminal is not None:
                    terminal.close()
                server.close()

    def test_default_keys_preview_new_windows_and_reload(self):
        with tempfile.TemporaryDirectory(prefix="binder-collapse-") as directory:
            server = Harness(Path(directory))
            terminal = None
            try:
                other = server.t("new-session", "-dP", "-F", "#{pane_id}", "-s", "ops", "-n", "logs", "sleep 86400")
                window = server.t("display-message", "-pt", other, "#{window_id}")
                server.t("set-option", "-g", "@tmux_pane_tree_session_order", "work,ops")
                server.t("set-option", "-g", "@tmux_pane_tree_compact_single_panes", "on")
                terminal = Terminal(server)
                terminal.wait(lambda: server.t("display-message", "-pt", server.main, "#{session_attached}"), "1")
                terminal.keys("\x02t")
                terminal.wait(lambda: bool(server.sidebar(server.window)))
                terminal.keys("\x02T")
                terminal.wait(lambda: terminal.location()[2], server.sidebar(server.window))
                terminal.wait(lambda: "▸ editor" in terminal.frame())
                self.assertNotIn("sleep", terminal.frame())
                terminal.keys("=")
                terminal.wait(lambda: terminal.frame().count("sleep"), 1)
                server.api("refresh")
                terminal.wait(lambda: terminal.frame().count("sleep"), 1)
                terminal.keys("G")
                terminal.wait(lambda: terminal.location(), ["ops", window, server.sidebar(window)])
                terminal.wait(lambda: "▸ logs" in terminal.frame())
                self.assertEqual(terminal.frame().count("sleep"), 1)
                terminal.keys("+")
                terminal.wait(lambda: terminal.frame().count("sleep"), 2)
                terminal.keys("j-")
                terminal.wait(lambda: "logs" in next(line for line in terminal.frame().splitlines() if line.startswith("▶")))
                terminal.wait(lambda: terminal.frame().count("sleep"), 1)
                terminal.keys("+")
                terminal.wait(lambda: terminal.frame().count("sleep"), 2)
                server.wait(lambda: len(server.api("snapshot", pane=server.sidebar(window))["view_state"].get("expanded_windows", [])), 2)
                server.api("reload-views")
                terminal.wait(lambda: terminal.frame().count("sleep"), 2)
                server.t("new-window", "-d", "-t", "ops", "-n", "fresh", "sleep 86400")
                terminal.wait(lambda: "▸ fresh" in terminal.frame())
                self.assertEqual(terminal.frame().count("sleep"), 2)
                terminal.keys("G")
                terminal.wait(lambda: "fresh" in next(line for line in terminal.frame().splitlines() if line.startswith("▶")))
                terminal.keys("+")
                terminal.wait(lambda: terminal.frame().count("sleep"), 3)
            finally:
                if terminal is not None:
                    terminal.close()
                server.close()


if __name__ == "__main__":
    unittest.main()
