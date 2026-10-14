from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import time
from pathlib import Path

from .runtime import ROOT

DEFAULT_PATHS = {'CLAUDE_SETTINGS': '~/.claude/settings.json', 'CODEX_CONFIG': '~/.codex/config.toml', 'CURSOR_HOOKS': '~/.cursor/hooks.json', 'OPENCODE_PLUGIN': '~/.config/opencode/plugins/tmux-pane-tree.js', 'PI_EXTENSION': '~/.pi/agent/extensions/tmux-pane-tree.ts', 'KIRO_AGENT': '~/.kiro/agents/tmux-pane-tree.json', 'KIRO_CLI_SETTINGS': '~/.kiro/settings/cli.json'}


def paths() -> dict[str, Path]:
    return {key: Path(os.environ.get(key, default)).expanduser() for key, default in DEFAULT_PATHS.items()}


def _write(path: Path, text: str) -> None:
    temporary = path.with_name(path.name + ".binder-new")
    try:
        temporary.write_text(text)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def backup(path: Path) -> None:
    if not path.exists():
        return
    stamp = time.strftime("%Y%m%d%H%M%S") + str(time.time_ns() % 1000000000)
    target = path.with_name(path.name + ".bak-tmux-sidebar-" + stamp)
    shutil.copy2(path, target)
    for previous in sorted(path.parent.glob(path.name + ".bak-tmux-sidebar-*"), reverse=True)[3:]:
        previous.unlink()

def install_claude(configured: dict[str, Path], root: Path) -> None:
    path = configured['CLAUDE_SETTINGS']
    plugin_dir = root
    text = path.read_text().strip()
    data = json.loads(text) if text else {}
    if not isinstance(data, dict):
        data = {}
    hooks = data.setdefault("hooks", {})
    command = str(plugin_dir / "scripts/features/hooks/hook-claude.sh")

    def ensure_event(event_name: str, async_enabled: bool) -> None:
        rules = hooks.setdefault(event_name, [])
        target = None
        for rule in rules:
            if rule.get("matcher", "") == "":
                target = rule
                break
        if target is None:
            target = {"matcher": "", "hooks": []}
            rules.append(target)
        hook_list = target.setdefault("hooks", [])
        for hook in hook_list:
            if hook.get("type") == "command" and hook.get("command") == command:
                hook["timeout"] = 10
                if async_enabled:
                    hook["async"] = True
                else:
                    hook.pop("async", None)
                return
        entry = {"type": "command", "command": command, "timeout": 10}
        if async_enabled:
            entry["async"] = True
        hook_list.append(entry)

    for event_name, async_enabled in [
        ("SessionStart", False),
        ("UserPromptSubmit", True),
        ("Stop", True),
        ("SubagentStop", True),
        ("Notification", True),
        ("PermissionRequest", True),
        ("SessionEnd", True),
        ("SubagentStart", True),
    ]:
        ensure_event(event_name, async_enabled)

    _write(path, json.dumps(data, indent=2) + "\n")

def install_codex(configured: dict[str, Path], root: Path) -> None:
    path = configured['CODEX_CONFIG']
    plugin_dir = root
    text = path.read_text()
    line = f'notify = ["bash", "{plugin_dir / "scripts/features/hooks/hook-codex.sh"}"]'
    if re.search(r"^notify\s*=\s*\[.*\]$", text, flags=re.M):
        text = re.sub(r"^notify\s*=\s*\[.*\]$", line, text, count=1, flags=re.M)
    else:
        text = text.rstrip() + ("\n" if text.rstrip() else "") + line + "\n"
    _write(path, text)

