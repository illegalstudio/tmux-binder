from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from binder.config import visuals
from binder.model import Model, minimum_layout_width, project_rows, window_badges
from binder.tmux import SCHEMAS


def row(kind: str, **values) -> dict:
    return {field: str(values.get(field, "")) for field in SCHEMAS[kind]}


def sample() -> Model:
    return Model.from_records({
        "S": [row("S", session_id="$0", session_name="work"), row("S", session_id="$1", session_name="ops")],
        "W": [row("W", session_id="$0", window_id="@0", window_name="editor", window_width=100, window_zoomed_flag=0, window_layout="abcd,100x30,0,0{25x30,0,0,9,74x30,26,0{36x30,26,0,0,37x30,63,0,1}}"), row("W", session_id="$1", window_id="@1", window_name="logs", window_width=100, window_zoomed_flag=0, window_layout="abcd,100x30,0,0,2")],
        "P": [
            row("P", pane_id="%0", window_id="@0", pane_current_command="claude", pane_title="claude", pane_active=1),
            row("P", pane_id="%1", window_id="@0", pane_current_command="bash", pane_title="shell"),
            row("P", pane_id="%2", window_id="@1", pane_current_command="tail", pane_title="logs", pane_active=1),
            row("P", pane_id="%9", window_id="@0", pane_current_command="python3", pane_title="Sidebar", **{"@tmux_sidebar_owned": "1"}),
        ],
        "C": [row("C", client_name="client", window_id="@0", pane_id="%0", session_id="$0")],
        "G": [row("G", pid=1)],
    })


