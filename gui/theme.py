"""Cyber / hacker-HUD theme for the eDBG GUI.

Single source of truth for the palette plus the two setup entry points:

    setup_fonts()  -> load bundled JetBrains Mono (system chain fallback)
    setup_theme()  -> build + bind the global HUD theme; returns accent handles

The palette constants are pure data (no DPG calls at import), so they can be
imported and unit-tested without creating a viewport.
"""

from __future__ import annotations

import os

import dearpygui.dearpygui as dpg

# =====================================================================
#  Palette  (near-black base, neon accents)
# =====================================================================

# ── Backgrounds ──
BG_BASE  = (10, 14, 20)      # #0a0e14 viewport / window
BG_PANEL = (16, 21, 30)      # #10151e docked panels / child
BG_ELEV  = (22, 28, 40)      # elevated: popups, frames, headers

# ── Lines ──
BORDER        = (34, 42, 56)   # subtle panel border
BORDER_ACCENT = (46, 90, 96)   # cyan-ish accent border (HUD "glow")

# ── Accents ──
ACCENT_GREEN = (78, 201, 176)  # #4ec9b0 attached / ok / connect
ACCENT_CYAN  = (86, 212, 255)  # #56d4ff primary accent / run
ACCENT_AMBER = (229, 192, 123) # #e5c07b running / warn
ACCENT_RED   = (255, 92, 92)   # #ff5c5c stopped / error / disconnect

# ── Text ──
TEXT     = (198, 208, 224)     # primary text
TEXT_DIM = (110, 120, 140)     # disabled / secondary

# ── Legacy aliases (kept so existing app.py status calls keep working) ──
CLR_GREEN  = ACCENT_GREEN
CLR_RED    = ACCENT_RED
CLR_YELLOW = ACCENT_AMBER
CLR_CYAN   = ACCENT_CYAN
CLR_WHITE  = TEXT
CLR_GRAY   = TEXT_DIM
CLR_BLUE   = ACCENT_CYAN

# ── Font size ──
FONT_SIZE = 15

# Directory holding bundled fonts (gui/assets/fonts)
_FONT_DIR = os.path.join(os.path.dirname(__file__), "assets", "fonts")


def _brighten(color: tuple[int, ...], amount: int) -> tuple[int, ...]:
    """Return a lighter variant of an RGB(A) tuple, clamped to 255."""
    rgb = tuple(min(255, c + amount) for c in color[:3])
    return rgb + tuple(color[3:])


def _darken(color: tuple[int, ...], amount: int) -> tuple[int, ...]:
    """Return a darker variant of an RGB(A) tuple, clamped to 0."""
    rgb = tuple(max(0, c - amount) for c in color[:3])
    return rgb + tuple(color[3:])


# =====================================================================
#  Fonts
# =====================================================================

#: Bundled font path — loaded first for an identical look across machines.
BUNDLED_FONT = os.path.join(_FONT_DIR, "JetBrainsMono-Regular.ttf")