def install_cursor(configured: dict[str, Path], root: Path) -> None:
    path = configured['CURSOR_HOOKS']
    plugin_dir = root
    text = path.read_text().strip()
    data = json.loads(text) if text else {}
    if not isinstance(data, dict):
        data = {}
    data["version"] = 1
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("cursor hooks config must contain an object-valued 'hooks' field")
    command = str(plugin_dir / "scripts/features/hooks/hook-cursor.sh")

    def ensure_event(event_name: str) -> None:
        entries = hooks.setdefault(event_name, [])
        if not isinstance(entries, list):
            entries = []
            hooks[event_name] = entries
        for entry in entries:
            if entry.get("command") == command:
                entry["timeout"] = 10
                return
        entries.append({"command": command, "timeout": 10})

    for event_name in (
        "sessionStart",
        "sessionEnd",
        "beforeSubmitPrompt",
        "preToolUse",
        "postToolUse",
        "postToolUseFailure",
        "subagentStart",
        "subagentStop",
        "afterAgentThought",
        "afterAgentResponse",
        "stop",
    ):
        ensure_event(event_name)

    _write(path, json.dumps(data, indent=2) + "\n")

def install_opencode(configured: dict[str, Path], root: Path) -> None:
    path = configured['OPENCODE_PLUGIN']
    plugin_dir = root
    hook = plugin_dir / "scripts/features/hooks/hook-opencode.sh"

    _write(path,
        """const hook = {hook_path!r}

    export const TmuxSidebarPlugin = async () => {{
      return {{
        event: async ({{ event }}) => {{
          const eventType = String(event?.type ?? "")
          const status = String(
            event?.properties?.status?.type
            ?? event?.status
            ?? event?.state
            ?? ""
          )
          const message = String(
            event?.properties?.status?.message
            ?? event?.properties?.error?.message
            ?? event?.message
            ?? event?.summary
            ?? event?.transcript_summary
            ?? ""
          )

          if (!eventType && !status && !message) {{
            return
          }}

          const payload = JSON.stringify({{
            event: eventType,
            status,
            message,
          }})

          const proc = Bun.spawn(["bash", hook, payload], {{
            env: {{
              ...process.env,
            }},
            stdin: "ignore",
            stdout: "ignore",
            stderr: "ignore",
          }})

          await proc.exited
        }},
      }}
    }}
    """.format(hook_path=str(hook))
    )

def install_pi(configured: dict[str, Path], root: Path) -> None:
    path = configured['PI_EXTENSION']
    plugin_dir = root
    hook = plugin_dir / "scripts/features/hooks/hook-pi.sh"

    _write(path,
        """import type {{ ExtensionAPI }} from '@mariozechner/pi-coding-agent'

    const hook = {hook_path!r}

    function pickMessage(event: unknown): string {{
      if (!event || typeof event !== 'object') {{
        return ''
      }}

      const record = event as Record<string, unknown>
      for (const key of ['message', 'summary', 'prompt', 'toolName', 'tool_name']) {{
        const value = record[key]
        if (typeof value === 'string' && value.trim()) {{
          return value.trim()
        }}
      }}
      return ''
    }}

    async function emit(pi: ExtensionAPI, eventName: string, event: unknown): Promise<void> {{
      const payload = JSON.stringify({{
        event: eventName,
        message: pickMessage(event),
      }})
      try {{
        await pi.exec('bash', [hook, payload])
      }} catch {{
        // Hook failures should not break the agent session.
      }}
    }}

    export default function (pi: ExtensionAPI) {{
      pi.on('session_start', async (event) => emit(pi, 'session_start', event))
      pi.on('session_shutdown', async (event) => emit(pi, 'session_shutdown', event))
      pi.on('agent_start', async (event) => emit(pi, 'agent_start', event))
      pi.on('agent_end', async (event) => emit(pi, 'agent_end', event))
      pi.on('turn_start', async (event) => emit(pi, 'turn_start', event))
      pi.on('tool_call', async (event) => emit(pi, 'tool_call', event))
    }}
    """.format(hook_path=str(hook))
    )

