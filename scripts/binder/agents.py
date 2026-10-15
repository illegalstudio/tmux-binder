from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

from .icons import AGENT_APPS, APP_ALIASES

STATUSES = {"idle", "running", "subagent-running", "needs-input", "done", "error"}
RANK = {"idle": 1, "running": 2, "subagent-running": 2, "done": 3, "needs-input": 4, "error": 4}


def resolve_agent_pane(panes: dict[str, dict], states: dict[str, dict], event: dict, pane: str, roots: list[str], explicit: bool = False) -> str:
    content = {pid: item for pid, item in panes.items() if not item["sidebar"]}
    if explicit and pane in content:
        return pane
    app = event.get("app", "")
    candidates = {pid: item for pid, item in content.items() if live_agent_app(item["pane_current_command"], item["pane_title"], states.get(pid, {})) == app}
    session_id = event.get("session_id", "")
    bound = [pid for pid in candidates if session_id and states.get(pid, {}).get("session_id") == session_id and states[pid].get("app") == app]
    if len(bound) == 1:
        return bound[0]
    roots = [os.path.realpath(root) for root in roots if root and os.path.isabs(root)]
    if not roots:
        return pane if pane in content else ""
    scores = {}
    for pid, item in candidates.items():
        path = os.path.realpath(item["pane_current_path"])
        related = [(path == root, min(len(path), len(root))) for root in roots if path == root or path.startswith(root.rstrip("/") + "/") or root.startswith(path.rstrip("/") + "/")]
        if related:
            scores[pid] = max(related)
    if not scores:
        return ""
    best = max(scores.values())
    matches = [pid for pid, score in scores.items() if score == best]
    return matches[0] if len(matches) == 1 else ""


def normalize(app: str, data: dict, event: str = "") -> dict:
    payload = dict(data)
    if app == "claude" and not payload.get("hook_event_name"):
        payload["hook_event_name"] = event or os.environ.get("CLAUDE_HOOK_EVENT_NAME", "")
    if app == "claude":
        payload.setdefault("notification_type", os.environ.get("CLAUDE_NOTIFICATION_TYPE", ""))
        payload.setdefault("message", os.environ.get("CLAUDE_NOTIFICATION_MESSAGE", ""))
    if app == "claude":
        status, subagent_event, message = parse_claude(payload)
    else:
        parser = {"codex": parse_codex, "cursor": parse_cursor, "opencode": parse_opencode, "pi": parse_pi, "kiro": parse_kiro, "grok": parse_grok}[app]
        status, message = parser(event, payload)
        subagent_event = ""
    native_event = re.sub(r"[^a-z]", "", str(payload.get("hook_event_name") or event).lower())
    if app == "codex" and native_event in {"subagentstart", "subagentstop"}:
        subagent_event = "start" if native_event == "subagentstart" else "stop"
    return {
        "app": app, "status": status, "message": message, "subagent_event": subagent_event,
        "event": str(payload.get("hook_event_name") or payload.get("hookEventName") or payload.get("event") or payload.get("type") or event),
        "session_id": str(payload.get("session_id") or payload.get("conversation_id") or payload.get("sessionId") or ""),
        "run_id": str(payload.get("turn_id") or payload.get("run_id") or payload.get("promptId") or ""),
        "notification_type": str(payload.get("notification_type") or payload.get("notificationType") or ""),
        "subagent_id": str(payload.get("agent_id") or payload.get("subagent_id") or ""),
        "delegated": app == "codex" and payload.get("permission_mode") in {"delegate", "dangerouslySkipPermissions"},
    }


