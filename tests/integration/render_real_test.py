from __future__ import annotations

import tempfile
import unittest
import re
from pathlib import Path

from coordinator_test import Harness
from live_preview_real_test import Terminal


class SidebarRendering(unittest.TestCase):
    def test_explicit_sidebar_resize_wins_over_coalesced_window_resizes(self):
        with tempfile.TemporaryDirectory(prefix="binder-manual-width-") as directory:
            server = Harness(Path(directory))
            terminal = None
            try:
                server.t("set-option", "-g", "@tmux_pane_tree_width", "30")
                terminal = Terminal(server)
                terminal.wait(lambda: server.t("display-message", "-pt", server.main, "#{session_attached}"), "1")
                terminal.keys("\x02t")
                terminal.wait(lambda: bool(server.sidebar(server.window)))
                sidebar = server.sidebar(server.window)
                terminal.wait(lambda: "editor" in server.t("capture-pane", "-pt", sidebar))
                server.t("resize-window", "-t", server.window, "-x", "32", ";", "resize-window", "-t", server.window, "-x", "120", ";", "resize-pane", "-t", sidebar, "-x", "41")
                terminal.wait(lambda: server.t("show-options", "-gqv", "@tmux_pane_tree_width"), "41")
                self.assertEqual(server.t("display-message", "-pt", sidebar, "#{pane_width}"), "41")
            finally:
                if terminal is not None:
                    terminal.close()
                server.close()

    def test_selected_activity_keeps_its_color_and_width_after_window_resizes(self):
        with tempfile.TemporaryDirectory(prefix="binder-selected-status-") as directory:
            server = Harness(Path(directory))
            terminal = None
            try:
                other = server.t("new-session", "-dP", "-F", "#{pane_id}", "-s", "ops", "-n", "activity", "sleep 86400")
                window = server.t("display-message", "-pt", other, "#{window_id}")
                server.t("set-option", "-g", "default-terminal", "tmux-256color")
                server.t("set-option", "-gw", "pane-active-border-style", "fg=blue")
                server.t("set-option", "-g", "@tmux_pane_tree_width", "30")
                server.t("set-option", "-g", "@tmux_pane_tree_session_order", "work,ops")
                terminal = Terminal(server)
                terminal.wait(lambda: server.t("display-message", "-pt", server.main, "#{session_attached}"), "1")
                terminal.keys("\x02t")
                terminal.wait(lambda: bool(server.sidebar(window)))
                terminal.keys("\x02T")
                terminal.wait(lambda: terminal.location()[2], server.sidebar(server.window))
                server.api("update-state", pane=other, event={"app": "codex", "status": "running"})
                terminal.wait(lambda: "●" in terminal.frame())
                terminal.keys("G")
                terminal.wait(lambda: terminal.location()[1], window)

                def badge_style():
                    line = next(line for line in server.t("capture-pane", "-ept", server.sidebar(window)).splitlines() if "●" in line)
                    foreground = background = -1
                    reverse = False
                    for escape in re.findall(r"\x1b\[([0-9;]*)m", line.split("●")[0]):
                        for code in map(int, escape.split(";") if escape else ["0"]):
                            if code == 0:
                                foreground = background = -1
                                reverse = False
                            elif code == 7:
                                reverse = True
                            elif code == 27:
                                reverse = False
                            elif 30 <= code <= 37:
                                foreground = code - 30
                            elif 40 <= code <= 47:
                                background = code - 40
                            elif code == 39:
                                foreground = -1
                            elif code == 49:
                                background = -1
                    return (foreground, background, reverse)

                terminal.wait(badge_style, (6, 4, False))
                sidebar = server.sidebar(window)
                server.t("resize-window", "-t", window, "-x", "32", ";", "resize-window", "-t", window, "-x", "120")
                terminal.wait(lambda: server.t("display-message", "-pt", sidebar, "#{pane_width}"), "30")
                self.assertEqual(server.t("show-options", "-gqv", "@tmux_pane_tree_width"), "30")
                for keys, expected in (("gg", server.window), ("G", window)):
                    terminal.keys(keys)
                    terminal.wait(lambda: terminal.location()[1], expected)
                    self.assertEqual(server.t("display-message", "-pt", server.sidebar(expected), "#{pane_width}"), "30")
                terminal.wait(badge_style, (6, 4, False))
            finally:
                if terminal is not None:
                    terminal.close()
                server.close()

    def test_window_summaries_keep_counts_visible_when_expanded_and_filtered(self):
        with tempfile.TemporaryDirectory(prefix="binder-agent-summary-") as directory:
            server = Harness(Path(directory))
            terminal = None
            try:
                second = server.t("split-window", "-dP", "-F", "#{pane_id}", "-t", server.main, "-v", "sleep 86400")
                third = server.t("split-window", "-dP", "-F", "#{pane_id}", "-t", second, "-v", "sleep 86400")
                server.t("rename-window", "-t", server.window, "backend-with-a-name-that-does-not-fit")
                server.t("set-option", "-g", "@tmux_pane_tree_width", "34")
                for pane, app in ((server.main, "claude"), (second, "codex"), (third, "claude")):
                    server.t("set-option", "-p", "-t", pane, "allow-set-title", "off")
                    server.t("select-pane", "-t", pane, "-T", app)
                terminal = Terminal(server)
                terminal.wait(lambda: server.t("display-message", "-pt", server.main, "#{session_attached}"), "1")
                terminal.keys("\x02t")
                terminal.wait(lambda: bool(server.sidebar(server.window)))
                terminal.keys("\x02T")
                terminal.wait(lambda: terminal.location()[2], server.sidebar(server.window))
                terminal.wait(lambda: "backend" in terminal.frame())
                for pane, app, status in ((server.main, "claude", "running"), (second, "codex", "running"), (third, "claude", "needs-input")):
                    server.api("update-state", pane=pane, event={"app": app, "status": status})

                def selected():
                    return next((line for line in terminal.frame().splitlines() if line.startswith("▶")), "")

                terminal.wait(lambda: selected().endswith("?1 ●2"))
                self.assertNotIn("claude", terminal.frame())
                terminal.keys("l")
                terminal.wait(lambda: terminal.frame().count("claude"), 2)
                self.assertTrue(selected().endswith("?1 ●2"))
                self.assertEqual(terminal.frame().count("●"), 3)
                self.assertEqual(terminal.frame().count("?"), 2)
                self.assertNotIn("C claude", terminal.frame())

                server.api("update-state", pane=server.main, event={"app": "claude", "status": "done"})
                terminal.wait(lambda: selected().endswith("? ● ✓"))
                server.api("update-state", pane=third, event={"app": "claude", "status": "idle"})
                terminal.wait(lambda: selected().endswith("● ✓"))
                server.api("update-state", pane=server.main, event={"app": "claude", "status": "running"})
                terminal.wait(lambda: selected().endswith("●2"))
                server.t("set-option", "-g", "@tmux_pane_tree_filter", "codex")
                terminal.wait(lambda: "claude" not in terminal.frame())
                self.assertTrue(selected().endswith("●2"))
                self.assertEqual(terminal.frame().count("codex"), 1)

                server.t("set-option", "-g", "@tmux_pane_tree_filter", "")
                terminal.wait(lambda: terminal.frame().count("claude"), 2)
                server.api("update-state", pane=third, event={"app": "claude", "status": "needs-input"})
                server.t("set-option", "-g", "@tmux_pane_tree_width", "18")
                terminal.wait(lambda: server.t("display-message", "-pt", server.sidebar(server.window), "#{pane_width}"), "18")
                terminal.wait(lambda: selected().endswith("?1 ●2"))
                self.assertTrue(selected().startswith("▶  ▾ "))
                server.t("set-option", "-g", "@tmux_pane_tree_icon_theme", "nerdfont")
                server.api("refresh")
                self.assertTrue(all(not row["icon"] for row in server.api("snapshot")["rows"] if row["kind"] == "pane"))
                terminal.wait(lambda: selected().endswith("?1 ●2"))
                terminal.keys("h")
                terminal.wait(lambda: "claude" not in terminal.frame())
                self.assertTrue(selected().endswith("?1 ●2"))
            finally:
                try:
                    if terminal is not None:
                        terminal.close()
                finally:
                    server.close()

    def test_long_names_keep_attention_visible_and_hints_follow_selection(self):
        with tempfile.TemporaryDirectory(prefix="binder-render-") as directory:
            server = Harness(Path(directory))
            terminal = None
            try:
                server.t("set-option", "-g", "mouse", "on")
                server.t("set-option", "-g", "default-terminal", "tmux-256color")
                server.t("set-option", "-gw", "pane-active-border-style", "fg=blue")
                server.t("set-option", "-gw", "pane-border-style", "fg=brightblack")
                server.t("set-option", "-g", "status-style", "bg=default,fg=default")
                server.t("rename-window", "-t", server.window, "Long window name with 界 and more text")
                terminal = Terminal(server)
                terminal.wait(lambda: server.t("display-message", "-pt", server.main, "#{session_attached}"), "1")
                terminal.keys("\x02t")
                terminal.wait(lambda: bool(server.sidebar(server.window)))
                terminal.keys("\x02T")
                terminal.wait(lambda: terminal.location()[2], server.sidebar(server.window))
                server.api("update-state", pane=server.main, event={"app": "claude", "status": "needs-input"})

                def selected():
                    return next((line for line in terminal.frame().splitlines() if line.startswith("▶")), "")

                terminal.wait(lambda: selected().endswith("?"))
                terminal.wait(lambda: "= expand" in terminal.frame())
                terminal.keys("=")
                terminal.wait(lambda: "- collapse" in terminal.frame())
                terminal.keys("j")
                terminal.wait(lambda: "Enter focus" in terminal.frame())
                self.assertTrue(selected().endswith("?"))
                terminal.keys("\r")
                terminal.wait(lambda: terminal.location()[2], server.main)
                terminal.wait(lambda: "Enter focus" not in server.t("capture-pane", "-pt", server.sidebar(server.window)))
                self.assertEqual(server.api("doctor")["agents"][server.main]["status"], "idle")
                terminal.keys("\x02T")
                terminal.wait(lambda: "Enter focus" in terminal.frame())
                terminal.output.clear()
                terminal.keys("\x1b[<2;6;29M\x1b[<2;6;29m")
                terminal.wait(lambda: b"New Session" in terminal.output)
                self.assertNotIn(b"Rename", terminal.output)
            finally:
                try:
                    if terminal is not None:
                        terminal.close()
                finally:
                    server.close()


if __name__ == "__main__":
    unittest.main()
