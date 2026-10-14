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


def colors(configuration: dict, styles: dict) -> dict[str, int]:
    result = {"session": curses.A_BOLD, "window": 0, "pane": 0}
    if not curses.has_colors() or curses.COLORS < 256:
        return result
    sources = {"session": "pane-active-border-style", "window": "pane-border-style", "pane": "status-style"}
    for index, (kind, source) in enumerate(sources.items(), 1):
        custom = configuration.get(kind, "")
        match = re.search(r"#?([a-fA-F0-9]{6})", custom) if custom else re.search(r"fg=#([a-fA-F0-9]{6})", styles.get(source, ""))
        if not match:
            continue
        try:
            curses.init_pair(index, color_number(match.group(1)), -1)
            result[kind] = curses.color_pair(index) | (curses.A_BOLD if kind == "session" else 0)
        except curses.error:
            continue
    return result


class Renderer:
    def __init__(self, screen) -> None:
        self.screen = screen
        self.previous: list[tuple] = []
        self.size = (0, 0)

    def draw(self, rows: list[dict], selected: str, scroll: int, attributes: dict[str, int], query: str = "", footer: str = "", cursor: bool = False, collapsed: set[str] | None = None) -> None:
        height, columns = self.screen.getmaxyx()
        cells = max(0, columns - 1)
        lines = height - bool(footer)
        frame = []
        for row in rows[scroll:scroll + max(0, lines)]:
            text = display_text(row["text"])
            if collapsed and row["key"] in collapsed:
                text += " …"
            text = clip(("▶ " if row["key"] == selected else "  ") + text, cells)
            attribute = curses.A_BOLD if row["key"] == selected else attributes.get(row["kind"], 0)
            if query and query.casefold() in text.casefold():
                attribute |= curses.A_UNDERLINE
            frame.append((text, attribute))
        while len(frame) < max(0, lines):
            frame.append(("", 0))
        if footer and height:
            frame.append((clip(display_text(footer), cells), 0 if cursor else curses.A_DIM))
        resized = self.size != (height, columns)
        for y, item in enumerate(frame):
            if resized or y >= len(self.previous) or self.previous[y] != item:
                text, attr = item
                try:
                    self.screen.move(y, 0)
                    self.screen.clrtoeol()
                    if cells > 0:
                        self.screen.addstr(y, 0, text, attr)
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
        self.size = (height, columns)
