"""Color utilities, ANSI TrueColor formatting, and Unicode color tags."""

import re
import unicodedata
from typing import Optional, Tuple

# Mapping of common color names to 24-bit RGB hex
COLOR_PALETTE = {
    "amber": "#f59e0b",
    "yellow": "#eab308",
    "green": "#10b981",
    "emerald": "#059669",
    "red": "#ef4444",
    "ruby": "#dc2626",
    "blue": "#3b82f6",
    "sky": "#0ea5e9",
    "cyan": "#06b6d4",
    "purple": "#8b5cf6",
    "violet": "#7c3aed",
    "pink": "#ec4899",
    "rose": "#f43f5e",
    "orange": "#f97316",
    "lime": "#84cc16",
    "teal": "#14b8a6",
    "gray": "#6b7280",
    "white": "#f9fafb",
    "black": "#000000",
}

# Unicode color tag glyphs for tab titles
UNICODE_COLOR_TAGS = {
    "red": "🔴",
    "green": "🟢",
    "yellow": "🟡",
    "amber": "🟡",
    "blue": "🔵",
    "purple": "🟣",
    "violet": "🟣",
    "orange": "🟠",
    "white": "⚪",
    "gray": "⚪",
    "brown": "🟤",
    "sq_red": "🟥",
    "sq_green": "🟩",
    "sq_blue": "🟦",
    "sq_yellow": "🟨",
    "sq_purple": "🟪",
    "sq_orange": "🟧",
    "sq_white": "⬜",
    "sq_black": "⬛",
}


def hex_to_rgb(hex_code: str) -> Optional[Tuple[int, int, int]]:
    """Convert hex color string (#rrggbb or rrggbb) to RGB tuple."""
    clean = hex_code.strip().lstrip("#")
    if len(clean) == 3:
        clean = "".join(c * 2 for c in clean)
    if len(clean) != 6:
        return None
    try:
        return (int(clean[0:2], 16), int(clean[2:4], 16), int(clean[4:6], 16))
    except ValueError:
        return None


def resolve_color_hex(color_spec: str) -> str:
    """Resolve a color name or hex string to normalized #rrggbb."""
    lowered = color_spec.strip().lower()
    if lowered in COLOR_PALETTE:
        return COLOR_PALETTE[lowered]
    if lowered.startswith("#"):
        return lowered
    if len(lowered) in (3, 6) and re.fullmatch(r"[0-9a-fA-F]+", lowered):
        return f"#{lowered}"
    return "#3b82f6"  # Fallback blue


def get_color_tag(color_spec: str, square: bool = False) -> str:
    """Get matching Unicode color tag glyph for tab titles."""
    clean = color_spec.strip().lower().lstrip("#")
    prefix = "sq_" if square else ""

    # Direct name match with prefix preferred if square
    if square and f"sq_{clean}" in UNICODE_COLOR_TAGS:
        return UNICODE_COLOR_TAGS[f"sq_{clean}"]
    if clean in UNICODE_COLOR_TAGS:
        return UNICODE_COLOR_TAGS[clean]

    # Match by RGB distance if hex
    rgb = hex_to_rgb(clean)
    if rgb:
        r, g, b = rgb
        # Simple color family classification
        if r > 180 and g < 100 and b < 100:
            return UNICODE_COLOR_TAGS[f"{prefix}red"]
        if g > 160 and r < 140:
            return UNICODE_COLOR_TAGS[f"{prefix}green"]
        if r > 180 and g > 150 and b < 90:
            return UNICODE_COLOR_TAGS[f"{prefix}yellow"]
        if r > 180 and g > 90 and b < 60:
            return UNICODE_COLOR_TAGS[f"{prefix}orange"]
        if b > 160 and r < 120:
            return UNICODE_COLOR_TAGS[f"{prefix}blue"]
        if r > 120 and b > 140 and g < 120:
            return UNICODE_COLOR_TAGS[f"{prefix}purple"]

    return "🔵" if not square else "🟦"


def colorize(
    text: str,
    fg: Optional[str] = None,
    bg: Optional[str] = None,
    bold: bool = False,
    dim: bool = False,
) -> str:
    """Format text with ANSI TrueColor (24-bit RGB) and styles."""
    codes = []
    if bold:
        codes.append("1")
    if dim:
        codes.append("2")

    if fg:
        hex_val = resolve_color_hex(fg)
        rgb = hex_to_rgb(hex_val)
        if rgb:
            codes.append(f"38;2;{rgb[0]};{rgb[1]};{rgb[2]}")

    if bg:
        hex_val = resolve_color_hex(bg)
        rgb = hex_to_rgb(hex_val)
        if rgb:
            codes.append(f"48;2;{rgb[0]};{rgb[1]};{rgb[2]}")

    if not codes:
        return text

    prefix = f"\033[{';'.join(codes)}m"
    suffix = "\033[0m"
    return f"{prefix}{text}{suffix}"


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences to get visible character string."""
    return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)


def display_width(text: str) -> int:
    """Terminal column width of text (emoji and CJK take two columns, combining marks none)."""
    width = 0
    chars = strip_ansi(text)
    for i, ch in enumerate(chars):
        if unicodedata.combining(ch) or ch in ("\u200d", "\ufe0e", "\ufe0f"):
            continue
        emoji_presentation = i + 1 < len(chars) and chars[i + 1] == "\ufe0f"
        width += 2 if emoji_presentation or unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def truncate_display(text: str, width: int) -> str:
    """Truncate plain text to a display width, adding an ellipsis when cut."""
    if display_width(text) <= width:
        return text
    out, used = "", 0
    for ch in text:
        w = display_width(ch)
        if used + w > width - 1:
            break
        out += ch
        used += w
    return out + "…"


def pad_ansi(text: str, width: int) -> str:
    """Pad a string containing ANSI escape codes based on its visible width."""
    pad = max(0, width - display_width(text))
    return text + (" " * pad)

