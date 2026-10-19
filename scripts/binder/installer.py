from __future__ import annotations

import json
import ast
import os
import re
import shlex
import shutil
import time
from pathlib import Path

from .runtime import ROOT

DEFAULT_PATHS = {'CLAUDE_SETTINGS': '~/.claude/settings.json', 'CODEX_CONFIG': '~/.codex/config.toml', 'CODEX_HOOKS': '~/.codex/hooks.json', 'CURSOR_HOOKS': '~/.cursor/hooks.json', 'OPENCODE_PLUGIN': '~/.config/opencode/plugins/tmux-pane-tree.js', 'PI_EXTENSION': '~/.pi/agent/extensions/tmux-pane-tree.ts', 'KIRO_AGENT': '~/.kiro/agents/tmux-pane-tree.json', 'KIRO_CLI_SETTINGS': '~/.kiro/settings/cli.json', 'GROK_HOOKS': '~/.grok/hooks/tmux-binder.json'}
AGENT_PATHS = {"claude": ("CLAUDE_SETTINGS",), "codex": ("CODEX_CONFIG", "CODEX_HOOKS"), "cursor": ("CURSOR_HOOKS",), "opencode": ("OPENCODE_PLUGIN",), "pi": ("PI_EXTENSION",), "kiro": ("KIRO_AGENT", "KIRO_CLI_SETTINGS"), "grok": ("GROK_HOOKS",)}


def paths() -> dict[str, Path]:
    defaults = {**DEFAULT_PATHS, "GROK_HOOKS": str(Path(os.environ.get("GROK_HOME", "~/.grok")) / "hooks/tmux-binder.json")}
    return {key: Path(os.environ.get(key, default)).expanduser() for key, default in defaults.items()}


def _write(path: Path, text: str) -> None:
    path = path.resolve()
    temporary = path.with_name(path.name + ".binder-new")
    created = False
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        with os.fdopen(descriptor, "w") as handle:
            handle.write(text)
        if path.exists():
            temporary.chmod(path.stat().st_mode & 0o777)
        temporary.replace(path)
    finally:
        if created:
            temporary.unlink(missing_ok=True)


def backup(path: Path) -> None:
    if not path.exists():
        return
    stamp = time.strftime("%Y%m%d%H%M%S") + str(time.time_ns() % 1000000000)
    target = path.with_name(path.name + ".bak-tmux-sidebar-" + stamp)
    shutil.copy2(path, target)
    for previous in sorted(path.parent.glob(path.name + ".bak-tmux-sidebar-*"), reverse=True)[3:]:
        previous.unlink()

def _object(path: Path) -> dict:
    text = path.read_text().strip() if path.exists() else ""
    data = json.loads(text) if text else {}
    if not isinstance(data, dict) or not isinstance(data.get("hooks", {}), dict):
        raise ValueError(f"Expected an object with object-valued hooks in {path}")
    return data


def _owned(command: str, app: str, root: Path) -> bool:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    names = {f"hook-{app}.sh", f"{app}-hook.sh", "update-pane-state.sh"}
    owned = any((token.startswith(str(root) + "/") or any(f"/{name}/" in token for name in ("tmux-binder", "tmux-pane-tree", "tmux-sidebar"))) and (Path(token).name in names or (Path(token).name == "binder.py" and "hook" in tokens and app in tokens)) for token in tokens)
    if owned and any(token in {";", "&&", "||", "|", "&"} for token in tokens):
        raise ValueError("A Binder hook contains a compound command; separate unrelated commands before migration")
    return owned


def _grouped(path: Path, app: str, root: Path, events: tuple[str, ...]) -> None:
    data = _object(path)
    hooks = data.setdefault("hooks", {})
    installed: set[str] = set()
    for event, rules in list(hooks.items()):
        if not isinstance(rules, list):
            raise ValueError(f"Expected hook groups for {event} in {path}")
        cleaned = []
        for rule in rules:
            items = []
            for item in rule.get("hooks", []):
                if _owned(str(item.get("command", "")), app, root):
                    if event in events and rule.get("matcher", "") in {"", "*"} and event not in installed:
                        items.append(_handler(app, root, event))
                        installed.add(event)
                else:
                    items.append(item)
            if items or not rule.get("hooks"):
                cleaned.append({**rule, "hooks": items})
        if cleaned:
            hooks[event] = cleaned
        else:
            hooks.pop(event, None)
    for event in events:
        if event in installed:
            continue
        rules = hooks.setdefault(event, [])
        target = next((rule for rule in rules if rule.get("matcher", "") in {"", "*"}), None)
        if target is None:
            target = {"matcher": "", "hooks": []}
            rules.append(target)
        target.setdefault("hooks", []).append(_handler(app, root, event))
    _write(path, json.dumps(data, indent=2) + "\n")


def _handler(app: str, root: Path, event: str) -> dict:
    entry = {"type": "command", "command": shlex.quote(str(root / f"scripts/features/hooks/hook-{app}.sh")), "timeout": 10}
    if app != "grok" and event not in {"SessionStart", "SessionEnd"}:
        entry["async"] = True
    return entry


def _without_notify(text: str, root: Path) -> str:
    lines = text.splitlines(keepends=True)
    removed: set[int] = set()
    for index, line in enumerate(lines):
        match = re.match(r"^[ \t]*notify[ \t]*=[ \t]*(.*)", line)
        if not match:
            continue
        value = line[match.start(1):]
        for end in range(index, len(lines)):
            if end > index:
                value += lines[end]
            try:
                command = ast.literal_eval(value)
            except (SyntaxError, ValueError):
                continue
            if isinstance(command, list) and all(isinstance(item, str) for item in command) and _owned(shlex.join(command), "codex", root):
                removed.update(range(index, end + 1))
            break
    return "".join(line for index, line in enumerate(lines) if index not in removed)


