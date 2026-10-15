from __future__ import annotations

import subprocess
import time
import logging
from dataclasses import dataclass, field

SCHEMAS = {
    "P": ("pane_id", "window_id", "pane_current_command", "pane_title", "pane_active", "pane_width", "pane_height", "pane_dead", "pane_pid", "pane_current_path", "@tmux_sidebar_owned", "pane_left", "pane_top"),
    "W": ("session_id", "window_id", "window_index", "window_name", "window_width", "window_height", "window_layout", "window_zoomed_flag", "window_active", "automatic-rename", "automatic-rename-format"),
    "S": ("session_id", "session_name", "session_attached", "window_id"),
    "C": ("client_name", "client_pid", "session_id", "window_id", "pane_id", "client_activity", "client_flags"),
    "G": ("pid", "@tmux_sidebar_enabled", "@tmux_sidebar_main_pane", "pane-active-border-style", "pane-border-style", "status-style", "pane-border-format", "@tmux_pane_tree_resized_pane", "@tmux_pane_tree_focus_pane", "@tmux_pane_tree_focus_client", "window-status-current-style", "window-status-style"),
}


def record_format(kind: str) -> str:
    return kind + "".join("#{n:" + key + "}:#{" + key + "}" for key in SCHEMAS[kind])


def parse_records(raw: bytes) -> dict[str, list[dict[str, str]]]:
    records: dict[str, list[dict[str, str]]] = {kind: [] for kind in SCHEMAS}
    offset = 0
    while offset < len(raw):
        kind = chr(raw[offset])
        if kind not in SCHEMAS:
            raise ValueError(f"Unexpected tmux record at byte {offset}")
        offset += 1
        row = {}
        for name in SCHEMAS[kind]:
            end = raw.find(b":", offset)
            if end < 0 or not raw[offset:end].isdigit():
                raise ValueError(f"Invalid length in tmux record {kind}.{name} at {offset}: {raw[offset:offset + 80]!r}")
            length = int(raw[offset:end])
            offset = end + 1
            if offset + length > len(raw):
                raise ValueError("Truncated tmux record")
            row[name] = raw[offset:offset + length].decode("utf-8", errors="replace")
            offset += length
        if raw[offset:offset + 1] != b"\n":
            raise ValueError("Unterminated tmux record")
        offset += 1
        records[kind].append(row)
    return records


class TmuxError(RuntimeError):
    pass


def command_script(commands: list[list[str]]) -> bytes:
    def quote(value: str) -> str:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t") + '"'
    return ("\n".join(" ".join(quote(str(value)) for value in command) for command in commands) + "\n").encode()


@dataclass
class Tmux:
    socket_path: str
    calls: int = 0
    seconds: float = 0
    children: list = field(default_factory=list)

    def run(self, *arguments: str, required: bool = True) -> str:
        return self.raw(*arguments, required=required).decode("utf-8", errors="replace").rstrip("\n")

    def raw(self, *arguments: str, required: bool = True) -> bytes:
        return self.execute([list(arguments)], required=required)

    def execute(self, commands: list[list[str]], required: bool = True) -> bytes:
        self.calls += 1
        started = time.monotonic()
        try:
            result = subprocess.run(["tmux", "-S", self.socket_path, "source-file", "-"], input=command_script(commands), capture_output=True, timeout=5)
        finally:
            self.seconds += time.monotonic() - started
        if result.returncode and required:
            raise TmuxError(result.stderr.decode(errors="replace").strip() or "tmux command failed")
        return result.stdout

    def batch(self, commands: list[list[str]], required: bool = True) -> str:
        if not commands:
            return ""
        return self.execute(commands, required=required).decode("utf-8", errors="replace").rstrip("\n")

    def snapshot(self) -> dict[str, list[dict[str, str]]]:
        commands = [
            ["list-panes", "-a", "-F", record_format("P")],
            ["list-windows", "-a", "-F", record_format("W")],
            ["list-sessions", "-F", record_format("S")],
            ["list-clients", "-F", record_format("C")],
            ["display-message", "-p", record_format("G")],
        ]
        return parse_records(self.execute(commands))

    def spawn(self, *arguments: str) -> None:
        self.calls += 1
        child = subprocess.Popen(["tmux", "-S", self.socket_path, *arguments], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.children.append(child)

    def reap(self) -> None:
        for child in self.children[:]:
            if child.poll() is not None:
                _, error = child.communicate()
                if child.returncode:
                    logging.warning("tmux UI command: %s", error.decode(errors="replace").strip())
                self.children.remove(child)
