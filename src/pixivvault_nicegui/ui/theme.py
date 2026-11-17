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
:root { --pv-bg-vault:#0b0b10; --pv-bg-surface:#15161c; --pv-bg-card:#1e202d; --pv-border-vault:#2a2b38; --pv-pixiv-blue:#0096FA; --pv-pixiv-blue-hover:#0080d6; --pv-pixiv-blue-glow:rgba(0,150,250,.25); --pv-glass-bg:rgba(21,22,28,.94); --pv-text-main:#e8e8ef; --pv-text-muted:#8a8a99; --pv-text-elsewhere:#b9b9c4;
  --pv-page:#111418; --pv-on-surface:#e1e2e8; --pv-on-surface-variant:#c3c6cf; --pv-primary:#9ec7f9; --pv-primary-rgb:158,199,249; --pv-container:#32353a; --pv-container-pill:#191c20;
  --pv-auth-red:#ef5350; --pv-auth-green:#66bb6a; --pv-auth-yellow:#ffee58; --pv-auth-blue:#42a5f5; --pv-auth-primary:#0096FA;
  --pv-bg:var(--pv-bg-vault); --pv-surface:var(--pv-bg-surface); --pv-raised:var(--pv-bg-card); --pv-border:var(--pv-border-vault); --pv-text:var(--pv-text-main); --pv-muted:var(--pv-text-muted); --pv-overlay:var(--pv-glass-bg); }
/* ライトは元(暗色カードが残る)と違い、本人の裁定で明るいカードにする。 */
body.body--light { --pv-page:#f8f9ff; --pv-bg-vault:#f3f5fb; --pv-bg-surface:#ffffff; --pv-bg-card:#eef1f8; --pv-border-vault:#c7cdd7; --pv-glass-bg:rgba(255,255,255,.96); --pv-text-main:#191c20; --pv-text-muted:#4b5058; --pv-text-elsewhere:#3a3f47;
  --pv-on-surface:#191c20; --pv-on-surface-variant:#43474e; --pv-primary:#2a5886; --pv-primary-rgb:42,88,134; --pv-container:#e1e2e8; --pv-container-pill:#f2f3fa;
  --pv-auth-red:#c62828; --pv-auth-green:#2e7d32; --pv-auth-yellow:#7a5c00; --pv-auth-blue:#1565c0; --pv-auth-primary:#00639b;
  /* var() は宣言した要素で解決されるので、別名は光側でも宣言し直す（:root の別名は暗色のまま残る）。 */
  --pv-bg:var(--pv-bg-vault); --pv-surface:var(--pv-bg-surface); --pv-raised:var(--pv-bg-card); --pv-border:var(--pv-border-vault); --pv-text:var(--pv-text-main); --pv-muted:var(--pv-text-muted); --pv-overlay:var(--pv-glass-bg); }

