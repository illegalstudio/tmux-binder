from __future__ import annotations

import curses
import json
import unittest
from pathlib import Path
from unittest.mock import Mock

from binder.runtime import Identity
from ui.sidebar_ui_lib.application import View
from ui.sidebar_ui_lib.view import clip, clusters, control_key, ensure_visible, shortcut_step, visible_rows, width


class UnicodeLayout(unittest.TestCase):
    def test_width_uses_terminal_cells(self):
        for text, expected in [("abc", 3), ("界", 2), ("e\u0301", 1), ("👩‍💻", 2), ("🇮🇹", 2), ("✅", 2)]:
            with self.subTest(text=text):
                self.assertEqual(width(text), expected)

    def test_clipping_does_not_split_clusters(self):
        for text in ("a界bc", "e\u0301abc", "👩‍💻abc", "🇮🇹abc"):
            for cells in range(6):
                clipped = clip(text, cells)
                self.assertLessEqual(width(clipped), cells)
                self.assertFalse(clipped.startswith("\u0301"))
                self.assertFalse(clipped.endswith("\u200d"))

    def test_combining_character_backspace(self):
        self.assertEqual("".join(clusters("café\u0301")[:-1]), "caf")

    def test_scroll_margin_clamps_on_small_terminal(self):
        self.assertEqual(ensure_visible(10, 0, 3, 8), 9)
        self.assertEqual(ensure_visible(-1, 10, 0, 8), 0)

    def test_shortcut_prefix_and_control_decoding(self):
        configured = {"top": "gg", "bottom": "G", "delete": "dd"}
        self.assertEqual(shortcut_step("", "g", configured), ("g", ""))
        self.assertEqual(shortcut_step("g", "G", configured), ("", "bottom"))
        self.assertEqual(control_key("C-o"), "\x0f")
        self.assertEqual(control_key("gg"), "")


class Screen:
    def getmaxyx(self):
        return 20, 30


