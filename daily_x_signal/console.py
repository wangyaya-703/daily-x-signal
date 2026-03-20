from __future__ import annotations

from typing import Any


def render_table(headers: list[str], rows: list[list[Any]]) -> str:
    if not headers:
        return ""
    normalized_rows = [[_stringify(cell) for cell in row] for row in rows]
    widths = [len(_stringify(header)) for header in headers]
    for row in normalized_rows:
        for idx, cell in enumerate(row):
            if idx < len(widths):
                widths[idx] = max(widths[idx], len(cell))

    border = "+" + "+".join("-" * (width + 2) for width in widths) + "+"
    header_line = "|" + "|".join(f" {_stringify(header).ljust(widths[idx])} " for idx, header in enumerate(headers)) + "|"
    lines = [border, header_line, border]
    for row in normalized_rows:
        padded = row + [""] * (len(headers) - len(row))
        lines.append("|" + "|".join(f" {padded[idx].ljust(widths[idx])} " for idx in range(len(headers))) + "|")
    lines.append(border)
    return "\n".join(lines)


def print_section(title: str) -> None:
    print(f"\n== {title} ==")


def bool_text(value: bool) -> str:
    return "Yes" if value else "No"


def status_text(ok: bool) -> str:
    return "OK" if ok else "WARN"


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    return text.replace("\n", " / ")
