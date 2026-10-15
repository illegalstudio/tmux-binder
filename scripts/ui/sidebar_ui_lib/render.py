from __future__ import annotations

import curses
import re

from .view import clip, display_text, width


def color_number(value: str) -> int:
    rgb = [int(value[index:index + 2], 16) for index in (0, 2, 4)]
    levels = [0, 95, 135, 175, 215, 255]
    red, green, blue = [min(range(6), key=lambda index: abs(levels[index] - component)) for component in rgb]
    cube = 16 + red * 36 + green * 6 + blue
    grey = min(range(24), key=lambda index: sum(abs(8 + index * 10 - component) for component in rgb))
    cube_error = sum(abs(levels[index] - component) for index, component in zip((red, green, blue), rgb))
    return 232 + grey if sum(abs(8 + grey * 10 - component) for component in rgb) < cube_error else cube


def style_color(value: str) -> int:
    value = value.strip().lower()
    if re.fullmatch(r"#?[0-9a-f]{6}", value):
        return color_number(value.lstrip("#"))
    names = ("black", "red", "green", "yellow", "blue", "magenta", "cyan", "white")
    if value in names:
        return names.index(value)
    if value.startswith("bright") and value[6:] in names:
        return 8 + names.index(value[6:])
    number = value.removeprefix("colour").removeprefix("color")
    return int(number) if number.isdigit() and 0 <= int(number) < 256 else -1


def style_parts(style: str) -> tuple[int, int, int]:
    values = dict(part.split("=", 1) for part in style.split(",") if "=" in part)
    flags = 0
    for token, attribute in (("bold", curses.A_BOLD), ("dim", curses.A_DIM), ("reverse", curses.A_REVERSE), ("underscore", curses.A_UNDERLINE)):
        if token in style.split(","):
            flags |= attribute
    return style_color(values.get("fg", "")), style_color(values.get("bg", "")), flags


def colors(configuration: dict, styles: dict) -> dict[str, int]:
    result = {"session": curses.A_BOLD, "window": 0, "pane": curses.A_DIM, "guide": curses.A_DIM, "selected": curses.A_REVERSE | curses.A_BOLD}
    for status in ("running", "subagent-running", "needs-input", "done", "error"):
        result["status:" + status] = curses.A_BOLD
        result["selected-status:" + status] = result["selected"]
    if not curses.has_colors() or curses.COLORS < 256:
        return result
    pairs: dict[tuple[int, int], int] = {}

    def pair(foreground: int, background: int = -1) -> int:
        key = (foreground, background)
        if key == (-1, -1):
            return 0
        if key not in pairs:
            index = len(pairs) + 1
            try:
                curses.init_pair(index, *key)
                pairs[key] = curses.color_pair(index)
            except curses.error:
                return 0
        return pairs[key]

    sources = {"session": "pane-active-border-style", "window": "window-status-style", "pane": "status-style", "guide": "pane-border-style"}
    for kind, source in sources.items():
        foreground, background, flags = style_parts(styles.get(source, ""))
        if background != -1:
            foreground = -1
        custom = configuration.get(kind, "")
        if custom:
            foreground = style_color(custom)
        result[kind] |= pair(foreground) | flags
        if kind == "pane" and custom:
            result[kind] &= ~curses.A_DIM
    foreground, background, flags = style_parts(styles.get("window-status-current-style", ""))
    if foreground == -1 and background == -1:
        foreground = style_parts(styles.get("pane-active-border-style", ""))[0]
    result["selected"] = pair(foreground, background) | flags | curses.A_BOLD
    if background == -1:
        result["selected"] |= curses.A_REVERSE
    selected_background = foreground if result["selected"] & curses.A_REVERSE else background
    for status, color in (("running", 6), ("subagent-running", 6), ("needs-input", 3), ("done", 2), ("error", 1)):
        result["status:" + status] = pair(color) | curses.A_BOLD
        result["selected-status:" + status] = pair(color + 8 if color == selected_background else color, selected_background) | curses.A_BOLD
    return result


def badge_runs(row: dict, collapsed: bool, cells: int) -> list[tuple[str, str]]:
    if cells <= 0:
        return []
    badge = row.get("collapsed_badge", row.get("badge", "")) if collapsed else row.get("badge", "")
    status = row.get("collapsed_status", row.get("status", "")) if collapsed else row.get("status", "")
    parts = row.get("badges", [{"text": badge, "symbol": badge, "status": status}])
    parts = [part for part in parts if part.get("text")]
    runs: list[tuple[str, str]] = []
    used = 0
    for index, part in enumerate(parts):
        text = (" " if runs else "") + display_text(part["text"])
        reserve = int(index < len(parts) - 1)
        if used + width(text) + reserve <= cells:
            runs.append((text, part["status"]))
            used += width(text)
            continue
        if not runs:
            symbol = clip(display_text(part.get("symbol", part["text"])), cells)
            runs.append((symbol, part["status"]))
            used = width(symbol)
        if used < cells:
            runs.append(("…", ""))
        break
    return runs