class Keyboard(unittest.TestCase):
    def setUp(self):
        self.view = View(Screen(), Identity("/socket", 1, Path("/unused"), Path("/unused/preferences")), "%9")
        self.rows = [
            {"kind": "session", "key": "$0", "session": "work", "text": "work"},
            {"kind": "window", "key": "$0/@0", "parent": "$0", "window": "@0", "session_id": "$0", "name": "editor", "text": "editor"},
            {"kind": "pane", "key": "$0/@0/%0", "parent": "$0/@0", "pane_id": "%0", "window": "@0", "session_id": "$0", "text": "shell"},
            {"kind": "pane", "key": "$0/@0/%1", "parent": "$0/@0", "pane_id": "%1", "window": "@0", "session_id": "$0", "text": "café 界"},
        ]
        self.view.rows = self.rows[:]
        self.view.snapshot = {"rows": self.rows, "main": "%0"}
        self.view.selected = "$0/@0/%0"
        self.view.jumps = [("main", "%0"), ("sidebar", self.view.selected)]
        self.view.jump_index = 1

    def commands(self):
        return [json.loads(line) for line in self.view.outgoing.splitlines()]

    def test_navigation_and_jump_list(self):
        self.view.handle_key("G")
        self.assertEqual(self.view.selected, "$0/@0/%1")
        self.view.handle_key("\x0f")
        self.assertEqual(self.view.selected, "$0/@0/%0")
        self.view.handle_key("\x0f")
        self.assertEqual(self.commands()[-1]["command"], "focus-main")

    def test_search_accepts_unicode(self):
        for key in "/界":
            self.view.handle_key(key)
        self.assertEqual(self.view.selected, "$0/@0/%1")
        self.assertEqual(self.view.query, "界")
        self.view.handle_key("\x1b")
        self.assertEqual(self.view.query, "")

    def test_name_prompt_passes_text_as_data(self):
        self.view.action("rename_window")
        self.view.handle_key("\x15")
        for char in 'A; $(touch nope) "界"':
            self.view.handle_key(char)
        self.view.handle_key("\n")
        command = self.commands()[-1]
        self.assertEqual(command["command"], "name")
        self.assertEqual(command["name"], 'A; $(touch nope) "界"')

    def test_closing_and_focus_commands(self):
        self.view.handle_key("\x0c")
        self.view.handle_key("q")
        self.assertEqual([message["command"] for message in self.commands()], ["focus-main", "close"])

    def test_collapsing_and_expanding_keeps_selection(self):
        self.view.handle_key("h")
        self.assertEqual(self.view.selected, "$0/@0")
        self.assertEqual(len(self.view.rows), 2)
        self.view.handle_key("l")
        self.assertEqual(len(self.view.rows), 4)

    def test_window_collapse_never_hides_its_session(self):
        for key in ("h", curses.KEY_LEFT, "-"):
            with self.subTest(key=key):
                self.view.select("$0/@0")
                self.view.branch("$0/@0", True)
                for _ in range(2):
                    self.view.handle_key(key)
                    self.assertEqual(self.view.selected, "$0/@0")
                    self.assertEqual([row["key"] for row in self.view.rows], ["$0", "$0/@0"])
                    self.assertNotIn("$0", self.view.collapsed)

    def test_session_headers_ignore_branch_controls(self):
        self.view.select("$0")
        for key in ("h", curses.KEY_LEFT, "-", "l", curses.KEY_RIGHT, "+", "="):
            with self.subTest(key=key):
                self.view.handle_key(key)
                self.assertEqual(self.view.rows, self.rows)
                self.assertEqual(self.view.collapsed, set())

    def test_old_session_collapses_are_cleared_on_restore_and_handoff(self):
        for source in ("view_state", "navigation"):
            with self.subTest(source=source):
                self.view.snapshot = {}
                self.view.input_id = 0
                state = {"collapsed": ["$0", "$0/@0"], "selected": "$0/@0"}
                if source == "navigation":
                    state.update(input_id=100, apply=True, client="origin")
                self.view.accept({"type": "snapshot", "revision": 2, "rows": self.rows, "main": "%0", "focused": True, source: state})
                self.assertEqual(self.view.collapsed, {"$0/@0"})
                self.assertEqual([row["key"] for row in self.view.rows], ["$0", "$0/@0"])
                self.assertEqual(self.view.selected, "$0/@0")

    def test_menu_confirmation_can_be_cancelled(self):
        self.view.action("confirm_close")
        self.view.handle_key("n")
        self.assertEqual(self.commands(), [])

    def test_default_collapse_preserves_expansion_and_closes_future_windows(self):
        message = {"type": "snapshot", "revision": 2, "rows": self.rows, "main": "%0", "focused": True, "collapse_panes": True}
        self.view.accept(message)
        self.assertEqual(self.view.selected, "$0/@0")
        self.assertEqual(len(self.view.rows), 2)
        self.view.handle_key("=")
        self.view.accept({**message, "revision": 3})
        self.assertEqual(len(self.view.rows), 4)
        fresh = {"kind": "window", "key": "$0/@1", "parent": "$0", "text": "fresh"}
        self.view.accept({**message, "revision": 4, "rows": self.rows + [fresh]})
        self.assertIn("$0/@1", self.view.collapsed)
        self.assertNotIn("$0/@0", self.view.collapsed)
        self.view.select("$0/@0/%1")
        self.view.handle_key("-")
        self.assertEqual(self.view.selected, "$0/@0")
        self.view.accept({**message, "revision": 5})
        self.assertEqual(len(self.view.rows), 2)

    def test_search_opens_hidden_pane_and_keeps_it_open_on_refresh(self):
        message = {"type": "snapshot", "revision": 2, "rows": self.rows, "main": "%0", "focused": True, "collapse_panes": True}
        self.view.accept(message)
        for key in "/界":
            self.view.handle_key(key)
        self.view.accept({**message, "revision": 3})
        self.assertEqual(self.view.selected, "$0/@0/%1")
        self.assertEqual(len(self.view.rows), 4)

    def test_saved_expansion_survives_view_restart(self):
        self.view.snapshot = {}
        self.view.accept({"type": "snapshot", "revision": 2, "rows": self.rows, "main": "%0", "focused": False, "collapse_panes": True, "view_state": {"expanded_windows": ["$0/@0"]}})
        self.assertEqual(len(self.view.rows), 4)

    def test_expanding_without_live_preview_moves_to_first_child(self):
        self.view.handle_key("-")
        self.view.handle_key("+")
        self.view.handle_key("j")
        self.assertEqual(self.view.selected, "$0/@0/%0")

    def test_removed_selection_moves_to_available_main(self):
        self.view.attributes = {}
        message = {"type": "snapshot", "revision": 2, "rows": self.rows[:-1], "main": "%0", "focused": True}
        self.view.selected = "$0/@0/%1"
        self.view.accept(message)
        self.assertEqual(self.view.selected, "$0/@0/%0")

    def test_queued_unfocused_snapshot_preserves_new_keyboard_selection(self):
        message = {"type": "snapshot", "revision": 2, "rows": self.rows, "main": "%0", "focused": False}
        self.view.accept(message)
        self.view.handle_key("G")
        self.view.accept({**message, "revision": 3})
        self.assertEqual(self.view.selected, "$0/@0/%1")

    def test_losing_focus_or_changing_main_resets_selection(self):
        message = {"type": "snapshot", "revision": 2, "rows": self.rows, "main": "%0", "focused": True}
        self.view.accept(message)
        self.view.handle_key("G")
        self.view.accept({**message, "revision": 3, "focused": False})
        self.assertEqual(self.view.selected, "$0/@0/%0")
        self.view.accept({**message, "revision": 4, "focused": False, "main": "%1"})
        self.assertEqual(self.view.selected, "$0/@0/%1")

    def test_live_navigation_includes_windows_and_batches_preview_until_draw(self):
        self.view.snapshot.update(live_preview=True, client="client", focused=True)
        for key in ("j", "k", "k"):
            self.view.handle_key(key)
        self.assertEqual(self.view.selected, "$0/@0")
        self.assertEqual(self.commands(), [])
        self.view.renderer = Mock()
        self.view.draw()
        previews = [message for message in self.commands() if message["command"] == "preview"]
        self.assertEqual(len(previews), 1)
        self.assertEqual(previews[0]["key"], "$0/@0")
        self.assertFalse(self.view.preview_dirty)

    def test_vertical_navigation_and_top_bottom_skip_session_titles(self):
        self.view.rows += [
            {"kind": "session", "key": "$1", "text": "ops"},
            {"kind": "window", "key": "$1/@1", "parent": "$1", "text": "logs"},
        ]
        self.view.snapshot.update(rows=self.view.rows, live_preview=True)
        self.view.select("$0/@0/%1")
        self.view.handle_key("j")
        self.assertEqual(self.view.selected, "$1/@1")
        self.view.handle_key("k")
        self.assertEqual(self.view.selected, "$0/@0/%1")
        for key in "gg":
            self.view.handle_key(key)
        self.assertEqual(self.view.selected, "$0/@0")
        self.view.handle_key("k")
        self.assertEqual(self.view.selected, "$0/@0")
        self.view.handle_key("G")
        self.assertEqual(self.view.selected, "$1/@1")

    def test_older_handoff_cannot_replace_newer_keyboard_input(self):
        self.view.snapshot.update(live_preview=True, client="client", focused=True)
        self.view.handle_key("G")
        stamp = self.view.input_id
        message = {**self.view.snapshot, "type": "snapshot", "revision": 2, "navigation": {"input_id": stamp - 1, "apply": True, "selected": "$0/@0/%0"}}
        self.view.accept(message)
        self.assertEqual(self.view.selected, "$0/@0/%1")
        self.assertTrue(self.view.preview_waiting)
        self.view.accept({**message, "navigation": {"input_id": stamp, "apply": False}})
        self.assertFalse(self.view.preview_waiting)

    def test_handoff_restores_search_and_navigation_before_destination_focus(self):
        navigation = {"input_id": 100, "apply": True, "client": "origin", "selected": "$0/@0", "collapsed": ["$0/@0"], "expanded_windows": ["$1/@1"], "query": "界", "searching": True, "scroll": 3, "jumps": [["main", "%0"], ["sidebar", "$0/@0"]], "jump_index": 1, "origin_session": "$0"}
        self.view.accept({"type": "snapshot", "revision": 2, "rows": self.rows, "focused": False, "main": "%0", "navigation": navigation})
        self.assertEqual(self.view.selected, "$0/@0")
        self.assertEqual(self.view.query, "界")
        self.assertTrue(self.view.searching)
        self.assertEqual(self.view.collapsed, {"$0/@0"})
        self.assertEqual(self.view.expanded_windows, {"$1/@1"})
        self.assertEqual(self.view.jumps[0], ("main", "%0"))
        self.assertEqual(self.view.applied_input_id, 100)
        self.assertFalse(self.view.preview_dirty)

    def test_hidden_main_selects_its_collapsed_parent(self):
        self.view.collapsed = {"$0/@0"}
        self.view.accept({"type": "snapshot", "revision": 2, "rows": self.rows, "focused": False, "main": "%0"})
        self.assertEqual(self.view.selected, "$0/@0")

    def test_closing_a_header_requires_confirmation(self):
        self.view.selected = "$0/@0"
        self.view.handle_key("x")
        self.assertEqual(self.view.prompt["action"], "confirm")
        self.assertEqual(self.commands(), [])
