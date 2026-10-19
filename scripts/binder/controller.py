from __future__ import annotations

import logging
import json
import os
import re
import shlex
import sys
import time
from typing import Callable

from .agents import AgentStore, live_agent_app, resolve_agent_pane, terminal_status
from .config import enabled, integer, option, parse_options, shortcuts, visuals
from .model import Model, project_rows
from .runtime import ENTRYPOINT, ROOT, Identity, atomic_json, read_json, state_home
from .tmux import Tmux, TmuxError

RECONCILE_SECONDS = 30.0
AGENT_SAMPLE_SECONDS = 1.0
EVENT_CHANNEL = "tmux-binder-events"
EVENT_PENDING = "TMUX_BINDER_EVENTS_PENDING"
RENAME_SOURCE = "@tmux_pane_tree_rename_format"
SIDEBAR_RENAME_FORMAT = "#{?@tmux_sidebar_owned,#{window_name},#{E:" + RENAME_SOURCE + "}}"
# Explicit members avoid locale-dependent Unicode range collation in tmux regexes.
TITLE_FORMAT = "#{s/^[" + "".join(chr(code) for code in (*range(0x2800, 0x2900), 0x25D0, 0x25D1)) + "]/⠿/:pane_title}"


class Application:
    def __init__(self, identity: Identity, emit: Callable[[str, dict], None]) -> None:
        self.identity = identity
        self.event_channel = EVENT_CHANNEL + "-" + str(os.getpid())
        self.tmux = Tmux(identity.socket_path)
        version = self.tmux.run("display-message", "-p", "#{version}")
        release = re.match(r"(\d+)\.(\d+)", version)
        if release and tuple(map(int, release.groups())) < (3, 5):
            raise RuntimeError("tmux-binder requires tmux 3.5+ for pane title control and native mouse menus")
        self.emit = emit
        saved = read_json(identity.directory / "state.json")
        preferences = read_json(identity.preferences)
        self.agents = AgentStore(saved.get("agents", {}), set(saved.get("delegated", [])))
        self.window_state: dict[str, dict] = saved.get("windows", {})
        self.window_main: dict[str, str] = saved.get("window_main", {})
        self.client_main: dict[str, str] = saved.get("client_main", {})
        self.view_state: dict[str, dict] = saved.get("views", {})
        self.window_client: dict[str, str] = {}
        self.options = self._load_options()
        self.icons, self.badges = visuals(self.options)
        self.preferred_width = integer(option(self.options, "width"), integer(str(preferences.get("width", "")), 25, 1), 1)
        if not option(self.options, "width") and not preferences:
            legacy = state_home() / "sidebar-width.txt"
            if legacy.exists():
                self.preferred_width = integer(legacy.read_text().strip(), 25, 1)
        self.model = Model.from_records(self.tmux.snapshot())
        self.exited_windows: set[str] = set()
        self.is_open = self.model.globals.get("@tmux_sidebar_enabled") == "1"
        self.rows: list[dict] = []
        self.captures: dict[str, str] = {}
        self.programmatic: set[str] = set()
        self.respawns: dict[str, list[float]] = {}
        self.errors: dict[str, str] = {}
        self.revision = 0
        self.rendered: dict[str, int] = {}
        self.row_maps: dict[str, dict] = {}
        self.navigations: list[dict] = []
        self.preview_states: dict[str, dict] = {}
        self.preview_rendered: dict[str, int] = {}
        self.latest_preview: dict[str, int] = {}
        self.next_reconcile = time.monotonic() + RECONCILE_SECONDS
        self.next_sample = 0.0
        self.last_event = "startup"
        self.events = 0
        self.last_publication: tuple = ()
        self.saved_json = ""
        self.ui_command = "exec " + shlex.join([sys.executable, str(ROOT / "scripts/ui/sidebar-ui.py")])
        self._import_legacy()
        self.install_bindings()
        self.refresh()

    def _import_legacy(self) -> None:
        for window_id, window in self.model.windows.items():
            suffix = "_w" + window_id.lstrip("@")
            layout = self.options.get("@tmux_sidebar_layout" + suffix, "")
            panes = self.options.get("@tmux_sidebar_panes" + suffix, "")
            if layout and panes and window_id not in self.window_state:
                self.window_state[window_id] = {"layout": layout, "panes": sorted(panes.split(","))}
        if self.agents.panes or not self.identity.socket_path.endswith("/default"):
            return
        for pane_id, pane in self.model.panes.items():
            path = state_home() / f"pane-{pane_id}.json"
            try:
                old = read_json(path)
            except ValueError:
                logging.warning("Ignoring invalid legacy state %s", path)
                continue
            if old and old.get("window_id") == pane["window_id"]:
                self.agents.panes[pane_id] = old

    def _load_options(self) -> dict[str, str]:
        return parse_options(self.tmux.batch([["show-options", "-g"], ["show-options", "-gw", "automatic-rename-format"]]), ("automatic-rename-format",))

    def save(self) -> None:
        data = {
            "engine_pid": os.getpid(),
            "agents": self.agents.panes, "delegated": sorted(self.agents.delegated), "windows": self.window_state,
            "window_main": self.window_main, "client_main": self.client_main,
            "views": self.view_state,
        }
        serialized = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        if serialized != self.saved_json:
            atomic_json(self.identity.directory / "state.json", data)
            self.saved_json = serialized

    def _set_open(self, value: bool) -> None:
        self.is_open = value
        self.tmux.run("set-option", "-g", "@tmux_sidebar_enabled", "1" if value else "0")

    def _remember_width(self, width: int) -> None:
        if width == self.preferred_width:
            return
        self.preferred_width = width
        self.options["@tmux_pane_tree_width"] = str(width)
        self.tmux.run("set-option", "-g", "@tmux_pane_tree_width", str(width))
        atomic_json(self.identity.preferences, {"width": width})
        path = state_home() / "sidebar-width.txt"
        temporary = path.with_name(path.name + f".new.{os.getpid()}")
        try:
            temporary.write_text(str(width) + "\n")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def _update_main(self) -> None:
        for window_id in self.model.windows:
            content = self.model.content(window_id)
            active = next((pane for pane in content if pane["pane_active"] == "1"), None)
            if active:
                self.window_main[window_id] = active["pane_id"]
            elif self.window_main.get(window_id) not in {pane["pane_id"] for pane in content} and content:
                self.window_main[window_id] = content[0]["pane_id"]
        for client_id, client in self.model.clients.items():
            pane = self.model.panes.get(client["pane_id"], {})
            if pane and not pane["sidebar"]:
                self.client_main[client_id] = pane["pane_id"]
            known = self.window_client.get(client["window_id"], "")
            if known not in self.model.clients or self.model.clients[known]["window_id"] != client["window_id"]:
                self.window_client[client["window_id"]] = client_id

    def read_options(self) -> None:
        options = self._load_options()
        visual_keys = lambda values: {key: value for key, value in values.items() if "_icon_" in key or "_badge_" in key}
        try:
            if visual_keys(options) != visual_keys(self.options):
                icons, badges = visuals(options)
            else:
                icons, badges = self.icons, self.badges
        except ValueError as error:
            message = str(error)
            if self.errors.get("configuration") != message:
                logging.error("Rejected configuration change: %s", message)
                self.emit("notice", {"type": "notice", "error": message})
            self.errors["configuration"] = message
            return
        self.errors.pop("configuration", None)
        self.options = options
        self.icons, self.badges = icons, badges

    def _snapshot(self) -> None:
        previous = self.model
        self.model = Model.from_records(self.tmux.snapshot())
        self.exited_windows.update(
            wid for wid in self.model.windows
            if self.model.sidebar(wid) and not self.model.content(wid)
            and any(pane["pane_id"] not in self.model.panes for pane in previous.content(wid))
        )
        self.exited_windows.intersection_update(self.model.windows)

    def refresh(self, reconcile: bool = True) -> None:
        previous = self.model
        self.read_options()
        configured_width = integer(option(self.options, "width"), self.preferred_width, 1)
        if configured_width != self.preferred_width:
            self._remember_width(configured_width)
            commands = []
            for wid, window in self.model.windows.items():
                pid = self.model.sidebar(wid)
                if pid and window["window_zoomed_flag"] != "1":
                    commands.append(["resize-pane", "-t", pid, "-x", str(min(configured_width, max(1, self.model.available_sidebar_width(wid))))])
                    self.programmatic.add(wid)
            self.tmux.batch(commands)
        self._snapshot()
        self.repair_content_exit(previous)
        resized_pane, _, resized_width = self.model.globals.get("@tmux_pane_tree_resized_pane", "").partition(":")
        if resized_pane:
            resized = self.model.panes.get(resized_pane, {})
            if resized and not resized["sidebar"]:
                self.window_state.get(resized["window_id"], {})["layout"] = ""
            if reconcile:
                self.tmux.run("set-option", "-gu", "@tmux_pane_tree_resized_pane")
        self.is_open = self.model.globals.get("@tmux_sidebar_enabled") == "1"
        self._update_main()
        focus_pane = self.model.globals.get("@tmux_pane_tree_focus_pane", "")
        if focus_pane:
            focused = self.model.panes.get(focus_pane, {})
            focus_client = self.model.globals.get("@tmux_pane_tree_focus_client", "")
            client = self.model.clients.get(focus_client, {})
            if focused and not focused["sidebar"] and focused["pane_active"] == "1" and self.model.visible(focused["window_id"]) and (not client or client["window_id"] == focused["window_id"]):
                self.agents.acknowledge(focus_pane)
            if client and focused and client["window_id"] == focused["window_id"]:
                self.window_client[focused["window_id"]] = focus_client
            self.tmux.batch([["set-option", "-gu", "@tmux_pane_tree_focus_pane"], ["set-option", "-gu", "@tmux_pane_tree_focus_client"]])
        missing = [pane for pane in previous.panes.values() if pane["sidebar"] and pane["pane_id"] not in self.model.panes and pane["window_id"] in self.model.windows]
        if missing and self.is_open:
            self.close()
            return
        for pane_id in list(self.agents.panes):
            if pane_id not in self.model.panes:
                del self.agents.panes[pane_id]
        for window_id in list(self.window_state):
            if window_id not in self.model.windows:
                del self.window_state[window_id]
        for values in (self.window_main, self.window_client, self.view_state):
            for window_id in set(values) - self.model.windows.keys():
                del values[window_id]
        self.client_main = {client: pane for client, pane in self.client_main.items() if client in self.model.clients}
        self.latest_preview = {client: stamp for client, stamp in self.latest_preview.items() if client in self.model.clients}
        for values in (self.preview_states, self.preview_rendered, self.rendered, self.row_maps, self.respawns):
            for pane_id in set(values) - self.model.panes.keys():
                del values[pane_id]
        changed = False
        if reconcile and self.is_open:
            automatic_resize = self.model.globals.get("@tmux_pane_tree_auto_resize") == "1"
            if automatic_resize:
                self.tmux.run("set-option", "-gu", "@tmux_pane_tree_auto_resize")
            changed = self.ensure_all()
            changed = self.sync_geometry(automatic_resize, resized_pane, resized_width) or changed
            changed = self.recover_views() or changed
        if changed:
            before_reconcile = self.model
            self._snapshot()
            self.repair_content_exit(before_reconcile)
            self._update_main()
            self._record_geometry()
        rows = project_rows(self.model, self.agents.panes, self.options, self.icons, self.badges, self.captures)
        context = tuple((pid, pane["pane_active"], pane["pane_width"], pane["pane_height"], self.window_main.get(pane["window_id"], ""), self.window_client.get(pane["window_id"], ""), self.model.visible(pane["window_id"])) for pid, pane in self.model.panes.items() if pane["sidebar"])
        publication = (context, shortcuts(self.options), option(self.options, "scrolloff"), option(self.options, "live_preview"), option(self.options, "collapse_panes"), {kind: option(self.options, "color_" + kind) for kind in ("session", "window", "pane")}, tuple(self.model.globals.get(key, "") for key in ("pane-active-border-style", "pane-border-style", "status-style", "pane-border-format", "window-status-current-style", "window-status-style")))
        if rows != self.rows or publication != self.last_publication:
            self.rows = rows
            self.last_publication = publication
            self.revision += 1
            self.publish()
        self.save()
        self.next_reconcile = time.monotonic() + RECONCILE_SECONDS

    def _record_geometry(self) -> None:
        for window_id, window in self.model.windows.items():
            pane = self.model.panes.get(self.model.sidebar(window_id))
            if not pane or window["window_zoomed_flag"] == "1":
                continue
            state = self.window_state.setdefault(window_id, {})
            state["geometry"] = [int(pane["pane_width"]), int(window["window_width"])]
            state["zoomed"] = False
        self.programmatic.clear()

    def repair_content_exit(self, previous: Model) -> None:
        empty = {wid for wid in self.model.windows if self.model.sidebar(wid) and not self.model.content(wid)}
        orphaned = empty & self.exited_windows
        recovered: set[str] = set()
        for client in self.model.clients.values():
            window = client["window_id"]
            old = previous.clients.get(client["client_name"], {})
            entered = old.get("window_id") != window or old.get("session_id") != client["session_id"]
            if window not in empty or window in orphaned or window in recovered or not entered or not previous.sidebar(window):
                continue
            try:
                path = self.tmux.run("display-message", "-p", "-t", client["session_id"], "#{session_path}")
                width = int(self.model.windows[window]["window_width"])
                content_width = max(1, width - min(self.preferred_width, max(1, width - 2)) - 1)
                self.tmux.run("split-window", "-h", "-t", self.model.sidebar(window), "-c", path, "-l", str(content_width))
            except TmuxError as error:
                message = "Cannot restore a working pane: " + str(error)
                self.errors[window] = message
                logging.warning("%s in %s", message, window)
                self.emit("notice", {"type": "notice", "error": message})
                continue
            self.errors.pop(window, None)
            self.window_state.setdefault(window, {}).update(panes=[], layout="")
            recovered.add(window)
        if recovered:
            self._snapshot()
            empty.difference_update(wid for wid in recovered if self.model.content(wid))
        live = {wid: [pane for pane in self.model.content(wid) if pane["pane_dead"] != "1"] for wid in self.model.windows if wid not in empty}
        commands = []
        for name, client in self.model.clients.items():
            window = client["window_id"]
            old = previous.clients.get(name, {})
            old_pane = previous.panes.get(old.get("pane_id", ""), {})
            exited = bool(old_pane and not old_pane["sidebar"] and old["pane_id"] not in self.model.panes)
            if window not in empty and not (exited and self.model.panes.get(client["pane_id"], {}).get("sidebar")):
                continue
            targets = []
            for wid, panes in live.items():
                if not panes:
                    continue
                for sid in self.model.windows[wid]["links"]:
                    session = self.model.sessions[sid]
                    targets.append((sid != client["session_id"], wid != window, wid != session["window_id"], list(self.model.sessions).index(sid), int(self.model.windows[wid]["indices"][sid]), sid, wid))
            if not targets:
                if window in empty:
                    commands.append(["detach-client", "-t", name])
                continue
            *_, session, target_window = min(targets)
            panes = live[target_window]
            target = next((pane["pane_id"] for pane in panes if pane["pane_id"] == self.window_main.get(target_window)), panes[0]["pane_id"])
            commands.append(["select-pane", "-t", target])
            if target_window != window or session != client["session_id"]:
                commands.extend([["select-window", "-t", session + ":" + target_window], ["switch-client", "-c", name, "-t", session]])
        commands.extend(["kill-window", "-t", wid] for wid in sorted(orphaned))
        if commands:
            self.tmux.batch(commands)
            if len(orphaned) < len(self.model.windows):
                self._snapshot()
            else:
                self.model = Model()
                self.exited_windows.clear()

    def ensure_all(self, focus_window: str = "") -> bool:
        created = self.protect_window_names()
        for window_id, window in self.model.windows.items():
            if self.model.sidebar(window_id):
                continue
            content = self.model.content(window_id)
            signature = [window["window_width"], window["window_height"], window["window_layout"], self.preferred_width]
            if self.window_state.get(window_id, {}).get("blocked") == signature:
                continue
            available = self.model.available_sidebar_width(window_id)
            if not content or available < 1:
                continue
            target = content[0]["pane_id"]
            active = next((pane["pane_id"] for pane in content if pane["pane_active"] == "1"), target)
            self.window_state.setdefault(window_id, {}).update(layout=window["window_layout"], panes=sorted(pane["pane_id"] for pane in content))
            width = str(min(self.preferred_width, available))
            try:
                pane_id = self.tmux.run("split-window", "-t", target, "-h", "-b", "-d", "-f", "-l", width, "-P", "-F", "#{pane_id}", self.ui_command)
            except TmuxError as error:
                if "no space" not in str(error) and "pane too small" not in str(error):
                    raise
                self.window_state[window_id]["blocked"] = signature
                self.errors[window_id] = str(error)
                logging.warning("Sidebar does not fit in %s: %s", window_id, error)
                continue
            self.errors.pop(window_id, None)
            self.tmux.batch([
                ["set-option", "-p", "-t", pane_id, "@tmux_sidebar_owned", "1"],
                ["set-option", "-p", "-t", pane_id, "allow-set-title", "off"],
                ["set-option", "-p", "-t", pane_id, "remain-on-exit", "on"],
                ["select-pane", "-t", pane_id, "-T", "Sidebar"],
            ])
            if focus_window == window_id:
                self.tmux.run("select-pane", "-t", pane_id)
            elif window["window_zoomed_flag"] == "1":
                self.tmux.run("resize-pane", "-Z", "-t", active)
            self.programmatic.add(window_id)
            created = True
        return created

    def protect_window_names(self) -> bool:
        commands = []
        for window_id, window in self.model.windows.items():
            state = self.window_state.setdefault(window_id, {})
            original = window["automatic-rename-format"]
            saved = state.get("rename_format")
            if original == SIDEBAR_RENAME_FORMAT:
                if not saved:
                    raise RuntimeError(f"Missing original automatic rename format for {window_id}")
                if not saved["local"] and saved["value"] != self.options["automatic-rename-format"]:
                    saved["value"] = self.options["automatic-rename-format"]
                    commands.append(["set-option", "-w", "-t", window_id, RENAME_SOURCE, saved["value"]])
                continue
            local = parse_options(self.tmux.run("show-options", "-w", "-t", window_id), ("automatic-rename", "automatic-rename-format"))
            state["rename_format"] = {"value": original, "local": "automatic-rename-format" in local}
            commands.extend([
                ["set-option", "-w", "-t", window_id, RENAME_SOURCE, original],
                ["set-option", "-w", "-t", window_id, "automatic-rename-format", SIDEBAR_RENAME_FORMAT],
            ])
            sidebar = self.model.panes.get(self.model.sidebar(window_id), {})
            target = self.window_main.get(window_id, "")
            if sidebar.get("pane_active") == "1" and window["automatic-rename"] == "1" and target:
                name = self.tmux.run("display-message", "-p", "-t", target, original)
                commands.append(["rename-window", "-t", window_id, "--", name.replace("#", "##")])
                if "automatic-rename" in local:
                    commands.append(["set-option", "-w", "-t", window_id, "automatic-rename", local["automatic-rename"]])
                else:
                    commands.append(["set-option", "-wu", "-t", window_id, "automatic-rename"])
                window["window_name"] = name
            window["automatic-rename-format"] = SIDEBAR_RENAME_FORMAT
        if commands:
            self.save()
            self.tmux.batch(commands)
        return bool(commands)

    def restore_window_names(self) -> None:
        commands = []
        for window_id, window in self.model.windows.items():
            saved = self.window_state.get(window_id, {}).get("rename_format")
            if not saved:
                continue
            if window["automatic-rename-format"] == SIDEBAR_RENAME_FORMAT:
                if saved["local"]:
                    commands.append(["set-option", "-w", "-t", window_id, "automatic-rename-format", saved["value"]])
                else:
                    commands.append(["set-option", "-wu", "-t", window_id, "automatic-rename-format"])
            commands.append(["set-option", "-wu", "-t", window_id, RENAME_SOURCE])
        self.tmux.batch(commands)

    def sync_geometry(self, automatic_resize: bool = False, resized_pane: str = "", resized_width: str = "") -> bool:
        commands: list[list[str]] = []
        new_preference = 0
        for window_id, window in self.model.windows.items():
            pane_id = self.model.sidebar(window_id)
            pane = self.model.panes.get(pane_id)
            if not pane:
                continue
            state = self.window_state.setdefault(window_id, {})
            if window["window_zoomed_flag"] == "1":
                state["zoomed"] = True
                continue
            geometry = [int(pane["pane_width"]), int(window["window_width"])]
            prior = state.get("geometry")
            content = sorted(item["pane_id"] for item in self.model.content(window_id))
            topology_changed = "panes" in state and state["panes"] != content
            if topology_changed:
                state["layout"] = ""
                state["panes"] = content
            manual = pane_id == resized_pane and str(geometry[0]) == resized_width and window_id not in self.programmatic
            automatic = not manual and (automatic_resize or state.get("zoomed") or topology_changed or (prior and prior[1] != geometry[1]))
            if window_id not in self.programmatic and prior and not automatic and prior[0] != geometry[0]:
                new_preference = geometry[0]
            elif automatic and geometry[0] != min(self.preferred_width, max(1, self.model.available_sidebar_width(window_id))):
                commands.append(["resize-pane", "-t", pane_id, "-x", str(min(self.preferred_width, max(1, self.model.available_sidebar_width(window_id))))])
                self.programmatic.add(window_id)
            state["geometry"] = geometry
            state["zoomed"] = False
        if new_preference:
            self._remember_width(new_preference)
            for window_id, window in self.model.windows.items():
                pane_id = self.model.sidebar(window_id)
                if not pane_id or window["window_zoomed_flag"] == "1":
                    continue
                width = min(self.preferred_width, max(1, self.model.available_sidebar_width(window_id)))
                if int(self.model.panes[pane_id]["pane_width"]) != width:
                    commands.append(["resize-pane", "-t", pane_id, "-x", str(width)])
                    self.programmatic.add(window_id)
        if commands:
            self.tmux.batch(commands)
        return bool(commands)

    def recover_views(self) -> bool:
        changed = False
        for pane_id, pane in self.model.panes.items():
            if not pane["sidebar"] or pane["pane_dead"] != "1":
                continue
            now = time.monotonic()
            failures = [stamp for stamp in self.respawns.get(pane_id, []) if now - stamp < 60]
            if len(failures) >= 3:
                self.errors[pane_id] = "View failed three times in 60 seconds; inspect engine.log and reload"
                continue
            failures.append(now)
            self.respawns[pane_id] = failures
            logging.warning("Restarting failed sidebar %s", pane_id)
            self.tmux.run("respawn-pane", "-k", "-t", pane_id, self.ui_command)
            changed = True
        return changed

    def sample_agents(self) -> None:
        if not self.is_open:
            return
        targets = [pane_id for pane_id, pane in self.model.panes.items() if not pane["sidebar"] and live_agent_app(pane["pane_current_command"], pane["pane_title"], self.agents.panes.get(pane_id, {})) == "codex"]
        if not targets or not any(self.model.sidebar(window) and self.model.visible(window) for window in self.model.windows):
            return
        commands = []
        for pane_id in targets:
            commands.extend([["display-message", "-p", "BINDER-CAPTURE:" + pane_id], ["capture-pane", "-p", "-t", pane_id]])
        output = self.tmux.batch(commands, required=False)
        captures = {}
        for section in output.split("BINDER-CAPTURE:")[1:]:
            pane_id, _, capture = section.partition("\n")
            if pane_id in targets:
                captures[pane_id] = terminal_status(capture)
        if captures != self.captures:
            self.captures = captures
            self.rows = project_rows(self.model, self.agents.panes, self.options, self.icons, self.badges, self.captures)
            self.revision += 1
            self.publish()

    def tick(self) -> None:
        now = time.monotonic()
        self.tmux.reap()
        if now >= self.next_reconcile:
            self.refresh()
        if now >= self.next_sample:
            self.next_sample = now + AGENT_SAMPLE_SECONDS
            self.sample_agents()
        for pending in self.navigations[:]:
            ready = self.preview_rendered.get(pending["sidebar"], 0) >= pending["input_id"] if "input_id" in pending else self.rendered.get(pending["sidebar"], -1) >= pending["revision"]
            if ready or now >= pending["deadline"]:
                if pending["client"] in self.model.clients:
                    self.tmux.batch([
                        ["select-window", "-t", pending["session"] + ":" + pending["window"]],
                        ["switch-client", "-c", pending["client"], "-t", pending["session"]],
                    ])
                self.navigations.remove(pending)
                self.refresh()

    def view_message(self, pane_id: str) -> dict:
        pane = self.model.panes.get(pane_id, {})
        window = pane.get("window_id", "")
        client = self.window_client.get(window, "")
        navigation = self.preview_states.get(pane_id, {})
        if self.preview_rendered.get(pane_id, 0) >= navigation.get("input_id", 0):
            navigation = {**navigation, "apply": False}
        return {
            "type": "snapshot", "revision": self.revision, "rows": self.rows,
            "main": self.window_main.get(window, ""), "focused": pane.get("pane_active") == "1",
            "client": client, "session": self.model.clients.get(client, {}).get("session_id", ""),
            "live_preview": enabled(option(self.options, "live_preview"), True),
            "collapse_panes": enabled(option(self.options, "collapse_panes"), True),
            "navigation": navigation if navigation.get("client") == client else {},
            "visible": self.model.visible(window), "shortcuts": shortcuts(self.options),
            "scrolloff": integer(option(self.options, "scrolloff"), 8),
            "colors": {key: option(self.options, "color_" + key) for key in ("session", "window", "pane")},
            "styles": {key: self.model.globals.get(key, "") for key in ("pane-active-border-style", "pane-border-style", "status-style", "pane-border-format", "window-status-current-style", "window-status-style")}, "open": self.is_open,
            "view_state": self.view_state.get(window, {}),
        }

    def publish(self) -> None:
        self.emit("snapshots", {})

    def client_for(self, pane_id: str, explicit: str = "") -> str:
        if explicit:
            if explicit not in self.model.clients:
                raise ValueError("Originating tmux client is no longer attached")
            return explicit
        window = self.model.panes.get(pane_id, {}).get("window_id", "")
        client = self.window_client.get(window, "")
        if client in self.model.clients and self.model.clients[client]["window_id"] == window:
            return client
        candidates = [item for item in self.model.clients.values() if item["window_id"] == window]
        return max(candidates, key=lambda item: int(item.get("client_activity") or 0))["client_name"] if candidates else ""

    def toggle(self, pane: str = "", window: str = "", client: str = "") -> None:
        self.read_options()
        if self.is_open:
            self.close()
            return
        self._set_open(True)
        window = window or self.model.panes.get(pane, {}).get("window_id", "")
        if client:
            self.window_client[window] = client
        if pane in self.model.panes and not self.model.panes[pane]["sidebar"]:
            self.window_main[window] = pane
            if client:
                self.client_main[client] = pane
        focus = window if enabled(option(self.options, "focus_on_open"), True) else ""
        self.ensure_all(focus)
        self.refresh(False)
        self._record_geometry()
        self.save()

    def close(self) -> None:
        self.navigations.clear()
        self.preview_states.clear()
        self._set_open(False)
        commands = [["kill-pane", "-t", pane_id] for pane_id, pane in self.model.panes.items() if pane["sidebar"]]
        self.tmux.batch(commands, required=False)
        self._snapshot()
        layouts = []
        for window_id, state in self.window_state.items():
            panes = sorted(pane["pane_id"] for pane in self.model.content(window_id))
            if window_id in self.model.windows and panes == state.get("panes") and state.get("layout"):
                layouts.append(["select-layout", "-t", window_id, state["layout"]])
        self.tmux.batch(layouts, required=False)
        self.restore_window_names()
        self.window_state.clear()
        self.programmatic.clear()
        self.refresh(False)

    def focus(self, pane: str, window: str = "", client: str = "") -> None:
        if not self.is_open:
            self.toggle(pane, window, client)
            return
        if self.model.panes.get(pane, {}).get("sidebar"):
            self.focus_main(pane, client)
            return
        window = window or self.model.panes.get(pane, {}).get("window_id", "")
        if client:
            self.window_client[window] = client
        self.window_main[window] = pane
        if client:
            self.client_main[client] = pane
        self.ensure_all()
        self.refresh(False)
        sidebar = self.model.sidebar(window)
        if sidebar:
            self.fit_sidebar(window)
            self.tmux.run("select-pane", "-t", sidebar)
        self.refresh(False)

    def fit_sidebar(self, window: str) -> bool:
        sidebar = self.model.sidebar(window)
        info = self.model.windows[window]
        if not sidebar or info["window_zoomed_flag"] == "1":
            return False
        expected = min(self.preferred_width, max(1, self.model.available_sidebar_width(window)))
        if int(self.model.panes[sidebar]["pane_width"]) == expected:
            return False
        self.tmux.run("resize-pane", "-t", sidebar, "-x", str(expected))
        self.window_state.setdefault(window, {})["geometry"] = [expected, int(info["window_width"])]
        return True

    def focus_main(self, sidebar: str, client: str = "", target: str = "", session: str = "") -> None:
        client = self.client_for(sidebar, client)
        window = self.model.panes.get(sidebar, {}).get("window_id", "")
        if target in self.model.panes and not self.model.panes[target]["sidebar"]:
            target_window = self.model.panes[target]["window_id"]
            links = self.model.windows[target_window]["links"]
            session = session if session in links else links[0]
            self.activate(sidebar, {"kind": "pane", "session_id": session, "window": target_window, "pane_id": target}, client)
            return
        target = self.client_main.get(client, "")
        if self.model.panes.get(target, {}).get("window_id") != window:
            target = self.window_main.get(window, "")
        if target in self.model.panes:
            self.tmux.run("select-pane", "-t", target)
            self.agents.acknowledge(target)
        self.refresh(False)

    def destination(self, row: dict) -> tuple[str, str, str]:
        session = row.get("session_id", "")
        if session not in self.model.sessions:
            raise ValueError("Selected session no longer exists")
        window = row.get("window", "")
        if row["kind"] == "session":
            window = self.model.sessions[session]["window_id"]
        if window not in self.model.windows or session not in self.model.windows[window]["links"]:
            raise ValueError("Selected window no longer exists")
        content = self.model.content(window)
        target = row.get("pane_id", "")
        if not target.startswith("%"):
            target = self.window_main.get(window, "") or (content[0]["pane_id"] if content else "")
        if target not in {pane["pane_id"] for pane in content}:
            raise ValueError("Selected pane no longer exists")
        return session, window, target

    def preview(self, source: str, row: dict, client: str, state: dict) -> None:
        if not self.is_open or not enabled(option(self.options, "live_preview"), True):
            return
        client = self.client_for(source, client)
        input_id = int(state.get("input_id", 0))
        if not client or input_id <= self.latest_preview.get(client, 0):
            return
        session, window, target = self.destination(row)
        sidebar = self.model.sidebar(window)
        if not sidebar:
            return
        self.latest_preview[client] = input_id
        self.navigations = [item for item in self.navigations if item["client"] != client]
        transfer = {**state, "selected": row["key"], "client": client, "input_id": input_id}
        self.preview_states[source] = {**transfer, "apply": bool(state.get("managed"))}
        self.preview_states[sidebar] = {**transfer, "apply": True}
        self.window_main[window] = target
        self.client_main[client] = target
        self.window_client[window] = client
        resized = self.fit_sidebar(window)
        if self.model.panes[sidebar]["pane_active"] != "1":
            self.tmux.run("select-pane", "-t", sidebar)
            self.refresh(False)
        elif resized:
            self.refresh(False)
        current = self.model.clients.get(client, {})
        if current.get("window_id") != window or current.get("session_id") != session:
            self.navigations.append({"sidebar": sidebar, "input_id": input_id, "deadline": time.monotonic() + .4, "client": client, "session": session, "window": window})
        self.publish()

    def activate(self, source: str, row: dict, client: str = "") -> None:
        client = self.client_for(source, client)
        session, window, target = self.destination(row)
        self.navigations = [item for item in self.navigations if item["client"] != client]
        self.preview_states = {pid: state for pid, state in self.preview_states.items() if state.get("client") != client}
        self.window_main[window] = target
        if client:
            self.client_main[client] = target
        self.agents.acknowledge(target)
        self.tmux.batch([["select-pane", "-t", target], ["set-option", "-g", "@tmux_sidebar_main_pane", target]])
        self.refresh(False)
        self.navigations.append({"sidebar": self.model.sidebar(window), "revision": self.revision, "deadline": time.monotonic() + 0.4, "client": client, "session": session, "window": window})
        self.publish()

    def handle_event(self, kind: str, pane: str = "", window: str = "", client: str = "", **unused: object) -> None:
        self.last_event = kind
        self.events += 1
        if kind == "resize" and pane in self.model.panes and not self.model.panes[pane]["sidebar"]:
            state = self.window_state.get(window, {})
            state["layout"] = ""
        self.refresh()
        current = self.model.panes.get(pane)
        origin = self.model.clients.get(client)
        if kind == "focus" and current and current["pane_active"] == "1" and self.model.visible(current["window_id"]):
            if origin and origin["window_id"] != current["window_id"]:
                return
            if client:
                self.window_client[current["window_id"]] = client
            if not current["sidebar"]:
                self.window_main[current["window_id"]] = pane
                if client:
                    self.client_main[client] = pane
                if self.agents.acknowledge(pane):
                    self.refresh(False)

    def update_agent(self, event: dict, pane: str = "", roots: list[str] | None = None, explicit: bool = True) -> None:
        if pane not in self.model.panes:
            self.refresh()
        target = resolve_agent_pane(self.model.panes, self.agents.panes, event, pane, roots or [], explicit)
        if not target and roots:
            self.refresh()
            target = resolve_agent_pane(self.model.panes, self.agents.panes, event, pane, roots, explicit)
        pane = target
        metadata = self.model.panes.get(pane)
        if not metadata or metadata["sidebar"]:
            return
        if event.get("app") and live_agent_app(metadata["pane_current_command"], metadata["pane_title"], self.agents.panes.get(pane, {})) != event["app"]:
            self.refresh()
            metadata = self.model.panes.get(pane)
            if not metadata:
                return
        session = self.model.sessions.get(self.model.windows[metadata["window_id"]]["links"][0], {})
        extra = {**metadata, "session_name": session.get("session_name", ""), "window_name": self.model.windows[metadata["window_id"]]["window_name"]}
        delegated_count = len(self.agents.delegated)
        if self.agents.update(pane, event, extra):
            self.rows = project_rows(self.model, self.agents.panes, self.options, self.icons, self.badges, self.captures)
            self.revision += 1
            self.publish()
            self.save()
        elif len(self.agents.delegated) != delegated_count:
            self.save()

    def named_action(self, action: str, row: dict, name: str) -> None:
        if not name.strip():
            return
        session = row.get("session_id", row.get("session", ""))
        window = row.get("window", "")
        if action == "add_window":
            self.tmux.run("new-window", "-d", "-a", "-t", session + ":" + window, "-n", name)
        elif action == "add_session":
            self.tmux.run("new-session", "-d", "-s", name)
            order = [value for value in option(self.options, "session_order").split(",") if value] or [item["session_name"] for item in self.model.sessions.values()]
            after = self.model.sessions.get(session, {}).get("session_name", session)
            index = order.index(after) + 1 if after in order else len(order)
            order.insert(index, name)
            self.set_option("session_order", ",".join(dict.fromkeys(order)))
        elif action == "rename_window":
            self.tmux.run("rename-window", "-t", window, name)
        elif action == "rename_session":
            old_name = self.model.sessions.get(session, {}).get("session_name", session)
            self.tmux.run("rename-session", "-t", session, name)
            order = option(self.options, "session_order")
            if order:
                self.set_option("session_order", ",".join(name if value == old_name else value for value in order.split(",")))
        else:
            raise ValueError("Unknown naming action")
        self.refresh()

    def set_option(self, suffix: str, value: str) -> None:
        name = "@tmux_pane_tree_" + suffix
        self.tmux.run("set-option", "-g", name, value)
        self.options[name] = value

    def remove_row(self, row: dict) -> None:
        if row["kind"] == "session":
            self.tmux.run("kill-session", "-t", row["session_id"])
        elif row["kind"] == "window":
            self.tmux.run("kill-window", "-t", row["window"])
        else:
            self.tmux.run("kill-pane", "-t", row["pane_id"])
            window = row["window"]
            remaining = [pane for pane in self.model.content(window) if pane["pane_id"] != row["pane_id"]]
            if not remaining and self.model.sidebar(window):
                self.tmux.run("kill-window", "-t", window, required=False)
        self.refresh()

    def configure(self) -> None:
        options = self._load_options()
        icons, badges = visuals(options)
        self.options = options
        self.icons, self.badges = icons, badges
        width = integer(option(self.options, "width"), self.preferred_width, 1)
        if width != self.preferred_width:
            self.preferred_width = width
            commands = []
            for wid, window in self.model.windows.items():
                sidebar = self.model.sidebar(wid)
                if sidebar and window["window_zoomed_flag"] != "1":
                    commands.append(["resize-pane", "-t", sidebar, "-x", str(min(width, max(1, self.model.available_sidebar_width(wid))))])
                    self.programmatic.add(wid)
            self.tmux.batch(commands)
        self.install_bindings()
        self.refresh()

    def shell_command(self, command: str, **arguments: str) -> str:
        parts = [sys.executable, str(ENTRYPOINT), command]
        for key, value in arguments.items():
            parts.extend(["--" + key.replace("_", "-"), value])
        return "exec " + shlex.join(parts)

    def install_bindings(self) -> None:
        commands = []
        installed = self.tmux.batch([["show-hooks", "-g"], ["show-hooks", "-gw"]]).splitlines()
        supported = {line.split("[", 1)[0].split(" ", 1)[0] for line in installed}
        old_hooks = {"client-active": 198, "client-attached": 199, "client-session-changed": 200, "client-focus-in": 202, "after-select-window": 203, "after-select-pane": 204, "session-window-changed": 205, "after-split-window": 206, "after-new-window": 207, "after-kill-pane": 208, "after-resize-pane": 209, "after-rename-window": 210, "after-rename-session": 211, "window-layout-changed": 212, "session-created": 213, "pane-exited": 214, "pane-died": 215}
        commands.extend([["set-hook", "-gu", f"{hook}[{index}]"] for hook, index in old_hooks.items() if hook in supported])
        for key in self.options:
            if re.match(r"@tmux_sidebar_(?:pane|creating|layout|panes|focus)_w\d+$|@tmux_pane_tree_geometry_w\d+$", key):
                commands.append(["set-option", "-gu", key])
        events = {
            "client-attached": "focus", "client-detached": "structure", "client-session-changed": "focus", "client-focus-in": "focus",
            "after-select-window": "focus", "after-select-pane": "focus", "session-window-changed": "focus",
            "window-layout-changed": "layout", "after-resize-pane": "resize", "session-created": "structure",
            "session-closed": "structure", "session-renamed": "structure", "window-linked": "structure", "window-unlinked": "structure",
            "window-renamed": "structure", "after-split-window": "structure",
            "after-kill-pane": "structure", "pane-died": "structure", "pane-exited": "structure",
            "pane-title-changed": "metadata", "client-resized": "layout", "window-resized": "layout", "window-pane-changed": "focus",
            "after-set-option": "configuration", "after-new-window": "structure",
        }
        for line in installed:
            if EVENT_CHANNEL in line:
                commands.append(["set-hook", "-gu", line.split(" ", 1)[0]])
        for index, (hook, kind) in enumerate(events.items(), 230):
            if hook not in supported:
                continue
            # Repeated wait-for signals can cancel each other before a listener returns.
            signal_command = "set-environment -gh " + EVENT_PENDING + " " + self.event_channel + " ; wait-for -S " + self.event_channel
            hook_command = "if-shell -F " + shlex.quote("#{!=:#{" + EVENT_PENDING + "}," + self.event_channel + "}") + " " + shlex.quote(signal_command)
            if kind == "metadata":
                condition = "#{&&:#{@tmux_sidebar_enabled},#{!=:" + TITLE_FORMAT + ",#{@tmux_pane_tree_observed_title}}}"
                update = "set-option -pF @tmux_pane_tree_observed_title '" + TITLE_FORMAT + "' ; " + hook_command
                hook_command = "if-shell -F " + shlex.quote(condition) + " " + shlex.quote(update)
            elif kind == "resize":
                hook_command = "set-option -gF @tmux_pane_tree_resized_pane '#{pane_id}:#{pane_width}' ; " + hook_command
            elif hook in {"window-resized", "client-resized"}:
                hook_command = "set-option -g @tmux_pane_tree_auto_resize 1 ; " + hook_command
            elif kind == "focus":
                hook_command = "set-option -gF @tmux_pane_tree_focus_pane '#{pane_id}' ; set-option -gF @tmux_pane_tree_focus_client '#{client_name}' ; " + hook_command
            commands.append(["set-hook", "-g", f"{hook}[{index}]", hook_command])
        for action, default in (("toggle", "t"), ("focus", "T")):
            key = option(self.options, action + "_key", default) or default
            previous = self.options.get("@tmux_sidebar_bound_" + action + "_key", default)
            if previous != key:
                commands.append(["unbind-key", previous])
            command = self.shell_command(action, pane="#{pane_id}", window="#{window_id}", client="#{client_name}")
            commands.append(["bind-key", key, "run-shell", "-b", command])
            commands.append(["set-option", "-g", "@tmux_sidebar_bound_" + action + "_key", key])
        for action, default in (("jump_back", "C-o"), ("jump_forward", "C-i")):
            key = shortcuts(self.options)[action]
            previous = self.options.get("@tmux_sidebar_bound_" + action + "_shortcut", default)
            if previous != key:
                commands.append(["unbind-key", "-n", previous])
            if re.fullmatch("C-[A-Za-z]", key):
                command = self.shell_command("view-action", action=action, pane="#{pane_id}", client="#{client_name}")
                commands.append(["bind-key", "-n", key, "if-shell", "-F", "#{@tmux_sidebar_owned}", "run-shell -b " + shlex.quote(command), "send-keys " + key])
                commands.append(["set-option", "-g", "@tmux_sidebar_bound_" + action + "_shortcut", key])
        original = self.options.get("@tmux_pane_tree_original_mouse_binding", "")
        if not original:
            bindings = self.tmux.run("list-keys", "-T", "root", required=False)
            binding = next((line for line in bindings.splitlines() if re.match(r"bind-key\s+(?:-T\s+root\s+)?MouseDown3Pane\s", line)), "")
            match = re.match(r"bind-key\s+(?:-T\s+root\s+)?MouseDown3Pane\s+(.+)", binding)
            original = match.group(1) if match else "select-pane -t= ; send-keys -M"
        legacy_menu = re.match(r'if-shell -F "#\{m:Sidebar,#\{pane_title\}\}" \{ if-shell .*? \{ source-file .*? \} \} \{ (.*) \}$', original)
        if legacy_menu:
            original = legacy_menu.group(1)
        if original != self.options.get("@tmux_pane_tree_original_mouse_binding"):
            commands.append(["set-option", "-g", "@tmux_pane_tree_original_mouse_binding", original])
        menu = self.shell_command("menu", pane="#{pane_id}", row="#{mouse_y}", client="#{client_name}")
        commands.append(["bind-key", "-T", "root", "MouseDown3Pane", "if-shell", "-F", "#{@tmux_sidebar_owned}", "select-pane -t=", original])
        original_up = self.options.get("@tmux_pane_tree_original_mouse_up_binding", "")
        if not original_up:
            bindings = self.tmux.run("list-keys", "-T", "root", required=False)
            binding = next((line for line in bindings.splitlines() if re.match(r"bind-key\s+(?:-T\s+root\s+)?MouseUp3Pane\s", line)), "")
            match = re.match(r"bind-key\s+(?:-T\s+root\s+)?MouseUp3Pane\s+(.+)", binding)
            original_up = match.group(1) if match else "send-keys -M"
            commands.append(["set-option", "-g", "@tmux_pane_tree_original_mouse_up_binding", original_up])
        commands.append(["bind-key", "-T", "root", "MouseUp3Pane", "if-shell", "-F", "#{@tmux_sidebar_owned}", "run-shell -b " + shlex.quote(menu), original_up])
        self.tmux.batch(commands)

    def menu(self, pane: str, mouse_row: int, client: str = "", key: str = "") -> None:
        client = self.client_for(pane, client)
        if not client:
            return
        row_map = self.row_maps.get(pane, {})
        keys = row_map.get("keys", [])
        metadata = self.model.panes.get(pane, {})
        offset = mouse_row - int(metadata.get("pane_top", 0))
        index = offset + row_map.get("scroll", 0)
        key = key or (keys[index] if 0 <= offset < int(row_map.get("lines", metadata.get("pane_height", 0))) and 0 <= index < len(keys) else "")
        row = next((row for row in self.rows if row["key"] == key), None)
        if row and row["kind"] == "session":
            return
        def callback(action: str, command: str = "view-action") -> str:
            return "run-shell -b " + shlex.quote(self.shell_command(command, pane=pane, client=client, action=action, key=key))
        args = ["display-menu", "-M", "-c", client, "-t", pane, "-x", "R", "-y", str(max(0, mouse_row)), "-T", " Binder "]
        if row:
            args.extend(["Select", "s", callback("select"), "Rename", "r", callback("rename")])
            if row["kind"] == "session":
                args.extend(["Detach Session", "d", callback("detach", "menu-action")])
            else:
                args.extend(["Split Horizontal", "h", callback("split_horizontal", "menu-action"), "Split Vertical", "v", callback("split_vertical", "menu-action")])
                if row["kind"] == "pane" or row.get("pane_id", "").startswith("%"):
                    args.extend(["Zoom / Unzoom", "z", callback("zoom", "menu-action"), "Break to Window", "b", callback("break_window", "menu-action"), "Mark / Unmark", "M", callback("mark", "menu-action")])
            args.extend(["Close", "x", callback("confirm_close")])
        args.extend(["New Window", "w", callback("add_window"), "New Session", "n", callback("add_session"), "Refresh", "R", callback("refresh"), "Close Sidebar", "q", callback("close")])
        self.tmux.spawn(*args)

    def menu_action(self, action: str, row: dict, client: str = "") -> None:
        if action == "detach" and row["kind"] == "session":
            self.tmux.run("detach-client", "-s", row["session_id"])
        else:
            window = row.get("window", "")
            target = row.get("pane_id", "")
            if not target.startswith("%"):
                target = self.window_main.get(window, "")
            if target not in self.model.panes or self.model.panes[target]["sidebar"]:
                raise ValueError("Selected content pane no longer exists")
            if action in {"split_horizontal", "split_vertical"}:
                self.tmux.run("split-window", "-d", "-h" if action == "split_horizontal" else "-v", "-t", target)
            elif action == "zoom":
                self.tmux.run("resize-pane", "-Z", "-t", target)
            elif action == "break_window":
                self.tmux.run("break-pane", "-d", "-s", target)
            elif action == "mark":
                marked = self.tmux.run("display-message", "-pt", target, "#{pane_marked}") == "1"
                self.tmux.run("select-pane", "-M" if marked else "-m", "-t", target)
            else:
                raise ValueError("Unknown menu action")
        self.refresh()

    def diagnostics(self) -> dict:
        return {"pid": os.getpid(), "server_pid": self.identity.server_pid, "socket": self.identity.socket_path,
                "state": str(self.identity.directory), "open": self.is_open, "revision": self.revision,
                "windows": len(self.model.windows), "views": sum(pane["sidebar"] for pane in self.model.panes.values()),
                "clients": len(self.model.clients), "tmux_calls": self.tmux.calls, "tmux_seconds": round(self.tmux.seconds, 6),
                "events": self.events, "last_event": self.last_event, "errors": self.errors,
                "agents": self.agents.panes, "window_main": self.window_main, "client_main": self.client_main}