@dataclass
class AgentStore:
    panes: dict[str, dict] = field(default_factory=dict)
    delegated: set[str] = field(default_factory=set)

    def update(self, pane: str, event: dict, metadata: dict) -> bool:
        status = event.get("status", "")
        if status not in STATUSES:
            if not status:
                return False
            raise ValueError(f"Invalid agent status: {status}")
        key = event.get("app", "") + ":" + event.get("session_id", "")
        if event.get("delegated") and event.get("session_id"):
            self.delegated.add(key)
        normalized = re.sub(r"[^a-z]", "", event.get("event", "").lower())
        explicit_subagent = normalized in {"subagentstart", "subagentstop"}
        if (explicit_subagent and event.get("app") not in {"claude", "codex"}) or ((event.get("delegated") or key in self.delegated) and status in {"done", "needs-input"}):
            return False
        previous = self.panes.get(pane, {})
        if event.get("app") == "grok" and event.get("notification_type") == "idle_prompt" and previous.get("status") in {"done", "error"}:
            return False
        stamp = int(event.get("updated_at") or time.time_ns())
        if stamp <= 0:
            raise ValueError("Agent timestamp must be positive")
        prior_stamp = int(previous.get("updated_at", 0))
        if stamp < prior_stamp:
            return False
        lifecycle = event.get("subagent_event", "")
        if stamp == prior_stamp and not lifecycle and RANK.get(previous.get("status", ""), 0) > RANK[status]:
            return False
        run = event.get("run_id", "")
        prior_run = previous.get("run_id", "")
        if event.get("app") == "grok" and run and prior_run and run != prior_run and normalized != "userpromptsubmit":
            return False
        if run and prior_run and run != prior_run and status not in {"running", "idle"}:
            return False
        count = int(previous.get("subagent_count", 0))
        ids = set(previous.get("subagent_ids", []))
        agent_id = event.get("subagent_id", "")
        if lifecycle == "start":
            if not agent_id or agent_id not in ids:
                count += 1
            if agent_id:
                ids.add(agent_id)
            status = "subagent-running"
        elif lifecycle == "stop":
            if not agent_id or agent_id in ids:
                count = max(0, count - 1)
            ids.discard(agent_id)
            if count:
                status = "subagent-running"
        elif status == "running" and count and normalized in {"pretooluse", "posttooluse"}:
            status = "subagent-running"
        elif status in {"idle", "done", "running"}:
            count = 0
            ids.clear()
        self.panes[pane] = {
            **metadata, "pane_id": pane, "app": event.get("app", ""), "status": status,
            "message": str(event.get("message", "")), "updated_at": stamp,
            "subagent_count": count, "subagent_ids": sorted(ids), "run_id": run or prior_run,
            "session_id": event.get("session_id") or previous.get("session_id", ""),
        }
        return True

    def acknowledge(self, pane: str) -> bool:
        state = self.panes.get(pane)
        if not state or state.get("status") != "needs-input":
            return False
        state["status"] = "idle"
        state["updated_at"] = time.time_ns()
        return True


def terminal_status(capture: str) -> str:
    return "running" if re.search(r"^\s*[•·]\s+Working \([^)]*esc to interrupt\)\s*$", capture, re.MULTILINE) else ""


def effective_status(command: str, title: str, state: dict, captured_status: str = "") -> str:
    app = live_agent_app(command, title, state)
    if not app:
        return ""
    status = str(state.get("status", "")).lower()
    if app == "codex":
        return captured_status or (status if status in ACTIVE_STATUSES else "")
    if status == "idle":
        return ""
    if app == "claude":
        title_status = claude_title_status(title)
        if title_status == "running" and status == "subagent-running":
            return status
        if title_status == "idle":
            return "" if status in {"running", "subagent-running"} else (status if status in ACTIVE_STATUSES else "")
        if title_status:
            return title_status
    return status if status in ACTIVE_STATUSES else ""

SEMVER_PATTERN = re.compile(r"^\d+[._]\d+[._]\d+(?:[-+][0-9A-Za-z.-]+)?$")
NON_AGENT_COMMANDS = {
    "",
    "ash",
    "bash",
    "fish",
    "htop",
    "ksh",
    "less",
    "nano",
    "nvim",
    "sh",
    "ssh",
    "tail",
    "tmux",
    "top",
    "vi",
    "vim",
    "yazi",
    "zsh",
}
BADGE_OPTION_SUFFIXES: dict[str, str] = {
    "running": "badge_running",
    "subagent-running": "badge_subagent_running",
    "needs-input": "badge_needs_input",
    "done": "badge_done",
    "error": "badge_error",
}
ACTIVE_STATUSES = {"running", "subagent-running", "needs-input", "error", "done"}


