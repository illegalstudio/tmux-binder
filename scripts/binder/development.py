from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from .runtime import ROOT, Identity, connect, request
from .tmux import Tmux


def reload(identity: Identity) -> None:
    try:
        channel = connect(identity, start=False)
    except (ConnectionRefusedError, FileNotFoundError):
        channel = None
    if channel is not None:
        channel.close()
        request(identity, "shutdown")
        deadline = time.monotonic() + 5
        while identity.endpoint.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
    tmux = Tmux(identity.socket_path)
    tmux.run("source-file", str(ROOT / "tmux-pane-tree.conf"))
    request(identity, "configure")
    request(identity, "reload-views")


def install(identity: Identity) -> None:
    destination = Path(os.environ.get("PLUGIN_DST", str(Path.home() / ".config/tmux/plugins/tmux-binder"))).expanduser()
    if destination.resolve() != ROOT.resolve():
        destination.mkdir(parents=True, exist_ok=True)
        for name in ("scripts", "examples"):
            shutil.copytree(ROOT / name, destination / name, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for name in ("sidebar.tmux", "tmux-pane-tree.tmux", "tmux-pane-tree.conf", "sidebar.conf", "README.md", "LICENSE"):
            shutil.copy2(ROOT / name, destination / name)
    Tmux(identity.socket_path).run("source-file", str(destination / "tmux-pane-tree.conf"))
