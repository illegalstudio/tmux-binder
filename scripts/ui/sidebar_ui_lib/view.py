from __future__ import annotations

import re
import unicodedata


def clusters(text: str) -> list[str]:
    result: list[str] = []
    for char in text:
        code = ord(char)
        combining = bool(unicodedata.combining(char)) or char in {"\ufe0e", "\ufe0f", "\u200d"} or 0x1F3FB <= code <= 0x1F3FF
        flag_pair = result and len(result[-1]) == 1 and 0x1F1E6 <= ord(result[-1]) <= 0x1F1FF and 0x1F1E6 <= code <= 0x1F1FF
        if result and (combining or result[-1].endswith("\u200d") or flag_pair):
            result[-1] += char
        else:
            result.append(char)
    return result


def cluster_width(cluster: str) -> int:
    widths = [0 if unicodedata.combining(char) or unicodedata.category(char) in {"Cf", "Cc", "Mn", "Me"} else 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1 for char in cluster]
    if "\u200d" in cluster or any(0x1F3FB <= ord(char) <= 0x1F3FF for char in cluster):
        return max(widths, default=0)
    if "\ufe0f" in cluster or (len(cluster) == 2 and all(0x1F1E6 <= ord(char) <= 0x1F1FF for char in cluster)):
        return 2
    return sum(widths)


def width(text: str) -> int:
    return sum(cluster_width(cluster) for cluster in clusters(text))


def clip(text: str, cells: int) -> str:
    if cells <= 0:
        return ""
    if width(text) <= cells:
        return text
    output = []
    remaining = cells - 1
    for cluster in clusters(text):
        size = cluster_width(cluster)
        if size > remaining:
            break
        output.append(cluster)
        remaining -= size
    return "".join(output) + "…"


def display_text(text: str) -> str:
    return "".join(" " if unicodedata.category(char) == "Cc" else char for char in text)


def visible_rows(rows: list[dict], collapsed: set[str]) -> list[dict]:
    return [row for row in rows if not any(row["key"].startswith(parent + "/") for parent in collapsed)]


def navigable(rows: list[dict], collapsed: set[str]) -> list[dict]:
    return [row for row in rows if "pane_id" in row or row["key"] in collapsed]


def selection_for(rows: list[dict], key: str, main: str) -> str:
    if any(row["key"] == key for row in rows):
        return key
    match = next((row for row in rows if row.get("pane_id") == main), None)
    if match:
        return match["key"]
    return next((row["key"] for row in rows if "pane_id" in row), rows[0]["key"] if rows else "")


def ensure_visible(index: int, scroll: int, lines: int, margin: int) -> int:
    if index < 0 or lines <= 0:
        return 0
    margin = min(margin, max(0, (lines - 1) // 2))
    if index < scroll + margin:
        return max(0, index - margin)
    if index >= scroll + lines - margin:
        return max(0, index - lines + margin + 1)
    return max(0, scroll)


def shortcut_step(pending: str, char: str, shortcuts: dict[str, str]) -> tuple[str, str]:
    candidate = pending + char
    for name, shortcut in shortcuts.items():
        if shortcut == candidate:
            return "", name
    if any(shortcut.startswith(candidate) for shortcut in shortcuts.values()):
        return candidate, ""
    if pending:
        return shortcut_step("", char, shortcuts)
    return "", ""


def control_key(shortcut: str) -> str:
    return chr(ord(shortcut[-1].lower()) & 31) if re.fullmatch("C-[A-Za-z]", shortcut) else ""