def load_payload(raw_payload: str | dict) -> dict[str, Any]:
    if isinstance(raw_payload, dict):
        return raw_payload
    payload = raw_payload.strip()
    if not payload:
        return {}
    try:
        loaded = json.loads(payload)
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def parse_claude(payload: str) -> tuple[str, str, str]:
    data = load_payload(payload)
    event = str(data.get("hook_event_name") or os.environ.get("CLAUDE_HOOK_EVENT_NAME") or "").strip()
    notification_type = str(data.get("notification_type") or "").strip().lower()
    message = str(data.get("message") or data.get("notification_type") or "").strip()
    subagent_event = ""

    if event in ("SessionStart", "SessionEnd"):
        status = "idle"
    elif event in ("UserPromptSubmit", "PostToolUse"):
        status = "running"
    elif event == "SubagentStart":
        status = "running"
        subagent_event = "start"
    elif event == "SubagentStop":
        status = "running"
        subagent_event = "stop"
    elif event == "Notification" and notification_type == "idle_prompt":
        status = "idle"
    elif event in ("Notification", "PermissionRequest"):
        status = "needs-input"
    elif event == "Stop":
        status = "done"
    elif event == "PostToolUseFailure":
        status = "error"
    else:
        status = ""

    return status, subagent_event, message


def parse_grok(event: str, payload: str | dict) -> tuple[str, str]:
    data = load_payload(payload)
    if data.get("subagentType") or data.get("subagent_type"):
        return "", ""
    name = re.sub(r"[^a-z]", "", str(data.get("hook_event_name") or data.get("hookEventName") or event).lower())
    statuses = {"sessionstart": "idle", "sessionend": "idle", "userpromptsubmit": "running", "posttooluse": "running", "posttoolusefailure": "error", "permissiondenied": "needs-input", "stop": "done", "stopfailure": "error", "stopcancelled": "idle"}
    status = statuses.get(name, "")
    if name == "notification":
        status = {"permission_prompt": "needs-input", "idle_prompt": "idle", "task_complete": "done"}.get(str(data.get("notificationType") or data.get("notification_type") or ""), "")
    return status, str(data.get("message") or data.get("errorDetails") or "")


def parse_codex(event: str, payload: str) -> tuple[str, str]:
    data = load_payload(payload)
    raw_event = str(
        event
        or data.get("hook_event_name")
        or data.get("event")
        or data.get("type")
        or ""
    ).strip().lower().replace("_", "-")
    notif_type = str(data.get("notification_type") or "").strip().lower()
    status_hint = str(data.get("status") or data.get("state") or "").strip().lower().replace("_", "-")
    message = str(data.get("summary") or data.get("transcript_summary") or data.get("message") or "").strip()
    native = raw_event.replace("-", "")

    done_events = {
        "agent-turn-complete",
        "complete",
        "completed",
        "done",
        "finish",
        "finished",
        "session-end",
        "stop",
        "stopped",
        "task-complete",
        "turn-complete",
    }
    done_statuses = {
        "complete",
        "completed",
        "done",
        "finished",
        "stopped",
    }
    idle_events = {
        "session-start",
    }
    running_events = {
        "agent-turn-start",
        "agent-turn-started",
        "start",
        "task-start",
        "task-started",
        "turn-start",
        "turn-started",
    }

    if native in {"sessionstart", "sessionend", "interrupt"}:
        status = "idle"
    elif native in {"userpromptsubmit", "pretooluse", "posttooluse", "subagentstart", "subagentstop"}:
        status = "running"
    elif notif_type == "permission_prompt":
        status = "needs-input"
    elif raw_event in done_events or status_hint in done_statuses:
        status = "done"
    elif raw_event in idle_events:
        status = "idle"
    elif raw_event in running_events:
        status = "running"
    elif (
        raw_event.startswith("permission")
        or raw_event.startswith("approve")
        or raw_event in ("approval-requested", "approval-needed", "input-required")
    ):
        status = "needs-input"
    elif raw_event.startswith("error") or raw_event.startswith("fail"):
        status = "error"
    elif notif_type == "idle_prompt" or raw_event == "idle-prompt":
        status = "idle"
    elif status_hint == "running":
        status = "running"
    else:
        status = ""

    return status, message


def parse_opencode(event: str, payload: str) -> tuple[str, str]:
    data = load_payload(payload)
    raw_event = str(
        event
        or data.get("hook_event_name")
        or data.get("event")
        or data.get("type")
        or ""
    ).strip().lower().replace("_", "-")
    status_hint = str(data.get("status") or data.get("state") or "").strip().lower().replace("_", "-")
    message = str(data.get("summary") or data.get("transcript_summary") or data.get("message") or "").strip()

    if raw_event == "session.error" or status_hint in ("error", "failed"):
        status = "error"
    elif raw_event == "permission.asked":
        status = "needs-input"
    elif raw_event == "session.created" or status_hint in ("idle", "ready"):
        status = "idle"
    elif raw_event == "session.status" or status_hint == "running":
        status = "running"
    elif raw_event == "session.idle":
        status = "done"
    else:
        status = "needs-input" if status_hint else ""

    return status, message


