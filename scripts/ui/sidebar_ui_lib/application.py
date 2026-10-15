from __future__ import annotations

import argparse
import curses
import os
import select
import socket
import sys
import time

from binder.config import SHORTCUTS
from binder.runtime import MAX_MESSAGE, Identity, connect, decode, encode, request

from .render import Renderer, colors
from .view import clusters, control_key, ensure_visible, navigable, selection_for, shortcut_step, visible_rows


class View:
    def __init__(self, screen, identity: Identity, pane: str) -> None:
        self.screen, self.identity, self.pane = screen, identity, pane
        self.renderer = Renderer(screen)
        self.channel: socket.socket | None = None
        self.incoming = bytearray()
        self.outgoing = bytearray()
        self.snapshot: dict = {}
        self.rows: list[dict] = []
        self.selected = ""
        self.collapsed: set[str] = set()
        self.expanded_windows: set[str] = set()
        self.scroll = 0
        self.user_scrolled = False
        self.pending = ""
        self.searching = False
        self.query = ""
        self.prompt: dict | None = None
        self.notice = ""
        self.notice_until = 0.0
        self.attributes: dict[str, int] = {}
        self.dirty = True
        self.revision = -1
        self.jumps: list[tuple[str, str]] = []
        self.jump_index = -1
        self.running = True
        self.preview_dirty = False
        self.preview_waiting = False
        self.input_id = 0
        self.applied_input_id = 0
        self.origin_session = ""
        self.navigation_request = 0
        self.navigation_acknowledged = 0
        self.deferred_keys: list[str | int] = []

    def send(self, command: str, **values: object) -> None:
        self.outgoing.extend(encode({"command": command, "pane": self.pane, "client": self.snapshot.get("client", ""), **values}))
        if len(self.outgoing) > MAX_MESSAGE:
            raise ConnectionError("Coordinator is not consuming view commands")

    def reconnect(self) -> None:
        if self.channel:
            self.channel.close()
        self.channel = connect(self.identity)
        self.channel.setblocking(False)
        self.incoming.clear()
        self.outgoing.clear()
        self.preview_waiting = False
        self.preview_dirty = False
        self.navigation_request = 0
        self.deferred_keys.clear()
        self.send("subscribe")

    def accept(self, message: dict) -> None:
        if "error" in message:
            self.preview_waiting = False
            self.navigation_request = 0
            self.deferred_keys.clear()
            self.notice = message["error"]
            self.notice_until = time.monotonic() + 5
            self.dirty = True
            return
        if message.get("type") == "action":
            if message.get("client"):
                self.snapshot["client"] = message["client"]
            self.action(message.get("action", ""), message.get("key", ""))
            return
        if message.get("type") != "snapshot":
            return
        previous = self.snapshot
        self.snapshot = message
        self.revision = message["revision"]
        if not previous:
            saved = message.get("view_state", {})
            self.collapsed = set(saved.get("collapsed", []))
            self.expanded_windows = set(saved.get("expanded_windows", []))
            self.selected = saved.get("selected", "")
            self.scroll = saved.get("scroll", 0)
        navigation = message.get("navigation", {})
        navigation_id = int(navigation.get("input_id", 0))
        transferred = False
        managed = navigation.get("managed", False)
        if navigation_id and (managed or navigation_id >= self.input_id):
            if managed:
                self.navigation_acknowledged = max(self.navigation_acknowledged, int(navigation.get("acknowledged", {}).get(self.pane, 0)))
            self.preview_waiting = self.navigation_acknowledged < self.navigation_request if managed else False
            fresh = navigation_id > (self.applied_input_id if managed else self.input_id)
            self.applied_input_id = max(self.applied_input_id, navigation_id)
            if navigation.get("apply") and fresh:
                self.input_id = max(self.input_id, navigation_id)
                self.selected = navigation["selected"]
                self.user_scrolled = False
                self.collapsed = set(navigation.get("collapsed", []))
                self.expanded_windows = set(navigation.get("expanded_windows", []))
                self.scroll = navigation.get("scroll", 0)
                self.query = navigation.get("query", "")
                self.searching = navigation.get("searching", False)
                self.jumps = [tuple(entry) for entry in navigation.get("jumps", [])]
                self.jump_index = navigation.get("jump_index", -1)
                self.origin_session = navigation.get("origin_session", "")
                self.snapshot["client"] = navigation["client"]
                transferred = True
        self.collapsed.difference_update(row["key"] for row in message["rows"] if row["kind"] == "session")
        self.collapsed.update(row["key"] for row in message["rows"] if row["kind"] == "window" and (message.get("collapse_panes", False) or row.get("compact")) and row["key"] not in self.expanded_windows)
        self.rows = visible_rows(message["rows"], self.collapsed)
        main = message.get("main", "")
        if not message.get("focused") and not transferred and not self.preview_waiting:
            reset = not previous or previous.get("focused") or previous.get("main") != main
            self.selected = selection_for(self.rows, "" if reset else self.selected, main, message["rows"])
            if reset:
                self.jumps = []
                self.jump_index = -1
                self.searching = False
                self.pending = ""
        else:
            self.selected = selection_for(self.rows, self.selected, main, message["rows"])
            if not self.jumps:
                self.jumps = [("main", main), ("sidebar", self.selected)] if main else [("sidebar", self.selected)]
                self.jump_index = len(self.jumps) - 1
                self.origin_session = message.get("session", "")
        if previous.get("colors") != message.get("colors") or previous.get("styles") != message.get("styles"):
            self.attributes = colors(message.get("colors", {}), message.get("styles", {}))
            self.renderer.invalidate()
        if message.get("visible") and not previous.get("visible"):
            self.renderer.invalidate()
        self.dirty = True
        if not self.preview_waiting:
            deferred, self.deferred_keys = self.deferred_keys, []
            for key in deferred:
                self.handle_key(key)

    def navigation_state(self) -> dict:
        return {"input_id": self.input_id, "base_id": self.applied_input_id, "selected": self.selected, "collapsed": sorted(self.collapsed), "expanded_windows": sorted(self.expanded_windows), "collapse_panes": self.snapshot.get("collapse_panes", False), "scroll": self.scroll, "query": self.query, "searching": self.searching, "jumps": self.jumps, "jump_index": self.jump_index, "origin_session": self.origin_session}

    def navigate(self, action: str) -> None:
        self.navigation_request = time.monotonic_ns()
        self.input_id = self.navigation_request
        self.user_scrolled = False
        self.preview_waiting = True
        self.send("navigate", action=action, request_id=self.navigation_request, state=self.navigation_state())

    def row(self, key: str = "") -> dict | None:
        return next((row for row in self.snapshot.get("rows", []) if row["key"] == (key or self.selected)), None)

    def select(self, key: str, record: bool = False, preview: bool = False) -> None:
        changed = key != self.selected
        self.selected = key
        self.user_scrolled = False
        self.dirty = True
        if record:
            entry = ("sidebar", key)
            self.jumps = self.jumps[:self.jump_index + 1]
            if not self.jumps or self.jumps[-1] != entry:
                self.jumps.append(entry)
            self.jump_index = len(self.jumps) - 1
        if changed and preview and self.snapshot.get("live_preview"):
            self.input_id = time.monotonic_ns()
            self.preview_dirty = True
            self.preview_waiting = True

    def search_next(self, direction: int) -> None:
        raw = self.snapshot.get("rows", [])
        matches = []
        seen = set()
        for index, candidate in enumerate(raw):
            if self.query.casefold() not in candidate["text"].casefold():
                continue
            row = candidate if "pane_id" in candidate else next((child for child in raw[index + 1:] if child["key"].startswith(candidate["key"] + "/") and "pane_id" in child), None)
            if row is not None and row["key"] not in seen:
                matches.append(row)
                seen.add(row["key"])
        if not matches:
            return
        index = next((index for index, row in enumerate(matches) if row["key"] == self.selected), -1 if direction > 0 else 0)
        row = matches[(index + direction) % len(matches)]
        self.collapsed = {parent for parent in self.collapsed if not row["key"].startswith(parent + "/")}
        self.expanded_windows.update(parent["key"] for parent in raw if parent["kind"] == "window" and row["key"].startswith(parent["key"] + "/"))
        self.rows = visible_rows(raw, self.collapsed)
        self.select(row["key"], preview=True)

    def branch(self, key: str, expand: bool) -> None:
        row = self.row(key)
        if not row:
            return
        if row["kind"] == "pane":
            self.branch(row["parent"], expand)
            return
        if row["kind"] != "window":
            return
        if expand:
            self.collapsed.discard(key)
            self.expanded_windows.add(key)
        else:
            self.collapsed.add(key)
            self.expanded_windows.discard(key)
        self.rows = visible_rows(self.snapshot.get("rows", []), self.collapsed)
        if not expand and self.selected.startswith(key + "/"):
            self.select(key, preview=True)
        self.dirty = True

    def action(self, action: str, key: str = "") -> None:
        if key:
            self.select(key)
        row = self.row()
        if action in {"jump_back", "jump_forward"}:
            index = self.jump_index + (-1 if action == "jump_back" else 1)
            if 0 <= index < len(self.jumps):
                self.jump_index = index
                location, value = self.jumps[index]
                if location == "main":
                    self.preview_dirty = False
                    self.preview_waiting = False
                    self.send("focus-main", target=value, session=self.origin_session)
                else:
                    self.select(value, preview=True)
            return
        if action == "close":
            self.preview_dirty = False
            self.preview_waiting = False
            self.send("close")
        elif action == "focus_main":
            self.preview_dirty = False
            self.preview_waiting = False
            self.send("focus-main")
        elif action in {"go_top", "go_bottom"}:
            if self.snapshot.get("live_preview"):
                self.navigate("top" if action == "go_top" else "bottom")
                return
            targets = navigable(self.rows, self.collapsed, self.snapshot.get("live_preview", False))
            if targets:
                self.select(targets[0 if action == "go_top" else -1]["key"], True, preview=True)
        elif action == "select" and row:
            self.preview_dirty = False
            self.preview_waiting = False
            self.send("activate", key=row["key"])
        elif action in {"add_window", "add_session", "rename_window", "rename_session", "rename"} and row:
            if action == "rename":
                action = "rename_session" if row["kind"] == "session" else "rename_window"
            initial = row.get("session", "") if action == "rename_session" else row.get("name", "") if action == "rename_window" else ""
            self.prompt = {"action": action, "key": row["key"], "text": initial, "label": action.replace("_", " ").capitalize() + ": "}
        elif action == "confirm_close" and row:
            self.prompt = {"action": "confirm", "key": row["key"], "text": "", "label": "Close selected item? [y/N] "}
        elif action == "close_pane" and row:
            if row["kind"] != "pane":
                self.action("confirm_close")
                return
            targets = navigable(self.rows, self.collapsed, self.snapshot.get("live_preview", False))
            index = next((index for index, item in enumerate(targets) if item["key"] == self.selected), 0)
            if len(targets) > 1:
                self.select(targets[index + 1 if index + 1 < len(targets) else index - 1]["key"])
            self.send("remove", key=row["key"])
        elif action == "toggle_filter":
            self.send("toggle-filter")
        elif action == "refresh":
            self.send("refresh")
        self.dirty = True

    def handle_key(self, key: str | int) -> None:
        if key == curses.KEY_RESIZE:
            curses.update_lines_cols()
            self.renderer.invalidate()
            self.dirty = True
            return
        navigation_key = key in {"j", "k", curses.KEY_DOWN, curses.KEY_UP, "h", "l", curses.KEY_LEFT, curses.KEY_RIGHT, "+", "=", "-"}
        if self.deferred_keys or (self.navigation_request > self.navigation_acknowledged and self.preview_waiting and not navigation_key):
            self.deferred_keys.append(key)
            return
        if key == curses.KEY_MOUSE:
            self.mouse()
            return
        if self.prompt:
            if key == "\x1b":
                self.prompt = None
            elif self.prompt["action"] == "confirm":
                if key in {"y", "Y"}:
                    self.send("remove", key=self.prompt["key"])
                self.prompt = None
            elif key in {"\n", "\r"}:
                self.send("name", action=self.prompt["action"], key=self.prompt["key"], name=self.prompt["text"])
                self.prompt = None
            elif key in {curses.KEY_BACKSPACE, "\x7f", "\b"}:
                self.prompt["text"] = "".join(clusters(self.prompt["text"])[:-1])
            elif key == "\x15":
                self.prompt["text"] = ""
            elif isinstance(key, str) and key.isprintable():
                self.prompt["text"] += key
            self.dirty = True
            return
        if self.searching:
            if key == "\x1b":
                self.searching, self.query = False, ""
            elif key in {"\n", "\r"}:
                self.searching = False
            elif key in {curses.KEY_BACKSPACE, "\x7f", "\b"}:
                self.query = "".join(clusters(self.query)[:-1])
                self.search_next(1)
            elif isinstance(key, str) and key.isprintable():
                self.query += key
                current = self.row()
                if not current or self.query.casefold() not in current["text"].casefold():
                    self.search_next(1)
            self.dirty = True
            return
        configured = self.snapshot.get("shortcuts", SHORTCUTS)
        control_action = next((name for name, shortcut in configured.items() if control_key(shortcut) == key and control_key(shortcut)), "")
        if control_action:
            self.action(control_action)
            return
        if self.query and not self.pending and key in {"n", "N"}:
            self.search_next(1 if key == "n" else -1)
            return
        if key == "\x1b" and self.query:
            self.query = ""
            self.dirty = True
            return
        if isinstance(key, str) and (self.pending or any(value.startswith(key) for value in configured.values())):
            self.pending, action = shortcut_step(self.pending, key, configured)
            if action:
                self.action(action)
            return
        if key in {"j", "k", curses.KEY_DOWN, curses.KEY_UP}:
            if self.snapshot.get("live_preview"):
                self.navigate("down" if key in {"j", curses.KEY_DOWN} else "up")
                return
            targets = navigable(self.rows, self.collapsed, self.snapshot.get("live_preview", False))
            if targets:
                down = key in {"j", curses.KEY_DOWN}
                position = next((i for i, row in enumerate(self.rows) if row["key"] == self.selected), -1)
                target_keys = {row["key"] for row in targets}
                candidates = self.rows[position + 1:] if down else self.rows[:position][::-1]
                target = next((row for row in candidates if row["key"] in target_keys), None)
                if target:
                    self.select(target["key"], preview=True)
        elif key in {"q", "\x1b"}:
            self.action("close")
        elif key == "\x0c":
            self.action("focus_main")
        elif key in {"\n", "\r"}:
            self.action("select")
        elif key == "/":
            self.searching, self.query = True, ""
            self.dirty = True
        elif key == "p":
            self.send("toggle-panes")
        elif key == "m":
            index = next((i for i, row in enumerate(self.rows) if row["key"] == self.selected), 0)
            self.send("menu", row=max(0, index - self.scroll), key=self.selected)
        elif key in {"h", curses.KEY_LEFT, "-"}:
            if self.snapshot.get("live_preview"):
                self.navigate("collapse")
            else:
                self.branch(self.selected, False)
        elif key in {"l", curses.KEY_RIGHT, "+", "="}:
            if self.snapshot.get("live_preview"):
                self.navigate("expand")
            else:
                self.branch(self.selected, True)

    def mouse(self) -> None:
        try:
            _, _, y, _, state = curses.getmouse()
        except curses.error:
            return
        if state & (curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED | curses.BUTTON3_CLICKED | curses.BUTTON3_PRESSED):
            if not 0 <= y < self.screen.getmaxyx()[0] - bool(self.footer()):
                return
        if state & curses.BUTTON4_PRESSED:
            self.scroll = max(0, self.scroll - 3)
            self.user_scrolled = True
        elif state & getattr(curses, "BUTTON5_PRESSED", 0x08000000):
            self.scroll += 3
            self.user_scrolled = True
        elif state & (curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED):
            index = y + self.scroll
            if 0 <= index < len(self.rows):
                self.select(self.rows[index]["key"])
                self.action("select")
        elif state & (curses.BUTTON3_CLICKED | curses.BUTTON3_PRESSED):
            self.send("menu", row=y)
        self.dirty = True

    def footer(self) -> str:
        if self.prompt:
            return self.prompt["label"] + self.prompt["text"]
        if self.searching or self.query:
            return "/" + self.query
        if self.notice:
            return self.notice
        if not self.snapshot.get("focused") or self.screen.getmaxyx()[0] < 3:
            return ""
        row = self.row()
        if row and row.get("expandable"):
            return ("= expand" if row["key"] in self.collapsed else "- collapse") + "  / search"
        return "Enter focus  / search"

    def draw(self) -> None:
        footer = self.footer()
        height, _ = self.screen.getmaxyx()
        lines = max(0, height - bool(footer))
        index = next((index for index, row in enumerate(self.rows) if row["key"] == self.selected), -1)
        if not self.user_scrolled:
            self.scroll = ensure_visible(index, self.scroll, lines, self.snapshot.get("scrolloff", 8))
        self.scroll = min(max(0, self.scroll), max(0, len(self.rows) - lines))
        self.renderer.draw(self.rows, self.selected, self.scroll, self.attributes, self.query, footer, self.searching or bool(self.prompt), self.collapsed, focused=self.snapshot.get("focused", False))
        self.send("rendered", revision=self.revision, keys=[row["key"] for row in self.rows], scroll=self.scroll, lines=lines, selected=self.selected, collapsed=sorted(self.collapsed), expanded_windows=sorted(self.expanded_windows), navigation_input_id=self.applied_input_id)
        if self.preview_dirty:
            self.send("preview", key=self.selected, state=self.navigation_state())
            self.preview_dirty = False
        self.dirty = False

    def run(self) -> None:
        self.screen.keypad(True)
        self.screen.nodelay(True)
        if hasattr(curses, "set_escdelay"):
            curses.set_escdelay(25)
        try:
            curses.curs_set(0)
            curses.start_color()
            curses.use_default_colors()
        except curses.error:
            pass
        curses.mousemask(curses.ALL_MOUSE_EVENTS | getattr(curses, "REPORT_MOUSE_POSITION", 0))
        self.reconnect()
        try:
            while self.running:
                if self.dirty:
                    self.draw()
                if self.notice and self.notice_until and time.monotonic() >= self.notice_until:
                    self.notice = ""
                    self.dirty = True
                assert self.channel is not None
                readable, writable, _ = select.select([sys.stdin.fileno(), self.channel], [self.channel] if self.outgoing else [], [], 1 if self.notice else None)
                try:
                    if self.channel in writable and self.outgoing:
                        sent = self.channel.send(self.outgoing)
                        del self.outgoing[:sent]
                    if self.channel in readable:
                        chunk = self.channel.recv(65536)
                        if not chunk:
                            raise ConnectionError("Coordinator disconnected")
                        self.incoming.extend(chunk)
                        if len(self.incoming) > MAX_MESSAGE:
                            raise ValueError("Coordinator message exceeds size limit")
                        while b"\n" in self.incoming:
                            raw, _, remaining = self.incoming.partition(b"\n")
                            self.incoming = bytearray(remaining)
                            self.accept(decode(raw))
                except (ConnectionError, BrokenPipeError, OSError):
                    self.notice = "Reconnecting…"
                    self.dirty = True
                    self.reconnect()
                if sys.stdin.fileno() in readable:
                    while True:
                        try:
                            key = self.screen.get_wch()
                        except curses.error:
                            break
                        self.handle_key(key)
        finally:
            if self.channel:
                self.channel.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dump-render", action="store_true")
    args = parser.parse_args()
    identity = Identity.resolve()
    if args.dump_render:
        message = request(identity, "snapshot")
        print("\n".join("  " + row["text"] for row in message["rows"]))
        return
    pane = os.environ.get("TMUX_PANE", "")
    if not pane:
        raise RuntimeError("The sidebar UI must run inside a tmux pane")
    curses.wrapper(lambda screen: View(screen, identity, pane).run())
