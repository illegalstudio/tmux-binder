from __future__ import annotations

import fcntl
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

PROTOCOL = 1
MAX_MESSAGE = 4 * 1024 * 1024
ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = ROOT / "scripts" / "binder.py"
_DAEMONS: list[subprocess.Popen] = []


def state_home() -> Path:
    override = os.environ.get("TMUX_PANE_TREE_STATE_DIR") or os.environ.get("TMUX_SIDEBAR_STATE_DIR")
    return Path(override) if override else Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "tmux-sidebar"


@dataclass(frozen=True)
class Identity:
    socket_path: str
    server_pid: int
    directory: Path
    preferences: Path

    @classmethod
    def resolve(cls, socket_path: str = "") -> Identity:
        context = os.environ.get("TMUX", "").rsplit(",", 2)
        if not socket_path and len(context) == 3:
            socket_path = context[0]
        if len(context) == 3 and socket_path == context[0] and context[1].isdigit():
            key = hashlib.sha256(os.fsencode(socket_path)).hexdigest()[:16]
            base = state_home()
            return cls(socket_path, int(context[1]), base / "servers" / f"{key}-{context[1]}", base / "preferences" / f"{key}.json")
        args = ["tmux", *(["-S", socket_path] if socket_path else []), "display-message", "-p", "#{socket_path}\n#{pid}"]
        result = subprocess.run(args, capture_output=True, timeout=5)
        if result.returncode:
            raise RuntimeError(result.stderr.decode(errors="replace").strip() or "No tmux server is running")
        resolved, pid = result.stdout.decode().strip().splitlines()
        key = hashlib.sha256(os.fsencode(resolved)).hexdigest()[:16]
        base = state_home()
        return cls(resolved, int(pid), base / "servers" / f"{key}-{pid}", base / "preferences" / f"{key}.json")

    @property
    def endpoint(self) -> Path:
        return self.directory / "engine.sock"

    def prepare(self) -> None:
        self.directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.directory.chmod(0o700)
        if len(os.fsencode(self.endpoint)) > 103:
            raise ValueError("State path is too long for a Unix socket; set TMUX_PANE_TREE_STATE_DIR to a shorter private path")


def encode(message: dict) -> bytes:
    return json.dumps({"protocol": PROTOCOL, **message}, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"


def decode(raw: bytes) -> dict:
    if len(raw) > MAX_MESSAGE:
        raise ValueError("IPC message exceeds size limit")
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get("protocol") != PROTOCOL:
        raise ValueError("Incompatible tmux-binder protocol; reload the plugin")
    return value


@contextmanager
def bootstrap_lock(identity: Identity) -> Iterator[None]:
    identity.prepare()
    with (identity.directory / "bootstrap.lock").open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def connect(identity: Identity, start: bool = True) -> socket.socket:
    if len(os.fsencode(identity.endpoint)) > 103:
        raise ValueError("State path is too long for a Unix socket; set TMUX_PANE_TREE_STATE_DIR to a shorter private path")
    _DAEMONS[:] = [child for child in _DAEMONS if child.poll() is None]
    def attempt() -> socket.socket:
        channel = socket.socket(socket.AF_UNIX)
        channel.settimeout(10)
        try:
            channel.connect(str(identity.endpoint))
        except BaseException:
            channel.close()
            raise
        return channel

    try:
        return attempt()
    except (FileNotFoundError, ConnectionRefusedError):
        if not start:
            raise
    with bootstrap_lock(identity):
        try:
            return attempt()
        except (FileNotFoundError, ConnectionRefusedError):
            pass
        with (identity.directory / "engine.log").open("ab", buffering=0) as log:
            environment = {**os.environ, "TMUX_PANE_TREE_STATE_DIR": str(identity.directory.parent.parent), "TMUX": f"{identity.socket_path},{identity.server_pid},0"}
            child = subprocess.Popen(
                [sys.executable, str(ENTRYPOINT), "--socket", identity.socket_path, "serve"],
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True, close_fds=True, env=environment,
            )
            _DAEMONS.append(child)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            try:
                return attempt()
            except (FileNotFoundError, ConnectionRefusedError):
                time.sleep(0.025)
        raise RuntimeError(f"Coordinator failed to start; see {identity.directory / 'engine.log'}")


def request(identity: Identity, command: str, **arguments: object) -> dict:
    if command in {"doctor", "snapshot"}:
        for attempt in range(2):
            try:
                return _request(identity, command, **arguments)
            except (ConnectionResetError, BrokenPipeError, ConnectionError):
                if attempt:
                    raise
        raise AssertionError("Unreachable request state")
    return _request(identity, command, **arguments)


def _request(identity: Identity, command: str, **arguments: object) -> dict:
    with connect(identity) as channel:
        channel.sendall(encode({"command": command, **arguments}))
        raw = b""
        while b"\n" not in raw:
            chunk = channel.recv(65536)
            if not chunk:
                raise ConnectionError("Coordinator disconnected before replying")
            raw += chunk
            if len(raw) > MAX_MESSAGE:
                raise ValueError("IPC response exceeds size limit")
        reply = decode(raw.split(b"\n", 1)[0])
        if "error" in reply:
            raise RuntimeError(reply["error"])
        return reply


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + f".new.{os.getpid()}")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            os.chmod(temporary, 0o600)
            json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return value