def install_kiro_agent(configured: dict[str, Path], root: Path) -> None:
    path = configured['KIRO_AGENT']
    plugin_dir = root
    hook = plugin_dir / "scripts/features/hooks/hook-kiro.sh"
    command_prefix = f"bash {shlex.quote(str(hook))}"

    data = {
        "name": "tmux-pane-tree",
        "description": "Update tmux-pane-tree sidebar status badges from Kiro hook events.",
        "includeMcpJson": True,
        "hooks": {
            "agentSpawn": [
                {"command": f"{command_prefix} agentSpawn"},
            ],
            "userPromptSubmit": [
                {"command": f"{command_prefix} userPromptSubmit"},
            ],
            "preToolUse": [
                {"matcher": "*", "command": f"{command_prefix} preToolUse"},
            ],
            "postToolUse": [
                {"matcher": "*", "command": f"{command_prefix} postToolUse"},
            ],
            "stop": [
                {"command": f"{command_prefix} stop"},
            ],
        },
    }

    _write(path, json.dumps(data, indent=2) + "\n")

def install_kiro_settings(configured: dict[str, Path], root: Path) -> None:
    path = configured['KIRO_CLI_SETTINGS']
    text = path.read_text().strip() if path.exists() else ""
    try:
        data = json.loads(text) if text else {}
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}

    chat = data.get("chat")
    if not isinstance(chat, dict):
        chat = {}
        data["chat"] = chat

    current_default = str(chat.get("defaultAgent") or "").strip()
    if current_default not in ("", "kiro_default", "default"):
        return

    chat["defaultAgent"] = "tmux-pane-tree"
    _write(path, json.dumps(data, indent=2) + "\n")

def install() -> None:
    configured = paths()
    root = Path(os.environ.get("PLUGIN_DST", str(ROOT))).resolve()
    for key, path in configured.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        backup(path)
        if not path.exists() and key in {"CLAUDE_SETTINGS", "CURSOR_HOOKS", "KIRO_CLI_SETTINGS"}:
            _write(path, "{}\n")
        elif not path.exists() and key == "CODEX_CONFIG":
            _write(path, "")
    install_claude(configured, root)
    install_codex(configured, root)
    install_cursor(configured, root)
    install_opencode(configured, root)
    install_pi(configured, root)
    install_kiro_agent(configured, root)
    install_kiro_settings(configured, root)

def remove() -> None:
    configured = paths()
    root = Path(os.environ.get("PLUGIN_DST", str(ROOT))).resolve()
    hooks = str(root / "scripts/features/hooks")
    for key in ("CLAUDE_SETTINGS", "CURSOR_HOOKS"):
        path = configured[key]
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        entries = data.get("hooks", {})
        for event, rules in list(entries.items()):
            cleaned = []
            for rule in rules:
                if key == "CLAUDE_SETTINGS":
                    rule = dict(rule)
                    rule["hooks"] = [item for item in rule.get("hooks", []) if not str(item.get("command", "")).startswith(hooks + "/")]
                    if rule["hooks"]:
                        cleaned.append(rule)
                elif not str(rule.get("command", "")).startswith(hooks + "/"):
                    cleaned.append(rule)
            if cleaned:
                entries[event] = cleaned
            else:
                entries.pop(event, None)
        backup(path)
        _write(path, json.dumps(data, indent=2) + "\n")
    path = configured["CODEX_CONFIG"]
    if path.exists():
        text = re.sub(r"^notify\s*=.*$", lambda match: "" if hooks in match.group() else match.group(), path.read_text(), flags=re.M)
        backup(path)
        _write(path, text)
    for key in ("OPENCODE_PLUGIN", "PI_EXTENSION", "KIRO_AGENT"):
        path = configured[key]
        if path.exists() and hooks in path.read_text():
            backup(path)
            path.unlink()
    path = configured["KIRO_CLI_SETTINGS"]
    if path.exists():
        data = json.loads(path.read_text())
        if data.get("chat", {}).get("defaultAgent") == "tmux-pane-tree":
            data["chat"].pop("defaultAgent")
            backup(path)
            _write(path, json.dumps(data, indent=2) + "\n")
