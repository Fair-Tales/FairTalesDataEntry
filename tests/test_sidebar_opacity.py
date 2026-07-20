"""Regression lock for #215 — the navigation sidebar background must be OPAQUE.

On mobile the Streamlit sidebar is an overlay drawer that slides over the main
page. The brand tint was applied as a semi-transparent ``rgba(..., 0.18)``, which
let the page content show straight through the sidebar on Android and made the
navigation unreadable. The fix pre-blends the yellow over white so it keeps the
same soft look while staying fully opaque. Pure helper — no Streamlit runtime.
"""

import re

import utilities


def test_opaque_helper_returns_solid_rgb():
    colour = utilities._yellow_opaque_over_white(0.18)
    # Solid rgb(...), never rgba/transparent.
    assert re.fullmatch(r"rgb\(\d{1,3}, \d{1,3}, \d{1,3}\)", colour)
    assert "rgba" not in colour


def test_opaque_helper_matches_manual_blend_over_white():
    # yellow (253,201,25) at 0.18 over white (255,255,255).
    r = round(253 * 0.18 + 255 * 0.82)
    g = round(201 * 0.18 + 255 * 0.82)
    b = round(25 * 0.18 + 255 * 0.82)
    assert utilities._yellow_opaque_over_white(0.18) == f"rgb({r}, {g}, {b})"


def test_sidebar_css_is_opaque():
    css = utilities._BRAND_SIDEBAR_CSS
    assert '[data-testid="stSidebar"]' in css
    # The load-bearing property: a solid rgb background, no transparency.
    assert "rgba" not in css
    assert re.search(r"background-color:\s*rgb\(\d+, \d+, \d+\)", css)
