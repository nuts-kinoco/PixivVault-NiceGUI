"""UI tokenとNiceGUI用の独立スタイル定義。Fletに依存しない。"""

TOKENS = {
    "background": "#0b0b10",
    "surface": "#15161c",
    "raised": "#1e202d",
    "accent": "#0096FA",
    "border": "#2a2b38",
    "text": "#e8e8ef",
    "muted": "#8a8a99",
    "spacing": (4, 8, 16, 24),
    "gap": 12,
    "radius_card": 12,
    "radius_control": 8,
    "heading_font": '"Yu Mincho", "YuMincho", serif',
    "body_font": 'system-ui, -apple-system, "Segoe UI", sans-serif',
}

CSS = """
html, body { width:100%; height:100%; overflow:hidden; }
body { background: #0b0b10; color: #e8e8ef; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
.nicegui-content { height:100dvh; min-height:0; padding:0 !important; overflow:hidden; }
.pv-shell { height:100dvh; min-height:0; width:100%; max-width:100%; padding:24px; gap:16px; box-sizing:border-box; display:flex; flex-direction:column; overflow:hidden; }
.pv-content { width:100%; min-width:0; min-height:0; flex:1 1 0; display:flex; flex-direction:column; overflow:hidden; }
.pv-gallery { flex:1 1 0; min-height:0; min-width:0; overflow:hidden; display:flex; flex-direction:column; }
.pv-header { display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:12px; width:100%; flex-shrink:0; }
.pv-title { font-family:"Yu Mincho", "YuMincho", serif; font-size:28px; font-weight:700; letter-spacing:.06em; }
.pv-subtitle { color:#8a8a99; font-size:13px; }
.pv-nav { display:flex; flex-wrap:wrap; gap:8px; width:100%; flex-shrink:0; }
.pv-tab { background:#15161c !important; border:1px solid #2a2b38 !important; border-radius:8px !important; font-family:"Yu Mincho", "YuMincho", serif; }
.pv-nav .q-btn.pv-tab { color:#e8e8ef !important; }
.pv-nav .q-btn.pv-tab.pv-tab-active { background:#0096FA !important; color:#e8e8ef !important; }
.pv-tab-disabled { opacity:.48; cursor:not-allowed; }
.pv-toolbar { background:#15161c; padding:16px; border-radius:12px; width:100%; display:flex; flex-wrap:wrap; align-items:center; gap:12px; flex-shrink:0; }
.pv-search { flex:1 1 240px; min-width:160px; }
.pv-toolbar .q-btn.pv-size-button { background:transparent !important; color:#e8e8ef !important; border:1px solid transparent !important; }
.pv-toolbar .q-btn.pv-size-selected { background:#0096FA !important; color:#e8e8ef !important; border:1px solid #2a2b38 !important; border-radius:8px; }
.pv-learning .q-btn.pv-judgment { color:#e8e8ef !important; border:1px solid #2a2b38 !important; border-radius:8px; }
.pv-learning .q-btn.pv-judgment-like { background:#0096FA !important; color:#e8e8ef !important; border-color:#0096FA !important; }
.pv-learning .q-btn.pv-judgment-superLike { background:rgba(0,150,250,.25) !important; border-color:#0096FA !important; }
.pv-learning .q-btn.pv-undo { background:transparent !important; color:#8a8a99 !important; }
.pv-counter { color:#8a8a99; font-size:13px; }
.pv-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(min(100%,max(170px,calc((100% - (var(--pv-max-cols, 6) - 1) * 12px) / var(--pv-max-cols, 6)))),1fr)); gap:12px; width:100%; min-width:0; }
.pv-gallery-scroll { flex:1 1 0; overflow-y:auto; overflow-x:hidden; min-height:0; max-height:none; width:100%; overflow-anchor:none; }
.pv-more-cell { min-width:0; display:flex; align-items:center; justify-content:center; border:1px solid #2a2b38; border-radius:12px; }
.pv-gallery > .row, .pv-gallery > .pv-counter, .pv-gallery > .pv-empty { flex-shrink:0; }
.pv-card { background:#15161c; border:1px solid #2a2b38; border-radius:12px; overflow:hidden; cursor:pointer; min-width:0; }
.pv-card:hover { background:#2a2b38; }
.pv-thumb { display:block; width:100%; aspect-ratio:1 / 1; min-width:0; background:#1e202d; border-bottom:1px solid #2a2b38; overflow:hidden; }
.pv-thumb img { width:100%; height:100%; object-fit:contain !important; }
.pv-art { aspect-ratio:var(--pv-art-ratio, 1); background:#1e202d; display:flex; align-items:center; justify-content:center; color:#8a8a99; font-size:12px; border-bottom:1px solid #2a2b38; }
.pv-card-meta { padding:12px; min-width:0; }
.pv-card-title { font-size:14px; font-weight:600; color:#e8e8ef; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.pv-card-author { font-size:12px; color:#8a8a99; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.pv-empty { width:100%; padding:48px 16px; color:#8a8a99; text-align:center; }
.pv-viewer { width:min(92vw, 620px); max-width:92vw; max-height:calc(100dvh - 32px); overflow-y:auto; overflow-x:hidden; min-width:0; background:#15161c; color:#e8e8ef; padding:16px; border:1px solid #2a2b38; border-radius:12px; box-sizing:border-box; }
.pv-view-art { width:100%; max-width:100%; max-height:min(55dvh, 560px); min-height:0; object-fit:contain; }
.pv-view-art img { object-fit:contain !important; max-height:min(55dvh, 560px); }
.pv-viewer .q-btn, .pv-toolbar .q-btn { max-width:100%; }
.pv-viewer .row { flex-wrap:wrap; gap:8px; }
.pv-viewer .row > * { min-width:0; }
.pv-description { overflow-wrap:anywhere; }
.pv-learning { flex:1 1 0; min-height:0; min-width:0; width:100%; display:flex; flex-direction:column; overflow-y:auto; overflow-x:hidden; }
.pv-learning > :not(.pv-view-art) { flex-shrink:0; }
.pv-learning .pv-view-art { flex:1 1 auto; width:100%; min-height:160px; max-width:560px; max-height:45dvh; object-fit:contain; }
.pv-learning .pv-view-art img { width:100%; height:100%; object-fit:contain !important; }
.pv-view-title { font-family:"Yu Mincho", "YuMincho", serif; font-size:20px; }
.pv-description { color:#e8e8ef; font-size:13px; white-space:pre-wrap; }
@media(max-width:1280px) { .pv-shell { padding:16px; gap:12px; } .pv-toolbar { padding:12px; } .pv-view-art { max-height:48dvh; } }
@media(max-width:600px) { .pv-shell { padding:12px; } .pv-title { font-size:24px; } .pv-toolbar { padding:12px; } }
"""

__all__ = ["TOKENS", "CSS"]
