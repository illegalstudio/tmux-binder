from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from .runtime import Identity, request
from .icons import AGENT_APPS


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="tmux-binder", description="Shared tmux sidebar coordinator")
    result.add_argument("--socket", default="")
    commands = result.add_subparsers(dest="command", required=True)
    for command in ("serve", "configure", "toggle", "close", "focus", "focus-main", "ensure", "refresh", "snapshot", "doctor", "shutdown", "reload", "install", "reload-views", "clear-state", "toggle-filter", "toggle-panes", "activate", "remove", "menu", "menu-action", "view-action", "name", "event"):
        sub = commands.add_parser(command)
        for argument in ("pane", "window", "client", "target", "key", "session", "window-index"):
            sub.add_argument("--" + argument, default="")
        sub.add_argument("--row", type=int, default=0)
        sub.add_argument("--action", default="")
        sub.add_argument("--name", default="")
        sub.add_argument("--kind", default="structure")
        sub.add_argument("--after-session", dest="session")
    hook = commands.add_parser("hook")
    hook.add_argument("app", choices=AGENT_APPS)
    hook.add_argument("arguments", nargs="*")
    update = commands.add_parser("update-state")
    update.add_argument("--pane", default="")
    update.add_argument("--app", default="")
    update.add_argument("--status", required=True)
    update.add_argument("--message", default="")
    update.add_argument("--updated-at", type=int)
    update.add_argument("--subagent-event", choices=("start", "stop", ""), default="")
    compat = commands.add_parser("compat")
    compat.add_argument("script")
    compat.add_argument("arguments", nargs=argparse.REMAINDER)
    commands.add_parser("render")
    install = commands.add_parser("install-hooks")
    install.add_argument("--remove", action="store_true")
    install.add_argument("--agent", action="append", choices=AGENT_APPS)
    return result


def read_hook(arguments: list[str]) -> tuple[str, dict, str]:
    first, second = (arguments + ["", ""])[:2]
    if first.lstrip().startswith(("{", "[")):
        raw, event = first, second
    elif second.lstrip().startswith(("{", "[")):
        raw, event = second, first
    else:
        raw, event = "", first
        # Native hooks supply JSON on a pipe. The producer may write after
        # our process starts, so wait for its payload instead of dropping it.
        if not sys.stdin.isatty():
            raw = sys.stdin.read(1024 * 1024)
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    return event, payload if isinstance(payload, dict) else {}, raw


def compatibility(script: str, arguments: list[str]) -> list[str]:
    name = Path(script).stem
    command = {
        "toggle-sidebar": "toggle", "close-sidebar": "close", "focus-sidebar": "focus",
        "focus-main-pane": "focus-main", "ensure-sidebar-pane": "ensure", "ensure-all-sidebar-panes": "ensure",
        "notify-sidebar": "event", "on-pane-focus": "event", "handle-pane-exited": "event",
        "refresh-sidebar": "refresh", "render-sidebar": "render", "reload-sidebar-panes": "reload-views",
        "apply-key-overrides": "configure", "configure-pane-border-format": "configure", "bind-context-menu": "configure",
        "request-sidebar-action": "view-action", "clear-pane-state": "clear-state", "update-pane-state": "update-state",
        "show-context-menu": "menu", "remember-main-pane": "event", "install-agent-hooks": "install-hooks",
    }.get(name, "")
    if name.startswith("hook-"):
        return ["hook", name[5:], *arguments]
    if name in {"add-session", "add-window", "rename-session", "rename-window"}:
        return ["name", "--action", name.replace("-", "_"), *arguments]
    if not command:
        raise ValueError(f"Unsupported legacy entrypoint: {script}")
    if name == "update-pane-state" or (arguments and arguments[0].startswith("--")):
        return [command, *arguments]
    converted = [command]
    if command == "event":
        converted.extend(["--kind", "focus" if name in {"on-pane-focus", "remember-main-pane"} else ("resize" if name == "notify-sidebar" and arguments else "structure")])
    if name == "request-sidebar-action":
        return [command, "--action", arguments[0] if arguments else "", "--pane", os.environ.get("TMUX_PANE", "")]
    if arguments:
        converted.extend(["--pane", arguments[0]])
    if len(arguments) > 1:
        converted.extend(["--row" if command == "menu" else "--window", arguments[1]])
    if command == "menu" and len(arguments) > 4:
        converted.extend(["--client", arguments[4]])
    return converted


