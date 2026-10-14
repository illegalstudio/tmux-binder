from __future__ import annotations

import fcntl
import logging
import os
import selectors
import signal
import socket
import subprocess
import time
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler

from .agents import normalize
from .config import enabled, option
from .controller import Application, EVENT_CHANNEL
from .runtime import MAX_MESSAGE, Identity, decode, encode


@dataclass
class Peer:
    channel: socket.socket
    incoming: bytearray = field(default_factory=bytearray)
    outgoing: bytearray = field(default_factory=bytearray)
    pane: str = ""
    closing: bool = False


class Server:
    def __init__(self, identity: Identity) -> None:
        self.identity = identity
        self.selector = selectors.DefaultSelector()
        self.peers: dict[int, Peer] = {}
        self.application: Application | None = None
        self.events: list[dict] = []
        self.events_at = 0.0
        self.stopping = False
        self.waiter: subprocess.Popen | None = None
        self.save_at = 0.0

    def watch_tmux(self) -> None:
        if self.waiter is not None:
            return
        self.waiter = subprocess.Popen(["tmux", "-S", self.identity.socket_path, "wait-for", EVENT_CHANNEL], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.selector.register(self.waiter.stdout, selectors.EVENT_READ, "tmux")

    def emit(self, kind: str, message: dict) -> None:
        if self.application is None:
            return
        for peer in list(self.peers.values()):
            if not peer.pane:
                continue
            if kind == "snapshots":
                self.send(peer, self.application.view_message(peer.pane), snapshot=True)
            elif kind == "notice" or peer.pane == kind:
                self.send(peer, message)

    def send(self, peer: Peer, message: dict, snapshot: bool = False) -> None:
        payload = encode(message)
        if len(payload) > MAX_MESSAGE:
            self.disconnect(peer)
            return
        if len(peer.outgoing) + len(payload) > MAX_MESSAGE:
            logging.warning("Disconnecting slow view %s", peer.pane)
            self.disconnect(peer)
            return
        peer.outgoing.extend(payload)
        try:
            self.selector.modify(peer.channel, selectors.EVENT_READ | selectors.EVENT_WRITE, peer)
        except (KeyError, ValueError):
            return

    def disconnect(self, peer: Peer) -> None:
        self.peers.pop(peer.channel.fileno(), None)
        try:
            self.selector.unregister(peer.channel)
        except (KeyError, ValueError):
            pass
        peer.channel.close()

    def flush_events(self) -> None:
        if not self.events or self.application is None:
            return
        events, self.events = self.events, []
        self.events_at = 0
        app = self.application
        for event in events:
            if event.get("kind") == "resize":
                pane = app.model.panes.get(event.get("pane", ""), {})
                if pane and not pane["sidebar"]:
                    app.window_state.get(pane["window_id"], {})["layout"] = ""
        app.last_event = events[-1].get("kind", "structure")
        app.events += len(events)
        app.refresh()
        acknowledged = False
        for event in events:
            if event.get("kind") != "focus":
                continue
            pane_id = event.get("pane", "")
            pane = app.model.panes.get(pane_id, {})
            client_id = event.get("client", "")
            client = app.model.clients.get(client_id)
            if not pane or pane["pane_active"] != "1" or not app.model.visible(pane["window_id"]):
                continue
            if client and client["window_id"] != pane["window_id"]:
                continue
            if client_id:
                app.window_client[pane["window_id"]] = client_id
            if not pane["sidebar"]:
                app.window_main[pane["window_id"]] = pane_id
                if client_id:
                    app.client_main[client_id] = pane_id
                acknowledged = app.agents.acknowledge(pane_id) or acknowledged
        if acknowledged:
            app.refresh(False)
        self.watch_tmux()

    def row_for(self, message: dict) -> dict:
        assert self.application is not None
        app = self.application
        key = message.get("key", "")
        if key:
            row = next((row for row in app.rows if row["key"] == key), None)
            if row is None:
                raise ValueError("Selected item no longer exists")
            return row
        target = message.get("target", "") or message.get("pane", "")
        row = next((row for row in app.rows if row.get("pane_id") == target or row.get("window") == target or row.get("session_id") == target), None)
        if row:
            return row
        session = message.get("session", "")
        sid = next((key for key, value in app.model.sessions.items() if key == session or value["session_name"] == session), "")
        window = message.get("window", "")
        if message.get("window_index"):
            window = next((wid for wid, item in app.model.windows.items() if item["indices"].get(sid) == str(message["window_index"])), "")
        if sid:
            if not window:
                window = next((wid for wid, value in app.model.windows.items() if sid in value["links"]), "")
            return {"kind": "window", "session_id": sid, "window": window}
        if app.rows:
            return next((row for row in app.rows if row["kind"] != "session"), app.rows[0])
        raise ValueError("No available session")

    def dispatch(self, peer: Peer, message: dict) -> dict | None:
        app = self.application
        assert app is not None
        command = message.get("command", "")
        pane = message.get("pane", "") or peer.pane
        client = message.get("client", "")
        if command == "event":
            self.events.append(message)
            if not self.events_at:
                self.events_at = time.monotonic() + 0.025
            return {"ok": True}
        if command == "rendered":
            if not peer.pane:
                raise ValueError("Render acknowledgement requires a subscribed view")
            app.rendered[peer.pane] = int(message.get("revision", -1))
            app.row_maps[peer.pane] = {"keys": message.get("keys", []), "scroll": int(message.get("scroll", 0))}
            window = app.model.panes.get(peer.pane, {}).get("window_id", "")
            state = {"collapsed": message.get("collapsed", []), "selected": message.get("selected", ""), "scroll": int(message.get("scroll", 0))}
            if app.view_state.get(window) != state:
                app.view_state[window] = state
                self.save_at = time.monotonic() + 0.3
            return None
        self.flush_events()
        if command == "subscribe":
            if pane not in app.model.panes:
                app.refresh()
            if pane not in app.model.panes:
                raise ValueError("Sidebar pane no longer exists")
            peer.pane = pane
            return app.view_message(pane)
        if command == "configure":
            app.configure()
            if enabled(option(app.options, "install_agent_hooks")):
                from .installer import install
                install()
        elif command == "toggle":
            app.toggle(pane, message.get("window", ""), client)
        elif command == "close":
            app.close()
        elif command == "focus":
            app.focus(pane, message.get("window", ""), client)
        elif command == "focus-main":
            app.focus_main(pane, client)
        elif command in {"ensure", "refresh"}:
            app.refresh()
        elif command == "activate":
            app.activate(pane, self.row_for(message), client)
        elif command == "name":
            app.named_action(message["action"], self.row_for(message), message.get("name", ""))
        elif command == "remove":
            app.remove_row(self.row_for(message))
        elif command in {"toggle-filter", "toggle-panes"}:
            suffix = "filter_enabled" if command == "toggle-filter" else "hide_panes"
            app.set_option(suffix, "off" if enabled(option(app.options, suffix), suffix == "filter_enabled") else "on")
            app.refresh(False)
        elif command == "agent":
            event = normalize(message["app"], message.get("payload", {}), message.get("event", ""))
            event["updated_at"] = message.get("updated_at")
            app.update_agent(event, pane, message.get("roots"))
        elif command == "update-state":
            app.update_agent(message.get("event", {}), pane)
        elif command == "clear-state":
            if app.agents.acknowledge(pane):
                app.refresh(False)
        elif command == "menu":
            app.menu(pane, int(message.get("row", 0)), client, message.get("key", ""))
        elif command == "menu-action":
            app.menu_action(message.get("action", ""), self.row_for(message), client)
        elif command == "view-action":
            if client:
                app.window_client[app.model.panes.get(pane, {}).get("window_id", "")] = client
            self.emit(pane, {"type": "action", "action": message.get("action", ""), "key": message.get("key", ""), "client": client})
        elif command == "reload-views":
            for pid, info in app.model.panes.items():
                if info["sidebar"]:
                    app.tmux.run("respawn-pane", "-k", "-t", pid, app.ui_command)
            app.refresh(False)
        elif command in {"doctor", "snapshot"}:
            if command == "doctor":
                return {"ok": True, **app.diagnostics(), "connected_views": len({item.pane for item in self.peers.values() if item.pane}), "waiter_pid": self.waiter.pid if self.waiter else None}
            return {"ok": True, **app.view_message(pane)}
        elif command == "shutdown":
            self.stopping = True
        else:
            raise ValueError(f"Unknown command: {command}")
        return {"ok": True, "revision": app.revision}

    def handle(self, peer: Peer, mask: int) -> None:
        if mask & selectors.EVENT_READ:
            chunk = peer.channel.recv(65536)
            if not chunk:
                self.disconnect(peer)
                return
            peer.incoming.extend(chunk)
            if len(peer.incoming) > MAX_MESSAGE:
                self.disconnect(peer)
                return
            while b"\n" in peer.incoming:
                raw, _, remaining = peer.incoming.partition(b"\n")
                peer.incoming = bytearray(remaining)
                try:
                    reply = self.dispatch(peer, decode(raw))
                except Exception as error:
                    logging.exception("Request failed")
                    reply = {"error": str(error)}
                if reply is not None:
                    self.send(peer, reply)
                if not peer.pane:
                    peer.closing = True
        if mask & selectors.EVENT_WRITE and peer.outgoing:
            sent = peer.channel.send(peer.outgoing)
            del peer.outgoing[:sent]
            if not peer.outgoing:
                if peer.closing:
                    self.disconnect(peer)
                else:
                    self.selector.modify(peer.channel, selectors.EVENT_READ, peer)

    def run(self) -> None:
        self.identity.prepare()
        with (self.identity.directory / "engine.lock").open("a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            self.identity.endpoint.unlink(missing_ok=True)
            listener = socket.socket(socket.AF_UNIX)
            listener.bind(str(self.identity.endpoint))
            os.chmod(self.identity.endpoint, 0o600)
            listener.listen(64)
            listener.setblocking(False)
            self.selector.register(listener, selectors.EVENT_READ)
            wake_read, wake_write = socket.socketpair()
            wake_read.setblocking(False)
            wake_write.setblocking(False)
            self.selector.register(wake_read, selectors.EVENT_READ, "wake")
            previous_fd = signal.set_wakeup_fd(wake_write.fileno())
            def stop(signum: int, frame: object) -> None:
                self.stopping = True
            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
            try:
                self.application = Application(self.identity, self.emit)
                self.application.tmux.run("wait-for", "-S", EVENT_CHANNEL)
                self.watch_tmux()
                while not self.stopping:
                    now = time.monotonic()
                    timeout = min(1.0, max(0, self.events_at - now)) if self.events_at else 1.0
                    if self.application.navigations:
                        timeout = min(timeout, 0.01)
                    if self.save_at:
                        timeout = min(timeout, max(0, self.save_at - now))
                    for key, mask in self.selector.select(timeout):
                        if key.fileobj is listener:
                            channel, _ = listener.accept()
                            channel.setblocking(False)
                            peer = Peer(channel)
                            self.peers[channel.fileno()] = peer
                            self.selector.register(channel, selectors.EVENT_READ, peer)
                        elif key.data == "wake":
                            wake_read.recv(1024)
                        elif key.data == "tmux":
                            self.selector.unregister(key.fileobj)
                            key.fileobj.close()
                            assert self.waiter is not None
                            self.waiter.wait()
                            self.waiter = None
                            self.events.append({"kind": "native"})
                            if not self.events_at:
                                self.events_at = time.monotonic() + 0.025
                        else:
                            try:
                                self.handle(key.data, mask)
                            except (BrokenPipeError, ConnectionResetError, OSError):
                                self.disconnect(key.data)
                    if self.events_at and time.monotonic() >= self.events_at:
                        self.flush_events()
                        self.watch_tmux()
                    if not os.path.exists(self.identity.socket_path):
                        break
                    try:
                        os.kill(self.identity.server_pid, 0)
                    except ProcessLookupError:
                        break
                    self.application.tick()
                    if self.save_at and time.monotonic() >= self.save_at:
                        self.application.save()
                        self.save_at = 0
                if self.identity.directory.exists():
                    self.application.save()
            finally:
                if self.waiter is not None:
                    self.selector.unregister(self.waiter.stdout)
                    self.waiter.terminate()
                    self.waiter.communicate(timeout=3)
                for peer in list(self.peers.values()):
                    if peer.outgoing:
                        try:
                            peer.channel.settimeout(0.1)
                            peer.channel.sendall(peer.outgoing)
                        except OSError:
                            pass
                signal.set_wakeup_fd(previous_fd)
                for peer in list(self.peers.values()):
                    self.disconnect(peer)
                self.selector.close()
                listener.close()
                wake_read.close()
                wake_write.close()
                self.identity.endpoint.unlink(missing_ok=True)


def serve(identity: Identity) -> None:
    identity.prepare()
    handler = RotatingFileHandler(identity.directory / "engine.log", maxBytes=512000, backupCount=2)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=[handler])
    Server(identity).run()
