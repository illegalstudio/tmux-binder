from __future__ import annotations

import unittest
import curses
from unittest.mock import Mock, patch

from ui.sidebar_ui_lib.render import Renderer, colors, row_layout, row_text
from ui.sidebar_ui_lib.view import width


class RowLayout(unittest.TestCase):
    def test_unicode_labels_and_badges_fit_even_narrow_panes(self):
        row = {"kind": "pane", "label": "café 界 👩‍💻 e\u0301 very long title", "icon": "界", "badge": "?"}
        for cells in range(40):
            with self.subTest(cells=cells):
                text, prefix, badge = row_text(row, True, False, cells)
                self.assertLessEqual(width(text), cells)
                self.assertLessEqual(width(prefix) + width(badge), cells)
                if cells >= width(row["badge"]):
                    self.assertTrue(text.endswith(row["badge"]))

    def test_collapsed_window_reserves_the_urgent_badge_separately_from_its_title(self):
        row = {"kind": "window", "label": "very long window name", "expandable": True, "collapsed_badge": "?", "collapsed_status": "needs-input"}
        text, _, badge = row_text(row, True, True, 24)
        self.assertIn("▸", text)
        self.assertEqual(badge, "?")
        self.assertTrue(text.endswith("?"))
        self.assertEqual(width(text), 24)

    def test_mixed_summaries_keep_priority_and_fit_narrow_rows(self):
        parts = [{"text": "?1", "symbol": "?", "status": "needs-input"}, {"text": "!2", "symbol": "!", "status": "error"}, {"text": "↳1", "symbol": "↳", "status": "subagent-running"}, {"text": "●3", "symbol": "●", "status": "running"}, {"text": "✓1", "symbol": "✓", "status": "done"}]
        row = {"kind": "window", "label": "a long window name", "expandable": True, "badges": parts}
        for cells in range(40):
            with self.subTest(cells=cells):
                text, prefix, runs = row_layout(row, True, False, cells)
                badge = "".join(run for run, _ in runs)
                self.assertLessEqual(width(text), cells)
                self.assertLessEqual(width(prefix) + width(badge), cells)
                if cells:
                    self.assertTrue(badge.startswith("?"))
                if cells >= 24:
                    self.assertEqual(badge, "?1 !2 ↳1 ●3 ✓1")
                self.assertEqual(row_text(row, True, True, cells)[2], badge)

    def test_renderer_colors_each_status_on_normal_and_selected_rows(self):
        screen = Mock()
        screen.getmaxyx.return_value = (2, 30)
        row = {"kind": "window", "key": "$0/@0", "label": "backend", "badges": [{"text": "?", "status": "needs-input"}, {"text": "●2", "status": "running"}]}
        attributes = {"window": 0, "selected": 33, "status:needs-input": 11, "status:running": 22, "selected-status:needs-input": 44, "selected-status:running": 55}
        renderer = Renderer(screen)
        with patch("ui.sidebar_ui_lib.render.curses.curs_set"), patch("ui.sidebar_ui_lib.render.curses.doupdate"):
            renderer.draw([row], "", 0, attributes)
            writes = [(call.args[2], call.args[3]) for call in screen.addstr.call_args_list]
            self.assertIn(("?", 11), writes)
            self.assertIn((" ●2", 22), writes)
            screen.addstr.reset_mock()
            renderer.draw([row], row["key"], 0, attributes)
            writes = [(call.args[2], call.args[3]) for call in screen.addstr.call_args_list]
            self.assertIn(("?", 44), writes)
            self.assertIn((" ●2", 55), writes)

    def test_monochrome_terminals_keep_statuses_emphasized(self):
        with patch("ui.sidebar_ui_lib.render.curses.has_colors", return_value=False):
            attributes = colors({}, {})
        self.assertTrue(attributes["status:needs-input"])
        self.assertTrue(attributes["status:running"])
        self.assertTrue(attributes["window-highlight"] & curses.A_REVERSE)

    def test_window_highlight_persists_without_focus_and_retains_badge_colors(self):
        screen = Mock()
        screen.getmaxyx.return_value = (5, 30)
        window = {"kind": "window", "key": "$0/@0", "window": "@0", "label": "backend", "badges": [{"text": "?", "status": "needs-input"}]}
        pane = {"kind": "pane", "key": "$0/@0/%0", "window": "@0", "label": "shell"}
        other = {"kind": "window", "key": "$1/@1", "window": "@1", "label": "frontend"}
        linked = {**window, "key": "$1/@0"}
        unmarked = {"kind": "window", "key": "$1/@2", "window": "@2", "label": "idle"}
        rows = [window, pane, other, linked, unmarked]
        attributes = {"window-highlight": 77, "window-highlight-status:needs-input": 88, "status:needs-input": 11}
        renderer = Renderer(screen)
        with patch("ui.sidebar_ui_lib.render.curses.curs_set"), patch("ui.sidebar_ui_lib.render.curses.doupdate"):
            renderer.draw(rows, pane["key"], 0, attributes, focused=False, highlighted_windows={"@0", "@1"}, hovered=window["key"])
            writes = [(call.args[2], call.args[3]) for call in screen.addstr.call_args_list]
            self.assertIn(("?", 88), writes)
            self.assertEqual(renderer.previous[0][1], 77 | curses.A_UNDERLINE)
            self.assertEqual(renderer.previous[0][3], 77)
            self.assertEqual(renderer.previous[2][1], 77)
            self.assertEqual(renderer.previous[3][1], 77)
            self.assertEqual(renderer.previous[4][1], 0)
            screen.addstr.reset_mock()
            renderer.draw(rows, pane["key"], 0, attributes, focused=False)
            self.assertEqual(renderer.previous[0][1], 0)
            self.assertIn(("?", 11), [(call.args[2], call.args[3]) for call in screen.addstr.call_args_list])

    def test_selected_marked_window_keeps_native_selection_and_restores_its_mark(self):
        screen = Mock()
        screen.getmaxyx.return_value = (2, 30)
        row = {"kind": "window", "key": "$0/@0", "window": "@0", "label": "backend", "badges": [{"text": "?", "status": "needs-input"}]}
        attributes = {"selected": 33, "selected-status:needs-input": 44, "window-highlight": 77, "window-highlight-status:needs-input": 88}
        renderer = Renderer(screen)
        with patch("ui.sidebar_ui_lib.render.curses.curs_set"), patch("ui.sidebar_ui_lib.render.curses.doupdate"):
            renderer.draw([row], row["key"], 0, attributes, highlighted_windows={"@0"})
            self.assertEqual(renderer.previous[0][1], 33 | curses.A_UNDERLINE)
            self.assertEqual(renderer.previous[0][3], 33)
            self.assertIn(("?", 44), [(call.args[2], call.args[3]) for call in screen.addstr.call_args_list])
            screen.addstr.reset_mock()
            renderer.draw([row], row["key"], 0, attributes, focused=False, highlighted_windows={"@0"})
            self.assertEqual(renderer.previous[0][1], 77)
            self.assertIn(("?", 88), [(call.args[2], call.args[3]) for call in screen.addstr.call_args_list])
            renderer.draw([row], row["key"], 0, attributes)
            self.assertEqual(renderer.previous[0][1], 33)

    def test_hover_underlines_only_the_label_and_preserves_status_color(self):
        screen = Mock()
        screen.getmaxyx.return_value = (2, 30)
        row = {"kind": "window", "key": "$0/@0", "label": "backend", "badges": [{"text": "?", "status": "needs-input"}]}
        renderer = Renderer(screen)
        with patch("ui.sidebar_ui_lib.render.curses.curs_set"), patch("ui.sidebar_ui_lib.render.curses.doupdate"):
            renderer.draw([row], "", 0, {"status:needs-input": 11}, hovered=row["key"])
            writes = [(call.args[2], call.args[3]) for call in screen.addstr.call_args_list]
            self.assertIn(("backend", curses.A_UNDERLINE), writes)
            self.assertIn(("?", 11), writes)
            screen.addstr.reset_mock()
            renderer.draw([row], "", 0, {"status:needs-input": 11})
            self.assertTrue(screen.addstr.called)
            self.assertFalse(renderer.previous[0][1] & curses.A_UNDERLINE)


if __name__ == "__main__":
    unittest.main()
