from __future__ import annotations

from dataclasses import dataclass, field
import json
import re

from .agents import canonical_pane_app, effective_status, live_agent_app, normalize_token, pane_display_label, window_display_name
from .config import enabled, option


def minimum_layout_width(layout: str, excluded: set[str] | None = None) -> int:
    excluded = excluded or set()
    if layout.startswith("{"):
        def visit(node: dict) -> int:
            if node["t"] == "p":
                return 0 if node.get("I", "%" + str(node.get("l", ""))) in excluded else 1
            children = [value for child in node.get("c", []) if (value := visit(child)) > 0]
            return sum(children) + max(0, len(children) - 1) if node["t"] == "h" else max(children, default=0)
        return max(1, visit(json.loads(layout)["L"]))
    body = layout.partition(",")[2]
    def parse(offset: int) -> tuple[int, int]:
        geometry = re.match(r"\d+x\d+,\d+,\d+", body[offset:])
        if geometry is None:
            raise ValueError("Invalid tmux layout geometry")
        offset += len(geometry.group())
        if body[offset:offset + 1] == ",":
            leaf = re.match(r",(\d+)", body[offset:])
            if leaf is None:
                raise ValueError("Invalid tmux layout pane")
            return (0 if "%" + leaf[1] in excluded else 1), offset + len(leaf.group())
        direction = body[offset:offset + 1]
        if direction not in {"{", "["}:
            raise ValueError("Invalid tmux layout split")
        offset += 1
        children = []
        while True:
            value, offset = parse(offset)
            if value:
                children.append(value)
            if body[offset:offset + 1] == ("}" if direction == "{" else "]"):
                offset += 1
                break
            if body[offset:offset + 1] != ",":
                raise ValueError("Invalid tmux layout separator")
            offset += 1
        minimum = sum(children) + max(0, len(children) - 1) if direction == "{" else max(children, default=0)
        return minimum, offset
    value, _ = parse(0)
    return max(1, value)

@dataclass
class Model:
    panes: dict[str, dict] = field(default_factory=dict)
    windows: dict[str, dict] = field(default_factory=dict)
    sessions: dict[str, dict] = field(default_factory=dict)
    clients: dict[str, dict] = field(default_factory=dict)
    globals: dict = field(default_factory=dict)

    @classmethod
    def from_records(cls, records: dict[str, list[dict]]) -> Model:
        model = cls()
        for row in records["P"]:
            model.panes[row["pane_id"]] = {**row, "sidebar": row["@tmux_sidebar_owned"] == "1"}
        for row in records["W"]:
            window = model.windows.setdefault(row["window_id"], {**row, "links": [], "indices": {}})
            window["links"].append(row["session_id"])
            window["indices"][row["session_id"]] = row["window_index"]
        for row in records["S"]:
            model.sessions[row["session_id"]] = row
        for row in records["C"]:
            model.clients[row["client_name"]] = row
        model.globals = records["G"][0] if records["G"] else {}
        return model

    def content(self, window: str) -> list[dict]:
        return [pane for pane in self.panes.values() if pane["window_id"] == window and not pane["sidebar"]]

    def sidebar(self, window: str) -> str:
        return next((pane["pane_id"] for pane in self.panes.values() if pane["window_id"] == window and pane["sidebar"]), "")

    def available_sidebar_width(self, window: str) -> int:
        info = self.windows[window]
        minimum = minimum_layout_width(info["window_layout"], {self.sidebar(window)})
        return max(0, int(info["window_width"]) - minimum - 1)

    def visible(self, window: str) -> bool:
        if not any(client["window_id"] == window for client in self.clients.values()):
            return False
        info = self.windows.get(window, {})
        return info.get("window_zoomed_flag") != "1" or self.panes.get(self.sidebar(window), {}).get("pane_active") == "1"


