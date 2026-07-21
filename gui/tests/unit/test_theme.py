"""Unit tests for gui.theme — palette + setup entry points.

These tests avoid creating a DPG viewport; they exercise the pure-data palette
and the presence/shape of the setup functions.
"""

from __future__ import annotations

import re

import gui.theme as theme


# ── Palette ──────────────────────────────────────────────────────────

PALETTE_NAMES = [
    "BG_BASE", "BG_PANEL", "BG_ELEV", "BORDER", "BORDER_ACCENT",
    "ACCENT_GREEN", "ACCENT_CYAN", "ACCENT_AMBER", "ACCENT_RED",
    "TEXT", "TEXT_DIM",
]


def test_palette_constants_exist():
    for name in PALETTE_NAMES:
        assert hasattr(theme, name), f"missing palette constant {name}"


def test_palette_values_are_rgb_or_rgba_tuples():
    for name in PALETTE_NAMES:
        val = getattr(theme, name)
        assert isinstance(val, tuple), f"{name} must be a tuple"
        assert len(val) in (3, 4), f"{name} must be RGB or RGBA"
        for ch in val:
            assert isinstance(ch, int) and 0 <= ch <= 255, \
                f"{name} channel out of range: {ch}"


def test_base_is_near_black():
    # HUD look: base background must be very dark
    r, g, b = theme.BG_BASE[:3]
    assert max(r, g, b) <= 30, "BG_BASE should be near-black"


def test_setup_entry_points_defined():
    assert callable(getattr(theme, "setup_theme", None))
    assert callable(getattr(theme, "setup_fonts", None))


# ── Font pipeline ────────────────────────────────────────────────────

def test_bundled_font_is_first_candidate():
    cands = theme.font_candidates()
    assert cands, "candidate list must not be empty"
    assert cands[0] == theme.BUNDLED_FONT
    assert cands[0].endswith("JetBrainsMono-Regular.ttf")


def test_bundled_font_path_under_assets_fonts():
    assert os.path.join("assets", "fonts") in theme.BUNDLED_FONT


def test_setup_fonts_graceful_when_none_exist(monkeypatch):
    # No candidate exists -> must return None, not raise, and not touch DPG.
    monkeypatch.setattr(theme.os.path, "exists", lambda p: False)
    called = {"font": False}
    monkeypatch.setattr(theme.dpg, "font_registry", _NullCtx)
    monkeypatch.setattr(theme.dpg, "add_font",
                        lambda *a, **k: called.__setitem__("font", True))
    monkeypatch.setattr(theme.dpg, "bind_font", lambda *a, **k: None)
    assert theme.setup_fonts() is None
    assert called["font"] is False


def test_setup_fonts_loads_first_existing(monkeypatch):
    cands = theme.font_candidates()
    target = cands[2]  # pretend only the 3rd candidate exists
    monkeypatch.setattr(theme.os.path, "exists", lambda p: p == target)
    monkeypatch.setattr(theme.dpg, "font_registry", _NullCtx)
    monkeypatch.setattr(theme.dpg, "add_font", lambda *a, **k: "FONT")
    bound = {"path": None}
    monkeypatch.setattr(theme.dpg, "bind_font",
                        lambda f, *a, **k: bound.__setitem__("path", f))
    assert theme.setup_fonts() == target


class _NullCtx:
    """Minimal context-manager stand-in for dpg.font_registry()."""
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        return False

import os  # noqa: E402  (used by font tests)


# ── app.py must not carry an inline RGB palette anymore ──────────────

def test_app_has_no_inline_rgb_palette():
    import pathlib
    app_src = (pathlib.Path(theme.__file__).parent / "app.py").read_text()
    # Strip import lines, then assert no bare 3-int tuples like (30, 30, 46)
    body = "\n".join(
        ln for ln in app_src.splitlines()
        if not ln.strip().startswith(("import", "from"))
    )
    inline = re.findall(r"\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*\)", body)
    assert not inline, f"inline RGB tuples remain in app.py: {inline[:5]}"
