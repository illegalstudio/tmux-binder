from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import signal
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from binder.runtime import Identity, request
from binder.controller import EVENT_CHANNEL, EVENT_PENDING

ROOT = Path(__file__).resolve().parents[2]


class Harness:
    def __init__(self, directory: Path, state: Path | None = None) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.directory = directory
        self.socket = str(directory / "s")
        self.state = state or directory / "state"
        self.env = {**os.environ, "TMUX_PANE_TREE_STATE_DIR": str(self.state), "TMUX_PANE_TREE_FONT_DIRS": str(directory / "fonts"), "PYTHONDONTWRITEBYTECODE": "1"}
        self.env.pop("TMUX", None)
        self.env.pop("TMUX_PANE", None)
        self.t("new-session", "-d", "-s", "work", "-n", "editor", "sleep 86400")
        self.t("set-environment", "-g", "TMUX_PANE_TREE_STATE_DIR", str(self.state))
        self.t("set-environment", "-g", "TMUX_PANE_TREE_FONT_DIRS", str(directory / "fonts"))
        self.t("set-option", "-g", "@tmux_pane_tree_icon_theme", "ascii")
        self.t("set-option", "-g", "@tmux_pane_tree_focus_on_open", "0")
        pid = int(self.t("display-message", "-p", "#{pid}"))
        key = hashlib.sha256(os.fsencode(self.socket)).hexdigest()[:16]
        self.identity = Identity(self.socket, pid, self.state / "servers" / f"{key}-{pid}", self.state / "preferences" / f"{key}.json")
        self.main = self.t("display-message", "-p", "-t", "work", "#{pane_id}")
        self.window = self.t("display-message", "-p", "-t", self.main, "#{window_id}")
        self.api("configure")

    def t(self, *args: str) -> str:
        return subprocess.check_output(["tmux", "-S", self.socket, "-f", "/dev/null", *args], env=self.env, text=True, stderr=subprocess.PIPE, timeout=10).rstrip("\n")

    def api(self, command: str, **args):
        return request(self.identity, command, **args)

    def wait(self, read, expected=True, seconds=6):
        deadline = time.monotonic() + seconds
        actual = None
        while time.monotonic() < deadline:
            actual = read()
            if actual == expected:
                return actual
            time.sleep(.025)
        log = self.identity.directory / "engine.log"
        raise AssertionError(f"Expected {expected!r}; received {actual!r}\n{log.read_text() if log.exists() else ''}")

    def open(self):
        self.api("toggle", pane=self.main, window=self.window)
        self.wait(lambda: self.sidebar(self.window) != "")

    def sidebar(self, window: str) -> str:
        return next((line.split("|")[0] for line in self.t("list-panes", "-t", window, "-F", "#{pane_id}|#{@tmux_sidebar_owned}").splitlines() if line.endswith("|1")), "")

    def close(self):
        try:
            self.t("kill-server")
        except (subprocess.SubprocessError, OSError):
            pass
        deadline = time.monotonic() + 4
        while self.identity.endpoint.exists() and time.monotonic() < deadline:
            time.sleep(.025)
        if self.identity.endpoint.exists():
            path = self.identity.directory / "state.json"
            if path.exists():
                pid = json.loads(path.read_text()).get("engine_pid")
                if pid:
                    os.kill(pid, signal.SIGTERM)
                    time.sleep(.1)