def main(arguments: list[str] | None = None) -> None:
    entered_at = time.time_ns()
    args = parser().parse_args(arguments)
    if args.command == "compat":
        main((["--socket", args.socket] if args.socket else []) + compatibility(args.script, args.arguments))
        return
    if args.command == "install-hooks":
        from .installer import install, remove
        (remove if args.remove else install)(args.agent)
        return
    if args.command == "hook":
        if args.app in {"claude", "cursor"} and os.environ.get("GROK_HOOK_EVENT"):
            return
        event, payload, raw = read_hook(args.arguments)
        if args.app == "grok":
            event = event or os.environ.get("GROK_HOOK_EVENT", "")
            payload.setdefault("sessionId", os.environ.get("GROK_SESSION_ID", ""))
            payload.setdefault("cwd", payload.get("workspaceRoot") or os.environ.get("GROK_WORKSPACE_ROOT", ""))
        explicit = next((str(payload.get(key)) for key in ("pane_id", "pane", "tmux_pane") if re.fullmatch(r"%\d+", str(payload.get(key, "")))), "")
        candidates = [explicit, os.environ.get("CURSOR_TMUX_PANE", ""), os.environ.get("TMUX_PANE", "")]
        pane = next((str(value) for value in candidates if value and re.fullmatch(r"%\d+", str(value))), "")
        roots = payload.get("workspace_roots", [])
        if isinstance(roots, str):
            roots = [roots]
        roots = [value for value in roots if isinstance(value, str)] if isinstance(roots, list) else []
        cwd = payload.get("cwd")
        if isinstance(cwd, str) and cwd and (pane or os.environ.get("TMUX") or args.socket):
            roots = [cwd, *roots]
        if args.app == "cursor":
            prompt = str(payload.get("hook_event_name") or event).lower() == "beforesubmitprompt"
            print(json.dumps({"continue": True} if prompt else {}))
        if not pane and not roots:
            return
        try:
            identity = Identity.resolve(args.socket)
            if args.app == "codex" and not payload.get("hook_event_name"):
                forward = Path(os.environ.get("TMUX_SIDEBAR_CODEX_NOTIFY_FORWARD", str(Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))) / "hooks/peon-ping/adapters/codex.sh")))
                if forward.is_file() and os.access(forward, os.X_OK):
                    subprocess.run([str(forward), raw], input=raw, text=True, timeout=5, check=False)
            request(identity, "agent", app=args.app, payload=payload, event=event, pane=pane, pane_explicit=bool(explicit), roots=roots, updated_at=entered_at)
        except (ConnectionError, RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as error:
            print(f"tmux-binder: {args.app} status update failed: {error}", file=sys.stderr)
        return
    identity = Identity.resolve(args.socket)
    if args.command in {"reload", "install"}:
        from .development import reload, install
        (reload if args.command == "reload" else install)(identity)
        return
    if args.command == "serve":
        from .server import serve
        serve(identity)
        return
    data = vars(args).copy()
    command = data.pop("command")
    data.pop("socket", None)
    if "pane" in data and not data["pane"]:
        data["pane"] = os.environ.get("TMUX_PANE", "")
    if command == "update-state":
        data = {"pane": data["pane"], "event": {key: value for key, value in data.items() if key != "pane"}}
        data["event"]["updated_at"] = data["event"].get("updated_at") or entered_at
    if command == "render":
        message = request(identity, "snapshot")
        print("\n".join("  " + row["text"] for row in message["rows"]))
        return
    reply = request(identity, command, **data)
    if command in {"doctor", "snapshot"}:
        print(json.dumps(reply, ensure_ascii=False, indent=2))
