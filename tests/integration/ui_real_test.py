from __future__ import annotations

import fcntl
import os
import pty
import re
import select
import struct
import subprocess
import tempfile
import termios
import time
import unittest
from pathlib import Path

from coordinator_test import Harness


class InteractiveUI(unittest.TestCase):
    def test_keyboard_unicode_prompts_navigation_and_native_menu(self):
        with tempfile.TemporaryDirectory(prefix="binder-ui-") as directory:
            server = Harness(Path(directory) / "a")
            server.t("set-option", "-g", "@tmux_pane_tree_collapse_panes", "off")
            server.t("set-option", "-sg", "escape-time", "10")
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 120, 0, 0))
            client = subprocess.Popen(["tmux", "-S", server.socket, "attach-session", "-t", "work"], stdin=slave, stdout=slave, stderr=slave, env={**server.env, "TERM": "xterm-256color"})
            os.close(slave)
            captured = bytearray()

            def drain(seconds=.05):
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline:
                    if select.select([master], [], [], min(.025, max(0, deadline - time.monotonic())))[0]:
                        captured.extend(os.read(master, 65536))

            def wait(read, expected=True):
                deadline = time.monotonic() + 6
                actual = None
                while time.monotonic() < deadline:
                    drain()
                    actual = read()
                    if actual == expected:
                        return actual
                log = server.identity.directory / "engine.log"
                frames = [(line, server.t("capture-pane", "-pt", line.split("|")[0])) for line in server.t("list-panes", "-a", "-F", "#{pane_id}|#{@tmux_sidebar_owned}").splitlines() if line.endswith("|1")]
                self.fail(f"Expected {expected!r}, got {actual!r}; frames: {frames!r}; terminal: {bytes(captured[-1500:])!r}; log: {log.read_text() if log.exists() else ''}")

            def keys(text):
                os.write(master, text.encode())
                drain(.05)

            def frame(pane):
                return server.t("capture-pane", "-pt", pane)

            try:
                wait(lambda: server.t("display-message", "-p", "#{session_attached}"), "1")
                content_pid = server.t("display-message", "-p", "-t", server.main, "#{pane_pid}")
                keys("\x02t")
                wait(lambda: bool(server.sidebar(server.window)))
                sidebar = server.sidebar(server.window)
                wait(lambda: "editor" in frame(sidebar))
                keys("\x02T")
                wait(lambda: server.t("display-message", "-p", "-t", sidebar, "#{pane_active}"), "1")
                keys("aw")
                wait(lambda: "Add window:" in frame(sidebar))
                new_name = 'nuovo|界 "ok";'
                keys(new_name + "\r")
                wait(lambda: server.api("doctor")["windows"], 2)
                snapshot = server.api("snapshot")
                target = next(row["window"] for row in snapshot["rows"] if row.get("name") == new_name)
                keys("/界\r\r")
                wait(lambda: server.t("list-clients", "-F", "#{window_id}"), target)
                target_sidebar = server.sidebar(target)
                keys("\x02T")
                wait(lambda: server.t("display-message", "-p", "-t", target_sidebar, "#{pane_active}"), "1")
                keys("r")
                wait(lambda: "Rename window:" in frame(target_sidebar))
                keys("\x15rinominata\r")
                wait(lambda: server.t("display-message", "-p", "-t", target_sidebar, "#{window_name}"), "rinominata")
                keys("h")
                wait(lambda: "▸" in frame(target_sidebar))
                captured.clear()
                keys("m")
                wait(lambda: b"New Session" in captured)
                keys("q")
                wait(lambda: server.api("doctor")["views"], 0)
                keys("\x02t")
                wait(lambda: bool(server.sidebar(target)))
                drain(.2)
                captured.clear()
                keys("\x1b[<2;6;3M")
                keys("\x1b[<2;6;3m")
                wait(lambda: b"Close Sidebar" in captured)
                prefix = captured[:captured.rfind(b"Close Sidebar")]
                menu_row, menu_col = map(int, re.findall(rb"\x1b\[(\d+);(\d+)H", prefix)[-1])
                keys(f"\x1b[<0;{menu_col + 5};{menu_row}M\x1b[<0;{menu_col + 5};{menu_row}m")
                wait(lambda: server.api("doctor")["views"], 0)
                self.assertEqual(server.t("display-message", "-p", "-t", server.main, "#{pane_pid}"), content_pid)
            finally:
                client.terminate()
                client.wait(timeout=5)
                os.close(master)
                server.close()


if __name__ == "__main__":
    unittest.main()
