from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from coordinator_test import Harness
from live_preview_real_test import Terminal


class WindowOrder(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="binder-window-order-")
        self.addCleanup(temporary.cleanup)
        self.server = Harness(Path(temporary.name))
        self.addCleanup(self.server.close)
        self.windows = [self.server.window]
        for index, name in ((4, "build"), (12, "logs")):
            self.windows.append(self.server.t("new-window", "-dP", "-F", "#{window_id}", "-t", f"work:{index}", "-n", name, "sleep 86400"))
        self.monitor = self.server.t("new-session", "-dP", "-F", "#{window_id}", "-s", "ops", "-n", "monitor", "sleep 86400")
        self.server.t("set-option", "-g", "@tmux_pane_tree_session_order", "work,ops")
        self.terminal = Terminal(self.server)
        self.addCleanup(self.terminal.close)
        self.addCleanup(self.server.api, "close")
        self.terminal.wait(lambda: self.server.t("display-message", "-pt", self.server.main, "#{session_attached}"), "1")

    def open(self):
        self.terminal.keys("\x02t")
        self.terminal.wait(lambda: bool(self.server.sidebar(self.server.window)))
        self.terminal.keys("\x02T")
        self.terminal.wait(lambda: self.terminal.location()[2], self.server.sidebar(self.server.window))
        self.terminal.wait(lambda: self.rendered_order("work"), ["editor", "build", "logs"])
        self.terminal.keys("gg")
        self.terminal.wait(lambda: "editor" in self.selected_line())

    def frame(self):
        return self.server.t("capture-pane", "-pt", self.server.sidebar(self.terminal.location()[1]))

    def selected_line(self):
        return next((line for line in self.frame().splitlines() if line.startswith("▶")), "")

    def rendered_order(self, session):
        active = False
        result = []
        for line in self.frame().splitlines():
            if line.strip() in {"work", "ops"}:
                active = line.strip() == session
            elif active and (match := re.search(r"\b(editor|build|logs|monitor)\b", line)):
                result.append(match[1])
        return result

    def native_order(self, session="work"):
        return self.server.t("list-windows", "-t", session, "-F", "#{window_id}").splitlines()

    def content_processes(self):
        records = self.server.t("list-panes", "-a", "-F", "#{pane_id}|#{pane_pid}|#{window_id}|#{@tmux_sidebar_owned}").splitlines()
        return {parts[0]: parts[1:3] for line in records if (parts := line.split("|"))[3] != "1"}

    def test_keyboard_reorders_without_wrapping_or_changing_focus_and_processes(self):
        self.open()
        before = self.content_processes()
        location = self.terminal.location()
        first, second, third = self.windows
        self.terminal.keys("KJ")
        self.terminal.wait(lambda: self.native_order(), [second, first, third])
        self.terminal.wait(lambda: self.rendered_order("work"), ["build", "editor", "logs"])
        self.terminal.keys("J")
        self.terminal.wait(lambda: self.native_order(), [second, third, first])
        self.terminal.keys("JK")
        self.terminal.wait(lambda: self.native_order(), [second, first, third])
        self.terminal.keys("K")
        self.terminal.wait(lambda: self.rendered_order("work"), ["editor", "build", "logs"])
        self.assertIn("editor", self.selected_line())
        self.assertEqual(self.terminal.location(), location)
        self.assertEqual(self.native_order("ops"), [self.monitor])
        self.assertEqual(self.content_processes(), before)
        self.assertEqual(self.server.t("list-windows", "-t", "work", "-F", "#{window_index}").splitlines(), ["0", "4", "12"])

    def test_selected_pane_moves_its_window_and_keeps_the_branch_expanded(self):
        self.open()
        self.terminal.keys("lj")
        self.terminal.wait(lambda: "sleep" in self.selected_line())
        before = self.content_processes()
        location = self.terminal.location()
        self.terminal.keys("J")
        self.terminal.wait(lambda: self.rendered_order("work"), ["build", "editor", "logs"])
        self.assertIn("sleep", self.selected_line())
        self.assertIn("▾ editor", self.frame())
        self.assertEqual(self.terminal.location(), location)
        self.assertEqual(self.content_processes(), before)

    def test_menu_reorders_a_nonactive_window_without_live_preview(self):
        self.server.t("set-option", "-g", "@tmux_pane_tree_live_preview", "off")
        self.open()
        location = self.terminal.location()
        self.terminal.keys("j")
        self.terminal.wait(lambda: "build" in self.selected_line())
        self.assertEqual(self.terminal.location(), location)
        self.terminal.output.clear()
        self.terminal.keys("m")
        self.terminal.wait(lambda: b"Move Window Up" in self.terminal.output and b"Move Window Down" in self.terminal.output)
        self.terminal.keys("K")
        self.terminal.wait(lambda: self.rendered_order("work"), ["build", "editor", "logs"])
        self.assertIn("build", self.selected_line())
        self.assertEqual(self.terminal.location(), location)

    def test_linked_windows_keep_session_local_order_and_observe_native_swaps(self):
        first, second, third = self.windows
        self.server.t("link-window", "-d", "-s", second, "-t", "ops:6")
        self.server.t("link-window", "-d", "-s", first, "-t", "ops:14")
        self.open()
        self.terminal.wait(lambda: self.rendered_order("work"), ["editor", "build", "logs"])
        self.terminal.wait(lambda: self.rendered_order("ops"), ["monitor", "build", "editor"])
        before = self.content_processes()
        self.terminal.keys("J")
        self.terminal.wait(lambda: self.native_order(), [second, first, third])
        self.assertEqual(self.native_order("ops"), [self.monitor, second, first])
        self.terminal.wait(lambda: self.rendered_order("work"), ["build", "editor", "logs"])
        self.server.t("swap-window", "-d", "-s", "ops:" + first, "-t", "ops:" + second)
        self.terminal.wait(lambda: self.rendered_order("ops"), ["monitor", "editor", "build"])
        self.assertEqual(self.native_order(), [second, first, third])
        self.assertEqual(self.content_processes(), before)

    def test_reordering_skips_filtered_windows(self):
        self.open()
        for window in (self.windows[0], self.windows[2]):
            pane = next(pane for pane, (_, parent) in self.content_processes().items() if parent == window)
            self.server.t("select-pane", "-t", pane, "-T", "visible-project")
        self.server.t("set-option", "-g", "@tmux_pane_tree_filter", "visible-project")
        self.server.api("refresh")
        self.terminal.wait(lambda: self.rendered_order("work"), ["editor", "logs"])
        self.terminal.keys("J")
        self.terminal.wait(lambda: self.rendered_order("work"), ["logs", "editor"])
        self.assertEqual(self.native_order(), self.windows[::-1])
        self.assertIn("editor", self.selected_line())

    def test_custom_shortcuts_reorder_windows(self):
        self.server.t("set-option", "-g", "@tmux_pane_tree_move_window_up_shortcut", "U")
        self.server.t("set-option", "-g", "@tmux_pane_tree_move_window_down_shortcut", "D")
        self.open()
        self.terminal.keys("D")
        self.terminal.wait(lambda: self.rendered_order("work"), ["build", "editor", "logs"])
        self.terminal.keys("U")
        self.terminal.wait(lambda: self.rendered_order("work"), ["editor", "build", "logs"])
        self.assertIn("editor", self.selected_line())

    def test_movement_after_navigation_uses_the_destination_window(self):
        self.open()
        self.terminal.keys("jK")
        self.terminal.wait(lambda: self.rendered_order("work"), ["build", "editor", "logs"])
        self.terminal.wait(lambda: self.terminal.location()[1], self.windows[1])
        self.assertEqual(self.terminal.location()[2], self.server.sidebar(self.windows[1]))
        self.assertIn("build", self.selected_line())

    def test_reordering_two_nonactive_windows_keeps_the_current_window(self):
        self.server.t("set-option", "-g", "@tmux_pane_tree_live_preview", "off")
        self.open()
        location = self.terminal.location()
        self.terminal.keys("jJ")
        self.terminal.wait(lambda: self.rendered_order("work"), ["editor", "logs", "build"])
        self.assertIn("build", self.selected_line())
        self.assertEqual(self.terminal.location(), location)
        self.terminal.keys("K")
        self.terminal.wait(lambda: self.rendered_order("work"), ["editor", "build", "logs"])
        self.assertEqual(self.terminal.location(), location)


if __name__ == "__main__":
    unittest.main()