def install_claude(configured: dict[str, Path], root: Path) -> None:
    _grouped(configured["CLAUDE_SETTINGS"], "claude", root, ("SessionStart", "UserPromptSubmit", "Stop", "SubagentStop", "Notification", "PermissionRequest", "SessionEnd", "SubagentStart", "PostToolUse", "PostToolUseFailure", "StopFailure"))


def install_codex(configured: dict[str, Path], root: Path) -> None:
    _grouped(configured["CODEX_HOOKS"], "codex", root, ("SessionStart", "UserPromptSubmit", "PostToolUse", "PermissionRequest", "Stop", "SessionEnd", "SubagentStart", "SubagentStop", "Interrupt"))
    path = configured["CODEX_CONFIG"]
    if path.exists():
        original = path.read_text()
        updated = _without_notify(original, root)
        if updated != original:
            _write(path, updated)


def install_grok(configured: dict[str, Path], root: Path) -> None:
    _grouped(configured["GROK_HOOKS"], "grok", root, ("SessionStart", "SessionEnd", "UserPromptSubmit", "PostToolUse", "PostToolUseFailure", "PermissionDenied", "Stop", "StopFailure", "StopCancelled", "Notification"))


def install_cursor(configured: dict[str, Path], root: Path) -> None:
    path = configured["CURSOR_HOOKS"]
    data = _object(path)
    data["version"] = 1
    hooks = data.setdefault("hooks", {})
    events = ("sessionStart", "sessionEnd", "beforeSubmitPrompt", "postToolUse", "postToolUseFailure", "afterAgentThought", "afterAgentResponse", "stop")
    installed: set[str] = set()
    for event, items in list(hooks.items()):
        cleaned = []
        for item in items:
            if _owned(str(item.get("command", "")), "cursor", root):
                if event in events and item.get("matcher", "") in {"", "*"} and event not in installed:
                    cleaned.append({"command": shlex.quote(str(root / "scripts/features/hooks/hook-cursor.sh")), "timeout": 10})
                    installed.add(event)
            else:
                cleaned.append(item)
        if cleaned:
            hooks[event] = cleaned
        else:
            hooks.pop(event, None)
    for event in events:
        if event not in installed:
            hooks.setdefault(event, []).append({"command": shlex.quote(str(root / "scripts/features/hooks/hook-cursor.sh")), "timeout": 10})
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
        """import type {{ ExtensionAPI }} from '@earendil-works/pi-coding-agent'

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

def _selected_paths(agents: list[str] | None) -> tuple[list[str], set[str]]:
    selected = list(dict.fromkeys(agents if agents is not None else AGENT_PATHS))
    if any(app not in AGENT_PATHS for app in selected):
        raise ValueError("Unknown agent integration")
    return selected, {key for app in selected for key in AGENT_PATHS[app]}


def install(agents: list[str] | None = None) -> None:
    configured = paths()
    root = Path(os.environ.get("PLUGIN_DST", str(ROOT))).resolve()
    selected, keys = _selected_paths(agents)
    for key in keys & {"CLAUDE_SETTINGS", "CODEX_HOOKS", "CURSOR_HOOKS", "KIRO_CLI_SETTINGS", "GROK_HOOKS"}:
        _object(configured[key])
    for key in sorted(keys):
        path = configured[key]
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        backup(path)
    installers = {"claude": install_claude, "codex": install_codex, "cursor": install_cursor, "opencode": install_opencode, "pi": install_pi, "grok": install_grok}
    for app in selected:
        if app == "kiro":
            install_kiro_agent(configured, root)
            install_kiro_settings(configured, root)
        else:
            installers[app](configured, root)


def remove(agents: list[str] | None = None) -> None:
    configured = paths()
    root = Path(os.environ.get("PLUGIN_DST", str(ROOT))).resolve()
    selected, _ = _selected_paths(agents)
    for app, key in (("claude", "CLAUDE_SETTINGS"), ("codex", "CODEX_HOOKS"), ("grok", "GROK_HOOKS")):
        path = configured[key]
        if app not in selected or not path.exists():
            continue
        backup(path)
        _grouped(path, app, root, ())
    path = configured["CURSOR_HOOKS"]
    if "cursor" in selected and path.exists():
        data = _object(path)
        for event, items in list(data.get("hooks", {}).items()):
            cleaned = [item for item in items if not _owned(str(item.get("command", "")), "cursor", root)]
            if cleaned:
                data["hooks"][event] = cleaned
            else:
                data["hooks"].pop(event, None)
        backup(path)
        _write(path, json.dumps(data, indent=2) + "\n")
    path = configured["CODEX_CONFIG"]
    if "codex" in selected and path.exists():
        original = path.read_text()
        text = _without_notify(original, root)
        if text != original:
            backup(path)
            _write(path, text)
    for app, key in (("opencode", "OPENCODE_PLUGIN"), ("pi", "PI_EXTENSION"), ("kiro", "KIRO_AGENT")):
        path = configured[key]
        if app in selected and path.exists() and (str(root) in path.read_text() or "tmux-pane-tree" in path.read_text()) and f"hook-{app}.sh" in path.read_text():
            backup(path)
            path.unlink()
    path = configured["KIRO_CLI_SETTINGS"]
    if "kiro" in selected and path.exists():
        data = json.loads(path.read_text())
        if data.get("chat", {}).get("defaultAgent") == "tmux-pane-tree":
            data["chat"].pop("defaultAgent")
            backup(path)
            _write(path, json.dumps(data, indent=2) + "\n")