def font_candidates() -> list[str]:
    """Ordered font paths: bundled JetBrains Mono first, then system mono."""
    return [
        BUNDLED_FONT,
        "/System/Library/Fonts/SFMono-Regular.otf",
        "/System/Library/Fonts/Menlo.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    ]


def setup_fonts() -> str | None:
    """Load the bundled JetBrains Mono, falling back to system mono fonts.

    Returns the loaded font path, or None if none found (DPG default font).
    Graceful: never raises. Call after create_context(), before layout.
    """
    with dpg.font_registry():
        for path in font_candidates():
            if os.path.exists(path):
                try:
                    font = dpg.add_font(path, FONT_SIZE)
                    dpg.bind_font(font)
                    return path
                except Exception:
                    continue
    return None


# =====================================================================
#  Theme
# =====================================================================

def setup_theme() -> dict:
    """Build and bind the global HUD theme.

    Returns a dict of per-widget accent theme handles:
        {"green": ..., "red": ..., "run": ..., "panel": ...}
    """
    with dpg.theme() as global_theme:
        with dpg.theme_component(dpg.mvAll):
            # ── Backgrounds ──
            dpg.add_theme_color(dpg.mvThemeCol_WindowBg,   BG_BASE)
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg,    BG_PANEL)
            dpg.add_theme_color(dpg.mvThemeCol_PopupBg,    BG_ELEV)
            dpg.add_theme_color(dpg.mvThemeCol_MenuBarBg,  _darken(BG_BASE, 4))
            dpg.add_theme_color(dpg.mvThemeCol_DockingEmptyBg, BG_BASE)

            # ── Frames (inputs / combos) ──
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg,        BG_ELEV)
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, _brighten(BG_ELEV, 12))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive,  _brighten(BG_ELEV, 20))

            # ── Buttons (neutral = cyan-tinted dark) ──
            dpg.add_theme_color(dpg.mvThemeCol_Button,        (28, 48, 60))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (40, 72, 88))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,  (24, 42, 52))

            # ── Headers / collapsing / selectables ──
            dpg.add_theme_color(dpg.mvThemeCol_Header,        BG_ELEV)
            dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered, _brighten(BG_ELEV, 14))
            dpg.add_theme_color(dpg.mvThemeCol_HeaderActive,  _brighten(BG_ELEV, 22))

            # ── Title bars ──
            dpg.add_theme_color(dpg.mvThemeCol_TitleBg,          _darken(BG_BASE, 2))
            dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive,    BG_ELEV)
            dpg.add_theme_color(dpg.mvThemeCol_TitleBgCollapsed, BG_BASE)

            # ── Borders / separators (HUD accent lines) ──
            dpg.add_theme_color(dpg.mvThemeCol_Border,          BORDER)
            dpg.add_theme_color(dpg.mvThemeCol_Separator,       BORDER_ACCENT)
            dpg.add_theme_color(dpg.mvThemeCol_SeparatorHovered, ACCENT_CYAN)

            # ── Scrollbar ──
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarBg,          BG_BASE)
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrab,        (40, 50, 66))
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrabHovered, (56, 70, 90))
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrabActive,  ACCENT_CYAN)

            # ── Tabs (docked panels) ──
            dpg.add_theme_color(dpg.mvThemeCol_Tab,                BG_PANEL)
            dpg.add_theme_color(dpg.mvThemeCol_TabHovered,         (40, 72, 88))
            dpg.add_theme_color(dpg.mvThemeCol_TabActive,          (28, 60, 74))
            dpg.add_theme_color(dpg.mvThemeCol_TabUnfocused,       BG_PANEL)
            dpg.add_theme_color(dpg.mvThemeCol_TabUnfocusedActive, BG_ELEV)
            dpg.add_theme_color(dpg.mvThemeCol_DockingPreview,     _brighten(BORDER_ACCENT, 20))

            # ── Text ──
            dpg.add_theme_color(dpg.mvThemeCol_Text,         TEXT)
            dpg.add_theme_color(dpg.mvThemeCol_TextDisabled, TEXT_DIM)

            # ── Widgets ──
            dpg.add_theme_color(dpg.mvThemeCol_CheckMark,        ACCENT_CYAN)
            dpg.add_theme_color(dpg.mvThemeCol_SliderGrab,       ACCENT_CYAN)
            dpg.add_theme_color(dpg.mvThemeCol_SliderGrabActive, _brighten(ACCENT_CYAN, 20))

            # ── Style vars (tight HUD density, thin borders) ──
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding,     3)
            dpg.add_theme_style(dpg.mvStyleVar_GrabRounding,      2)
            dpg.add_theme_style(dpg.mvStyleVar_WindowRounding,    4)
            dpg.add_theme_style(dpg.mvStyleVar_ChildRounding,     4)
            dpg.add_theme_style(dpg.mvStyleVar_PopupRounding,     4)
            dpg.add_theme_style(dpg.mvStyleVar_ScrollbarRounding, 3)
            dpg.add_theme_style(dpg.mvStyleVar_TabRounding,       3)
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding,      8, 4)
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing,       7, 5)
            dpg.add_theme_style(dpg.mvStyleVar_ItemInnerSpacing,  6, 4)
            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding,     10, 8)
            dpg.add_theme_style(dpg.mvStyleVar_CellPadding,       6, 3)
            dpg.add_theme_style(dpg.mvStyleVar_WindowBorderSize,  1)
            dpg.add_theme_style(dpg.mvStyleVar_ChildBorderSize,   1)
            dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize,   1)
            dpg.add_theme_style(dpg.mvStyleVar_ScrollbarSize,     11)

    dpg.bind_theme(global_theme)

    return {
        "green": _accent_button_theme(ACCENT_GREEN),
        "red":   _accent_button_theme(ACCENT_RED),
        "run":   _accent_button_theme(ACCENT_CYAN),
    }


def _accent_button_theme(accent: tuple[int, ...]):
    """Build a button theme whose fill derives from an accent color."""
    base = _darken(accent, 120)
    with dpg.theme() as t:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button,        base)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, _brighten(base, 30))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,  _darken(base, 15))
            dpg.add_theme_color(dpg.mvThemeCol_Text,          _brighten(accent, 40))
            dpg.add_theme_color(dpg.mvThemeCol_Border,        accent)
    return t


def make_pane_content_theme():
    """Theme for child-windows used as colored output panes.

    Zero horizontal spacing so adjacent ``add_text`` segments in a
    horizontal group abut correctly (monospace alignment).  Tight
    vertical spacing for dense code/hex output.
    """
    with dpg.theme() as t:
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing,      0, 1)
            dpg.add_theme_style(dpg.mvStyleVar_ItemInnerSpacing, 0, 0)
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding,     4, 2)
    return t
