from __future__ import annotations

import fcntl
import errno
import os
import pty
import select
import signal
import struct
import subprocess
import tempfile
import termios
import time
import threading
import unittest
from pathlib import Path

from coordinator_test import Harness


class Terminal:
    def __init__(self, server: Harness, session: str = "work") -> None:
        self.server = server
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 120, 0, 0))
        self.client = subprocess.Popen(["tmux", "-S", server.socket, "attach-session", "-t", session], stdin=slave, stdout=slave, stderr=slave, env={**server.env, "TERM": "xterm-256color"})
        os.close(slave)
        self.output = bytearray()

    def drain(self, seconds: float = .03) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if select.select([self.master], [], [], min(.02, max(0, deadline - time.monotonic())))[0]:
                try:
                    data = os.read(self.master, 65536)
                except OSError as error:
                    if error.errno == errno.EIO:
                        return
                    raise
                if not data:
                    return
                self.output.extend(data)

    def keys(self, text: str) -> None:
        os.write(self.master, text.encode())
        self.drain()

    def wait(self, read, expected=True) -> None:
        deadline = time.monotonic() + 6
        actual = None
        while time.monotonic() < deadline:
            self.drain()
            try:
                actual = read()
            except StopIteration:
                continue
            if actual == expected:
                return
        raise AssertionError(f"Expected {expected!r}, received {actual!r}; terminal: {bytes(self.output[-1000:])!r}")

    def location(self) -> list[str]:
        lines = self.server.t("list-clients", "-F", "#{client_pid}|#{session_name}|#{window_id}|#{pane_id}").splitlines()
        return next(line.split("|")[1:] for line in lines if line.startswith(str(self.client.pid) + "|"))

    def frame(self) -> str:
        return self.server.t("capture-pane", "-pt", self.location()[2])

    def main(self) -> str:
        lines = self.server.t("list-clients", "-F", "#{client_pid}|#{client_name}").splitlines()
        name = next(line.split("|", 1)[1] for line in lines if line.startswith(str(self.client.pid) + "|"))
        return self.server.api("doctor")["client_main"].get(name, "")

    def close(self) -> None:
        self.drain()
        self.client.terminate()
        try:
            self.client.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.client.kill()
            self.client.wait(timeout=5)
        finally:
            os.close(self.master)


