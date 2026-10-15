from __future__ import annotations

import unittest

from ui.sidebar_ui_lib.render import row_text
from ui.sidebar_ui_lib.view import width


class RowLayout(unittest.TestCase):
    def test_unicode_labels_and_badges_fit_even_narrow_panes(self):
        row = {"kind": "pane", "label": "café 界 👩‍💻 e\u0301 very long title", "icon": "界", "badge": "❓"}
        for cells in range(40):
            with self.subTest(cells=cells):
                text, prefix, badge = row_text(row, True, False, cells)
                self.assertLessEqual(width(text), cells)
                self.assertLessEqual(width(prefix) + width(badge), cells)
                if cells >= width(row["badge"]):
                    self.assertTrue(text.endswith(row["badge"]))

    def test_collapsed_window_reserves_the_urgent_badge_separately_from_its_title(self):
        row = {"kind": "window", "label": "very long window name", "expandable": True, "collapsed_badge": "❓", "collapsed_status": "needs-input"}
        text, _, badge = row_text(row, True, True, 24)
        self.assertIn("▸", text)
        self.assertEqual(badge, "❓")
        self.assertTrue(text.endswith("❓"))
        self.assertEqual(width(text), 24)


if __name__ == "__main__":
    unittest.main()
