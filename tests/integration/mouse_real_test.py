from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from coordinator_test import Harness
from live_preview_real_test import Terminal


class MouseNavigation(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="binder-mouse-")
        self.addCleanup(temporary.cleanup)
        self.server = Harness(Path(temporary.name))
        self.addCleanup(self.server.close)
        self.server.t("set-option", "-g", "mouse", "on")
        self.server.t("set-option", "-g", "default-terminal", "tmux-256color")
        self.other = self.server.t("new-session", "-dP", "-F", "#{pane_id}", "-s", "ops", "-n", "logs", "sleep 86400")
        self.window = self.server.t("display-message", "-pt", self.other, "#{window_id}")
        self.server.t("set-option", "-g", "@tmux_pane_tree_session_order", "work,ops")
        self.terminal = Terminal(self.server)
        self.addCleanup(self.terminal.close)
        # Stop views while their coordinator is alive, avoiding late reconnects.
        self.addCleanup(self.server.api, "close")
        self.terminal.wait(lambda: self.server.t("display-message", "-pt", self.server.main, "#{session_attached}"), "1")
        self.terminal.keys("\x02t")
        self.terminal.wait(lambda: bool(self.server.sidebar(self.server.window)))
        self.terminal.wait(lambda: "logs" in self.frame())

    def frame(self, escapes: bool = False) -> str:
        pane = self.server.sidebar(self.terminal.location()[1])
        return self.server.t("capture-pane", "-pet" if escapes else "-pt", pane)

    def row(self, label: str) -> int:
        return next(index + 1 for index, line in enumerate(self.frame().splitlines()) if label in line)

    def click(self, row: int, column: int = 8) -> None:
        self.terminal.keys(f"\x1b[<0;{column};{row}M\x1b[<0;{column};{row}m")

    def test_single_click_previews_and_double_click_focuses_across_views(self):
        self.server.api("update-state", pane=self.other, event={"app": "claude", "status": "needs-input"})
        row = self.row("logs")
        self.click(row)
        self.terminal.wait(lambda: self.terminal.location(), ["ops", self.window, self.server.sidebar(self.window)])
        self.terminal.wait(lambda: "logs" in next(line for line in self.frame().splitlines() if line.startswith("▶")))
        self.assertEqual(self.terminal.main(), self.other)
        self.assertEqual(self.server.api("doctor")["agents"][self.other]["status"], "needs-input")
        self.click(row)
        self.terminal.wait(lambda: self.terminal.location()[2], self.other)
        self.assertEqual(self.server.api("doctor")["agents"][self.other]["status"], "idle")

    def test_hover_marks_the_label_without_selecting_or_focusing(self):
        location = self.terminal.location()
        selected = next(line for line in self.frame().splitlines() if line.startswith("▶"))
        self.terminal.keys(f"\x1b[<35;8;{self.row('logs')}M")

        def underlined():
            line = next(line for line in self.frame(True).splitlines() if "logs" in line)
            return bool(re.search(r"\x1b\[[0-9;]*4(?:;[0-9;]*)?m[^\x1b]*logs", line))

        self.terminal.wait(underlined)
        self.assertEqual(self.terminal.location(), location)
        self.assertEqual(next(line for line in self.frame().splitlines() if line.startswith("▶")), selected)
        self.terminal.wait(underlined, False)

    def test_session_headings_ignore_hover_and_mouse_clicks(self):
        self.terminal.keys("\x02T")
        sidebar = self.server.sidebar(self.server.window)
        self.terminal.wait(lambda: self.terminal.location()[2], sidebar)
        location = self.terminal.location()
        selected = next(line for line in self.frame().splitlines() if line.startswith("▶"))
        row = self.row("ops")
        self.terminal.keys(f"\x1b[<35;8;{row}M")
        self.terminal.drain(.1)
        heading = next(line for line in self.frame(True).splitlines() if "ops" in line)
        self.assertNotRegex(heading, r"\x1b\[[0-9;]*4(?:;[0-9;]*)?m[^\x1b]*ops")
        for _ in range(2):
            self.click(row)
            self.terminal.drain(.1)
            self.assertEqual(self.terminal.location(), location)
            self.assertEqual(next(line for line in self.frame().splitlines() if line.startswith("▶")), selected)
        self.terminal.output.clear()
        self.terminal.keys(f"\x1b[<2;8;{row}M\x1b[<2;8;{row}m")
        self.terminal.drain(.15)
        self.assertNotIn(b"Detach Session", self.terminal.output)
        self.assertNotIn(b"New Session", self.terminal.output)
        self.assertEqual(self.terminal.location(), location)

    def test_double_click_in_one_input_batch_activates_the_clicked_row(self):
        row = self.row("logs")
        click = f"\x1b[<0;8;{row}M\x1b[<0;8;{row}m"
        self.terminal.keys(click + click)
        self.terminal.wait(lambda: self.terminal.location(), ["ops", self.window, self.other])

    def test_single_click_without_preview_selects_a_pane_and_keeps_attention(self):
        self.server.t("set-option", "-g", "@tmux_pane_tree_live_preview", "off")
        self.server.api("update-state", pane=self.other, event={"app": "claude", "status": "needs-input"})
        self.terminal.keys("\x02T")
        self.terminal.wait(lambda: self.terminal.location()[2], self.server.sidebar(self.server.window))
        self.terminal.keys("/claude\r\x1bgg")
        self.terminal.wait(lambda: "claude" in self.frame())
        row = self.row("claude")
        self.click(row)
        self.terminal.wait(lambda: self.terminal.location()[2], self.server.sidebar(self.server.window))
        self.terminal.wait(lambda: "claude" in next(line for line in self.frame().splitlines() if line.startswith("▶")))
        self.assertEqual(self.terminal.location()[0], "work")
        self.assertEqual(self.server.api("doctor")["agents"][self.other]["status"], "needs-input")
        self.click(row)
        self.terminal.wait(lambda: self.terminal.location(), ["ops", self.window, self.other])
        self.assertEqual(self.server.api("doctor")["agents"][self.other]["status"], "idle")

    def test_footer_click_does_not_activate_and_right_click_still_opens_menu(self):
        self.terminal.keys("\x02T")
        sidebar = self.server.sidebar(self.server.window)
        self.terminal.wait(lambda: self.terminal.location()[2], sidebar)
        self.click(29)
        self.click(29)
        self.assertEqual(self.terminal.location()[2], sidebar)
        self.terminal.output.clear()
        row = self.row("logs")
        self.terminal.keys(f"\x1b[<2;8;{row}M\x1b[<2;8;{row}m")
        self.terminal.wait(lambda: b"Rename" in self.terminal.output)

    def test_scrolled_click_stays_under_the_pointer_during_preview(self):
        targets = {}
        for index in range(30):
            name = f"browse{index:02}"
            targets[name] = self.server.t("new-window", "-dP", "-F", "#{window_id}|#{pane_id}", "-t", "work", "-n", name, "sleep 86400").split("|")
        self.terminal.wait(lambda: self.server.api("doctor")["views"], len(targets) + 2)
        self.terminal.keys("\x02T")
        self.terminal.wait(lambda: self.terminal.location()[2], self.server.sidebar(self.server.window))
        self.terminal.wait(lambda: "browse20" in self.frame())
        self.terminal.keys("\x1b[<65;8;6M")
        self.terminal.wait(lambda: "editor" not in self.frame())
        row = 25
        name = re.search(r"browse\d+", self.frame().splitlines()[row - 1]).group()
        window, pane = targets[name]
        self.terminal.wait(lambda: bool(self.server.sidebar(window)))
        sidebar = self.server.sidebar(window)
        self.click(row)
        self.terminal.wait(lambda: self.terminal.location()[1:], [window, sidebar])
        self.assertIn(name, self.frame().splitlines()[row - 1])
        self.click(row)
        self.terminal.wait(lambda: self.terminal.location()[2], pane)


if __name__ == "__main__":
    unittest.main()
