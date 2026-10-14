from __future__ import annotations

import json
import unittest
from pathlib import Path

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

    def test_menu_confirmation_can_be_cancelled(self):
        self.view.action("confirm_close")
        self.view.handle_key("n")
        self.assertEqual(self.commands(), [])

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
