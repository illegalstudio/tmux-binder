from __future__ import annotations

import os
import signal
import tempfile
import unittest
from pathlib import Path

from coordinator_test import Harness
from live_preview_real_test import Terminal


class ContentExit(unittest.TestCase):
    def test_last_content_exit_moves_to_a_session_with_live_content(self):
        with tempfile.TemporaryDirectory(prefix="binder-content-exit-") as directory:
            server = Harness(Path(directory))
            terminal = None
            try:
                empty = server.t("new-session", "-dP", "-F", "#{pane_id}", "-s", "empty", "sleep 86400")
                server.t("set-option", "-p", "-t", empty, "@tmux_sidebar_owned", "1")
                target = server.t("new-session", "-dP", "-F", "#{pane_id}", "-s", "ops", "-n", "survivor", "printf 'LIVE CONTENT\\n'; sleep 86400")
                target_pid = server.t("display-message", "-pt", target, "#{pane_pid}")
                server.t("set-option", "-g", "detach-on-destroy", "on")
                terminal = Terminal(server)
                terminal.wait(lambda: server.t("display-message", "-pt", server.main, "#{session_attached}"), "1")
                terminal.keys("\x02t")
                terminal.wait(lambda: bool(server.sidebar(server.window)))
                terminal.wait(lambda: terminal.location()[2], server.main)
                os.kill(int(server.t("display-message", "-pt", server.main, "#{pane_pid}")), signal.SIGTERM)
                terminal.wait(lambda: terminal.location()[0], "ops")
                terminal.wait(lambda: terminal.location()[2], target)
                terminal.wait(lambda: "LIVE CONTENT" in terminal.frame())
                self.assertEqual(server.t("display-message", "-pt", target, "#{pane_pid}"), target_pid)
                self.assertNotIn(server.window, server.api("doctor")["window_main"])
                self.assertIn("empty", server.t("list-sessions", "-F", "#{session_name}").splitlines())
            finally:
                if terminal is not None:
                    terminal.close()
                server.close()

    def test_exiting_one_of_multiple_content_panes_keeps_the_window(self):
        with tempfile.TemporaryDirectory(prefix="binder-content-exit-") as directory:
            server = Harness(Path(directory))
            terminal = None
            try:
                target = server.t("split-window", "-dP", "-F", "#{pane_id}", "-t", server.main, "-v", "sleep 86400")
                terminal = Terminal(server)
                terminal.wait(lambda: server.t("display-message", "-pt", server.main, "#{session_attached}"), "1")
                terminal.keys("\x02t")
                terminal.wait(lambda: bool(server.sidebar(server.window)))
                os.kill(int(server.t("display-message", "-pt", server.main, "#{pane_pid}")), signal.SIGTERM)
                terminal.wait(lambda: terminal.location()[2], target)
                self.assertEqual(terminal.location()[1], server.window)
            finally:
                if terminal is not None:
                    terminal.close()
                server.close()

    def test_last_content_exit_prefers_another_window_in_the_same_session(self):
        with tempfile.TemporaryDirectory(prefix="binder-content-exit-") as directory:
            server = Harness(Path(directory))
            terminal = None
            try:
                target = server.t("new-window", "-dP", "-F", "#{pane_id}", "-t", "work", "-n", "survivor", "sleep 86400")
                terminal = Terminal(server)
                terminal.wait(lambda: server.t("display-message", "-pt", server.main, "#{session_attached}"), "1")
                terminal.keys("\x02t")
                terminal.wait(lambda: bool(server.sidebar(server.window)))
                os.kill(int(server.t("display-message", "-pt", server.main, "#{pane_pid}")), signal.SIGTERM)
                terminal.wait(lambda: terminal.location()[2], target)
                self.assertEqual(terminal.location()[0], "work")
            finally:
                if terminal is not None:
                    terminal.close()
                server.close()

    def test_last_content_on_the_server_detaches_and_shuts_down(self):
        with tempfile.TemporaryDirectory(prefix="binder-content-exit-") as directory:
            server = Harness(Path(directory))
            terminal = None
            try:
                terminal = Terminal(server)
                terminal.wait(lambda: server.t("display-message", "-pt", server.main, "#{session_attached}"), "1")
                terminal.keys("\x02t")
                terminal.wait(lambda: bool(server.sidebar(server.window)))
                os.kill(int(server.t("display-message", "-pt", server.main, "#{pane_pid}")), signal.SIGTERM)
                terminal.client.wait(timeout=6)
                server.wait(lambda: not server.identity.endpoint.exists())
            finally:
                if terminal is not None:
                    terminal.close()
                server.close()


if __name__ == "__main__":
    unittest.main()