def pane_matches(pane: dict, state: dict, tokens: list[str]) -> bool:
    if not tokens:
        return True
    values = [pane["pane_current_command"], pane["pane_title"], str(state.get("app", "")), str(state.get("pane_current_command", "")), str(state.get("pane_title", ""))]
    values.extend([live_agent_app(values[0], values[1], state), *(normalize_token(value) for value in values[:])])
    return any(token in value.lower() for token in tokens for value in values)


def project_rows(model: Model, states: dict[str, dict], options: dict[str, str], icons: dict[str, str], badges: dict[str, str], captures: dict[str, str]) -> list[dict]:
    tokens = [value.strip().lower() for value in option(options, "filter").split(",") if value.strip()] if enabled(option(options, "filter_enabled"), True) else []
    hide = enabled(option(options, "hide_panes"))
    compact = enabled(option(options, "compact_single_panes"))
    order = [name.strip() for name in option(options, "session_order").split(",") if name.strip()]
    sessions = sorted(model.sessions.values(), key=lambda session: (order.index(session["session_name"]) if session["session_name"] in order else len(order), list(model.sessions).index(session["session_id"])))
    groups = []
    statuses = {pane_id: effective_status(pane["pane_current_command"], pane["pane_title"], states.get(pane_id, {}), captures.get(pane_id, "")) for pane_id, pane in model.panes.items() if not pane["sidebar"]}
    for session in sessions:
        windows = []
        for window in model.windows.values():
            if session["session_id"] not in window["links"]:
                continue
            content = model.content(window["window_id"])
            panes = [pane for pane in content if pane_matches(pane, states.get(pane["pane_id"], {}), tokens)]
            if panes:
                windows.append((window, content, panes))
        if windows:
            groups.append((session, windows))
    rows = []
    for si, (session, windows) in enumerate(groups):
        sid, name = session["session_id"], session["session_name"]
        last_session = si == len(groups) - 1
        prefix = "   " if last_session else "│  "
        rows.append({"kind": "session", "key": sid, "session_id": sid, "session": name, "text": f"{'└─' if last_session else '├─'} {name}"})
        for wi, (window, content, panes) in enumerate(windows):
            wid = window["window_id"]
            key = sid + "/" + wid
            last_window = wi == len(windows) - 1
            pane_prefix = prefix + ("   " if last_window else "│  ")
            legacy_panes = [{"id": pane["pane_id"], "label": pane["pane_current_command"], "title": pane["pane_title"], "active": pane["pane_active"] == "1"} for pane in panes]
            label = window_display_name(window["window_name"], legacy_panes, states)
            compact_window = compact and len(content) == 1
            visible_panes = panes
            window_row = {"kind": "window", "key": key, "parent": sid, "session_id": sid, "session": name, "window": wid, "name": window["window_name"]}
            if compact_window:
                pane_id = panes[0]["pane_id"]
                window_row["pane_id"] = pane_id
                badge = badges.get(statuses.get(pane_id, ""), "")
                if badge:
                    label += " " + badge
                visible_panes = []
            elif hide:
                visible_panes = [pane for pane in panes if badges.get(statuses.get(pane["pane_id"], ""), "")]
                if not visible_panes:
                    window_row["pane_id"] = wid
            window_row["text"] = f"{prefix}{'└─' if last_window else '├─'} {label}"
            rows.append(window_row)
            for pi, pane in enumerate(visible_panes):
                pid = pane["pane_id"]
                state = states.get(pid, {})
                command, title = pane["pane_current_command"], pane["pane_title"]
                label = pane_display_label(command, title, state)
                icon = icons.get(canonical_pane_app(command, title, state), "")
                badge = badges.get(statuses.get(pid, ""), "")
                text = f"{pane_prefix}{'└─' if pi == len(visible_panes) - 1 else '├─'} "
                text += (icon + " " if icon else "") + label + (" " + badge if badge else "")
                rows.append({"kind": "pane", "key": key + "/" + pid, "parent": key, "pane_id": pid, "session_id": sid, "session": name, "window": wid, "name": window["window_name"], "text": text, "status": statuses.get(pid, "")})
    return rows
