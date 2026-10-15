from __future__ import annotations

import json
import unittest
from pathlib import Path

from binder.agents import AgentStore, effective_status, live_agent_app, normalize, resolve_agent_pane, terminal_status


class AgentEvents(unittest.TestCase):
    def test_grok_native_names_and_camelcase_lifecycle(self):
        for command, title in (("grok", ""), ("grok-native", ""), ("node", "Grok Build")):
            self.assertEqual(live_agent_app(command, title, {}), "grok")
        store = AgentStore()
        for event, expected in (("session_start", "idle"), ("user_prompt_submit", "running"), ("post_tool_use", "running"), ("permission_denied", "needs-input"), ("post_tool_use_failure", "error"), ("stop", "done"), ("stop_cancelled", "idle")):
            normalized = normalize("grok", {"hookEventName": event, "sessionId": "session", "promptId": "turn"})
            store.update("%1", normalized, {})
            self.assertEqual(store.panes["%1"]["status"], expected)
            self.assertEqual(store.panes["%1"]["session_id"], "session")

    def test_grok_idle_notifications_preserve_completion_and_ignore_child_stops(self):
        store = AgentStore()
        store.update("%1", normalize("grok", {"hookEventName": "user_prompt_submit", "promptId": "new"}), {})
        self.assertFalse(store.update("%1", normalize("grok", {"hookEventName": "stop_cancelled", "promptId": "old"}), {}))
        self.assertFalse(store.update("%1", normalize("grok", {"hookEventName": "stop", "subagentType": "explore"}), {}))
        self.assertEqual(store.panes["%1"]["status"], "running")
        store.update("%1", normalize("grok", {"hookEventName": "stop", "promptId": "new"}), {})
        store.update("%1", normalize("grok", {"hookEventName": "notification", "notificationType": "idle_prompt"}), {})
        self.assertEqual(store.panes["%1"]["status"], "done")

    def test_workspace_routing_rejects_stale_environment_and_ignores_non_agent_panes(self):
        panes = {
            "%1": {"sidebar": False, "pane_current_command": "codex", "pane_title": "", "pane_current_path": "/projects/one"},
            "%2": {"sidebar": False, "pane_current_command": "codex", "pane_title": "", "pane_current_path": "/projects/two"},
            "%3": {"sidebar": False, "pane_current_command": "bash", "pane_title": "", "pane_current_path": "/projects/two"},
            "%4": {"sidebar": True, "pane_current_command": "python3", "pane_title": "", "pane_current_path": "/projects/two"},
        }
        self.assertEqual(resolve_agent_pane(panes, {}, {"app": "codex"}, "%1", ["/projects/two"]), "%2")
        self.assertEqual(resolve_agent_pane(panes, {}, {"app": "codex"}, "%1", ["/unrelated"]), "")

    def test_workspace_routing_does_not_guess_between_equal_candidates(self):
        panes = {pid: {"sidebar": False, "pane_current_command": "codex", "pane_title": "", "pane_current_path": "/projects/shared"} for pid in ("%1", "%2")}
        event = {"app": "codex", "session_id": "session"}
        self.assertEqual(resolve_agent_pane(panes, {}, event, "%1", ["/projects/shared"]), "")
        states = {"%2": {"app": "codex", "session_id": "session"}}
        self.assertEqual(resolve_agent_pane(panes, states, event, "%1", ["/projects/shared/nested"]), "%2")
        self.assertEqual(resolve_agent_pane(panes, {}, event, "%1", ["/elsewhere"], explicit=True), "%1")

    def test_agent_session_binding_survives_events_without_a_session_id(self):
        store = AgentStore()
        store.update("%1", normalize("codex", {"hook_event_name": "UserPromptSubmit", "session_id": "session"}), {})
        store.update("%1", normalize("codex", {"hook_event_name": "PostToolUse"}), {})
        self.assertEqual(store.panes["%1"]["session_id"], "session")

    def test_codex_native_lifecycle_and_subagents(self):
        store = AgentStore()
        for event, expected in (("SessionStart", "idle"), ("UserPromptSubmit", "running"), ("SubagentStart", "subagent-running"), ("PostToolUse", "subagent-running"), ("PermissionRequest", "needs-input"), ("PostToolUse", "subagent-running"), ("SubagentStop", "running"), ("Stop", "done"), ("Interrupt", "idle"), ("SessionEnd", "idle")):
            with self.subTest(event=event):
                store.update("%1", normalize("codex", {"hook_event_name": event, "session_id": "parent", "turn_id": "run", "agent_id": "child", "permission_mode": "bypassPermissions"}), {})
                self.assertEqual(store.panes["%1"]["status"], expected)

    def test_existing_agent_event_contracts(self):
        fixtures = json.loads((Path(__file__).parents[1] / "fixtures/agent_events.json").read_text())
        self.assertGreaterEqual(len(fixtures), 30)
        for case in fixtures:
            with self.subTest(app=case["app"], event=case["event"], payload=case["payload"]):
                self.assertEqual(normalize(case["app"], case["payload"], case["event"])["status"], case["status"])

    def test_unknown_events_are_ignored(self):
        for app in ("claude", "codex", "cursor", "opencode", "pi", "kiro"):
            with self.subTest(app=app):
                self.assertEqual(normalize(app, {"event": "heartbeat"})["status"], "")

    def test_delegated_completions_are_suppressed_without_session_id(self):
        store = AgentStore()
        self.assertFalse(store.update("%1", normalize("codex", {"permission_mode": "delegate"}, "agent-turn-complete"), {}))
        self.assertNotIn("%1", store.panes)

    def test_delegated_session_is_remembered(self):
        store = AgentStore()
        store.update("%1", normalize("codex", {"session_id": "child", "permission_mode": "delegate"}, "task_started"), {})
        self.assertFalse(store.update("%1", normalize("codex", {"session_id": "child"}, "agent-turn-complete"), {}))
        self.assertEqual(store.panes["%1"]["status"], "running")

    def test_cursor_subagent_events_do_not_finish_parent(self):
        store = AgentStore()
        self.assertFalse(store.update("%1", normalize("cursor", {"event": "subagentStop"}), {}))

    def test_claude_lifecycle_tracks_distinct_subagents(self):
        store = AgentStore()
        for agent in ("a", "b", "a"):
            store.update("%1", normalize("claude", {"hook_event_name": "SubagentStart", "agent_id": agent}), {})
        self.assertEqual(store.panes["%1"]["subagent_count"], 2)
        for agent in ("a", "a"):
            store.update("%1", normalize("claude", {"hook_event_name": "SubagentStop", "agent_id": agent}), {})
        self.assertEqual(store.panes["%1"]["subagent_count"], 1)
        store.update("%1", normalize("claude", {"hook_event_name": "SubagentStop", "agent_id": "b"}), {})
        self.assertEqual(store.panes["%1"]["status"], "running")
        self.assertEqual(store.panes["%1"]["subagent_count"], 0)

    def test_turn_boundary_clears_missing_subagent_stop(self):
        store = AgentStore()
        store.update("%1", normalize("claude", {"hook_event_name": "SubagentStart"}), {})
        store.update("%1", normalize("claude", {"hook_event_name": "Stop"}), {})
        self.assertEqual(store.panes["%1"]["subagent_count"], 0)

    def test_old_events_cannot_replace_new_state(self):
        store = AgentStore()
        store.update("%1", {"app": "claude", "status": "done", "updated_at": 200}, {})
        self.assertFalse(store.update("%1", {"app": "claude", "status": "running", "updated_at": 100}, {}))
        self.assertEqual(store.panes["%1"]["status"], "done")

    def test_equal_timestamp_prioritizes_attention(self):
        store = AgentStore()
        store.update("%1", {"status": "needs-input", "updated_at": 200}, {})
        store.update("%1", {"status": "running", "updated_at": 200}, {})
        self.assertEqual(store.panes["%1"]["status"], "needs-input")

    def test_late_completion_from_another_run_is_rejected(self):
        store = AgentStore()
        store.update("%1", {"status": "running", "run_id": "new", "updated_at": 200}, {})
        self.assertFalse(store.update("%1", {"status": "done", "run_id": "old", "updated_at": 300}, {}))

    def test_focus_only_acknowledges_input(self):
        store = AgentStore()
        store.update("%1", {"status": "done"}, {})
        self.assertFalse(store.acknowledge("%1"))
        store.update("%2", {"status": "needs-input"}, {})
        self.assertTrue(store.acknowledge("%2"))
        self.assertEqual(store.panes["%2"]["status"], "idle")
        self.assertEqual(store.panes["%1"]["status"], "done")

    def test_invalid_status_is_rejected(self):
        with self.assertRaises(ValueError):
            AgentStore().update("%1", {"status": "surprise"}, {})

    def test_agent_detection_preserves_live_shell_and_claude_versions(self):
        state = {"app": "claude", "status": "running"}
        self.assertEqual(live_agent_app("bash", "bash", state), "")
        self.assertEqual(live_agent_app("2_1_201", "assistant", state), "claude")
        for command, app in [("codex-aarch64-apple-darwin", "codex"), ("cursor-agent", "cursor"), ("opencode", "opencode"), ("kiro-cli", "kiro")]:
            self.assertEqual(live_agent_app(command, "", {}), app)

    def test_claude_title_idle_clears_stale_busy(self):
        self.assertEqual(effective_status("claude", "✳ ready", {"status": "running"}), "")
        self.assertEqual(effective_status("claude", "✳ ready", {"status": "done"}), "done")
        self.assertEqual(effective_status("claude", "⠋ processing", {"status": "subagent-running"}), "subagent-running")

    def test_codex_terminal_compatibility(self):
        busy = "header\n• Working (2s • esc to interrupt)\n"
        self.assertEqual(terminal_status(busy), "running")
        self.assertEqual(terminal_status("user quoted Working in a message"), "")
        self.assertEqual(effective_status("codex", "", {"status": "done"}, terminal_status(busy)), "running")
