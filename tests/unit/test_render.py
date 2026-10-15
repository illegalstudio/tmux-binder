from __future__ import annotations

import unittest
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


if __name__ == "__main__":
    unittest.main()
