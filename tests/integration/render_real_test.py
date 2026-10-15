from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from coordinator_test import Harness
from live_preview_real_test import Terminal


class SidebarRendering(unittest.TestCase):
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

                terminal.wait(lambda: selected().endswith("❓"))
                terminal.wait(lambda: "= expand" in terminal.frame())
                terminal.keys("=")
                terminal.wait(lambda: "- collapse" in terminal.frame())
                terminal.keys("j")
                terminal.wait(lambda: "Enter focus" in terminal.frame())
                self.assertTrue(selected().endswith("❓"))
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
