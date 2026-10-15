from __future__ import annotations

import os
import signal
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from coordinator_test import Harness, ROOT
from live_preview_real_test import Terminal


class ContentExit(unittest.TestCase):
    def test_content_exit_during_sidebar_creation_is_not_lost(self):
        with tempfile.TemporaryDirectory(prefix="binder-content-exit-") as directory:
            server = Harness(Path(directory))
            terminal = None
            try:
                target = server.t("new-session", "-dP", "-F", "#{pane_id}", "-s", "zops", "-n", "survivor", "printf 'SURVIVING CONTENT\\n'; sleep 86400")
                window = server.t("display-message", "-pt", target, "#{window_id}")
                hook = f"if-shell -F '#{{==:#{{window_id}},{window}}}' 'kill-pane -t {server.main}'"
                server.t("set-hook", "-g", "after-split-window[999]", hook)
                terminal = Terminal(server)
                terminal.wait(lambda: server.t("display-message", "-pt", server.main, "#{session_attached}"), "1")
                terminal.keys("\x02t")
                terminal.wait(lambda: terminal.location()[2], target)
                terminal.wait(lambda: "SURVIVING CONTENT" in terminal.frame())
                server.wait(lambda: server.window not in server.api("doctor")["window_main"])
                self.assertNotIn(server.window, server.t("list-windows", "-a", "-F", "#{window_id}").splitlines())
            finally:
                if terminal is not None:
                    terminal.close()
                server.close()

    def test_project_selector_restores_content_in_an_existing_sidebar_only_session(self):
        with tempfile.TemporaryDirectory(prefix="binder-project-selector-") as directory:
            root = Path(directory)
            project = root / "Developer" / "illegal.studio"
            project.mkdir(parents=True)
            socket = str(root / "s")
            env = {key: value for key, value in os.environ.items() if key not in {"TMUX", "TMUX_PANE"}}
            server = terminal = None
            try:
                subprocess.run(["tmux", "-S", socket, "-f", "/dev/null", "new-session", "-d", "-s", "illegal_studio", "-c", str(project), "sleep 86400"], env=env, check=True)
                subprocess.run(["tmux", "-S", socket, "set-option", "-p", "-t", "illegal_studio", "@tmux_sidebar_owned", "1"], env=env, check=True)
                server = Harness(root)
                server.t("set-option", "-g", "default-shell", "/bin/sh")
                server.t("set-option", "-g", "@tmux_pane_tree_width", "30")
                sidebar = server.sidebar(server.t("display-message", "-pt", "illegal_studio", "#{window_id}"))
                server.t("respawn-pane", "-k", "-t", sidebar, "exec " + shlex.join([sys.executable, str(ROOT / "scripts/ui/sidebar-ui.py")]))
                terminal = Terminal(server)
                terminal.wait(lambda: server.t("display-message", "-pt", server.main, "#{session_attached}"), "1")
                terminal.keys("\x02t")
                terminal.wait(lambda: bool(server.sidebar(server.window)))
                main_pid = server.t("display-message", "-pt", server.main, "#{pane_pid}")
                selector = root / "project-selector.sh"
                selector.write_text("#!/bin/sh\nsleep .1\nexec tmux switch-client -t =illegal_studio\n")
                server.t("bind-key", "P", "new-window", "/bin/sh " + shlex.quote(str(selector)))
                terminal.keys("\x02P")
                def at_project():
                    session, _, pane = terminal.location()
                    return session == "illegal_studio" and pane != sidebar
                terminal.wait(at_project)
                target = terminal.location()[2]
                self.assertEqual(server.t("display-message", "-pt", target, "#{pane_current_path}"), str(project))
                server.wait(lambda: server.t("display-message", "-pt", target, "#{pane_current_command}"), "sh")
                self.assertEqual(server.t("display-message", "-pt", sidebar, "#{pane_width}"), "30")
                self.assertEqual(server.t("display-message", "-pt", server.main, "#{pane_pid}"), main_pid)
                self.assertEqual(server.t("show-options", "-gqv", "@tmux_sidebar_enabled"), "1")
                server.wait(lambda: len(server.t("list-windows", "-a", "-F", "#{window_id}").splitlines()), 2)
                target_pid = server.t("display-message", "-pt", target, "#{pane_pid}")
                terminal.keys("\x02P")
                terminal.wait(lambda: terminal.location()[2], target)
                server.wait(lambda: len(server.t("list-windows", "-a", "-F", "#{window_id}").splitlines()), 2)
                self.assertEqual(server.t("display-message", "-pt", target, "#{pane_pid}"), target_pid)
                server.t("resize-pane", "-t", sidebar, "-x", "35")
                server.wait(lambda: server.t("show-options", "-gqv", "@tmux_pane_tree_width"), "35")
            finally:
                if terminal is not None:
                    terminal.close()
                if server is not None:
                    server.close()
                else:
                    subprocess.run(["tmux", "-S", socket, "kill-server"], env=env, capture_output=True)

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
                try:
                    terminal.wait(lambda: terminal.location()[0], "ops")
                except AssertionError as error:
                    panes = server.t("list-panes", "-a", "-F", "#{session_name}|#{window_id}|#{pane_id}|#{pane_current_command}|#{pane_pid}|#{@tmux_sidebar_owned}")
                    raise AssertionError(str(error) + "\nTopology:\n" + panes + "\nCoordinator: " + str(server.api("doctor"))) from error
                terminal.wait(lambda: terminal.location()[2], target)
                terminal.wait(lambda: "LIVE CONTENT" in terminal.frame())
                self.assertEqual(server.t("display-message", "-pt", target, "#{pane_pid}"), target_pid)
                server.wait(lambda: server.window not in server.api("doctor")["window_main"])
                self.assertNotIn(server.window, server.t("list-windows", "-a", "-F", "#{window_id}").splitlines())
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
