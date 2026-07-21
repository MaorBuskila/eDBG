"""Smoke: the DPG layout must actually build.

Everything else in the suite is deliberately DPG-free, which means a bad
widget call — a handler bound to an item type that rejects it, a duplicate
tag, a popup on a child window — sails through CI and only explodes at
startup. This test constructs the real layout.

Requires an importable dearpygui. It is skipped where the native extension
cannot load (no window server: DPG segfaults at import), so run it on a
desktop before shipping GUI changes:

    python3 -m pytest gui/tests/smoke -q
"""

from __future__ import annotations

import pytest

dpg = pytest.importorskip("dearpygui.dearpygui",
                          reason="dearpygui not importable here")


@pytest.fixture
def ctx():
    dpg.create_context()
    yield
    dpg.destroy_context()


def test_layout_builds_and_wires_copy_menus(ctx):
    """Regression: `dpg.popup()` on a pane raised
    'Item Handler Registry includes inapplicable handler: mvClickedHandler'
    because every output pane is an mvChildWindow."""
    from gui import app, theme

    theme.setup_fonts()
    accents = theme.setup_theme()
    app._theme_green_btn = accents["green"]
    app._theme_red_btn = accents["red"]
    app._theme_run_btn = accents["run"]
    app._pane_theme = theme.make_pane_content_theme()

    dpg.create_viewport(title="smoke", width=800, height=600)
    app._build_layout()
    app._apply_widget_themes()
    app._attach_copy_menus()
    app._setup_keybindings()


def test_every_tag_exists_exactly_once(ctx):
    from gui import app, theme

    theme.setup_fonts()
    accents = theme.setup_theme()
    app._theme_green_btn = accents["green"]
    app._theme_red_btn = accents["red"]
    app._theme_run_btn = accents["run"]
    app._pane_theme = theme.make_pane_content_theme()

    dpg.create_viewport(title="smoke", width=800, height=600)
    app._build_layout()

    missing = [k for k, tag in app.TAG.items() if not dpg.does_item_exist(tag)]
    assert not missing, f"TAGs never created: {missing}"


def test_copy_sources_all_reference_real_tags(ctx):
    """A typo'd pane key would only surface on right-click."""
    from gui import app
    unknown = [k for k in app._COPY_SOURCES if k not in app.TAG]
    assert not unknown, f"_COPY_SOURCES keys not in TAG: {unknown}"


def test_copy_getters_survive_an_empty_session(ctx):
    """Right-clicking before connecting must not raise."""
    from gui import app
    for key, (label, getter) in app._COPY_SOURCES.items():
        text = getter()
        assert isinstance(text, str) and text, f"{key} produced no text"
