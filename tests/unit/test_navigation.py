from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from binder.runtime import Identity
from binder.server import Peer, Server


class NavigationOrdering(unittest.TestCase):
    def setUp(self):
        self.server = Server(Identity("/socket", 1, Path("/unused"), Path("/unused/preferences")))
        self.addCleanup(self.server.selector.close)
        rows = []
        for index in range(3):
            session, window = f"${index}", f"${index}/@{index}"
            rows.extend([
                {"key": session, "kind": "session"},
                {"key": window, "kind": "window"},
                {"key": window + f"/%{index}", "parent": window, "kind": "pane"},
            ])
        self.server.application = SimpleNamespace(rows=rows, latest_preview={}, navigations=[], is_open=True, model=SimpleNamespace(panes={"%9": {}, "%10": {}}), client_for=lambda pane, client: client)
        self.server.flush_events = Mock()
        self.state = {"selected": "$0/@0", "collapsed": ["$0/@0", "$1/@1", "$2/@2"], "query": "keep", "base_id": 0}

    def move(self, pane="%9", action="down", state=None):
        self.server.dispatch(Peer(Mock(), pane=pane), {"command": "navigate", "client": "client", "action": action, "request_id": len(self.server.navigation_inputs) + 1, "state": state or self.state})
        return self.server.previews["client"]["state"]

    def test_steps_from_two_views_use_one_cursor_before_rendering_or_switching(self):
        self.server.application.navigations = [{"client": "client"}, {"client": "observer"}]
        first = self.move()
        second = self.move("%10", state={**self.state, "query": ""})
        self.assertEqual(first["selected"], "$1/@1")
        self.assertEqual(second["selected"], "$2/@2")
        self.assertEqual(second["query"], "keep")
        self.assertEqual(set(second["acknowledged"]), {"%9", "%10"})
        self.assertEqual(len(self.server.previews), 1)
        self.assertEqual(self.server.application.navigations, [{"client": "observer"}])

    def test_branch_input_changes_the_pending_destination_and_collapses_its_pane(self):
        self.move()
        expanded = self.move(action="expand")
        self.assertEqual(expanded["selected"], "$1/@1")
        self.assertNotIn("$1/@1", expanded["collapsed"])
        self.assertEqual(self.move()["selected"], "$1/@1/%1")
        collapsed = self.move(action="collapse")
        self.assertEqual(collapsed["selected"], "$1/@1")
        self.assertIn("$1/@1", collapsed["collapsed"])
        self.assertNotIn("$1", collapsed["collapsed"])
