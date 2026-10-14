#!/usr/bin/env bash
set -euo pipefail

. "$(dirname "$0")/real_tmux_testlib.sh"

real_tmux_start_server
real_tmux_source_file "$REPO_ROOT/${TMUX_BINDER_TEST_CONFIG:-tmux-pane-tree.conf}"
real_tmux set-option -g mouse on
real_tmux set-option -g @tmux_pane_tree_focus_on_open 0

python3 - "$REAL_TMUX_BIN" "$REAL_TMUX_SOCKET_PATH" "$REPO_ROOT" "$REAL_TMUX_STATE_DIR" <<'PY'
from __future__ import annotations

from collections.abc import Callable
import fcntl
import os
from pathlib import Path
import pty
import select
import shlex
import struct
import subprocess
import sys
import termios
import time

binary, socket, repo, state = sys.argv[1:]


def tmux(*args: str) -> str:
    return subprocess.check_output([binary, "-S", socket, *args], text=True, timeout=15).strip()


def wait_for(label: str, read: Callable[[], str], expected: str) -> str:
    deadline = time.monotonic() + 8
    actual = ""
    while time.monotonic() < deadline:
        actual = read()
        if actual == expected:
            return actual
        drain(0.05)
    raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def metric(pane: str, name: str) -> str:
    return tmux("display-message", "-pt", pane, "#{" + name + "}")


def sidebar(window: str) -> str:
    panes = tmux("list-panes", "-t", window, "-F", "#{pane_id}|#{@tmux_sidebar_owned}")
    return next((line.split("|")[0] for line in panes.splitlines() if line.endswith("|1")), "")


def wait_for_sidebar(window: str) -> str:
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        pane = sidebar(window)
        if pane:
            return pane
        drain(0.05)
    raise AssertionError(f"Sidebar did not appear in {window}")


def run_script(name: str, *args: str) -> None:
    tmux("run-shell", shlex.join(["bash", f"{repo}/scripts/features/sidebar/{name}.sh", *args]))


def assert_width(pane: str, width: int) -> None:
    wait_for(f"width of {pane}", lambda: metric(pane, "pane_width"), str(width))


def assert_saved_width(width: int) -> None:
    path = Path(state) / "sidebar-width.txt"
    wait_for("saved width", lambda: tmux("show-options", "-gqv", "@tmux_pane_tree_width"), str(width))
    wait_for("persisted width", lambda: path.read_text().strip() if path.is_file() else "", str(width))


def assert_rendered(pane: str, text: str) -> None:
    wait_for(f"rendered {text}", lambda: str(text in tmux("capture-pane", "-pt", pane)), "True")


def attach_client(session: str, width: int) -> tuple[subprocess.Popen[bytes], int]:
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, width, 0, 0))
    client = subprocess.Popen(
        [binary, "-S", socket, "-f", "/dev/null", "attach-session", "-t", session],
        stdin=slave, stdout=slave, stderr=slave,
        env={**os.environ, "TERM": "xterm-256color"},
    )
    os.close(slave)
    return client, master


client, master = attach_client("work", 100)
clients = [client]
masters = [master]


