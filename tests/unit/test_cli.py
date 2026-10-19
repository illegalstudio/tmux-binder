from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from binder.cli import main, read_hook


class HookCommands(unittest.TestCase):
    def test_native_payload_is_read_from_stdin_without_an_event_argument(self):
        raw = '{"hook_event_name":"UserPromptSubmit","session_id":"session"}'
        with patch("sys.stdin", io.StringIO(raw)):
            event, payload, original = read_hook([])
        self.assertEqual(event, "")
        self.assertEqual(payload["hook_event_name"], "UserPromptSubmit")
        self.assertEqual(original, raw)

    def test_grok_compatibility_does_not_deliver_duplicate_claude_or_cursor_updates(self):
        with patch.dict(os.environ, {"GROK_HOOK_EVENT": "post_tool_use", "TMUX_PANE": "%1"}, clear=True), patch("binder.cli.Identity.resolve") as resolve:
            for app in ("claude", "cursor"):
                main(["hook", app, '{"hook_event_name":"PostToolUse"}'])
        resolve.assert_not_called()

    def test_hooks_without_a_pane_do_not_start_or_query_tmux(self):
        with patch.dict(os.environ, {}, clear=True), patch("binder.cli.Identity.resolve") as resolve, patch("binder.cli.request") as request:
            main(["hook", "claude", '{"hook_event_name":"Stop"}'])
        resolve.assert_not_called()
        request.assert_not_called()

    def test_cwd_alone_does_not_make_an_outside_tmux_hook_query_the_server(self):
        with patch.dict(os.environ, {}, clear=True), patch("binder.cli.Identity.resolve") as resolve:
            main(["hook", "codex", '{"hook_event_name":"Stop","cwd":"/project"}'])
        resolve.assert_not_called()

    def test_cursor_prompt_returns_a_valid_neutral_response(self):
        output = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), redirect_stdout(output):
            main(["hook", "cursor", '{"hook_event_name":"beforeSubmitPrompt"}'])
        self.assertEqual(json.loads(output.getvalue()), {"continue": True})

    def test_failed_status_delivery_reports_context_without_blocking_the_agent(self):
        errors = io.StringIO()
        with patch.dict(os.environ, {"TMUX_PANE": "%1"}, clear=True), patch("binder.cli.Identity.resolve", side_effect=RuntimeError("server unavailable")), redirect_stderr(errors):
            main(["hook", "codex", '{"hook_event_name":"UserPromptSubmit"}'])
        self.assertIn("codex status update failed: server unavailable", errors.getvalue())

    def test_native_codex_events_do_not_call_the_legacy_notify_forwarder(self):
        with patch.dict(os.environ, {"TMUX_PANE": "%1"}, clear=True), patch("binder.cli.Identity.resolve"), patch("binder.cli.request") as request, patch("binder.cli.subprocess.run") as forward:
            main(["hook", "codex", '{"hook_event_name":"Stop"}'])
        forward.assert_not_called()
        self.assertEqual(request.call_args.kwargs["app"], "codex")
