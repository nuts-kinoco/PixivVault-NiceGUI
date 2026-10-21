"""UI tokenとNiceGUI用の独立スタイル定義。Fletに依存しない。"""

TOKENS = {
    "background": "#0b0b10",
    "surface": "#15161c",
    "raised": "#1e202d",
    "accent": "#0096FA",
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
body { background: #0b0b10; color: #e8e8ef; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
.pv-shell { min-height: 100vh; width: 100%; padding: 24px; gap: 16px; }
.pv-header { display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:12px; width:100%; }
.pv-title { font-family:"Yu Mincho", "YuMincho", serif; font-size:28px; font-weight:700; letter-spacing:.06em; }
.pv-subtitle { color:#8a8a99; font-size:13px; }
.pv-nav { display:flex; flex-wrap:wrap; gap:8px; width:100%; }
.pv-tab { background:#15161c !important; color:#e8e8ef !important; border-radius:8px !important; }
.pv-tab-active { background:#0096FA !important; color:white !important; }
.pv-tab-disabled { opacity:.48; cursor:not-allowed; }
.pv-toolbar { background:#15161c; padding:16px; border-radius:12px; width:100%; display:flex; flex-wrap:wrap; align-items:center; gap:12px; }
.pv-search { flex:1 1 240px; min-width:160px; }
.pv-counter { color:#8a8a99; font-size:13px; }
.pv-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(var(--pv-card-min, 190px),1fr)); gap:12px; width:100%; }
.pv-card { background:#15161c; border:1px solid #2a2b38; border-radius:12px; overflow:hidden; cursor:pointer; min-width:0; }
.pv-card:hover { background:#1e202d; }
.pv-art { aspect-ratio:var(--pv-art-ratio, 1); background:#1e202d; display:flex; align-items:center; justify-content:center; color:#8a8a99; font-size:12px; border-bottom:1px solid #2a2b38; }
.pv-card-meta { padding:12px; min-width:0; }
.pv-card-title { font-size:14px; font-weight:600; color:#e8e8ef; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.pv-card-author { font-size:12px; color:#8a8a99; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.pv-empty { width:100%; padding:48px 16px; color:#8a8a99; text-align:center; }
.pv-viewer { min-width:min(92vw, 620px); max-width:92vw; background:#15161c; color:#e8e8ef; padding:16px; border-radius:12px; }
.pv-view-art { width:100%; max-height:55vh; min-height:210px; aspect-ratio:var(--pv-art-ratio,1); }
.pv-view-title { font-family:"Yu Mincho", "YuMincho", serif; font-size:20px; }
.pv-description { color:#e8e8ef; font-size:13px; white-space:pre-wrap; }
@media(max-width:600px) { .pv-shell { padding:12px; } .pv-title { font-size:24px; } .pv-toolbar { padding:12px; } }
"""

__all__ = ["TOKENS", "CSS"]
