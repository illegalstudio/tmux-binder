from __future__ import annotations

import fcntl
import os
import signal
import struct
import tempfile
import termios
import unittest
import re
from pathlib import Path

from coordinator_test import Harness
from live_preview_real_test import Terminal


class SidebarRendering(unittest.TestCase):
    def test_selection_stays_distinct_on_marked_windows(self):
        with tempfile.TemporaryDirectory(prefix="binder-highlight-cursor-") as directory:
            server = Harness(Path(directory))
            terminal = None
            try:
                other = server.t("new-window", "-dP", "-F", "#{window_id}", "-t", "work", "-n", "notes", "sleep 86400")
                server.t("set-option", "-g", "default-terminal", "tmux-256color")
                server.t("set-option", "-g", "assume-paste-time", "0")
                server.t("set-option", "-gw", "window-status-current-style", "fg=white,bg=blue")
                terminal = Terminal(server)
                terminal.wait(lambda: server.t("display-message", "-pt", server.main, "#{session_attached}"), "1")
                terminal.keys("\x02t")
                terminal.wait(lambda: bool(server.sidebar(other)))
                terminal.keys("\x02T")
                terminal.wait(lambda: terminal.location()[2], server.sidebar(server.window))
                terminal.wait(lambda: "▸ editor" in terminal.frame())

                def codes(label):
                    line = next(line for line in server.t("capture-pane", "-ept", server.sidebar(terminal.location()[1])).splitlines() if label in line)
                    return {code for group in re.findall(r"\x1b\[([0-9;]*)m", line) for code in group.split(";")}

                terminal.keys("u")
                terminal.wait(lambda: {"44", "4"}.issubset(codes("editor")))
                self.assertNotIn("47", codes("editor"))
                terminal.keys("j")
                terminal.wait(lambda: terminal.location()[1], other)
                terminal.wait(lambda: "47" in codes("editor") and "44" in codes("notes"))
                terminal.keys("u")
                terminal.wait(lambda: {"44", "4"}.issubset(codes("notes")))
                self.assertIn("47", codes("editor"))
                terminal.keys("k")
                terminal.wait(lambda: terminal.location()[1], server.window)
                terminal.wait(lambda: {"44", "4"}.issubset(codes("editor")) and "47" in codes("notes"))
                terminal.keys("\r")
                terminal.wait(lambda: terminal.location()[2], server.main)
                terminal.wait(lambda: "47" in codes("editor") and "47" in codes("notes"))
                terminal.keys("\x02T")
                terminal.wait(lambda: terminal.location()[2], server.sidebar(server.window))
                terminal.wait(lambda: {"44", "4"}.issubset(codes("editor")))
                terminal.keys("u")
                terminal.wait(lambda: "44" in codes("editor") and "4" not in codes("editor"))
                self.assertIn("47", codes("notes"))
                self.assertEqual(server.api("snapshot", pane=server.sidebar(server.window))["highlighted_windows"], [other])
            finally:
                if terminal is not None:
                    terminal.close()
                server.close()

    def test_window_marks_are_shared_between_linked_sessions_and_removed_on_window_exit(self):
        with tempfile.TemporaryDirectory(prefix="binder-highlight-linked-") as directory:
            server = Harness(Path(directory))
            terminal = None
            try:
                server.t("new-session", "-d", "-s", "ops", "-n", "logs", "sleep 86400")
                server.t("link-window", "-s", server.window, "-t", "ops:5")
                server.t("select-window", "-t", "ops:5")
                server.t("set-option", "-g", "default-terminal", "tmux-256color")
                server.t("set-option", "-g", "assume-paste-time", "0")
                server.t("set-option", "-gw", "pane-active-border-style", "fg=blue")
                server.t("set-option", "-gw", "window-status-current-style", "fg=white,bg=blue")
                server.t("set-option", "-g", "@tmux_pane_tree_session_order", "work,ops")
                terminal = Terminal(server)
                terminal.wait(lambda: server.t("display-message", "-pt", server.main, "#{session_attached}"), "1")
                terminal.keys("\x02t")
                terminal.wait(lambda: bool(server.sidebar(server.window)))
                terminal.keys("\x02T")
                sidebar = server.sidebar(server.window)
                terminal.wait(lambda: terminal.location()[2], sidebar)
                terminal.wait(lambda: "▸ editor" in terminal.frame())
                client = server.t("list-clients", "-F", "#{client_name}")

                def highlighted_sessions():
                    current = ""
                    found = []
                    for line in server.t("capture-pane", "-ept", terminal.location()[2]).splitlines():
                        plain = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
                        if plain in {"work", "ops"}:
                            current = plain
                        codes = {code for group in re.findall(r"\x1b\[([0-9;]*)m", line) for code in group.split(";")}
                        if "editor" in plain and ("47" in codes or {"44", "4"}.issubset(codes)):
                            found.append(current)
                    return found

                terminal.keys("u")
                terminal.wait(highlighted_sessions, ["work", "ops"])
                for session in ("ops", "work"):
                    server.t("switch-client", "-c", client, "-t", session)
                    terminal.wait(lambda: terminal.location(), [session, server.window, sidebar])
                    session_id = server.t("display-message", "-pt", session, "#{session_id}")
                    terminal.wait(lambda: server.api("snapshot", pane=sidebar)["session"], session_id)
                    self.assertEqual(highlighted_sessions(), ["work", "ops"])
                terminal.keys("u")
                terminal.wait(highlighted_sessions, [])
                terminal.keys("u")
                terminal.wait(highlighted_sessions, ["work", "ops"])
                server.t("rename-window", "-t", server.window, "renamed")
                terminal.wait(lambda: "renamed" in terminal.frame())
                self.assertEqual(server.api("snapshot", pane=sidebar)["highlighted_windows"], [server.window])
                logs = server.t("display-message", "-pt", "ops:0", "#{window_id}")
                server.t("switch-client", "-c", client, "-t", "ops:0")
                terminal.wait(lambda: terminal.location()[1], logs)
                server.t("select-pane", "-t", server.sidebar(logs))
                terminal.wait(lambda: terminal.location()[2], server.sidebar(logs))
                server.t("unlink-window", "-t", "ops:5")
                server.api("refresh")
                self.assertEqual(server.api("snapshot", pane=server.sidebar(logs))["highlighted_windows"], [server.window])
                server.t("kill-window", "-t", server.window)
                terminal.wait(lambda: server.api("snapshot", pane=server.sidebar(logs))["highlighted_windows"], [])
            finally:
                if terminal is not None:
                    terminal.close()
                server.close()

    def test_u_marks_multiple_windows_through_focus_navigation_and_reload(self):
        with tempfile.TemporaryDirectory(prefix="binder-window-highlight-") as directory:
            server = Harness(Path(directory))
            terminal = None
            try:
                from binder.development import reload

                other = server.t("new-session", "-dP", "-F", "#{pane_id}", "-s", "ops", "-n", "logs", "sleep 86400")
                window = server.t("display-message", "-pt", other, "#{window_id}")
                server.t("set-option", "-g", "default-terminal", "tmux-256color")
                server.t("set-option", "-g", "assume-paste-time", "0")
                server.t("set-option", "-gw", "pane-active-border-style", "fg=blue")
                server.t("set-option", "-gw", "window-status-current-style", "fg=white,bg=blue")
                server.t("set-option", "-g", "@tmux_pane_tree_session_order", "work,ops")
                terminal = Terminal(server)
                terminal.wait(lambda: server.t("display-message", "-pt", server.main, "#{session_attached}"), "1")
                terminal.keys("\x02t")
                terminal.wait(lambda: bool(server.sidebar(window)))
                terminal.keys("\x02T")
                terminal.wait(lambda: terminal.location()[2], server.sidebar(server.window))
                terminal.wait(lambda: "▸ editor" in terminal.frame())
                before = server.t("list-panes", "-a", "-F", "#{pane_id}|#{pane_pid}")

                def window_line(label):
                    return next(line for line in server.t("capture-pane", "-ept", server.sidebar(terminal.location()[1])).splitlines() if label in line)

                def highlighted(label):
                    codes = {code for group in re.findall(r"\x1b\[([0-9;]*)m", window_line(label)) for code in group.split(";")}
                    return "47" in codes or {"44", "4"}.issubset(codes)

                self.assertFalse(highlighted("editor"))
                terminal.keys("u")
                terminal.wait(lambda: highlighted("editor"))
                terminal.keys("l")
                terminal.wait(lambda: "▾ editor" in terminal.frame())
                terminal.keys("j")
                terminal.wait(lambda: "sleep" in next(line for line in terminal.frame().splitlines() if line.startswith("▶")))
                terminal.keys("u")
                terminal.wait(lambda: not highlighted("editor"))
                terminal.keys("u")
                terminal.wait(lambda: highlighted("editor"))
                terminal.keys("\r")
                terminal.wait(lambda: terminal.location()[2], server.main)
                terminal.wait(lambda: highlighted("editor"))
                terminal.keys("\x02T")
                terminal.wait(lambda: terminal.location()[2], server.sidebar(server.window))
                terminal.keys("G")
                terminal.wait(lambda: terminal.location()[1], window)
                terminal.wait(lambda: highlighted("editor") and not highlighted("logs"))
                terminal.keys("u")
                terminal.wait(lambda: highlighted("editor") and highlighted("logs"))
                marked = sorted([server.window, window])
                self.assertEqual(server.api("snapshot", pane=server.sidebar(server.window))["highlighted_windows"], marked)
                server.api("update-state", pane=other, event={"app": "claude", "status": "needs-input"})
                terminal.wait(lambda: "?" in window_line("logs"))
                self.assertIn("33", [code for codes in re.findall(r"\x1b\[([0-9;]*)m", window_line("logs").split("?")[0]) for code in codes.split(";")])
                reload(server.identity)
                terminal.wait(lambda: highlighted("editor") and highlighted("logs"))
                self.assertEqual(server.api("snapshot", pane=server.sidebar(window))["highlighted_windows"], marked)
                server.t("set-option", "-g", "@tmux_pane_tree_live_preview", "off")
                server.api("refresh")
                terminal.keys("gg")
                terminal.wait(lambda: "sleep" in next(line for line in terminal.frame().splitlines() if line.startswith("▶")))
                self.assertEqual(terminal.location()[1], window)
                terminal.keys("u")
                terminal.wait(lambda: not highlighted("editor") and highlighted("logs"))
                self.assertEqual(server.api("snapshot", pane=server.sidebar(window))["highlighted_windows"], [window])
                terminal.keys("G")
                terminal.wait(lambda: "logs" in next(line for line in terminal.frame().splitlines() if line.startswith("▶")))
                terminal.keys("u")
                terminal.wait(lambda: not highlighted("editor") and not highlighted("logs"))
                self.assertEqual(server.api("snapshot", pane=server.sidebar(server.window))["highlighted_windows"], [])
                terminal.keys("gg")
                terminal.wait(lambda: "sleep" in next(line for line in terminal.frame().splitlines() if line.startswith("▶")))
                terminal.keys("u")
                terminal.wait(lambda: highlighted("editor"))
                terminal.keys("\x02t")
                terminal.wait(lambda: server.sidebar(window), "")
                terminal.keys("\x02t")
                terminal.wait(lambda: bool(server.sidebar(window)))
                terminal.wait(lambda: highlighted("editor") and not highlighted("logs"))
                content = {line.split("|")[0]: line.split("|")[1] for line in before.splitlines()}
                self.assertEqual(server.t("display-message", "-pt", server.main, "#{pane_pid}"), content[server.main])
                self.assertEqual(server.t("display-message", "-pt", other, "#{pane_pid}"), content[other])
            finally:
                if terminal is not None:
                    terminal.close()
                server.close()

    def test_client_resize_redraws_labels_without_keyboard_input(self):
        with tempfile.TemporaryDirectory(prefix="binder-client-resize-") as directory:
            server = Harness(Path(directory))
            terminal = None
            try:
                server.t("set-option", "-g", "default-terminal", "tmux-256color")
                server.t("rename-window", "-t", server.window, "backend-window")
                terminal = Terminal(server)
                terminal.wait(lambda: server.t("display-message", "-pt", server.main, "#{session_attached}"), "1")
                terminal.keys("\x02t")
                terminal.wait(lambda: bool(server.sidebar(server.window)))
                sidebar = server.sidebar(server.window)
                terminal.wait(lambda: "backend" in server.t("capture-pane", "-pt", sidebar))
                content_pid = server.t("display-message", "-pt", server.main, "#{pane_pid}")

                for rows, columns in ((12, 36), (36, 154), (59, 249), (30, 120)):
                    with self.subTest(rows=rows, columns=columns):
                        fcntl.ioctl(terminal.master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
                        os.kill(terminal.client.pid, signal.SIGWINCH)
                        terminal.wait(lambda: server.t("display-message", "-pt", sidebar, "#{window_width}x#{window_height}"), f"{columns}x{rows - 1}")
                        terminal.wait(lambda: "backend" in server.t("capture-pane", "-pt", sidebar))
                        self.assertEqual(server.sidebar(server.window), sidebar)
                        self.assertEqual(server.t("display-message", "-pt", server.main, "#{pane_pid}"), content_pid)
                self.assertEqual(server.t("show-options", "-gqv", "@tmux_pane_tree_width"), "")
                self.assertEqual(server.t("display-message", "-pt", sidebar, "#{pane_width}"), "25")
            finally:
                if terminal is not None:
                    terminal.close()
                server.close()

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
                for pane, title in ((server.main, "✳ Claude Code"), (second, "codex"), (third, "claude")):
                    server.t("set-option", "-p", "-t", pane, "allow-set-title", "off")
                    server.t("select-pane", "-t", pane, "-T", title)
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