class LivePreview(unittest.TestCase):
    def test_held_navigation_keeps_each_step_across_window_handoffs(self):
        with tempfile.TemporaryDirectory(prefix="binder-preview-repeat-") as directory:
            server = Harness(Path(directory))
            terminal = None
            try:
                server.t("rename-window", "-t", server.window, "step00")
                windows = [server.window]
                for index in range(1, 18):
                    if index == 9:
                        windows.append(server.t("new-session", "-dP", "-F", "#{window_id}", "-s", "ops", "-n", f"step{index:02}", "sleep 86400"))
                    else:
                        windows.append(server.t("new-window", "-dP", "-F", "#{window_id}", "-t", "work" if index < 9 else "ops", "-n", f"step{index:02}", "sleep 86400"))
                server.t("set-option", "-g", "@tmux_pane_tree_session_order", "work,ops")
                terminal = Terminal(server)
                self.open(server, terminal)
                server.t("set-option", "-g", "@tmux_pane_tree_collapse_panes", "on")
                server.api("refresh")
                terminal.wait(lambda: "▸ step00" in terminal.frame())
                terminal.keys("gg")
                terminal.wait(lambda: "step00" in next(line for line in terminal.frame().splitlines() if line.startswith("▶")))
                for interval in (.04, .02, .008):
                    for key, expected in (("j", 12), ("k", 0)):
                        with self.subTest(interval=interval, key=key):
                            def repeat():
                                for _ in range(12):
                                    os.write(terminal.master, key.encode())
                                    time.sleep(interval)
                            writer = threading.Thread(target=repeat)
                            writer.start()
                            seen = []
                            try:
                                while writer.is_alive():
                                    terminal.drain(.002)
                                    pane = terminal.location()[2]
                                    frame = server.t("capture-pane", "-pt", pane)
                                    selected = next((line for line in frame.splitlines() if line.startswith("▶") and "step" in line), "")
                                    if selected and terminal.location()[2] == pane:
                                        seen.append(int(selected.split("step", 1)[1][:2]))
                            finally:
                                writer.join()
                            terminal.wait(lambda: terminal.location()[1], windows[expected])
                            terminal.wait(lambda: f"step{expected:02}" in next(line for line in terminal.frame().splitlines() if line.startswith("▶")))
                            backwards = [(left, right) for left, right in zip(seen, seen[1:]) if (right < left if key == "j" else right > left)]
                            self.assertEqual(backwards, [], seen)
            finally:
                if terminal is not None:
                    terminal.close()
                server.close()

    def test_branch_and_enter_input_during_a_pending_handoff(self):
        with tempfile.TemporaryDirectory(prefix="binder-preview-pending-") as directory:
            server = Harness(Path(directory))
            terminal = None
            stopped = 0
            try:
                other = server.t("new-session", "-dP", "-F", "#{pane_id}", "-s", "ops", "-n", "logs", "sleep 86400")
                window = server.t("display-message", "-pt", other, "#{window_id}")
                server.t("set-option", "-g", "@tmux_pane_tree_session_order", "work,ops")
                terminal = Terminal(server)
                self.open(server, terminal)
                server.t("set-option", "-g", "@tmux_pane_tree_collapse_panes", "on")
                server.api("refresh")
                terminal.wait(lambda: "▸ logs" in terminal.frame())
                stopped = int(server.t("display-message", "-pt", server.sidebar(window), "#{pane_pid}"))
                os.kill(stopped, signal.SIGSTOP)
                terminal.keys("G")
                terminal.wait(lambda: "logs" in next(line for line in terminal.frame().splitlines() if line.startswith("▶")))
                terminal.keys("=")
                terminal.wait(lambda: "▾ logs" in terminal.frame())
                os.kill(stopped, signal.SIGCONT)
                stopped = 0
                terminal.wait(lambda: terminal.location(), ["ops", window, server.sidebar(window)])
                terminal.wait(lambda: "▾ logs" in terminal.frame())
                terminal.keys("j-\r")
                terminal.wait(lambda: terminal.location()[2], other)
                server.wait(lambda: server.api("snapshot", pane=server.sidebar(window))["view_state"].get("expanded_windows"), [])
            finally:
                if stopped:
                    os.kill(stopped, signal.SIGCONT)
                if terminal is not None:
                    terminal.close()
                server.close()

    def open(self, server: Harness, terminal: Terminal) -> None:
        server.t("set-option", "-g", "@tmux_pane_tree_collapse_panes", "off")
        terminal.wait(lambda: server.t("display-message", "-p", "-t", server.main, "#{session_attached}"), "1")
        terminal.keys("\x02t")
        terminal.wait(lambda: bool(server.sidebar(server.window)))
        terminal.keys("\x02T")
        terminal.wait(lambda: terminal.location()[2], server.sidebar(server.window))
        terminal.wait(lambda: any(line.startswith("▶") for line in terminal.frame().splitlines()))

    def test_navigation_previews_content_without_enter_and_keeps_keyboard_focus(self):
        with tempfile.TemporaryDirectory(prefix="binder-preview-") as directory:
            server = Harness(Path(directory))
            terminal = None
            try:
                other = server.t("new-session", "-dP", "-F", "#{pane_id}", "-s", "ops", "-n", "logs", "printf 'OPS CONTENT\\n'; sleep 86400")
                other_window = server.t("display-message", "-pt", other, "#{window_id}")
                server.t("set-option", "-g", "@tmux_pane_tree_session_order", "work,ops")
                terminal = Terminal(server)
                self.open(server, terminal)
                before = server.t("list-panes", "-a", "-F", "#{pane_id}|#{pane_pid}")
                terminal.keys("G")
                terminal.wait(lambda: terminal.location(), ["ops", other_window, server.sidebar(other_window)])
                terminal.wait(lambda: "▶" in terminal.frame() and "sleep" in next(line for line in terminal.frame().splitlines() if line.startswith("▶")))
                self.assertIn("OPS CONTENT", server.t("capture-pane", "-pt", other))
                terminal.keys("k")
                terminal.wait(lambda: "logs" in next(line for line in terminal.frame().splitlines() if line.startswith("▶")))
                terminal.keys("\r")
                terminal.wait(lambda: terminal.location()[2], other)
                self.assertEqual(server.t("list-panes", "-a", "-F", "#{pane_id}|#{pane_pid}"), before)
            finally:
                if terminal is not None:
                    terminal.close()
                server.close()

    def test_windows_panes_bursts_and_other_clients(self):
        with tempfile.TemporaryDirectory(prefix="binder-preview-") as directory:
            server = Harness(Path(directory))
            terminal = observer = None
            try:
                server.t("new-session", "-d", "-s", "observer", "sleep 86400")
                build = server.t("new-window", "-dP", "-F", "#{pane_id}", "-t", "work", "-n", "build", "printf 'BUILD CONTENT\\n'; sleep 86400")
                second = server.t("split-window", "-dP", "-F", "#{pane_id}", "-t", build, "-v", "printf 'SECOND CONTENT\\n'; sleep 86400")
                window = server.t("display-message", "-pt", build, "#{window_id}")
                server.t("set-option", "-g", "@tmux_pane_tree_session_order", "work,observer")
                observer = Terminal(server, "observer")
                terminal = Terminal(server)
                self.open(server, terminal)
                observer_location = observer.location()
                terminal.keys("ggjjjj")
                terminal.wait(lambda: terminal.location(), ["work", window, server.sidebar(window)])
                terminal.wait(terminal.main, second)
                self.assertIn("SECOND CONTENT", server.t("capture-pane", "-pt", second))
                terminal.keys("\x1bOA")
                terminal.wait(terminal.main, build)
                self.assertIn("BUILD CONTENT", server.t("capture-pane", "-pt", build))
                terminal.keys("gg" + "jk" * 100 + "jjjj")
                terminal.wait(terminal.main, second)
                terminal.wait(lambda: terminal.location()[2], server.sidebar(window))
                terminal.keys("\r")
                terminal.wait(lambda: terminal.location()[2], second)
                self.assertEqual(observer.location(), observer_location)
            finally:
                for attached in (terminal, observer):
                    if attached is not None:
                        attached.close()
                server.close()

    def test_explicit_session_selection_uses_linked_active_window_and_returns_to_origin(self):
        with tempfile.TemporaryDirectory(prefix="binder-preview-") as directory:
            server = Harness(Path(directory))
            terminal = None
            try:
                server.t("new-session", "-d", "-s", "ops", "-n", "logs", "sleep 86400")
                server.t("link-window", "-s", server.window, "-t", "ops:5")
                server.t("select-window", "-t", "ops:5")
                server.t("set-option", "-g", "@tmux_pane_tree_session_order", "work,ops")
                terminal = Terminal(server)
                self.open(server, terminal)
                sidebar = server.sidebar(server.window)
                session = server.t("display-message", "-pt", "ops:5", "#{session_id}")
                origin = server.t("display-message", "-pt", "work:editor", "#{session_id}")
                server.api("preview", pane=sidebar, key=session, state={
                    "input_id": time.monotonic_ns(), "selected": session,
                    "origin_session": origin,
                    "jumps": [["main", server.main], ["sidebar", session]], "jump_index": 1,
                })
                terminal.wait(lambda: terminal.location(), ["ops", server.window, sidebar])
                terminal.wait(lambda: "ops" in next(line for line in terminal.frame().splitlines() if line.startswith("▶")))
                terminal.keys("\x0f")
                terminal.wait(lambda: terminal.location(), ["work", server.window, server.main])
            finally:
                if terminal is not None:
                    terminal.close()
                server.close()

    def test_search_collapse_history_and_attention_survive_handoff(self):
        with tempfile.TemporaryDirectory(prefix="binder-preview-") as directory:
            server = Harness(Path(directory))
            terminal = None
            try:
                other = server.t("new-session", "-dP", "-F", "#{pane_id}", "-s", "ops", "-n", "logs", "sleep 86400")
                window = server.t("display-message", "-pt", other, "#{window_id}")
                server.t("set-option", "-g", "@tmux_pane_tree_session_order", "work,ops")
                terminal = Terminal(server)
                self.open(server, terminal)
                server.api("update-state", pane=other, event={"app": "claude", "status": "needs-input"})
                terminal.keys("hh")
                terminal.wait(lambda: "▸ editor" in terminal.frame())
                terminal.keys("/op")
                terminal.wait(lambda: terminal.location(), ["ops", window, server.sidebar(window)])
                terminal.keys("s")
                terminal.wait(lambda: "/ops" in terminal.frame())
                self.assertIn("▸ editor", terminal.frame())
                self.assertNotIn("▾ editor", terminal.frame())
                self.assertEqual(server.api("doctor")["agents"][other]["status"], "needs-input")
                terminal.keys("\r\x0f")
                terminal.wait(lambda: terminal.location(), ["work", server.window, server.main])
                terminal.keys("\x02T")
                terminal.wait(lambda: terminal.location()[2], server.sidebar(server.window))
                terminal.keys("G")
                terminal.wait(lambda: terminal.location()[2], server.sidebar(window))
                terminal.keys("\r")
                terminal.wait(lambda: terminal.location()[2], other)
                self.assertEqual(server.api("doctor")["agents"][other]["status"], "idle")
            finally:
                if terminal is not None:
                    terminal.close()
                server.close()


if __name__ == "__main__":
    unittest.main()
