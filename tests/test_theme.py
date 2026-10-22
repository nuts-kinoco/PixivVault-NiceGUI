"""UIトークンはNiceGUIやFletの起動なしで検証できる。"""

from pixivvault_nicegui.ui.theme import CSS, TOKENS


def test_theme_palette_and_spacing():
    assert TOKENS["background"] == "#0b0b10"
    assert TOKENS["surface"] == "#15161c"
    assert TOKENS["raised"] == "#1e202d"
    assert TOKENS["accent"] == "#0096FA"
    assert TOKENS["border"] == "#2a2b38"
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


def test_original_grid_and_viewport_css_contract():
    assert "--pv-max-cols, 6" in CSS
    assert "170px" in CSS and "gap:12px" in CSS
    assert ".pv-thumb {" in CSS and "aspect-ratio:1 / 1" in CSS
    assert "object-fit:contain" in CSS
    assert ".pv-gallery-scroll { flex:1 1 0; overflow-y:auto" in CSS
    assert ".pv-viewer { width:100%; height:100dvh;" in CSS
    assert ".pv-learning" in CSS
    assert "font-family:\"Yu Mincho\"" in CSS


def test_active_quasar_tab_keeps_contrasting_text_color():
    assert ".pv-nav .q-btn.pv-tab { color:#e8e8ef !important; }" in CSS
    assert (".pv-nav .q-btn.pv-tab.pv-tab-active "
            "{ background:#0096FA !important; color:#e8e8ef !important; }") in CSS


def test_viewport_is_bounded_with_one_gallery_scroll_region():
    assert "html, body { width:100%; height:100%; overflow:hidden; }" in CSS
    assert ".nicegui-content { height:100dvh; min-height:0; padding:0 !important; overflow:hidden; }" in CSS
    assert ".pv-shell { height:100dvh; min-height:0;" in CSS
    assert ".pv-content { width:100%; min-width:0; min-height:0; flex:1 1 0;" in CSS
    assert ".pv-gallery { flex:1 1 0; min-height:0;" in CSS
    assert ".pv-gallery-scroll { flex:1 1 0; overflow-y:auto; overflow-x:hidden; min-height:0; max-height:none;" in CSS
    assert ".pv-toolbar {" in CSS and "flex-shrink:0;" in CSS
    assert ".pv-learning { flex:1 1 0; min-height:0; min-width:0; width:100%; display:flex; flex-direction:column; overflow-y:auto;" in CSS
    assert "max-height:calc(100dvh - 220px)" not in CSS
    assert "min-height: 100vh" not in CSS


def test_selected_size_and_judgment_colors_reuse_original_tokens():
    assert ".pv-toolbar .q-btn.pv-size-selected" in CSS
    assert "background:#0096FA !important; color:#e8e8ef !important;" in CSS
    assert ".pv-toolbar .q-btn.pv-size-button" in CSS
    assert ".pv-learning .q-btn.pv-judgment-like" in CSS
    assert ".pv-learning .q-btn.pv-judgment-superLike" in CSS
    assert "background:rgba(0,150,250,.25) !important;" in CSS
    assert ".pv-learning .q-btn.pv-undo" in CSS
    assert ".pv-learning > :not(.pv-view-art) { flex-shrink:0; }" in CSS
    assert "min-height:160px; max-width:560px; max-height:45dvh;" in CSS


def test_legacy_ranking_cards_are_square_with_compact_metadata():
    assert ".pv-grid .pv-card { aspect-ratio:1 / 1;" in CSS
    assert "height:auto; padding:6px; display:flex; flex-direction:column; gap:3px;" in CSS
    assert ".pv-grid .pv-card-rank { flex:0 0 auto; font-size:11px; font-weight:700;" in CSS
    assert ".pv-grid .pv-thumb { flex:1 1 0; min-height:0; height:0; aspect-ratio:auto;" in CSS
    assert ".pv-grid .pv-card-title { font-size:11px;" in CSS
    assert "-webkit-line-clamp:2;" in CSS
    assert ".pv-grid .pv-card-author { font-size:10px;" in CSS


def test_fullscreen_viewer_preserves_image_aspect_and_bottom_controls():
    assert ".pv-viewer { width:100%; height:100dvh; max-width:100vw; max-height:100dvh;" in CSS
    assert CSS.count(".pv-viewer {") == 1
    assert "margin:0; padding:14px; background:#0b0b10;" in CSS
    assert "flex-direction:column; gap:8px;" in CSS
    assert ".pv-viewer-header { flex:0 0 auto;" in CSS
    assert "gap:8px; font-size:13px; font-weight:700;" in CSS
    assert ".pv-viewer-bottom { flex:0 0 48px; height:48px; min-height:48px;" in CSS
    assert "background:#15161c; border-top:1px solid #2a2b38;" in CSS
    assert ".pv-viewer-bottom .pv-view-title" in CSS and "font-size:13.5px;" in CSS
    assert ".pv-viewer-bottom .pv-subtitle { font-size:12.5px; color:#8a8a99;" in CSS
    assert ".pv-viewer-stage { position:relative; flex:1 1 0; min-height:0;" in CSS
    assert ".pv-viewer .pv-view-art { max-width:100%; max-height:none;" in CSS
    # Viewer rules must override the inline natural dimensions of VerifiedImage.
    assert ".pv-viewer .pv-view-art img { width:100% !important; height:100% !important; max-height:none; object-fit:contain !important; }" in CSS
    assert ".pv-viewer-details { position:absolute; bottom:0; left:0; right:0;" in CSS
    assert "max-height:30vh; overflow-y:auto; overflow-x:hidden; padding:12px; background:rgba(21,22,28,.94);" in CSS
    assert ".pv-viewer-stage .pv-viewer-status, .pv-viewer-stage .pv-viewer-retry { position:absolute;" in CSS
    assert ".pv-card { background:#0b0b10;" in CSS
    assert ".pv-thumb { display:block; width:100%; aspect-ratio:1 / 1; min-width:0; background:#15161c;" in CSS
