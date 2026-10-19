from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from binder.installer import install
from coordinator_test import Harness, ROOT
from live_preview_real_test import Terminal


class ClaudeLifecycle(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="binder-claude-")
        self.addCleanup(temporary.cleanup)
        self.server = Harness(Path(temporary.name))
        self.addCleanup(self.server.close)
        # Fresh %0 IDs hide strftime's numeric padding of literal pane IDs.
        # Exercise the same multi-digit ID boundary as a long-lived server.
        for _ in range(12):
            unused = self.server.t("split-window", "-dP", "-F", "#{pane_id}", "-t", self.server.main, "sleep 86400")
            self.server.t("kill-pane", "-t", unused)
        previous = self.server.main
        self.server.main = self.server.t("split-window", "-dP", "-F", "#{pane_id}", "-t", previous, "sleep 86400")
        self.server.t("kill-pane", "-t", previous)
        self.server.api("refresh")
        # Reproduce Claude's foreground command and static title without a model.
        executable = self.server.directory / "claude"
        self.executable = executable
        shutil.copy2(shutil.which("sleep"), executable)
        self.server.t("respawn-pane", "-k", "-t", self.server.main, "-c", str(self.server.directory), shlex.join([str(executable), "86400"]))
        self.server.wait(lambda: self.server.t("display-message", "-pt", self.server.main, "#{pane_current_command}"), "claude")
        self.server.t("set-option", "-p", "-t", self.server.main, "allow-set-title", "off")
        self.server.t("select-pane", "-t", self.server.main, "-T", "✳ Project notes")
        self.terminal = Terminal(self.server)
        self.addCleanup(self.terminal.close)
        self.addCleanup(self.server.api, "close")
        self.terminal.wait(lambda: self.server.t("display-message", "-pt", self.server.main, "#{session_attached}"), "1")
        self.server.open()
        self.sidebar = self.server.sidebar(self.server.window)
        self.terminal.wait(lambda: "editor" in self.frame())

    def frame(self) -> str:
        return self.server.t("capture-pane", "-pt", self.sidebar)

    def display_status(self, status: str) -> None:
        content = "\x1b[2J\x1b[H● Running 1 shell command…\n\n" + status + "\n\n──────────────── Piano ─\n❯ \n────────────────────────\n  Fable 5.1 | project\n"
        command = "printf '%s' " + shlex.quote(content) + "; exec " + shlex.join([str(self.executable), "86400"])
        self.server.t("respawn-pane", "-k", "-t", self.server.main, command)
        self.server.wait(lambda: self.server.t("display-message", "-pt", self.server.main, "#{pane_current_command}"), "claude")

    def hook(self, event: str, **extra: str) -> None:
        payload = {"hook_event_name": event, "session_id": "conversation", "cwd": str(self.server.directory), **extra}
        result = subprocess.run(
            [str(ROOT / "scripts/features/hooks/hook-claude.sh")],
            input=json.dumps(payload), text=True, capture_output=True, timeout=5,
            env={**self.server.env, "TMUX": f"{self.server.socket},{self.server.identity.server_pid},0", "TMUX_PANE": self.server.main},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "")

    def done(self) -> None:
        self.hook("UserPromptSubmit")
        self.terminal.wait(lambda: "●" in self.frame())
        self.hook("Stop")
        self.terminal.wait(lambda: "✓" in self.frame() and "●" not in self.frame())

    def test_new_prompt_replaces_completion_with_activity(self) -> None:
        self.done()
        self.hook("Notification", notification_type="idle_prompt")
        self.hook("UserPromptSubmit")
        self.terminal.wait(lambda: "●" in self.frame() and "✓" not in self.frame())

    def test_delayed_prompt_payload_still_replaces_completion(self) -> None:
        self.done()
        child = subprocess.Popen(
            [str(ROOT / "scripts/features/hooks/hook-claude.sh")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={**self.server.env, "TMUX": f"{self.server.socket},{self.server.identity.server_pid},0", "TMUX_PANE": self.server.main},
        )
        try:
            # The producer can be busy after spawning an asynchronous hook.
            time.sleep(.3)
            output, errors = child.communicate(json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "conversation"}), timeout=5)
            self.assertEqual(child.returncode, 0, errors)
            self.assertEqual(output + errors, "")
            self.terminal.wait(lambda: "●" in self.frame() and "✓" not in self.frame())
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate()

    def test_installed_completion_cannot_overtake_the_next_prompt(self) -> None:
        self.done()
        settings = self.server.directory / "claude.json"
        with patch.dict("os.environ", {"CLAUDE_SETTINGS": str(settings), "PLUGIN_DST": str(ROOT)}):
            install(["claude"])
        hooks = json.loads(settings.read_text())["hooks"]
        handler = hooks["Stop"][0]["hooks"][0]
        # Delay the old completion's delivery. Claude waits for synchronous
        # handlers before accepting another prompt, but not for async ones.
        child = subprocess.Popen(
            ["bash", "-c", 'sleep .3; exec "$@"', "stop", *shlex.split(handler["command"])],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={**self.server.env, "TMUX": f"{self.server.socket},{self.server.identity.server_pid},0", "TMUX_PANE": self.server.main},
        )
        try:
            child.stdin.write(json.dumps({"hook_event_name": "Stop", "session_id": "conversation"}))
            child.stdin.close()
            child.stdin = None
            if not handler.get("async"):
                child.wait(timeout=5)
            self.hook("UserPromptSubmit")
            output, errors = child.communicate(timeout=5)
            self.assertEqual(child.returncode, 0, errors)
            self.assertEqual(output + errors, "")
            self.terminal.wait(lambda: "●" in self.frame() and "✓" not in self.frame())
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate()

    def test_tool_continuation_replaces_completion_before_tool_finishes(self) -> None:
        self.done()
        self.hook("PreToolUse", tool_name="Bash")
        self.terminal.wait(lambda: "●" in self.frame() and "✓" not in self.frame())
        self.hook("PostToolUse", tool_name="Bash")
        self.hook("Stop")
        self.terminal.wait(lambda: "✓" in self.frame() and "●" not in self.frame())

    def test_busy_terminal_replaces_stale_completion_without_a_hook(self) -> None:
        self.done()

        # The observed Testing session has a fresh conversation, a static title
        # and no accepted hook since the previous conversation's Stop.
        self.display_status("✢ Onioning… (13m 44s · ↓ 47.6k tokens)")
        self.terminal.wait(lambda: "●" in self.frame() and "✓" not in self.frame())
        self.assertEqual(self.server.api("doctor")["agents"][self.server.main]["status"], "done")
        self.display_status("✻ Cooked for 13m 44s · done 5:00 PM")
        self.terminal.wait(lambda: "✓" in self.frame() and "●" not in self.frame())

    def test_busy_footer_with_a_tip_replaces_stale_completion(self) -> None:
        self.done()
        self.display_status("· Razzmatazzing… (running Stop hooks… 1/2 · 18s · ↓ 626 tokens)\n  ⎿  Tip: Share Claude Code and earn credits · /passes")
        self.terminal.wait(lambda: "●" in self.frame() and "✓" not in self.frame())
        self.display_status("✻ Worked for 18s · done 5:00 PM")
        self.terminal.wait(lambda: "✓" in self.frame() and "●" not in self.frame())

    def test_subagent_tools_keep_delegation_and_late_tools_keep_completion(self) -> None:
        self.hook("UserPromptSubmit")
        self.hook("SubagentStart", agent_id="child")
        self.hook("PreToolUse", agent_id="child", tool_name="Bash")
        self.terminal.wait(lambda: "↳" in self.frame())
        self.hook("Stop")
        self.terminal.wait(lambda: "✓" in self.frame())
        for event in ("PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest", "SubagentStop"):
            self.hook(event, agent_id="child")
            self.assertEqual(self.server.api("doctor")["agents"][self.server.main]["status"], "done", self.frame())
        self.terminal.wait(lambda: "✓" in self.frame() and "●" not in self.frame() and "↳" not in self.frame())


if __name__ == "__main__":
    unittest.main()
