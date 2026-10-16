from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from coordinator_test import Harness, ROOT


class CursorLifecycle(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="binder-cursor-")
        self.addCleanup(temporary.cleanup)
        self.server = Harness(Path(temporary.name) / "server")
        self.addCleanup(self.server.close)
        self.client = subprocess.Popen(
            ["tmux", "-S", self.server.socket, "-C", "attach-session", "-f", "no-output,ignore-size", "-t", "work"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=self.server.env,
        )
        self.addCleanup(self.close_client)
        self.server.wait(lambda: self.server.t("display-message", "-p", "#{session_attached}"), "1")

    def close_client(self) -> None:
        self.client.terminate()
        self.client.communicate(timeout=3)

    def start_agent(self, command: str) -> None:
        # A renamed executable gives tmux the CLI's real foreground command
        # without invoking a model or sharing the user's Cursor configuration.
        executable = self.server.directory / command
        shutil.copy2(shutil.which("sleep"), executable)
        self.server.t("respawn-pane", "-k", "-t", self.server.main, "-c", str(self.server.directory), shlex.join([str(executable), "86400"]))
        self.server.wait(lambda: self.server.t("display-message", "-pt", self.server.main, "#{pane_current_command}"), command)
        self.server.t("set-option", "-p", "-t", self.server.main, "allow-set-title", "off")
        self.server.t("select-pane", "-t", self.server.main, "-T", "Project notes")
        self.server.api("refresh")
        self.server.open()
        self.sidebar = self.server.sidebar(self.server.window)
        self.server.wait(lambda: "editor" in self.frame())

    def frame(self) -> str:
        return self.server.t("capture-pane", "-pt", self.sidebar)

    def hook(self, event: str, generation: str = "turn-1", **extra: str) -> None:
        payload = {
            "hook_event_name": event, "conversation_id": "conversation",
            "generation_id": generation, "workspace_roots": [str(self.server.directory)],
            **extra,
        }
        result = subprocess.run(
            [str(ROOT / "scripts/features/hooks/hook-cursor.sh")],
            input=json.dumps(payload), text=True, capture_output=True, timeout=5,
            env={**self.server.env, "TMUX": f"{self.server.socket},{self.server.identity.server_pid},0", "TMUX_PANE": self.server.main},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")

    def test_agent_alias_routes_workspace_hooks_and_renders_completion(self) -> None:
        self.start_agent("agent")
        self.hook("sessionStart", generation="conversation")
        self.hook("beforeSubmitPrompt")
        self.server.wait(lambda: "●" in self.frame())
        self.hook("stop", status="completed")
        self.server.wait(lambda: "✓" in self.frame() and "●" not in self.frame())

    def test_late_observations_cannot_restart_a_finished_turn(self) -> None:
        self.start_agent("cursor-agent")
        self.hook("beforeSubmitPrompt")
        self.server.wait(lambda: "●" in self.frame())
        self.hook("stop", status="completed")
        self.server.wait(lambda: "✓" in self.frame())
        for event in ("afterAgentResponse", "afterAgentThought", "postToolUse"):
            self.hook(event)
        self.assertEqual(self.server.api("doctor")["agents"][self.server.main]["status"], "done", self.frame())
        self.server.wait(lambda: "✓" in self.frame() and "●" not in self.frame())
        self.hook("beforeSubmitPrompt", generation="turn-2")
        self.server.wait(lambda: "●" in self.frame())
        self.hook("stop", status="completed")
        self.assertEqual(self.server.api("doctor")["agents"][self.server.main]["status"], "running", self.frame())
        self.hook("stop", generation="turn-2", status="completed")
        self.server.wait(lambda: "✓" in self.frame())

    def test_returning_to_a_shell_hides_stale_cursor_activity(self) -> None:
        self.start_agent("cursor-agent")
        self.hook("beforeSubmitPrompt")
        self.server.wait(lambda: "●" in self.frame())
        self.server.t("select-pane", "-t", self.server.main, "-T", "Cursor Agent")
        self.server.t("respawn-pane", "-k", "-t", self.server.main, "bash --noprofile --norc")
        self.server.wait(lambda: self.server.t("display-message", "-pt", self.server.main, "#{pane_current_command}"), "bash")
        self.server.api("refresh")
        self.server.wait(lambda: "●" not in self.frame())


if __name__ == "__main__":
    unittest.main()