html, body { width:100%; height:100%; overflow:hidden; }
body { background: var(--pv-page); color: var(--pv-on-surface); font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
.nicegui-content { height:100dvh; min-height:0; padding:0 !important; overflow:hidden; }
.pv-shell { height:100dvh; min-height:0; width:100%; max-width:100%; padding:10px; gap:10px; box-sizing:border-box; display:flex; flex-direction:column; overflow:hidden; }
.pv-content { position:relative; width:100%; min-width:0; min-height:0; flex:1 1 0; display:flex; flex-direction:column; overflow:hidden; }
.pv-metadata-view { flex:1 1 0; min-height:0; min-width:0; max-width:100%; overflow-y:auto; overflow-x:hidden; padding-right:8px; box-sizing:border-box; }
.pv-gallery { flex:1 1 0; min-height:0; min-width:0; overflow:hidden; display:flex; flex-direction:column; }
.pv-header { display:flex; flex-wrap:nowrap; align-items:flex-start; justify-content:space-between; gap:12px; width:100%; flex-shrink:0; }
.pv-header-main { display:flex; flex-direction:column; align-items:flex-start; gap:10px; flex:1 1 0; min-width:0; }
.pv-header-actions { display:flex; flex-wrap:nowrap; align-items:center; gap:5px; flex:0 0 auto; align-self:center; }
.pv-title { font-family:inherit; font-size:32px; font-weight:700; line-height:46px; }
.pv-subtitle { color:var(--pv-muted); font-size:13px; }
.pv-dialog-title { font-family:"Yu Mincho", "YuMincho", serif; color:var(--pv-text-main); }
.pv-auth { font-size:13px; font-weight:500; line-height:1.2; }
.pv-auth[data-tone=red] { color:var(--pv-auth-red); } .pv-auth[data-tone=green] { color:var(--pv-auth-green); }
.pv-auth[data-tone=yellow] { color:var(--pv-auth-yellow); } .pv-auth[data-tone=blue] { color:var(--pv-auth-blue); }
.pv-auth[data-tone=primary] { color:var(--pv-auth-primary); }
.pv-conn-row { display:flex; flex-wrap:wrap; align-items:center; gap:6px; min-height:32px; margin-top:8px; font-size:12px; color:var(--pv-on-surface-variant); }
.pv-conn-row .q-toggle__label, .pv-conn-row .q-toggle { font-size:12px; color:var(--pv-on-surface-variant); }
.pv-conn-dot { width:10px; height:10px; border-radius:50%; background:var(--pv-on-surface-variant); flex:0 0 10px; }
.pv-conn-dot[data-state=waiting] { background:var(--pv-auth-green); } .pv-conn-dot[data-state=off] { background:var(--pv-auth-red); }
.q-btn.pv-hbtn, .pv-hbtn { width:40px; height:40px; min-width:40px; min-height:40px; padding:0; border-radius:50%; color:var(--pv-on-surface-variant) !important; display:inline-flex; align-items:center; justify-content:center; text-decoration:none; }
.pv-hbtn .q-icon { font-size:24px; }
.q-btn.pv-hbtn { color:var(--pv-on-surface-variant) !important; }
.pv-hbtn.pv-tab-active, .q-btn.pv-hbtn.pv-tab-active { background:rgba(var(--pv-primary-rgb), .12) !important; color:var(--pv-primary) !important; }
.pv-cookie-banner { width:100%; flex-shrink:0; box-sizing:border-box; background:var(--pv-container); border:1px solid #42a5f5; border-radius:12px; padding:10px 14px; gap:12px; }
.pv-cookie-banner .pv-banner-title { font-weight:700; font-size:14px; color:var(--pv-on-surface); }
.pv-cookie-banner .pv-banner-body { font-size:14px; line-height:1.5; white-space:pre-line; color:var(--pv-on-surface-variant); }
.pv-pill { display:inline-flex; align-items:center; gap:8px; padding:8px 18px; border-radius:20px; background:var(--pv-container-pill); color:var(--pv-primary); font-size:14px; text-decoration:none; white-space:nowrap; }
.pv-nav { display:flex; flex-wrap:nowrap; gap:8px; width:100%; flex-shrink:0; overflow-x:auto; overflow-y:hidden; scrollbar-width:thin; }
.pv-tab { font-family:inherit; font-size:14px; font-weight:500; text-transform:none; width:162px; min-width:162px; flex:0 0 162px; height:32px; min-height:32px; padding:0 10px; border-radius:20px !important; box-sizing:border-box; }
.pv-tab .q-btn__content { flex-wrap:nowrap; white-space:nowrap; }
.pv-nav .q-btn.pv-tab { background:transparent !important; border:0 !important; color:var(--pv-on-surface) !important; }
.pv-nav .q-btn.pv-tab.pv-tab-active { background:rgba(var(--pv-primary-rgb), .12) !important; color:var(--pv-primary) !important; }
.pv-tab-disabled { opacity:.48; cursor:not-allowed; }
.pv-toolbar { background:var(--pv-surface); padding:16px; border-radius:12px; width:100%; display:flex; flex-wrap:wrap; align-items:center; gap:12px; flex-shrink:0; }
.pv-search { flex:1 1 240px; min-width:160px; }
.pv-toolbar .q-btn.pv-size-button { background:transparent !important; color:var(--pv-text) !important; border:1px solid transparent !important; }
.pv-toolbar .q-btn.pv-size-selected { background:#0096FA !important; color:#ffffff !important; border:1px solid var(--pv-border) !important; border-radius:8px; }
.pv-learning .q-btn.pv-judgment { color:var(--pv-text) !important; border:1px solid var(--pv-border) !important; border-radius:8px; }
.pv-learning .q-btn.pv-judgment-like { background:#0096FA !important; color:#ffffff !important; border-color:#0096FA !important; }
.pv-learning .q-btn.pv-judgment-superLike { background:rgba(0,150,250,.25) !important; border-color:#0096FA !important; }
.pv-learning .q-btn.pv-undo { background:transparent !important; color:var(--pv-muted) !important; }
.pv-counter { color:var(--pv-muted); font-size:13px; }
.pv-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(min(100%,max(170px,calc((100% - (var(--pv-max-cols, 6) - 1) * 12px) / var(--pv-max-cols, 6)))),1fr)); gap:12px; width:100%; min-width:0; }
.pv-gallery-scroll { flex:1 1 0; overflow-y:auto; overflow-x:hidden; min-height:0; max-height:none; width:100%; overflow-anchor:none; }
.pv-more-cell { min-width:0; display:flex; align-items:center; justify-content:center; border:1px solid var(--pv-border); border-radius:12px; }
.pv-gallery > .row, .pv-gallery > .pv-counter, .pv-gallery > .pv-empty { flex-shrink:0; }
.pv-card { background:var(--pv-bg); border:1px solid var(--pv-border); border-radius:12px; overflow:hidden; cursor:pointer; min-width:0; }
.pv-grid .pv-card { aspect-ratio:1 / 1; height:auto; padding:6px; display:flex; flex-direction:column; gap:3px; box-sizing:border-box; }
.pv-grid .pv-card-rank { flex:0 0 auto; font-size:11px; font-weight:700; line-height:1.2; color:var(--pv-text); }
.pv-grid .pv-thumb { flex:1 1 0; min-height:0; height:0; aspect-ratio:auto; border-bottom:0; border-radius:12px; }
.pv-grid .pv-card-meta { flex:0 0 auto; min-width:0; padding:0; display:flex; flex-direction:column; gap:3px; }
.pv-grid .pv-card-title { font-size:11px; font-weight:400; line-height:1.2; white-space:normal; display:-webkit-box; -webkit-box-orient:vertical; -webkit-line-clamp:2; overflow:hidden; }
.pv-grid .pv-card-author { font-size:10px; line-height:1.2; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.pv-card:hover { background:var(--pv-border); }
.pv-thumb { display:block; width:100%; aspect-ratio:1 / 1; min-width:0; background:var(--pv-surface); border-bottom:1px solid var(--pv-border); overflow:hidden; }
.pv-thumb img { width:100%; height:100%; object-fit:contain !important; }
.pv-art { aspect-ratio:var(--pv-art-ratio, 1); background:var(--pv-raised); display:flex; align-items:center; justify-content:center; color:var(--pv-muted); font-size:12px; border-bottom:1px solid var(--pv-border); }
.pv-card-meta { padding:12px; min-width:0; }
.pv-card-title { font-size:14px; font-weight:600; color:var(--pv-text); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.pv-card-author { font-size:12px; color:var(--pv-muted); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.pv-empty { width:100%; padding:48px 16px; color:var(--pv-muted); text-align:center; }
.pv-viewer { width:100%; height:100%; max-width:100%; max-height:100%; min-height:0; min-width:0; margin:0; padding:14px; background:var(--pv-bg); color:var(--pv-text); border:0; border-radius:0; box-sizing:border-box; overflow:hidden; display:flex; flex-direction:column; gap:8px; }
.pv-viewer-overlay { position:absolute; inset:0; z-index:10; }
.pv-viewer-overlay.hidden { display:none !important; }
.pv-viewer .q-btn { color:var(--pv-text) !important; }
.pv-viewer-header-action { border:1px solid var(--pv-border); border-radius:8px; height:32px; min-height:32px; background:var(--pv-surface) !important; font-size:12px; font-weight:400; }
.pv-viewer-header-action .q-icon { font-size:14px; }
.pv-viewer-header .pv-counter { color:var(--pv-text); font-size:12px; }
.pv-viewer-stage .pv-viewer-retry { margin-top:36px; }
.pv-viewer-header { flex:0 0 auto; min-width:0; display:flex; align-items:center; justify-content:space-between; gap:8px; font-size:13px; font-weight:700; }
.pv-viewer-bottom { flex:0 0 48px; height:48px; min-height:48px; min-width:0; display:flex; align-items:center; justify-content:space-between; gap:8px; padding:0 8px 0 12px; background:var(--pv-surface); border-top:1px solid var(--pv-border); box-sizing:border-box; }
.pv-viewer-bottom .pv-view-title { font-family:system-ui, -apple-system, "Segoe UI", sans-serif; font-size:13.5px; font-weight:400; }
.pv-viewer-bottom .pv-subtitle { font-size:12.5px; color:var(--pv-muted); }
.pv-viewer-bottom .q-btn { color:var(--pv-muted) !important; font-size:12px; }
.pv-viewer-bottom .q-btn.pv-subtitle { font-size:12.5px; }
.pv-viewer-bottom .pv-view-title, .pv-viewer-bottom .pv-subtitle { min-width:0; max-width:100%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.pv-viewer-stage { position:relative; flex:1 1 0; min-height:0; min-width:0; width:100%; display:flex; align-items:center; justify-content:center; gap:12px; overflow:hidden; }
.pv-viewer-viewport { position:relative; flex:1 1 0; min-width:0; min-height:0; height:100%; overflow:hidden; touch-action:none; }
.pv-viewer-viewport[data-zoomed="true"] { cursor:grab; }
.pv-viewer-viewport[data-dragging="true"] { cursor:grabbing; }
.pv-viewer-zoom-plane { display:flex; align-items:center; justify-content:center; width:100%; height:100%; min-width:0; min-height:0; transform-origin:center center; transform:translate3d(0,0,0) scale(1); will-change:transform; }
.pv-viewer-zoom-plane > .pv-view-art { flex:1 1 0; min-width:0; min-height:0; height:100%; max-height:none; }
.pv-viewer-zoom-tools { position:absolute; z-index:4; top:8px; right:54px; display:flex; align-items:center; flex-wrap:nowrap !important; gap:3px !important; padding:3px 5px; border:1px solid var(--pv-border); border-radius:8px; background:var(--pv-overlay); }
.pv-viewer-zoom-tools .q-btn { flex:0 0 auto; min-width:30px; min-height:28px; font-size:12px; }
.pv-viewer-zoom-value { width:46px; text-align:center; font-size:12px; font-variant-numeric:tabular-nums; }
.pv-viewer-zoom-help { position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0; }
.pv-viewer .pv-view-art { max-width:100%; max-height:none; min-height:0; object-fit:contain; }
.pv-viewer .pv-view-art img { width:100% !important; height:100% !important; max-height:none; object-fit:contain !important; }
.pv-viewer-details { position:absolute; bottom:0; left:0; right:0; z-index:2; max-height:30vh; overflow-y:auto; overflow-x:hidden; padding:12px; background:var(--pv-overlay); border-top:1px solid var(--pv-border); box-sizing:border-box; }
.pv-viewer-details.pv-viewer-details-reserved { height:min(30vh, 100%); max-height:100%; }
.pv-detail-skeleton-lines { width:100%; gap:8px; flex:0 0 auto; }
.pv-detail-skeleton-bar { width:100%; height:10px; flex:0 0 10px; border-radius:4px; background:var(--pv-border); }
.pv-detail-skeleton-tags { gap:8px; flex:0 0 auto; }
.pv-detail-skeleton-chip { width:64px; height:24px; flex:0 0 64px; border:1px solid var(--pv-border); border-radius:8px; }
.pv-viewer-stage .pv-description { overflow-wrap:anywhere; }
.pv-viewer-stage .pv-viewer-status, .pv-viewer-stage .pv-viewer-retry { position:absolute; z-index:3; left:50%; top:50%; transform:translate(-50%,-50%); }
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
.pv-view-title { font-size:20px; }
.pv-description { color:var(--pv-text); font-size:13px; white-space:pre-wrap; }
.pv-card-size { font-size:9px; line-height:1.2; min-width:32px; padding:3px 5px !important; }
.pv-card-size .q-icon { font-size:16px; }
.pv-library { flex:1 1 0; min-width:0; min-height:0; overflow:hidden; display:flex; flex-direction:column; gap:6px; }
.pv-library-toolbar { display:flex; align-items:center; flex-wrap:wrap; gap:10px; width:100%; flex:0 0 auto; }
.pv-library-heading { font-size:15px; font-weight:700; }
.pv-library-subviews { gap:2px; flex-wrap:nowrap; }
.pv-library .q-btn { color:var(--pv-text) !important; font-size:11px; }
.pv-library .pv-library-subview-selected, .pv-library .pv-size-selected { background:#0096FA !important; border-radius:8px; }
.pv-library .pv-card-size { font-size:9px; }
.pv-library-search { flex:1 1 180px; min-width:160px; max-width:300px; }
.pv-library-search .q-field__control, .pv-library-search .q-field__marginal { min-height:34px; height:34px; }
.pv-library-search .q-field__native { padding-top:3px; padding-bottom:3px; }
.pv-library-scope { font-size:11px; }
.pv-library-sizes { margin-left:auto; gap:2px; flex-wrap:nowrap; }
.pv-library-panel { flex:1 1 0; min-height:0; min-width:0; overflow:hidden; gap:6px; }
.pv-library-summary { align-items:center; gap:4px; flex:0 0 auto; }
.pv-library-detail-header { align-items:center; flex-wrap:nowrap; gap:6px; flex:0 0 auto; }
.pv-library-detail-title { font-size:12.5px; line-height:1.3; flex:1 1 0; min-width:0; display:-webkit-box; -webkit-box-orient:vertical; -webkit-line-clamp:2; overflow:hidden; }
.pv-library-unknown { color:#ffb300; font-size:10.5px; flex-shrink:0; }
.pv-library-error { align-items:center; color:#ef9a9a; flex-shrink:0; }
.pv-library-card-title { flex:0 0 auto; font-size:11px; line-height:1.2; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.pv-library-card-info { flex:0 0 auto; font-size:9.5px; line-height:1.2; color:var(--pv-muted); gap:6px; flex-wrap:nowrap; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.pv-library-thumb-wrap { position:relative; flex:1 1 0; min-height:0; width:100%; }
.pv-library-author-open { width:100%; height:100%; cursor:pointer; border-radius:12px; overflow:hidden; }
.pv-library-author-open .pv-thumb { height:100%; }
.pv-library .pv-thumb img { object-fit:cover !important; }
.pv-library .pv-library-profile { position:absolute; right:4px; top:4px; width:22px; height:22px; min-height:22px; background:var(--pv-raised); }
.pv-library-profile .q-icon { font-size:13px; }
.pv-library-more { aspect-ratio:1; padding:6px; }
.pv-library-more .q-icon { font-size:22px; }
.pv-library-author-open:focus-visible, .pv-library-page-card:focus-visible { outline:2px solid #0096FA; outline-offset:-2px; }
.pv-bookmarks-panel { display:flex; flex-direction:column; flex:1 1 0; min-height:0; overflow:hidden; }
.pv-bookmark-image-wrap { position:relative; flex:1 1 0; min-height:0; width:100%; }
.pv-bookmark-image-wrap .pv-library-author-open { display:block; }
.pv-bookmark-image-wrap .pv-thumb { height:100%; }
.pv-bookmark-badge { position:absolute; top:6px; right:6px; z-index:1; padding:2px 5px; border-radius:6px; background:var(--pv-raised); color:var(--pv-text); font-size:10px; }
.pv-bookmark-selection { gap:2px; min-width:0; flex:0 0 auto; }
.pv-bookmark-selection .q-checkbox { flex:0 0 auto; }
.pv-bookmark-selection .pv-library-card-title { flex:1 1 0; min-width:0; }
.pv-bookmark-image-wrap .pv-library-author-open:focus-visible { outline:2px solid #0096FA; }
.pv-library-body { flex:1 1 0; min-height:0; min-width:0; overflow:hidden; gap:12px; align-items:stretch; }
.pv-library-filter-content { flex:1 1 0; min-width:0; min-height:0; overflow:hidden; gap:0; }
.pv-library-filter-panel { flex:0 0 280px; width:280px; min-width:280px; min-height:0; overflow:auto; padding:12px; gap:6px; border:1px solid var(--pv-border); border-radius:12px; background:var(--pv-surface); box-sizing:border-box; }
.pv-library-filter-seat { flex:0 0 88px; min-height:88px; width:100%; gap:4px; padding:8px 4px; box-sizing:border-box; }
.pv-library-filter-state { font-size:10px; line-height:18px; border:1px solid var(--pv-border); border-radius:4px; padding:0 6px; color:var(--pv-muted); }
.pv-library-filter-reason { font-size:10.5px; line-height:1.4; color:var(--pv-muted); overflow-wrap:anywhere; }
.pv-library-tag-controls { flex:0 0 auto; gap:6px; padding-top:6px; }
.pv-library-tag-controls .q-field { width:100%; }
.pv-library-filter-now-host { flex:0 0 auto; gap:0; max-height:84px; overflow-y:auto; }
.pv-library-filter-now { gap:6px; align-items:center; }
.pv-library-tag-chips { gap:6px; }
.pv-library-tag-chip { align-items:center; flex-wrap:nowrap; max-width:100%; min-width:0; gap:4px; border:1px solid var(--pv-border); border-radius:999px; padding:2px 6px; background:var(--pv-raised); }
.pv-library-tag-chip > div { font-size:11px; overflow-wrap:anywhere; }
.pv-library-tag-chip.pv-tag-excluded { background:var(--pv-surface); color:var(--pv-muted); }
.pv-library-tag-chip.pv-tag-excluded > div { text-decoration:line-through; }
.pv-library-tag-chip .q-btn { min-width:22px; min-height:22px; padding:0; }
.pv-jobs-view { flex:1 1 0; min-height:0; min-width:0; display:flex; flex-direction:column; gap:10px; overflow-y:auto; overflow-x:hidden; }
.pv-jobs-view > .pv-toolbar, .pv-jobs-view > .pv-job-chips, .pv-jobs-view > .pv-subtitle { flex:0 0 auto; }
.pv-jobs-view .q-btn { color:var(--pv-text) !important; }
.pv-jobs-scroll { flex:1 1 0; min-height:0; width:100%; overflow-y:auto; overflow-x:hidden; }
.pv-download-body { flex:0 0 auto; min-height:180px; flex-wrap:nowrap; overflow:visible; align-items:stretch; gap:16px; }
.pv-download-candidates { flex:2 1 0; min-width:0; gap:0; }
.pv-download-targets { flex:1 1 0; min-width:220px; max-width:380px; min-height:0; max-height:260px; overflow-y:auto; padding:16px; border:1px solid var(--pv-border); border-radius:12px; background:var(--pv-surface); }
.pv-author-diff-list { flex:0 1 auto; max-height:64px; min-height:0; overflow-y:auto; }
.pv-author-remote-absent-list { flex:0 1 auto; max-height:48px; min-height:0; overflow-y:auto; }
.pv-download-item { padding:10px 12px; min-height:66px; flex:0 0 auto; border-bottom:1px solid var(--pv-border); flex-wrap:nowrap; }
.pv-job-row { min-height:56px; padding:8px 12px; gap:12px; border-left:3px solid var(--pv-muted); border-bottom:1px solid var(--pv-border); flex:0 0 auto; flex-wrap:nowrap; background:var(--pv-surface); }
.pv-job-row[data-state=running] { border-left-color:#0096FA; }
.pv-job-row[data-state=error] { border-left-color:#ef9a9a; }
.pv-job-row[data-state=done] { border-left-color:#81c784; }
.pv-job-row[data-state=paused] { border-left-color:#ffb300; }
.pv-job-description { flex:1 1 0; min-width:0; }
.pv-job-title { max-width:100%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:13px; }
.pv-job-description .pv-subtitle { font-size:10px; }
.pv-job-id-seat { width:192px; max-width:192px; flex:0 0 192px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.pv-job-state { min-width:66px; font-size:12px; }
.pv-job-actions { gap:4px; flex-wrap:nowrap; }
.pv-job-actions .q-btn { min-width:40px; font-size:11px; }
.pv-queue-view .pv-job-row { height:36px; min-height:36px; padding:3px 14px; gap:8px; border-left:0; flex:0 0 36px; box-sizing:border-box; overflow:hidden; }
.pv-queue-view .pv-job-row .q-checkbox { width:24px; height:24px; min-height:24px; padding:0; flex:0 0 24px; }
.pv-queue-view .pv-job-row .q-checkbox__inner { width:24px; min-width:24px; height:24px; }
.pv-queue-view .pv-job-attention { width:3px; height:30px; flex:0 0 3px; background:transparent; }
.pv-queue-view .pv-job-row[data-state=paused] .pv-job-attention,
.pv-queue-view .pv-job-row[data-state=stopping] .pv-job-attention,
.pv-queue-view .pv-job-row[data-state=error] .pv-job-attention { background:var(--pv-muted); }
.pv-queue-view .pv-job-status-wrap { width:30px; height:30px; flex:0 0 30px; border-radius:50%; background:var(--pv-raised); display:flex; align-items:center; justify-content:center; }
.pv-queue-view .pv-job-status-icon { font-size:15px; color:var(--pv-muted); }
.pv-queue-view .pv-job-row[data-state=running] .pv-job-status-icon { color:#0096FA; }
.pv-queue-view .pv-job-description { flex:1 1 0; min-width:0; height:30px; gap:0; }
.pv-queue-view .pv-job-head { height:26px; min-height:26px; min-width:0; gap:8px; flex-wrap:nowrap; overflow:hidden; }
.pv-queue-view .pv-job-head .pv-job-title { flex:1 1 100px; min-width:64px; max-width:45%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:12.5px; font-weight:500; }
.pv-queue-view .pv-job-id-seat { width:192px; max-width:192px; min-width:64px; flex:0 1 192px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:11px !important; }
.pv-queue-view .pv-job-state { flex:0 0 66px; min-width:66px; margin-right:auto; color:var(--pv-muted); font-size:11px; }
.pv-queue-view .pv-job-progress-count { flex:0 0 96px; width:96px; min-width:96px; text-align:right; font-size:11px; }
.pv-queue-view .pv-job-progress { width:100%; height:4px; flex:0 0 4px; overflow:hidden; }
.pv-queue-view .pv-job-progress .q-linear-progress__track { background:var(--pv-raised) !important; opacity:1 !important; }
.pv-queue-view .pv-job-row[data-state=running] .pv-job-progress .q-linear-progress__model,
.pv-queue-view .pv-job-row[data-state=error] .pv-job-progress .q-linear-progress__model { background:#0096FA !important; }
.pv-queue-view .pv-job-row[data-state=queued] .pv-job-progress .q-linear-progress__model,
.pv-queue-view .pv-job-row[data-state=paused] .pv-job-progress .q-linear-progress__model,
.pv-queue-view .pv-job-row[data-state=stopping] .pv-job-progress .q-linear-progress__model,
.pv-queue-view .pv-job-row[data-state=stopped] .pv-job-progress .q-linear-progress__model,
.pv-queue-view .pv-job-row[data-state=done] .pv-job-progress .q-linear-progress__model { background:var(--pv-muted) !important; }
.pv-queue-view .pv-job-progress[data-error=true] .q-linear-progress__track { opacity:1 !important; border-radius:2px !important; background:repeating-linear-gradient(90deg,var(--pv-border) 0 10%,transparent 10% 15%) !important; }
.pv-queue-view .pv-job-actions { gap:2px; flex:0 0 auto; flex-wrap:nowrap; }
.pv-queue-view .pv-job-actions .q-btn { min-width:40px; min-height:24px; padding:0 2px; font-size:10px; }
.pv-queue-view .pv-job-result { display:none !important; }
.pv-queue-view .pv-job-row[data-state=error] { height:auto; min-height:54px; flex:0 0 auto; overflow:visible; }
.pv-queue-view .pv-job-row[data-state=error] .pv-job-description { height:auto; }
.pv-queue-view .pv-job-row[data-state=error] .pv-job-result { display:block !important; white-space:normal; }
.pv-queue-view .pv-job-row[data-state=stopped] .pv-job-result, .pv-queue-view .pv-job-row[data-state=done] .pv-job-result { display:block !important; white-space:normal; }
.pv-job-chips { gap:4px; }
.pv-job-chips .q-btn { font-size:11px; border:1px solid var(--pv-border); border-radius:8px; }
.pv-job-chips [aria-pressed=true] { background:var(--pv-raised) !important; border-color:#0096FA; }
@media(max-width:1280px) { .pv-shell { padding:10px; gap:10px; } .pv-toolbar { padding:12px; } .pv-view-art { max-height:48dvh; } }
@media(max-width:1280px) { .pv-viewer .pv-view-art, .pv-viewer .pv-view-art img { max-height:none; } }
@media(max-width:600px) { .pv-shell { padding:10px; } .pv-title { font-size:24px; } .pv-toolbar { padding:12px; } }
"""

__all__ = ["TOKENS", "CSS"]

# M4 novel controls inherit the existing surfaces, typography and action color.
CSS += """
.pv-novel-preview { padding:16px; border:1px solid var(--pv-border); border-radius:12px; background:var(--pv-surface); min-width:0; }
.pv-novel-format { min-width:220px; max-width:100%; }
.pv-ugoira-format { flex:0 1 10rem; min-width:min(10rem, 100%); max-width:100%; }
/* Keep catalog rows and preview in document flow in the scrolling download
   view. A shrinking catalog lets following toolbars cover its live buttons. */
.pv-novel-download > * { flex-shrink:0; }
.pv-novel-catalog > .q-btn { flex-shrink:0; }
.pv-novel-text { white-space:pre-wrap; overflow-wrap:anywhere; max-width:100%; font-size:14px; line-height:1.8; }
.pv-novel-reader { width:min(900px, 92vw); max-width:92vw !important; height:min(82dvh, 900px); background:var(--pv-surface); color:var(--pv-text); padding:20px; display:flex; flex-direction:column; overflow:hidden; }
.pv-novel-reader-text { flex:1 1 0; min-height:0; width:100%; overflow-y:auto; padding-right:8px; }
.pv-novel-reader .q-btn { color:var(--pv-text) !important; }
"""
CSS += """
.pv-novel-reader-images { max-height:25vh; overflow:auto; flex:0 0 auto; }
"""