def parse_cursor(event: str, payload: str) -> tuple[str, str]:
    data = load_payload(payload)
    raw_event = str(
        event
        or data.get("hook_event_name")
        or data.get("event")
        or data.get("type")
        or ""
    ).strip().lower().replace("_", "").replace("-", "")
    status_hint = str(data.get("status") or data.get("state") or "").strip().lower().replace("_", "-")
    failure_type = str(data.get("failure_type") or "").strip().lower().replace("_", "-")
    message = str(
        data.get("agent_message")
        or data.get("error_message")
        or data.get("message")
        or data.get("summary")
        or ""
    ).strip()

    if raw_event in ("sessionstart", "sessionend"):
        status = "idle"
    elif raw_event in (
        "beforesubmitprompt",
        "pretooluse",
        "posttooluse",
        "subagentstart",
        "afteragentthought",
        "afteragentresponse",
    ):
        status = "running"
    elif raw_event == "subagentstop":
        status = "done"
    elif raw_event == "posttoolusefailure":
        status = "needs-input" if failure_type == "permission-denied" else "error"
    elif raw_event == "stop":
        if status_hint == "completed":
            status = "done"
        elif status_hint == "error":
            status = "error"
        elif status_hint == "aborted":
            status = "idle"
        else:
            status = ""
    else:
        status = ""

    return status, message


def write_result(status: str, subagent_event: str, message: str) -> None:
    print(status)
    print(subagent_event)
    if message:
        print(message)


def parse_pi(event: str, payload: str) -> tuple[str, str]:
    data = load_payload(payload)
    raw_event = str(
        event
        or data.get("event")
        or ""
    ).strip()
    message = str(data.get("message") or "").strip()

    if raw_event in ("session_start", "session_shutdown"):
        status = "idle"
    elif raw_event in ("agent_start", "turn_start", "tool_call"):
        status = "running"
    elif raw_event == "agent_end":
        status = "done"
    elif raw_event == "needs_input":
        status = "needs-input"
    elif raw_event == "error":
        status = "error"
    else:
        status = ""

    return status, message


def parse_kiro(event: str, payload: str) -> tuple[str, str]:
    data = load_payload(payload)
    raw_event = str(
        event
        or data.get("hook_event_name")
        or data.get("event")
        or ""
    ).strip()
    normalized = raw_event.lower().replace("_", "").replace("-", "")
    status_hint = str(data.get("status") or data.get("state") or "").strip().lower()
    message = str(
        data.get("message")
        or data.get("summary")
        or data.get("tool_name")
        or data.get("toolName")
        or data.get("prompt")
        or ""
    ).strip()

    if status_hint == "error" or normalized == "error":
        status = "error"
    elif normalized in ("agentspawn", "sessionstart", "sessionshutdown"):
        status = "idle"
    elif normalized in (
        "userpromptsubmit",
        "pretooluse",
        "posttooluse",
        "agentstart",
        "turnstart",
        "toolcall",
    ):
        status = "running"
    elif normalized in ("stop", "agentend", "turnend"):
        status = "done"
    elif normalized == "needsinput":
        status = "needs-input"
    else:
        status = ""

    return status, message


def normalize_token(value: str) -> str:
    token = value.strip().lower()
    if "/" in token:
        token = token.rsplit("/", 1)[-1]
    return token


def looks_like_codex(value: str) -> bool:
    return normalize_token(value).startswith("codex")


def looks_like_opencode(value: str) -> bool:
    return normalize_token(value).startswith("opencode")


def looks_like_cursor(value: str) -> bool:
    return normalize_token(value).startswith("cursor")


def looks_like_pi(value: str) -> bool:
    token = normalize_token(value)
    if token == "pi":
        return True
    return bool(re.search(r"\bpi\b", value) and re.search(r"π", value))


def looks_like_kiro(value: str) -> bool:
    token = normalize_token(value)
    return token in ("kiro", "kiro-cli") or token.startswith("kiro")


def looks_like_claude(value: str) -> bool:
    token = normalize_token(value)
    if token == "claude" or token.startswith("claude-") or token.startswith("claude_"):
        return True
    return bool(re.search(r"\bclaude\b", value, re.IGNORECASE))


def looks_like_semver(value: str) -> bool:
    return bool(SEMVER_PATTERN.match(normalize_token(value)))