class TreeProjection(unittest.TestCase):
    def setUp(self):
        self.model = sample()
        self.options = {"@tmux_pane_tree_icon_theme": "ascii"}
        self.icons, self.badges = visuals(self.options)

    def project(self, states=None, **options):
        return project_rows(self.model, states or {}, {**self.options, **{"@tmux_pane_tree_" + key: value for key, value in options.items()}}, self.icons, self.badges, {})

    def test_owned_sidebar_is_excluded(self):
        rows = self.project()
        self.assertEqual([row["pane_id"] for row in rows if row["kind"] == "pane"], ["%0", "%1", "%2"])

    def test_user_pane_named_sidebar_is_not_misidentified(self):
        self.model.panes["%1"]["pane_title"] = "Sidebar"
        self.assertIn("%1", [row.get("pane_id") for row in self.project()])

    def test_badges_are_projected_from_shared_agent_state(self):
        rows = self.project({"%0": {"app": "claude", "status": "needs-input"}})
        self.assertTrue(any("claude ?" in row["text"] for row in rows))

    def test_filter_preserves_actual_content_count(self):
        rows = self.project(filter="claude", compact_single_panes="on")
        self.assertEqual([row["kind"] for row in rows], ["session", "window", "pane"])
        self.assertEqual(rows[-1]["pane_id"], "%0")

    def test_filter_toggle_restores_full_tree(self):
        self.assertEqual(len([row for row in self.project(filter="claude") if row["kind"] == "pane"]), 1)
        self.assertEqual(len([row for row in self.project(filter="claude", filter_enabled="off") if row["kind"] == "pane"]), 3)

    def test_hidden_panes_keep_attention_visible(self):
        rows = self.project({"%0": {"app": "claude", "status": "done"}}, hide_panes="on")
        self.assertEqual([row["pane_id"] for row in rows if row["kind"] == "pane"], ["%0"])
        self.assertTrue(any(row.get("pane_id") == "@1" for row in rows))

    def test_compact_single_content_pane(self):
        rows = self.project(compact_single_panes="on")
        logs = next(row for row in rows if row.get("window") == "@1")
        self.assertEqual(logs["kind"], "window")
        self.assertEqual(logs["pane_id"], "%2")
        self.assertTrue(logs["compact"])
        self.assertTrue(any(row["kind"] == "pane" and row["parent"] == logs["key"] for row in rows))

    def test_window_summaries_match_when_expanded_or_collapsed(self):
        self.model.panes["%1"].update(pane_current_command="codex", pane_title="codex")
        rows = self.project({"%0": {"app": "claude", "status": "running"}, "%1": {"app": "codex", "status": "needs-input"}})
        editor = next(row for row in rows if row["key"] == "$0/@0")
        self.assertTrue(editor["text"].endswith("? ●"))
        self.assertEqual(editor["collapsed_text"], editor["text"])
        self.assertEqual(editor["collapsed_badge"], editor["badge"])

    def test_window_summaries_count_panes_and_exclude_the_sidebar(self):
        self.model.panes["%1"].update(pane_current_command="codex", pane_title="codex")
        self.model.panes["%3"] = {**self.model.panes["%0"], "pane_id": "%3"}
        states = {"%0": {"app": "claude", "status": "running"}, "%1": {"app": "codex", "status": "needs-input"}, "%3": {"app": "claude", "status": "running"}, "%9": {"app": "claude", "status": "error"}}
        editor = next(row for row in self.project(states) if row["key"] == "$0/@0")
        self.assertEqual(editor["badge"], "?1 ●2")
        self.assertEqual([(part["status"], part["count"]) for part in editor["badges"]], [("needs-input", 1), ("running", 2)])

    def test_window_summaries_include_filtered_panes_without_double_counting_links(self):
        self.model.panes["%1"].update(pane_current_command="codex", pane_title="codex")
        self.model.windows["@0"]["links"].append("$1")
        rows = self.project({"%0": {"app": "claude", "status": "running"}, "%1": {"app": "codex", "status": "needs-input"}}, filter="claude")
        windows = [row for row in rows if row["kind"] == "window"]
        self.assertEqual(len(windows), 2)
        self.assertEqual([row["badge"] for row in windows], ["? ●", "? ●"])
        self.assertEqual([row["pane_id"] for row in rows if row["kind"] == "pane"], ["%0", "%0"])

    def test_hidden_panes_and_custom_badges_preserve_window_activity(self):
        self.model.panes["%1"].update(pane_current_command="codex", pane_title="codex")
        self.badges.update(running="run", error="")
        rows = self.project({"%0": {"app": "claude", "status": "running"}, "%1": {"app": "codex", "status": "error"}}, hide_panes="on")
        editor = next(row for row in rows if row["key"] == "$0/@0")
        self.assertEqual(editor["badge"], "run")
        self.assertEqual([row["pane_id"] for row in rows if row["kind"] == "pane"], ["%0"])

    def test_compact_window_and_its_pane_both_keep_status(self):
        self.model.panes["%2"].update(pane_current_command="claude", pane_title="claude")
        rows = self.project({"%2": {"app": "claude", "status": "done"}}, compact_single_panes="on")
        window = next(row for row in rows if row["key"] == "$1/@1")
        pane = next(row for row in rows if row["key"] == "$1/@1/%2")
        self.assertTrue(window["compact"])
        self.assertEqual((window["badge"], pane["badge"]), ("✓", "✓"))

    def test_summary_priority_and_subagent_counts_are_per_pane(self):
        statuses = {"%0": "done", "%1": "running", "%2": "subagent-running", "%3": "error", "%4": "needs-input"}
        summary = window_badges([{"pane_id": key} for key in statuses], statuses, self.badges)
        self.assertEqual(" ".join(part["text"] for part in summary), "? ! ↳ ● ✓")
        self.assertEqual([part["count"] for part in summary], [1] * 5)
        delegated = next(row for row in self.project({"%0": {"app": "claude", "status": "subagent-running", "subagent_count": 20}}) if row["key"] == "$0/@0")
        self.assertEqual(delegated["badge"], "↳")

    def test_idle_and_completed_status_transitions_clear_stale_counts(self):
        for status, expected in (("running", "●"), ("done", "✓"), ("idle", "")):
            with self.subTest(status=status):
                window = next(row for row in self.project({"%0": {"app": "claude", "status": status}}) if row["key"] == "$0/@0")
                self.assertEqual(window["badge"], expected)

    def test_session_order_and_unknown_entries(self):
        rows = self.project(session_order="missing,ops,work")
        self.assertEqual([row["session"] for row in rows if row["kind"] == "session"], ["ops", "work"])

    def test_linked_windows_have_distinct_row_keys(self):
        self.model.windows["@0"]["links"].append("$1")
        rows = self.project()
        copies = [row for row in rows if row.get("pane_id") == "%0"]
        self.assertEqual(len(copies), 2)
        self.assertNotEqual(copies[0]["key"], copies[1]["key"])

    def test_projection_never_calls_tmux_or_reads_files(self):
        with patch("subprocess.run", side_effect=AssertionError("unexpected subprocess")), patch("pathlib.Path.read_text", side_effect=AssertionError("unexpected file read")):
            self.assertTrue(self.project())

    def test_visibility_uses_real_clients_and_zoom(self):
        self.assertTrue(self.model.visible("@0"))
        self.assertFalse(self.model.visible("@1"))
        self.model.windows["@0"]["window_zoomed_flag"] = "1"
        self.assertFalse(self.model.visible("@0"))
        self.model.panes["%9"]["pane_active"] = "1"
        self.assertTrue(self.model.visible("@0"))


class LayoutMinimum(unittest.TestCase):
    def test_horizontal_content_keeps_each_pane_and_border(self):
        model = sample()
        self.assertEqual(model.available_sidebar_width("@0"), 96)
        self.assertEqual(model.available_sidebar_width("@1"), 98)

    def test_vertical_content_shares_minimum_width(self):
        layout = "abcd,100x30,0,0[100x14,0,0,0,100x15,0,15,1]"
        self.assertEqual(minimum_layout_width(layout), 1)

    def test_nested_layout_removes_only_the_owned_sidebar(self):
        layout = "abcd,100x30,0,0{25x30,0,0,9,74x30,26,0[74x14,26,0{36x14,26,0,0,37x14,63,0,1},74x15,26,15,2]}"
        self.assertEqual(minimum_layout_width(layout), 5)
        self.assertEqual(minimum_layout_width(layout, {"%9"}), 3)

    def test_json_layout_has_the_same_minimum(self):
        layout = json.dumps({"L": {"t": "h", "c": [{"t": "p", "I": "%9"}, {"t": "v", "c": [{"t": "p", "I": "%0"}, {"t": "h", "c": [{"t": "p", "l": 1}, {"t": "p", "l": 2}]}]}]}})
        self.assertEqual(minimum_layout_width(layout, {"%9"}), 3)