class Coordinator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="binder-real-")
        cls.root = Path(cls.temporary.name)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.servers = []

    def tearDown(self):
        for server in self.servers:
            server.close()

    def server(self, suffix="", state=None):
        server = Harness.__new__(Harness)
        self.servers.append(server)
        short = hashlib.sha256((self._testMethodName + suffix).encode()).hexdigest()[:8]
        server.__init__(self.root / short, state)
        return server

    def test_concurrent_clients_use_one_coordinator(self):
        server = self.server()
        server.api("shutdown")
        server.wait(lambda: not server.identity.endpoint.exists())
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda _: server.api("doctor")["pid"], range(24)))
        self.assertEqual(len(set(results)), 1)
        self.assertEqual(server.t("display-message", "-p", "#{session_attached}"), "0")
        self.assertEqual(server.t("list-clients", "-F", "#{client_name}"), "")

    def test_native_hooks_and_reload_are_idempotent(self):
        server = self.server()
        server.api("configure")
        before = server.t("show-hooks", "-g") + server.t("show-hooks", "-gw")
        server.api("configure")
        after = server.t("show-hooks", "-g") + server.t("show-hooks", "-gw")
        self.assertEqual(before, after)
        ours = [line for line in after.splitlines() if "tmux-binder-events" in line]
        self.assertGreaterEqual(len(ours), 18)
        self.assertFalse(any("run-shell" in line for line in ours))
        self.assertTrue(any("binder.py" in line and " t " in line for line in server.t("list-keys", "-T", "prefix").splitlines()))

    def test_native_hook_bursts_retain_one_wakeup_without_a_listener(self):
        server = self.server()
        doctor = server.api("doctor")
        waiter = doctor["waiter_pid"]
        channel = EVENT_CHANNEL + "-" + str(doctor["pid"])
        server.api("shutdown")
        server.wait(lambda: server.identity.endpoint.exists(), False)
        if waiter:
            server.wait(lambda: Path(f"/proc/{waiter}").exists(), False)
        # Consume an earlier wakeup, or release the drain with a signal. Before
        # tmux 3.8 a killed wait-for client, including the terminated
        # coordinator waiter, stays queued and consumes the next signal.
        drain = subprocess.Popen(["tmux", "-S", server.socket, "wait-for", channel], env=server.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            drain.wait(timeout=.2)
        except subprocess.TimeoutExpired:
            server.t("wait-for", "-S", channel)
            drain.wait(timeout=1)
        for count in (1, 2, 3, 4):
            with self.subTest(notifications=count):
                server.t("set-environment", "-gh", EVENT_PENDING, "")
                for _ in range(count):
                    server.t("set-hook", "-R", "-g", "after-set-option")
                subprocess.run(["tmux", "-S", server.socket, "wait-for", channel], env=server.env, capture_output=True, check=True, timeout=1)
                self.assertNotIn(EVENT_PENDING + "=", server.t("show-environment", "-g"))

    def test_global_state_reaches_future_sessions(self):
        server = self.server()
        server.open()
        server.t("new-session", "-d", "-s", "other", "-n", "logs", "sleep 86400")
        server.wait(lambda: server.api("doctor")["views"], 2)
        server.api("close")
        server.wait(lambda: server.api("doctor")["views"], 0)
        self.assertEqual(server.api("doctor")["open"], False)

    def test_oversized_sidebar_preserves_horizontal_content_and_preference(self):
        server = self.server()
        server.t("resize-window", "-t", server.window, "-x", "236", "-y", "58")
        server.t("split-window", "-d", "-h", "-t", server.main, "sleep 86400")
        content = server.t("list-panes", "-t", server.window, "-F", "#{pane_id}|#{pane_pid}")
        server.t("set-option", "-g", "@tmux_pane_tree_width", "236")
        server.open()
        sidebar = server.sidebar(server.window)
        server.wait(lambda: server.api("doctor")["views"], 1)
        self.assertEqual(server.t("display-message", "-pt", sidebar, "#{pane_width}"), "232")
        self.assertEqual(server.api("doctor")["errors"], {})
        for _ in range(3):
            server.api("refresh")
        self.assertEqual(server.t("show-options", "-gqv", "@tmux_pane_tree_width"), "236")
        server.api("close")
        self.assertEqual(server.t("list-panes", "-t", server.window, "-F", "#{pane_id}|#{pane_pid}"), content)

    def test_linked_window_has_one_sidebar_and_two_tree_branches(self):
        server = self.server()
        server.t("new-session", "-d", "-s", "other", "sleep 86400")
        server.t("link-window", "-s", "work:editor", "-t", "other:3")
        server.open()
        server.wait(lambda: server.api("doctor")["views"], 2)
        rows = server.api("snapshot")["rows"]
        copies = [row for row in rows if row.get("pane_id") == server.main]
        self.assertEqual(len(copies), 2)
        self.assertNotEqual(copies[0]["key"], copies[1]["key"])

    def test_menu_actions_preserve_content_processes(self):
        server = self.server()
        server.open()
        before = server.t("display-message", "-pt", server.main, "#{pane_pid}")
        key = next(row["key"] for row in server.api("snapshot")["rows"] if row.get("pane_id") == server.main)
        server.api("menu-action", action="split_horizontal", key=key)
        self.assertEqual(len(server.t("list-panes", "-t", server.window).splitlines()), 3)
        server.api("menu-action", action="zoom", key=key)
        self.assertEqual(server.t("display-message", "-pt", server.main, "#{window_zoomed_flag}"), "1")
        server.api("menu-action", action="zoom", key=key)
        server.api("menu-action", action="mark", key=key)
        self.assertEqual(server.t("display-message", "-pt", server.main, "#{pane_marked}"), "1")
        server.api("menu-action", action="mark", key=key)
        self.assertEqual(server.t("display-message", "-pt", server.main, "#{pane_marked}"), "0")
        server.api("menu-action", action="break_window", key=key)
        server.wait(lambda: server.api("doctor")["views"], 2)
        self.assertEqual(server.t("display-message", "-pt", server.main, "#{pane_pid}"), before)
        server.api("activate", target=server.window)
        self.assertNotEqual(server.api("doctor")["window_main"][server.window], server.main)

    def test_migration_removes_the_legacy_menu_wrapper_from_content_binding(self):
        server = self.server()
        fallback = 'if-shell -F "#{mouse_any_flag}" { send-keys -M } { display-menu -T Pane Select s { select-pane } }'
        legacy = 'if-shell -F "#{m:Sidebar,#{pane_title}}" { if-shell "bash show-context-menu.sh" { source-file old-menu.tmux } } { ' + fallback + ' }'
        server.t("set-option", "-g", "@tmux_pane_tree_original_mouse_binding", legacy)
        server.api("configure")
        self.assertEqual(server.t("show-options", "-gqv", "@tmux_pane_tree_original_mouse_binding"), fallback)

    def test_two_servers_with_same_pane_ids_have_isolated_agent_state(self):
        shared = self.root / "shared-state"
        first, second = self.server("-one", shared), self.server("-two", shared)
        self.assertEqual(first.main, second.main)
        first.api("update-state", pane=first.main, event={"app": "claude", "status": "done"})
        second.api("update-state", pane=second.main, event={"app": "codex", "status": "needs-input"})
        first.api("refresh")
        self.assertEqual(first.api("doctor")["agents"][first.main]["status"], "done")
        self.assertEqual(second.api("doctor")["agents"][second.main]["status"], "needs-input")
        self.assertNotEqual(first.identity.directory, second.identity.directory)

    def test_name_and_title_delimiters_are_transported_without_corruption(self):
        server = self.server()
        name = 'build| "quoted" ;'
        server.api("name", action="rename_window", target=server.main, name=name)
        server.t("set-option", "-p", "-t", server.main, "allow-set-title", "off")
        server.t("select-pane", "-t", server.main, "-T", 'A|B\n界 "title"')
        server.api("refresh")
        self.assertTrue(any(row.get("name") == name for row in server.api("snapshot")["rows"]))
        self.assertEqual(server.t("list-windows", "-a", "-F", "#{window_name}"), name)
        self.assertEqual(len(server.api("snapshot")["rows"]), 3)

    def test_out_of_order_and_parallel_agent_updates_are_serialized(self):
        server = self.server()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda stamp: server.api("update-state", pane=server.main, event={"app": "claude", "status": "running", "updated_at": stamp}), range(100, 150)))
        server.api("update-state", pane=server.main, event={"app": "claude", "status": "done", "updated_at": 200})
        server.api("update-state", pane=server.main, event={"app": "claude", "status": "running", "updated_at": 175})
        state = server.api("doctor")["agents"][server.main]
        self.assertEqual(state["updated_at"], 200)
        self.assertEqual(state["status"], "done")

    def test_agent_json_argument_does_not_wait_for_stdin(self):
        server = self.server()
        env = {**server.env, "TMUX_PANE": server.main}
        child = subprocess.Popen([sys.executable, str(ROOT / "scripts/binder.py"), "--socket", server.socket, "hook", "claude", '{"hook_event_name":"Stop","message":"done"}'], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        child.wait(timeout=2)
        out, err = child.communicate()
        self.assertEqual(child.returncode, 0, err.decode())
        self.assertEqual(server.api("doctor")["agents"][server.main]["status"], "done")

    def test_shared_agent_environment_routes_by_workspace_and_remembers_the_session(self):
        server = self.server()
        project = self.root / "shared-agent-project"
        project.mkdir()
        target = server.t("new-session", "-dP", "-F", "#{pane_id}", "-s", "other", "-c", str(project), "sleep 86400")
        for pane in (server.main, target):
            server.t("set-option", "-p", "-t", pane, "allow-set-title", "off")
            server.t("select-pane", "-t", pane, "-T", "codex")
        server.api("refresh")
        env = {**server.env, "TMUX": f"{server.socket},{server.identity.server_pid},0", "TMUX_PANE": server.main}

        def emit(event, **extra):
            payload = {"hook_event_name": event, "session_id": "shared-session", "cwd": str(project), **extra}
            result = subprocess.run([sys.executable, str(ROOT / "scripts/binder.py"), "hook", "codex", json.dumps(payload)], env=env, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)

        emit("UserPromptSubmit")
        states = server.api("doctor")["agents"]
        self.assertIn(target, states)
        self.assertNotIn(server.main, states)
        self.assertEqual(states[target]["status"], "running")
        emit("Stop", cwd=str(project / "nested"))
        self.assertEqual(server.api("doctor")["agents"][target]["status"], "done")

    def test_bash_entrypoint_for_agent_events_remains_compatible(self):
        server = self.server()
        env = {**server.env, "TMUX": f"{server.socket},{server.identity.server_pid},0", "TMUX_PANE": server.main}
        result = subprocess.run(["bash", str(ROOT / "scripts/features/hooks/hook-claude.sh")], input='{"hook_event_name":"UserPromptSubmit"}', text=True, capture_output=True, env=env, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(server.api("doctor")["agents"][server.main]["status"], "running")

    def test_installed_agent_handlers_deliver_statuses_on_their_tmux_server(self):
        from binder.installer import DEFAULT_PATHS, install
        from unittest.mock import patch

        server = self.server()
        configured = {key: str(server.directory / key) for key in DEFAULT_PATHS}
        with patch.dict(os.environ, {**configured, "PLUGIN_DST": str(ROOT)}):
            install(["claude", "codex", "cursor", "opencode", "pi", "grok"])
        env = {**server.env, "TMUX": f"{server.socket},{server.identity.server_pid},0", "TMUX_PANE": server.main}
        cases = {
            "claude": [("UserPromptSubmit", {}, "running"), ("PermissionRequest", {}, "needs-input"), ("PostToolUse", {}, "running"), ("Stop", {}, "done"), ("UserPromptSubmit", {}, "running"), ("StopFailure", {}, "error")],
            "codex": [("UserPromptSubmit", {}, "running"), ("PermissionRequest", {}, "needs-input"), ("PostToolUse", {}, "running"), ("SubagentStart", {"agent_id": "child"}, "subagent-running"), ("SubagentStop", {"agent_id": "child"}, "running"), ("Stop", {}, "done")],
            "cursor": [("beforeSubmitPrompt", {}, "running"), ("postToolUseFailure", {"failure_type": "permission_denied"}, "needs-input"), ("postToolUse", {}, "running"), ("stop", {"status": "completed"}, "done")],
            "opencode": [("session.status", {"status": "running"}, "running"), ("permission.asked", {}, "needs-input"), ("session.idle", {}, "done")],
            "pi": [("agent_start", {}, "running"), ("agent_end", {}, "done")],
            "grok": [("UserPromptSubmit", {}, "running"), ("PermissionDenied", {}, "needs-input"), ("PostToolUse", {}, "running"), ("Stop", {}, "done"), ("Notification", {"notificationType": "idle_prompt"}, "done"), ("StopFailure", {}, "error"), ("StopCancelled", {}, "idle")],
        }
        for app, events in cases.items():
            server.t("set-option", "-p", "-t", server.main, "allow-set-title", "off")
            server.t("select-pane", "-t", server.main, "-T", app)
            for event, extra, expected in events:
                with self.subTest(app=app, event=event):
                    if app in {"claude", "codex", "cursor", "grok"}:
                        key = {"claude": "CLAUDE_SETTINGS", "codex": "CODEX_HOOKS", "cursor": "CURSOR_HOOKS", "grok": "GROK_HOOKS"}[app]
                        entries = json.loads(Path(configured[key]).read_text())["hooks"][event]
                        command = entries[0]["command"] if app == "cursor" else entries[0]["hooks"][0]["command"]
                    else:
                        command = "bash " + shlex.quote(str(ROOT / f"scripts/features/hooks/hook-{app}.sh"))
                    payload = {"hook_event_name": event, "event": event, "session_id": app, **extra}
                    result = subprocess.run(shlex.split(command), input=json.dumps(payload), text=True, capture_output=True, env=env, timeout=5)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stderr, "")
                    server.wait(lambda: server.api("doctor")["agents"].get(server.main, {}).get("status"), expected)
                    if app == "cursor":
                        self.assertEqual(json.loads(result.stdout), {"continue": True} if event == "beforeSubmitPrompt" else {})

    def test_view_failure_recovers_and_content_pid_is_unchanged(self):
        server = self.server()
        before = server.t("display-message", "-p", "-t", server.main, "#{pane_pid}")
        server.open()
        sidebar = server.sidebar(server.window)
        pid = int(server.t("display-message", "-p", "-t", sidebar, "#{pane_pid}"))
        os.kill(pid, signal.SIGTERM)
        server.wait(lambda: server.t("display-message", "-p", "-t", sidebar, "#{pane_pid}") != str(pid))
        self.assertTrue(server.api("doctor")["open"])
        self.assertEqual(server.t("display-message", "-p", "-t", server.main, "#{pane_pid}"), before)

    def test_coordinator_crash_recovers_without_changing_panes(self):
        server = self.server()
        server.open()
        sidebar = server.sidebar(server.window)
        server.wait(lambda: "editor" in server.t("capture-pane", "-pt", sidebar))
        processes = server.t("list-panes", "-a", "-F", "#{pane_id}|#{pane_pid}")
        engine_pid = server.api("doctor")["pid"]
        os.kill(engine_pid, signal.SIGKILL)
        server.wait(lambda: server.api("doctor")["pid"] != engine_pid)
        self.assertEqual(server.t("list-panes", "-a", "-F", "#{pane_id}|#{pane_pid}"), processes)
        server.t("rename-window", "-t", server.window, "recovered")
        server.wait(lambda: "recovered" in server.t("capture-pane", "-pt", sidebar))

    def test_idle_views_issue_no_periodic_tmux_queries(self):
        server = self.server()
        for index in range(1, 10):
            server.t("new-window", "-d", "-t", "work", "-n", f"view-{index}", "sleep 86400")
        server.open()
        server.wait(lambda: server.api("doctor")["views"], 10)
        time.sleep(.4)
        before = server.api("doctor")["tmux_calls"]
        time.sleep(2)
        after = server.api("doctor")["tmux_calls"]
        self.assertEqual(after, before)

    def test_animated_titles_do_not_wake_the_coordinator_for_each_frame(self):
        server = self.server()
        server.open()
        animation = "import sys,time\nfor i in range(120):\n frame = '\\u25d0\\u25d1'[i % 2] if i % 3 == 0 else chr(0x2801 + i % 100)\n sys.stdout.write('\\033]2;' + frame + ' Working\\007'); sys.stdout.flush(); time.sleep(.01)\nprint('animation complete',flush=True)\ntime.sleep(86400)"
        server.t("respawn-pane", "-k", "-t", server.main, shlex.join([sys.executable, "-c", animation]))
        server.wait(lambda: server.t("show-options", "-pqv", "-t", server.main, "@tmux_pane_tree_observed_title"), "⠿ Working")
        time.sleep(.15)
        before = server.api("doctor")
        time.sleep(.8)
        after = server.api("doctor")
        self.assertEqual(after["tmux_calls"], before["tmux_calls"])
        server.wait(lambda: "animation complete" in server.t("capture-pane", "-pt", server.main))
        server.t("select-pane", "-t", server.main, "-T", "✳ Waiting")
        server.wait(lambda: server.t("show-options", "-pqv", "-t", server.main, "@tmux_pane_tree_observed_title"), "✳ Waiting")
        server.wait(lambda: server.api("doctor")["events"] > after["events"])

    def test_closed_sidebar_does_not_probe_attached_agent_terminals(self):
        server = self.server()
        client = subprocess.Popen(["tmux", "-S", server.socket, "-C", "attach-session", "-f", "no-output,ignore-size", "-t", "work"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=server.env)
        try:
            server.wait(lambda: server.t("display-message", "-p", "#{session_attached}"), "1")
            server.t("select-pane", "-t", server.main, "-T", "codex")
            server.api("refresh")
            time.sleep(.15)
            before = server.api("doctor")["tmux_calls"]
            time.sleep(1.2)
            self.assertEqual(server.api("doctor")["tmux_calls"], before)
        finally:
            client.terminate()
            client.communicate(timeout=3)

    def test_invalid_requests_do_not_kill_coordinator(self):
        server = self.server()
        pid = server.api("doctor")["pid"]
        with self.assertRaises(RuntimeError):
            server.api("missing-command")
        self.assertEqual(server.api("doctor")["pid"], pid)

    def test_invalid_configuration_is_rejected_without_losing_the_coordinator(self):
        server = self.server()
        pid = server.api("doctor")["pid"]
        server.t("set-option", "-g", "@tmux_pane_tree_icon_theme", "invalid")
        server.api("refresh")
        self.assertIn("configuration", server.api("doctor")["errors"])
        self.assertEqual(server.api("doctor")["pid"], pid)
        server.t("set-option", "-g", "@tmux_pane_tree_icon_theme", "ascii")
        server.api("refresh")
        self.assertNotIn("configuration", server.api("doctor")["errors"])

    def test_removing_last_content_pane_removes_its_window(self):
        server = self.server()
        server.t("new-window", "-d", "-t", "work", "-n", "survivor", "sleep 86400")
        server.open()
        server.api("remove", target=server.main)
        server.wait(lambda: server.api("doctor")["windows"], 1)
        self.assertTrue(server.api("doctor")["open"])
        self.assertEqual(server.api("doctor")["views"], 1)
        self.assertNotIn(server.window, server.api("doctor")["window_main"])


if __name__ == "__main__":
    unittest.main()
