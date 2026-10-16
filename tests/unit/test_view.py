from __future__ import annotations

import curses
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from binder.runtime import Identity
from ui.sidebar_ui_lib.application import View
from ui.sidebar_ui_lib.view import clip, clusters, control_key, ensure_visible, navigate, shortcut_step, visible_rows, width


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
            {"kind": "window", "key": "$0/@0", "parent": "$0", "window": "@0", "session_id": "$0", "session": "work", "name": "editor", "text": "editor"},
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

    def mouse(self, y, state, now=10.0, x=8):
        with patch("ui.sidebar_ui_lib.application.curses.getmouse", return_value=(0, x, y, 0, state)), patch("ui.sidebar_ui_lib.application.time.monotonic", return_value=now):
            self.view.handle_key(curses.KEY_MOUSE)

    def test_single_click_previews_without_activating_and_release_is_ignored(self):
        self.view.snapshot.update(live_preview=True, focused=True)
        self.mouse(3, curses.BUTTON1_PRESSED)
        self.mouse(3, curses.BUTTON1_RELEASED)
        self.assertEqual(self.view.selected, "$0/@0/%1")
        self.assertTrue(self.view.preview_dirty)
        self.assertTrue(self.view.user_scrolled)
        self.assertEqual(self.commands(), [])
        self.mouse(3, curses.BUTTON1_PRESSED, now=10.2)
        self.assertEqual([(item["command"], item["key"]) for item in self.commands()], [("activate", "$0/@0/%1")])
        self.assertFalse(self.view.preview_dirty)

    def test_double_click_requires_the_same_item_and_position_within_the_interval(self):
        for y, x, now in ((2, 8, 10.1), (3, 9, 10.1), (3, 8, 10.5)):
            with self.subTest(y=y, x=x, now=now):
                self.view.mouse_click = {}
                self.mouse(3, curses.BUTTON1_PRESSED)
                self.mouse(y, curses.BUTTON1_PRESSED, now=now, x=x)
                self.assertEqual(self.commands(), [])

    def test_mouse_ignores_footer_blank_rows_and_prompts(self):
        self.view.snapshot["focused"] = True
        for y in (-1, 4, 19):
            with self.subTest(y=y):
                self.mouse(y, curses.BUTTON1_DOUBLE_CLICKED)
                self.assertEqual(self.commands(), [])
        self.view.action("rename_window")
        self.mouse(3, curses.BUTTON1_DOUBLE_CLICKED)
        self.assertEqual(self.commands(), [])
        self.assertEqual(self.view.selected, "$0/@0/%0")

    def test_click_uses_scrolled_coordinates_and_cancels_an_older_keyboard_reply(self):
        self.view.snapshot.update(live_preview=True, focused=True)
        self.view.handle_key("j")
        older = self.commands()[-1]["request_id"]
        self.view.handle_key("\r")
        self.assertEqual(self.view.deferred_keys, ["\r"])
        self.view.scroll = 1
        self.mouse(0, curses.BUTTON1_PRESSED)
        self.assertEqual(self.view.selected, "$0/@0")
        self.assertEqual(self.view.deferred_keys, [])
        self.view.accept({**self.view.snapshot, "type": "snapshot", "revision": 2, "navigation": {"managed": True, "apply": True, "client": "client", "input_id": older, "selected": "$0/@0/%1"}})
        self.assertEqual(self.view.selected, "$0/@0")
        self.assertNotIn("activate", [item["command"] for item in self.commands()])

    def test_hover_does_not_move_selection_or_send_commands_and_keyboard_clears_it(self):
        self.mouse(3, curses.REPORT_MOUSE_POSITION)
        self.assertEqual(self.view.hovered, "$0/@0/%1")
        self.assertEqual(self.view.selected, "$0/@0/%0")
        self.assertEqual(self.commands(), [])
        self.view.dirty = False
        self.mouse(3, curses.REPORT_MOUSE_POSITION, now=10.1)
        self.assertFalse(self.view.dirty)
        self.assertAlmostEqual(self.view.hover_until, 10.7)
        self.view.handle_key("l")
        self.assertEqual(self.view.hovered, "")
        self.assertEqual(self.view.hover_until, 0)

    def test_right_click_waits_for_release_before_opening_the_native_menu(self):
        self.mouse(3, curses.BUTTON3_PRESSED)
        self.assertEqual(self.commands(), [])
        self.mouse(3, curses.BUTTON3_RELEASED, now=10.1)
        self.assertEqual([(item["command"], item["row"]) for item in self.commands()], [("menu", 3)])

    def test_session_headings_ignore_all_pointer_interactions(self):
        selected = self.view.selected
        for preview in (False, True):
            self.view.snapshot["live_preview"] = preview
            for state in (curses.REPORT_MOUSE_POSITION, curses.BUTTON1_PRESSED, curses.BUTTON1_CLICKED, curses.BUTTON1_DOUBLE_CLICKED, curses.BUTTON3_PRESSED, curses.BUTTON3_RELEASED):
                with self.subTest(preview=preview, state=state):
                    self.mouse(0, state)
                    self.assertEqual(self.view.selected, selected)
                    self.assertEqual(self.view.hovered, "")
                    self.assertFalse(self.view.preview_dirty)
                    self.assertEqual(self.commands(), [])

    def test_session_heading_selection_is_discarded_on_restore_and_handoff(self):
        for source in ("view_state", "navigation"):
            with self.subTest(source=source):
                self.view.snapshot = {}
                self.view.input_id = self.view.applied_input_id = 0
                state = {"selected": "$0", "collapsed": ["$0/@0"]}
                if source == "navigation":
                    state.update(input_id=100, apply=True, client="origin")
                self.view.accept({"type": "snapshot", "revision": 2, "rows": self.rows, "main": "%0", "focused": True, source: state})
                self.assertEqual(self.view.selected, "$0/@0")

    def test_preview_handoff_retains_double_click_and_manual_scroll(self):
        self.view.snapshot.update(live_preview=True, focused=True)
        self.mouse(3, curses.BUTTON1_PRESSED)
        state = self.view.navigation_state()
        destination = View(Screen(), self.view.identity, "%10")
        destination.accept({**self.view.snapshot, "type": "snapshot", "revision": 2, "navigation": {**state, "apply": True, "client": "client"}})
        self.assertEqual(destination.mouse_click, self.view.mouse_click)
        self.assertTrue(destination.user_scrolled)
        with patch("ui.sidebar_ui_lib.application.curses.update_lines_cols"):
            destination.handle_key(curses.KEY_RESIZE)
        self.assertEqual(destination.mouse_click, self.view.mouse_click)
        with patch("ui.sidebar_ui_lib.application.curses.getmouse", return_value=(0, 8, 3, 0, curses.BUTTON1_PRESSED)), patch("ui.sidebar_ui_lib.application.time.monotonic", return_value=10.2):
            destination.mouse()
        self.assertEqual(json.loads(destination.outgoing.splitlines()[-1])["command"], "activate")

    def navigation_reply(self, state=None):
        command = self.commands()[-1]
        self.assertEqual(command["command"], "navigate")
        result = navigate(self.view.snapshot["rows"], state or command["state"], command["action"])
        navigation = {**result, "managed": True, "apply": True, "client": "client", "input_id": max(command["request_id"], self.view.applied_input_id) + 1, "acknowledged": {self.view.pane: command["request_id"]}}
        self.view.accept({**self.view.snapshot, "type": "snapshot", "revision": 2, "navigation": navigation})
        return navigation

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

    def test_r_opens_window_rename_immediately_and_R_renames_the_session(self):
        self.view.select("$0/@0")
        self.view.handle_key("r")
        self.assertEqual(self.view.prompt["action"], "rename_window")
        self.assertEqual(self.view.prompt["text"], "editor")
        self.view.handle_key("\x1b")
        self.view.handle_key("R")
        self.assertEqual(self.view.prompt["action"], "rename_session")
        self.assertEqual(self.view.prompt["text"], "work")

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

    def test_live_navigation_sends_relative_steps_without_changing_a_stale_cursor(self):
        self.view.snapshot.update(live_preview=True, client="client", focused=True)
        for key in ("j", "k", "k"):
            self.view.handle_key(key)
        self.assertEqual(self.view.selected, "$0/@0/%0")
        self.assertEqual([item["action"] for item in self.commands()], ["down", "up", "up"])
        self.view.renderer = Mock()
        self.view.draw()
        previews = [message for message in self.commands() if message["command"] == "preview"]
        self.assertEqual(previews, [])
        self.assertFalse(self.view.preview_dirty)

    def test_vertical_navigation_and_top_bottom_skip_session_titles(self):
        self.view.rows += [
            {"kind": "session", "key": "$1", "text": "ops"},
            {"kind": "window", "key": "$1/@1", "parent": "$1", "text": "logs"},
        ]
        self.view.snapshot.update(rows=self.view.rows, live_preview=True)
        self.view.select("$0/@0/%1")
        self.view.handle_key("j")
        self.navigation_reply()
        self.assertEqual(self.view.selected, "$1/@1")
        self.view.handle_key("k")
        self.navigation_reply()
        self.assertEqual(self.view.selected, "$0/@0/%1")
        for key in "gg":
            self.view.handle_key(key)
        self.navigation_reply()
        self.assertEqual(self.view.selected, "$0/@0")
        self.view.handle_key("k")
        self.navigation_reply()
        self.assertEqual(self.view.selected, "$0/@0")
        self.view.handle_key("G")
        self.navigation_reply()
        self.assertEqual(self.view.selected, "$1/@1")

    def test_navigation_defers_enter_until_its_own_input_is_acknowledged(self):
        self.view.snapshot.update(live_preview=True, client="client", focused=True)
        self.view.handle_key("j")
        request = self.commands()[-1]["request_id"]
        self.view.handle_key("\r")
        self.assertEqual([command["command"] for command in self.commands()], ["navigate"])
        older = {"managed": True, "apply": True, "client": "client", "input_id": request + 1, "selected": "$0/@0", "acknowledged": {self.view.pane: request - 1}}
        self.view.accept({**self.view.snapshot, "type": "snapshot", "revision": 2, "navigation": older})
        self.assertTrue(self.view.preview_waiting)
        self.assertEqual(self.view.deferred_keys, ["\r"])
        self.navigation_reply({"selected": "$0/@0/%0"})
        self.assertEqual(self.commands()[-1]["command"], "activate")
        self.assertEqual(self.commands()[-1]["key"], "$0/@0/%1")

    def test_navigation_replies_do_not_reset_a_query_or_expanded_branch(self):
        self.view.snapshot.update(live_preview=True, client="client", focused=True)
        self.view.query = "界"
        self.view.expanded_windows = {"$0/@0"}
        self.view.handle_key("k")
        self.navigation_reply()
        self.assertEqual(self.view.query, "界")
        self.assertEqual(self.view.expanded_windows, {"$0/@0"})

    def test_old_managed_reply_cannot_reverse_selection_or_acknowledgement(self):
        self.view.snapshot.update(live_preview=True, client="client", focused=True)
        self.view.handle_key("j")
        old = self.navigation_reply()
        self.view.handle_key("k")
        self.navigation_reply()
        self.view.accept({**self.view.snapshot, "navigation": old})
        self.assertEqual(self.view.selected, "$0/@0/%0")
        self.assertFalse(self.view.preview_waiting)

    def test_reconnect_does_not_replay_a_deferred_selection_action(self):
        self.view.snapshot.update(live_preview=True, client="client", focused=True)
        self.view.handle_key("j")
        self.view.handle_key("\r")
        with patch("ui.sidebar_ui_lib.application.connect", return_value=Mock()):
            self.view.reconnect()
        self.assertEqual([item["command"] for item in self.commands()], ["subscribe"])
        self.assertFalse(self.view.preview_waiting)
        self.assertEqual(self.view.deferred_keys, [])

    def test_older_handoff_cannot_replace_newer_keyboard_input(self):
        self.view.snapshot.update(live_preview=True, client="client", focused=True)
        self.view.handle_key("G")
        self.navigation_reply()
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