def should_preserve_live_label(command: str, title: str) -> bool:
    command_token = normalize_token(command)
    title_token = normalize_token(title)
    return command_token in NON_AGENT_COMMANDS or title_token in NON_AGENT_COMMANDS


def state_agent_app(command: str, title: str, state: dict | None) -> str:
    app = str((state or {}).get("app", "")).strip().lower()
    status = str((state or {}).get("status", "")).strip().lower()
    state_command = str((state or {}).get("pane_current_command", ""))
    state_title = str((state or {}).get("pane_title", ""))
    if app not in AGENT_APPS:
        return ""
    if app == "cursor":
        if status and status != "idle":
            return "cursor"
        # cursor-agent runs under a generic command like "node", so an idle
        # session is still cursor as long as the pane runs the same command
        # the hook captured when it wrote the state.
        if (
            state_command
            and not should_preserve_live_label(command, title)
            and normalize_token(command) == normalize_token(state_command)
        ):
            return "cursor"
        return ""
    state_matches_codex = looks_like_codex(state_command) or looks_like_codex(state_title)
    if app == "codex" and status == "idle":
        if state_matches_codex:
            return "codex"
    if should_preserve_live_label(command, title):
        if app == "codex" and status in ("done", "needs-input", "error") and state_matches_codex:
            return app
        return ""
    if app == "claude" and (looks_like_semver(command) or looks_like_semver(title)):
        return "claude"
    if status and status != "idle":
        return app
    return ""


def claude_title_status(title: str) -> str:
    match = re.search(r":\s*([a-z_-]+)\s*$", title.strip().lower())
    if match:
        suffix = match.group(1)
        return {
            "done": "done",
            "error": "error",
            "needs-input": "needs-input",
            "running": "running",
        }.get(suffix, "")
    if re.match(r"^[\u2800-\u28FF]", title.strip()):
        return "running"
    if title.strip().startswith("\u2733"):
        return "idle"
    return ""


def live_agent_app(command: str, title: str, state: dict | None) -> str:
    if normalize_token(command) in {"grok", "grok-native", "grok-cli"} or re.search(r"\bgrok\b", title, re.IGNORECASE):
        return "grok"
    if looks_like_codex(command) or looks_like_codex(title):
        return "codex"
    if looks_like_opencode(command) or looks_like_opencode(title):
        return "opencode"
    if looks_like_cursor(command) or looks_like_cursor(title):
        return "cursor"
    if looks_like_pi(command) or looks_like_pi(title):
        return "pi"
    if looks_like_kiro(command) or looks_like_kiro(title):
        return "kiro"
    if looks_like_claude(command) or looks_like_claude(title):
        return "claude"
    if looks_like_semver(command) and not should_preserve_live_label(command, title):
        return "claude"
    return state_agent_app(command, title, state)


def canonical_pane_app(command: str, title: str, state: dict | None) -> str:
    live_app = live_agent_app(command, title, state)
    if live_app:
        return live_app
    command_token = normalize_token(command)
    if command_token in APP_ALIASES:
        return APP_ALIASES[command_token]
    title_token = normalize_token(title)
    if title_token in APP_ALIASES:
        return APP_ALIASES[title_token]
    if command_token.startswith("python"):
        return "python"
    if command_token:
        return "unknown"
    return ""



def pane_display_label(command: str, title: str, state: dict | None) -> str:
    live_app = live_agent_app(command, title, state)
    if live_app:
        return live_app
    return command


def pane_icon(command: str, title: str, state: dict | None) -> str:
    return canonical_pane_app(command, title, state)


def auto_window_name(window_name: str, panes: list[dict]) -> bool:
    if (
        looks_like_semver(window_name)
        or looks_like_codex(window_name)
        or looks_like_opencode(window_name)
        or looks_like_cursor(window_name)
        or looks_like_claude(window_name)
    ):
        return True
    active_pane = next((pane for pane in panes if pane["active"]), panes[0] if panes else None)
    if active_pane is None:
        return False
    return normalize_token(window_name) == normalize_token(active_pane["label"])


def window_display_name(window_name: str, panes: list[dict], pane_states: dict[str, dict]) -> str:
    if not auto_window_name(window_name, panes):
        return window_name

    for pane in sorted(panes, key=lambda p: not p["active"]):
        pane_state = pane_states.get(pane["id"], {})
        label = pane_display_label(pane["label"], pane["title"], pane_state)
        if label != pane["label"]:
            return label

    return window_name
