"""Person colors: the preset swatches and the rules a chosen color must follow.

The same colors are used on the calendar, charts and goal list, in both light
and dark mode, so a color has to be readable on either background and easy to
tell apart from everyone else's."""

import colorsys
import math
import re

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

# Used for anyone who has not picked a color, in order of their name.
DEFAULT_PALETTE = ["#3b82f6", "#f97316", "#a855f7", "#ec4899", "#eab308", "#14b8a6"]

# Shown on a calendar day when EVERYONE had a workout day. Nobody can pick it
# (or anything close) as their own color, so the two are never confused.
EVERYONE_COLOR = "#16a34a"

# Offered as one-tap swatches. Every one passes check() and is far enough from
# the others that two people can both pick presets without looking alike.
PRESETS = [
    "#3b82f6",  # blue
    "#f97316",  # orange
    "#a855f7",  # purple
    "#ec4899",  # pink
    "#eab308",  # yellow
    "#14b8a6",  # teal
    "#ef4444",  # red
    "#84cc16",  # lime
    "#06b6d4",  # cyan
    "#6366f1",  # indigo
    "#b45309",  # brown
]

MIN_SATURATION = 0.25  # not gray: grays are used for goal lines and muted text
MIN_LIGHTNESS = 0.30   # not so dark it vanishes on the dark theme
MAX_LIGHTNESS = 0.68   # not so pale it vanishes on the light theme
MIN_DISTANCE = 80      # how different from another person's color (see distance())


def normalize(color: str | None) -> str | None:
    """Lowercase #rrggbb, or None if it is not in that form."""
    if not isinstance(color, str) or not HEX.match(color.strip()):
        return None
    return color.strip().lower()


def _rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def distance(a: str, b: str) -> float:
    """Perceptual-ish distance ("redmean"): 0 for identical, about 765 at most."""
    r1, g1, b1 = _rgb(a)
    r2, g2, b2 = _rgb(b)
    rm = (r1 + r2) / 2
    return math.sqrt(
        (2 + rm / 256) * (r1 - r2) ** 2
        + 4 * (g1 - g2) ** 2
        + (2 + (255 - rm) / 256) * (b1 - b2) ** 2
    )


def check(color: str, others: dict[str, str] | None = None) -> str | None:
    """None if the color is acceptable, otherwise a message saying why not.
    `color` must already be normalized. `others` maps other people's names to
    their current colors."""
    r, g, b = _rgb(color)
    _, lightness, saturation = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    if saturation < MIN_SATURATION:
        return "That color is too gray. Pick something with more color."
    if lightness < MIN_LIGHTNESS:
        return "That color is too dark to see on the dark theme. Pick a lighter one."
    if lightness > MAX_LIGHTNESS:
        return "That color is too pale to see on the light theme. Pick a darker one."
    if distance(color, EVERYONE_COLOR) < MIN_DISTANCE:
        return "That color is too close to the green used when everyone works out."
    for name, other in (others or {}).items():
        if distance(color, other) < MIN_DISTANCE:
            return f"That color is too close to {name.capitalize()}'s. Pick a different one."
    return None
