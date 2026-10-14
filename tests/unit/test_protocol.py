from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from binder.config import SHORTCUTS, enabled, integer, option, parse_options, shortcuts, visuals
from binder.runtime import MAX_MESSAGE, Identity, atomic_json, decode, encode, read_json
from binder.tmux import SCHEMAS, parse_records


def record(kind: str, values: list[str]) -> bytes:
    return kind.encode() + b"".join(str(len(value.encode())).encode() + b":" + value.encode() for value in values) + b"\n"


class Protocol(unittest.TestCase):
    def test_unicode_pipe_newline_and_quotes_in_tmux_records(self):
        data = {key: "" for key in SCHEMAS["P"]}
        data.update(pane_id="%1", window_id="@2", pane_title='A|B "quote"\n界😀\\end\tline')
        parsed = parse_records(record("P", list(data.values())))
        self.assertEqual(parsed["P"], [data])

    def test_multiple_record_types_and_linked_windows(self):
        raw = record("S", ["$0", "one", "0"]) + record("S", ["$1", "two", "1"])
        parsed = parse_records(raw)
        self.assertEqual(len(parsed["S"]), 2)

    def test_malformed_records_are_rejected(self):
        for raw in (b"X", b"Pno:", b"P100:short", record("S", ["$0", "one", "0"])[:-1]):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_records(raw)

    def test_ipc_version_and_payload_limits(self):
        self.assertEqual(decode(encode({"name": "界\n\"x\""}))["name"], "界\n\"x\"")
        for value in [b'{"protocol":999}', b'[]', b'{}', b"x" * (MAX_MESSAGE + 1)]:
            with self.assertRaises(ValueError):
                decode(value)

    def test_runtime_is_namespaced_by_server_and_generation(self):
        with patch.dict(os.environ, {"TMUX_PANE_TREE_STATE_DIR": "/test/state", "TMUX": "/socket/one,10,0"}):
            first = Identity.resolve()
            os.environ["TMUX"] = "/socket/two,10,0"
            other = Identity.resolve()
            os.environ["TMUX"] = "/socket/one,11,0"
            restarted = Identity.resolve()
        self.assertNotEqual(first.directory, other.directory)
        self.assertNotEqual(first.directory, restarted.directory)
        self.assertEqual(first.preferences, restarted.preferences)

    def test_atomic_state_never_leaves_temporary_files(self):
        with tempfile.TemporaryDirectory(prefix="binder-unit-") as directory:
            path = Path(directory) / "state.json"
            atomic_json(path, {"message": "quoted \" and\nUnicode 界"})
            self.assertEqual(read_json(path)["message"], "quoted \" and\nUnicode 界")
            self.assertEqual([p.name for p in Path(directory).iterdir()], ["state.json"])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_empty_missing_and_invalid_json_are_distinct(self):
        with tempfile.TemporaryDirectory(prefix="binder-unit-") as directory:
            path = Path(directory) / "state.json"
            self.assertEqual(read_json(path), {})
            path.write_text("[]")
            with self.assertRaises(ValueError):
                read_json(path)


class Configuration(unittest.TestCase):
    def test_alias_precedence_including_explicit_empty(self):
        options = {"@tmux_sidebar_filter": "old", "@tmux_pane_tree_filter": ""}
        self.assertEqual(option(options, "filter"), "")

    def test_quoted_tmux_option_values(self):
        parsed = parse_options('@tmux_pane_tree_filter "claude,codex"\n@tmux_sidebar_icon_codex "x y"\nstatus off\n')
        self.assertEqual(parsed["@tmux_sidebar_icon_codex"], "x y")
        self.assertNotIn("status", parsed)

    def test_shortcut_defaults_and_custom_sequences(self):
        options = {"@tmux_pane_tree_close_pane_shortcut": "dd"}
        self.assertEqual(shortcuts(options)["close_pane"], "dd")
        self.assertEqual(shortcuts({}), SHORTCUTS)

    def test_invalid_shortcut_set_resets_together(self):
        for changes in [
            {"close_pane": ""}, {"close_pane": "q"}, {"close_pane": "j", "go_bottom": "j"},
            {"close_pane": "g"}, {"close_pane": "C-long"},
        ]:
            options = {"@tmux_pane_tree_" + key + "_shortcut": value for key, value in changes.items()}
            self.assertEqual(shortcuts(options), SHORTCUTS)

    def test_options_validate_numbers_and_booleans(self):
        self.assertEqual(integer("-1", 8), 8)
        self.assertEqual(integer("0", 8), 0)
        self.assertEqual(integer("bad", 8), 8)
        self.assertTrue(enabled("YES"))
        self.assertFalse(enabled("off", True))

    def test_explicit_icon_and_badge_overrides(self):
        icons, badges = visuals({"@tmux_pane_tree_icon_theme": "ascii", "@tmux_pane_tree_icon_codex": "X!", "@tmux_pane_tree_badge_done": ""})
        self.assertEqual(icons["codex"], "X!")
        self.assertEqual(badges["done"], "")
        with self.assertRaises(ValueError):
            visuals({"@tmux_pane_tree_icon_theme": "missing"})
