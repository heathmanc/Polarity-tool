"""Central color tokens for the ISA-101-aligned high-performance HMI.

The operating display is intentionally light and low-chroma. Saturated color is
reserved for conditions that require attention. Camera imagery and ROI overlays
remain natural/high-contrast because they carry process information.
"""

from __future__ import annotations

# Base surfaces and text
APP_BACKGROUND = "#D8DCDE"
HEADER_BACKGROUND = "#ECEEEF"
SIDEBAR_BACKGROUND = "#E4E7E9"
SURFACE = "#F7F8F8"
SURFACE_ALT = "#ECEFF0"
SURFACE_STRONG = "#FFFFFF"
BORDER = "#8A949A"
BORDER_LIGHT = "#B9C0C4"
TEXT = "#1D2429"
TEXT_MUTED = "#59636A"
TEXT_DISABLED = "#8A9297"
NEUTRAL = TEXT

# Interaction/navigation. Blue-gray is not a process-state color.
BLUE = "#2F5D7C"
BLUE_LIGHT = "#DCE8EF"
FOCUS = "#2F6F9F"

# Process state colors. Keep these limited to explicit status/result use.
GOOD = "#2F6B3F"
BAD = "#B42318"
AMBER = "#9A6700"
GOOD_BG = "#E7EFE9"
BAD_BG = "#F7E6E4"
AMBER_BG = "#F7EED8"
NEUTRAL_BG = "#EEF0F1"

# ROI/role colors are deliberately not alarm red or normal/pass green.
ROLE_NEGATIVE = "#4F5960"
ROLE_POSITIVE = "#2F5D7C"
ROI_BATTERY = "#276A8E"
ROI_MARKING = "#76548A"
ROI_AUXILIARY = "#6A5A2F"

# Camera viewport colors.
VIEWPORT_BACKGROUND = "#C4C9CC"
VIEWPORT_BORDER = "#707A80"
VIEWPORT_PLACEHOLDER = "#424B50"


def tone_color(tone: str | None) -> str:
    return {
        "good": GOOD,
        "bad": BAD,
        "warning": AMBER,
        "info": BLUE,
        "neutral": TEXT,
    }.get(tone or "", TEXT)


def background_for_color(color: str) -> str:
    normalized = color.upper()
    if normalized == GOOD.upper():
        return GOOD_BG
    if normalized == BAD.upper():
        return BAD_BG
    if normalized == AMBER.upper():
        return AMBER_BG
    return SURFACE_ALT

# A region that rejected the part is drawn in BAD and heavier than a region
# that passed. The weight difference has to survive being read at station
# distance on a glare-lit floor, where hue alone does not carry.
FAILED_ROI_LINE_WIDTH = 6
FAILED_MARKING_LINE_WIDTH = 4
