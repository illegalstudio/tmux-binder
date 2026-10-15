from __future__ import annotations

import re
import shlex
import os
from functools import lru_cache
from pathlib import Path

from .icons import DEFAULT_BADGES, FONT_FILE_SUFFIXES, ICON_THEMES, NERD_FONT_BADGES

SHORTCUTS = {
    "add_window": "aw", "add_session": "as", "go_top": "gg", "go_bottom": "G",
    "jump_back": "C-o", "jump_forward": "C-i", "rename_session": "rs",
    "rename_window": "rw", "close_pane": "x", "toggle_filter": "f",
}


def enabled(value: str, default: bool = False) -> bool:
    return value.strip().lower() in {"1", "on", "true", "yes"} if value else default


def integer(value: str, default: int, minimum: int = 0) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number >= minimum else default


def parse_options(raw: str, include: tuple[str, ...] = ()) -> dict[str, str]:
    options = {}
    for line in raw.splitlines():
        name, _, value = line.partition(" ")
        if not name.startswith(("@tmux_pane_tree_", "@tmux_sidebar_")) and name not in include:
            continue
        parts = shlex.split(value)
        options[name] = parts[0] if parts else ""
    return options


def option(options: dict[str, str], suffix: str, default: str = "") -> str:
    for prefix in ("@tmux_pane_tree_", "@tmux_sidebar_"):
        if prefix + suffix in options:
            return options[prefix + suffix]
    return default


def shortcuts(options: dict[str, str]) -> dict[str, str]:
    configured = {name: option(options, name + "_shortcut", default) for name, default in SHORTCUTS.items()}
    values = list(configured.values())
    invalid = any(not value or "q" in value or (value.startswith("C-") and not re.fullmatch("C-[A-Za-z]", value)) for value in values)
    invalid |= len(set(values)) != len(values)
    invalid |= any(a != b and b.startswith(a) for a in values for b in values)
    return dict(SHORTCUTS) if invalid else configured


@lru_cache(maxsize=1)
def font_installed() -> bool:
    override = os.environ.get("TMUX_PANE_TREE_FONT_DIRS", os.environ.get("TMUX_SIDEBAR_FONT_DIRS"))
    roots = [Path(value).expanduser() for value in override.split(os.pathsep) if value] if override is not None else [
        Path.home() / "Library/Fonts", Path("/Library/Fonts"), Path("/System/Library/Fonts"),
        Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "fonts",
        Path.home() / ".fonts", Path("/usr/local/share/fonts"), Path("/usr/share/fonts"),
    ]
    for root in roots:
        if not root.exists():
            continue
        for path in ([root] if root.is_file() else root.rglob("*")):
            if path.suffix.lower() in FONT_FILE_SUFFIXES and re.search(r"nerd\s*font", path.name, re.I):
                return True
    return False


def visuals(options: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    theme = option(options, "icon_theme", "auto").lower()
    if theme == "auto":
        theme = "nerdfont" if font_installed() else "ascii"
    if theme not in ICON_THEMES:
        raise ValueError(f"Unknown icon theme: {theme}")
    icons = {app: option(options, "icon_" + app, icon) for app, icon in ICON_THEMES[theme].items()}
    defaults = NERD_FONT_BADGES if theme == "nerdfont" else DEFAULT_BADGES
    badges = {status: option(options, "badge_" + status.replace("-", "_"), badge) for status, badge in defaults.items()}
    return icons, badges
