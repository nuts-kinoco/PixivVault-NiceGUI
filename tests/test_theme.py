"""UIトークンはNiceGUIやFletの起動なしで検証できる。"""

from pixivvault_nicegui.ui.theme import CSS, TOKENS


def test_theme_palette_and_spacing():
    assert TOKENS["background"] == "#0b0b10"
    assert TOKENS["surface"] == "#15161c"
    assert TOKENS["raised"] == "#1e202d"
    assert TOKENS["accent"] == "#0096FA"
    assert TOKENS["text"] == "#e8e8ef"
    assert TOKENS["muted"] == "#8a8a99"
    assert TOKENS["spacing"] == (4, 8, 16, 24)
    assert TOKENS["gap"] == 12
    assert (TOKENS["radius_card"], TOKENS["radius_control"]) == (12, 8)


def test_theme_css_has_responsive_gallery_and_demo_placeholder():
    for selector in (".pv-grid", ".pv-card", ".pv-art", ".pv-viewer", "@media"):
        assert selector in CSS
    assert "Yu Mincho" in CSS
    assert "url(" not in CSS.lower()
    assert "linear-gradient" not in CSS
    assert "translateY" not in CSS
    assert "border:1px solid #2a2b38" in CSS