def drain(seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        ready, _, _ = select.select(masters, [], [], min(0.05, max(0, deadline - time.monotonic())))
        for descriptor in ready:
            os.read(descriptor, 65536)


try:
    wait_for("attached client", lambda: tmux("display-message", "-p", "#{session_attached}"), "1")
    client_name = next(
        line.split("|", 1)[1]
        for line in tmux("list-clients", "-F", "#{client_pid}|#{client_name}").splitlines()
        if line.startswith(f"{client.pid}|")
    )
    source_window = tmux("display-message", "-pt", "work:editor", "#{window_id}")
    main = tmux("display-message", "-pt", "work:editor", "#{pane_id}")
    main_pid = metric(main, "pane_pid")
    tmux("set-option", "-g", "@tmux_sidebar_enabled", "1")
    run_script("ensure-sidebar-pane", main, source_window)
    source = wait_for_sidebar(source_window)
    assert_rendered(source, "editor")
    assert_width(source, 25)
    source_pid = metric(source, "pane_pid")

    other_window = tmux("new-window", "-dP", "-F", "#{window_id}", "-t", "work", "-n", "second")
    tmux("select-window", "-t", other_window)
    other = wait_for_sidebar(other_window)
    assert_rendered(other, "second")
    tmux("select-window", "-t", source_window)
    tmux("resize-pane", "-t", source, "-x", "41")
    assert_width(source, 41)
    assert_saved_width(41)
    assert_width(other, 41)

    for window in (other_window, source_window):
        tmux("select-window", "-t", window)
    assert sidebar(source_window) == source
    assert metric(source, "pane_pid") == source_pid
    assert_width(source, 41)
    assert_rendered(source, "second")

    tmux("select-pane", "-t", main)
    drain(0.2)
    for event in ("\x1b[<0;42;6M", "\x1b[<32;46;6M", "\x1b[<32;50;6M", "\x1b[<0;50;6m"):
        os.write(master, event.encode())
        drain(0.25)
    assert_width(source, 49)
    assert_saved_width(49)
    assert_width(other, 49)
    assert_rendered(source, "editor")
    assert_rendered(other, "second")

    new_window = tmux("new-window", "-dP", "-F", "#{window_id}", "-t", "work", "-n", "third")
    tmux("select-window", "-t", new_window)
    new_sidebar = wait_for_sidebar(new_window)
    assert_width(new_sidebar, 49)
    assert_rendered(new_sidebar, "third")
    tmux("select-window", "-t", source_window)

    session_main = tmux("new-session", "-dP", "-F", "#{pane_id}", "-s", "ops", "-n", "logs")
    session_window = metric(session_main, "window_id")
    run_script("ensure-sidebar-pane", session_main, session_window)
    session_sidebar = wait_for_sidebar(session_window)
    assert_width(session_sidebar, 49)
    second_client, second_master = attach_client("ops", 130)
    clients.append(second_client)
    masters.append(second_master)
    wait_for("second terminal width", lambda: metric(session_sidebar, "window_width"), "130")
    assert_width(session_sidebar, 49)
    assert_saved_width(49)
    tmux("switch-client", "-c", client_name, "-t", "ops")
    wait_for("shared session width", lambda: metric(session_sidebar, "window_width"), "100")
    assert_width(session_sidebar, 49)
    assert_rendered(session_sidebar, "logs")
    tmux("switch-client", "-c", client_name, "-t", "work")
    wait_for("remaining terminal width", lambda: metric(session_sidebar, "window_width"), "130")
    assert_width(session_sidebar, 49)
    assert_saved_width(49)
    assert metric(source, "pane_pid") == source_pid

    tmux("resize-window", "-t", other_window, "-x", "35", "-y", "30")
    drain(0.3)
    assert_saved_width(49)
    tmux("resize-pane", "-t", source, "-x", "55")
    assert_saved_width(55)
    assert_width(new_sidebar, 55)
    assert_width(session_sidebar, 55)
    drain(0.3)
    assert_saved_width(55)
    assert int(metric(other, "pane_width")) < 35

    tmux("resize-pane", "-t", main, "-x", "40")
    assert_width(source, 59)
    assert_saved_width(59)
    assert_width(new_sidebar, 59)
    assert_width(session_sidebar, 59)
    assert_rendered(new_sidebar, "third")

    run_script("close-sidebar")
    wait_for("closed sidebar", lambda: sidebar(source_window), "")
    assert_saved_width(59)
    assert tmux("show-options", "-g").find("@tmux_pane_tree_geometry_") == -1
    tmux("select-window", "-t", source_window)
    run_script("toggle-sidebar")
    reopened = wait_for_sidebar(source_window)
    assert_width(reopened, 59)
    assert_rendered(reopened, "editor")
    assert metric(main, "pane_pid") == main_pid
    narrow_reopened = wait_for_sidebar(other_window)
    assert_width(narrow_reopened, 33)
    tmux("resize-pane", "-Z", "-t", main)
    assert metric(main, "window_zoomed_flag") == "1"
    tmux("resize-window", "-t", source_window, "-x", "120")
    drain(0.3)
    assert metric(main, "window_zoomed_flag") == "1"
    tmux("resize-pane", "-Z", "-t", main)
    assert_width(reopened, 59)
    assert_saved_width(59)
    assert_rendered(reopened, "editor")
    print("Verified saved width, two client sizes, window/session navigation, mouse drag and reopening.")
finally:
    for attached_client in clients:
        attached_client.terminate()
        try:
            attached_client.wait(timeout=5)
        except subprocess.TimeoutExpired:
            attached_client.kill()
            attached_client.wait()
    for descriptor in masters:
        os.close(descriptor)
PY