def row_layout(row: dict, selected: bool, collapsed: bool, cells: int) -> tuple[str, str, list[tuple[str, str]]]:
    prefix = "▶ " if selected else "  "
    if "label" not in row:
        label = row.get("collapsed_text", row["text"]) if collapsed else row["text"]
        label += " …" if collapsed else ""
    elif row["kind"] == "session":
        prefix += "▸ " if collapsed else ""
        label = row["label"]
    elif row["kind"] == "window":
        prefix += " " + (("▸" if collapsed else "▾") if row.get("expandable") else " ") + " "
        label = row["label"]
    else:
        icon = row.get("icon", "")
        prefix += "   " + (icon + " " if icon and icon != "?" else "")
        label = row["label"]
    budget = cells
    parts = row.get("badges", [])
    if len(parts) > 1:
        reserved = width(display_text(prefix)) + min(4, width(display_text(label))) + 1
        budget = max(min(width(display_text(parts[0]["text"])), cells), cells - reserved)
    runs = badge_runs(row, collapsed, budget)
    badge = "".join(text for text, _ in runs)
    available = max(0, cells - width(badge) - bool(badge))
    prefix = display_text(prefix)
    left = clip(prefix + display_text(label), available)
    text = left + " " * max(0, cells - width(left) - width(badge)) + badge
    return text, clip(prefix, width(left)), runs


def row_text(row: dict, selected: bool, collapsed: bool, cells: int) -> tuple[str, str, str]:
    text, prefix, runs = row_layout(row, selected, collapsed, cells)
    return text, prefix, "".join(text for text, _ in runs)


class Renderer:
    def __init__(self, screen) -> None:
        self.screen = screen
        self.previous: list[tuple] = []
        self.layout: tuple = ()
        self.needs_clear = True

    def invalidate(self) -> None:
        self.previous.clear()
        self.needs_clear = True

    def draw(self, rows: list[dict], selected: str, scroll: int, attributes: dict[str, int], query: str = "", footer: str = "", cursor: bool = False, collapsed: set[str] | None = None, focused: bool = True) -> None:
        height, columns = self.screen.getmaxyx()
        cells = max(0, columns - 1)
        lines = height - bool(footer)
        layout = (height, columns, scroll, tuple(row["key"] for row in rows[scroll:scroll + max(0, lines)]))
        if self.needs_clear or layout != self.layout:
            self.screen.clear()
            self.previous.clear()
            self.needs_clear = False
        self.layout = layout
        frame = []
        for row in rows[scroll:scroll + max(0, lines)]:
            is_collapsed = bool(collapsed and row["key"] in collapsed)
            chosen = row["key"] == selected
            highlighted = chosen and focused
            text, prefix, runs = row_layout(row, chosen, is_collapsed, cells)
            badge = "".join(text for text, _ in runs)
            attribute = attributes.get("selected", curses.A_REVERSE | curses.A_BOLD) if highlighted else attributes.get(row["kind"], 0)
            if chosen and not focused:
                attribute |= curses.A_DIM
            if query and query.casefold() in text.casefold():
                attribute |= curses.A_UNDERLINE
            badge_attributes = tuple((text, attributes.get(("selected-status:" if highlighted else "status:") + status, attribute)) for text, status in runs)
            guide_attribute = attribute if highlighted else attributes.get("guide", curses.A_DIM)
            frame.append((text, attribute, prefix, guide_attribute, badge, badge_attributes))
        while len(frame) < max(0, lines):
            frame.append(("", 0, "", 0, "", ()))
        if footer and height:
            frame.append((clip(display_text(footer), cells), 0 if cursor else curses.A_DIM, "", 0, "", ()))
        for y, item in enumerate(frame):
            if y < len(self.previous) and self.previous[y] == item:
                continue
            text, attr, prefix, guide_attr, badge, badge_attributes = item
            try:
                self.screen.move(y, 0)
                self.screen.clrtoeol()
                if cells > 0:
                    if prefix:
                        self.screen.addstr(y, 0, prefix, guide_attr)
                    body = text[len(prefix):len(text) - len(badge) if badge else len(text)]
                    if body:
                        self.screen.addstr(y, width(prefix), body, attr)
                    if badge:
                        column = cells - width(badge)
                        for run, badge_attr in badge_attributes:
                            self.screen.addstr(y, column, run, badge_attr)
                            column += width(run)
            except curses.error:
                continue
        try:
            curses.curs_set(1 if cursor else 0)
            if cursor and height and cells:
                self.screen.move(height - 1, min(width(footer), cells - 1))
            self.screen.noutrefresh()
            curses.doupdate()
        except curses.error:
            pass
        self.previous = frame
