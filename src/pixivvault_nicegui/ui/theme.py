"""UI tokenとNiceGUI用の独立スタイル定義。Fletに依存しない。"""

UI_FONT = '"Noto Sans JP", system-ui, -apple-system, "Segoe UI", sans-serif'

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
    "body_font": UI_FONT,
}

CSS = """
:root { --pv-ui-font:__PV_UI_FONT__; --pv-bg-vault:#0b0b10; --pv-bg-surface:#15161c; --pv-bg-card:#1e202d; --pv-border-vault:#2a2b38; --pv-pixiv-blue:#0096FA; --pv-pixiv-blue-hover:#0080d6; --pv-pixiv-blue-glow:rgba(0,150,250,.25); --pv-glass-bg:rgba(21,22,28,.94); --pv-text-main:#e8e8ef; --pv-text-muted:#8a8a99; --pv-text-elsewhere:#b9b9c4;
  --pv-page:#111418; --pv-on-surface:#e1e2e8; --pv-on-surface-variant:#c3c6cf; --pv-primary:#9ec7f9; --pv-primary-rgb:158,199,249; --pv-container:#32353a; --pv-container-pill:#191c20;
  --pv-download-accent-text:#0096FA; --pv-download-danger-text:#f16c6c;
  --pv-auth-red:#ef5350; --pv-auth-green:#66bb6a; --pv-auth-yellow:#ffee58; --pv-auth-blue:#42a5f5; --pv-auth-primary:#0096FA;
  --pv-tab-selected:#2d3844; --pv-fill:#0096FA; --pv-on-fill:#ffffff; --pv-on-fill-lib:#e8e8ef;
  --pv-bg:var(--pv-bg-vault); --pv-surface:var(--pv-bg-surface); --pv-raised:var(--pv-bg-card); --pv-border:var(--pv-border-vault); --pv-text:var(--pv-text-main); --pv-muted:var(--pv-text-muted); --pv-overlay:var(--pv-glass-bg); }
/* ライトは元(暗色カードが残る)と違い、本人の裁定で明るいカードにする。 */
body.body--light { --pv-page:#f8f9ff; --pv-bg-vault:#f3f5fb; --pv-bg-surface:#ffffff; --pv-bg-card:#eef1f8; --pv-border-vault:#c7cdd7; --pv-glass-bg:rgba(255,255,255,.96); --pv-text-main:#191c20; --pv-text-muted:#4b5058; --pv-text-elsewhere:#3a3f47;
  --pv-on-surface:#191c20; --pv-on-surface-variant:#43474e; --pv-primary:#2a5886; --pv-primary-rgb:42,88,134; --pv-container:#e1e2e8; --pv-container-pill:#f2f3fa;
  --pv-download-accent-text:#0065a8; --pv-download-danger-text:#ad2626;
  --pv-auth-red:#c62828; --pv-auth-green:#2e7d32; --pv-auth-yellow:#7a5c00; --pv-auth-blue:#1565c0; --pv-auth-primary:#00639b;
  /* ライトは主色の塗り・文字を 4.5:1 以上にする。Quasar の主色（ui.colors が body に直接置く）も !important で差し替える。 */
  --pv-tab-selected:#d3dde9; --pv-fill:#00639b; --pv-on-fill:#ffffff; --pv-on-fill-lib:#ffffff; --q-primary:var(--pv-fill) !important;
  /* var() は宣言した要素で解決されるので、別名は光側でも宣言し直す（:root の別名は暗色のまま残る）。 */
  --pv-bg:var(--pv-bg-vault); --pv-surface:var(--pv-bg-surface); --pv-raised:var(--pv-bg-card); --pv-border:var(--pv-border-vault); --pv-text:var(--pv-text-main); --pv-muted:var(--pv-text-muted); --pv-overlay:var(--pv-glass-bg); }

html, body { width:100%; height:100%; overflow:hidden; }
body { background: var(--pv-page); color: var(--pv-on-surface); font-family:var(--pv-ui-font); }
body {
  --pv-focus-ring-outline:2px solid var(--pv-primary);
  --pv-focus-ring-shadow:0 0 0 2px var(--pv-page), 0 0 0 4px var(--pv-primary);
}
.nicegui-content { height:100dvh; min-height:0; padding:0 !important; overflow:hidden; }
.pv-shell { height:100dvh; min-height:0; width:100%; max-width:100%; padding:10px; gap:10px; box-sizing:border-box; display:flex; flex-direction:column; overflow:hidden auto; scroll-padding-block:4px; }
.pv-shell-chrome { display:contents; }
.pv-automation-status { display:flex; flex-wrap:wrap; gap:4px 8px; font-size:11px; color:var(--pv-muted); }
.pv-automation-status > :empty { display:none; }
.pv-automation-status [data-testid="automation-unresolved"] { font-weight:700; color:var(--pv-text); }
.pv-automation-dialog.q-card { width:min(800px, 100%); max-width:calc(100vw - 32px);
  max-height:calc(100dvh - 32px); display:flex; flex-direction:column; gap:4px; overflow:hidden; }
.pv-automation-dialog > :not(.pv-automation-scroll) { flex:none; }
.pv-automation-scroll { overflow:auto; min-height:0; flex:1 1 auto; }
.pv-automation-dialog [role="tab"][aria-selected="true"] { background:var(--pv-tab-selected); color:var(--pv-primary); }
.pv-automation-status [data-testid="automation-status"] {
  position:absolute; width:1px; height:1px; overflow:hidden; clip-path:inset(50%);
}
@media(max-width:640px) {
  .pv-automation-dialog.q-card { padding:8px; }
}
.pv-content { position:relative; width:100%; min-width:0; min-height:0; flex:1 1 0; display:flex; flex-direction:column; overflow:hidden; }
.pv-metadata-view, .pv-scheduler-view { flex:1 1 0; min-height:0; min-width:0; max-width:100%; overflow-y:auto; overflow-x:hidden; padding-right:8px; box-sizing:border-box; }
.pv-gallery { flex:1 1 0; min-height:0; min-width:0; overflow:hidden; display:flex; flex-direction:column; }
.q-btn { text-transform:none; }
.pv-ranking-view { overflow-y:auto; overflow-x:hidden; }
.pv-ranking-view > .pv-gallery { min-height:190px; }
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
.pv-conn-row { display:flex; flex-wrap:wrap; align-items:center; gap:6px; min-height:32px; margin-top:9.4px; font-size:12px; color:var(--pv-on-surface-variant); }
.pv-conn-row .q-toggle__label, .pv-conn-row .q-toggle { font-size:12px; color:var(--pv-on-surface-variant); }
.pv-conn-row .q-toggle { min-height:28px; }
/* 元の Flet Switch の実測 43x26（トラック）。 */
.pv-conn-row .q-toggle__inner { width:43px; min-width:43px; height:26px; padding:0; font-size:26px; }
.pv-conn-row .q-toggle__track { height:26px; border-radius:13px; }
.pv-conn-row .q-toggle__thumb { top:3px; width:20px; height:20px; }
[dir="ltr"] .pv-conn-row .q-toggle__thumb { left:3px; }
[dir="ltr"] .pv-conn-row .q-toggle__inner--truthy .q-toggle__thumb { left:20px; }
.q-btn.pv-hbtn, .pv-hbtn { width:40px; height:40px; min-width:40px; min-height:40px; padding:0; border-radius:50%; color:var(--pv-on-surface-variant) !important; display:inline-flex; align-items:center; justify-content:center; text-decoration:none; }
.pv-hbtn .q-icon { font-size:24px; }
.q-btn.pv-hbtn { color:var(--pv-on-surface-variant) !important; }
.pv-hbtn.pv-tab-active, .q-btn.pv-hbtn.pv-tab-active { background:var(--pv-tab-selected) !important; color:var(--pv-primary) !important; }
.pv-cookie-banner { width:100%; flex-shrink:0; box-sizing:border-box; background:var(--pv-container); border:1px solid #42a5f5; border-radius:12px; padding:10px 14px; gap:12px; margin-top:2.5px; }
.pv-banner-icon { font-size:24px; }
.pv-cookie-banner .pv-banner-title { font-weight:700; font-size:14px; color:var(--pv-on-surface); }
.pv-cookie-banner .pv-banner-body { font-size:14px; line-height:1.5; white-space:pre-line; color:var(--pv-on-surface-variant); }
.pv-pill { display:inline-flex; align-items:center; gap:8px; padding:8px 18px; border-radius:20px; background:var(--pv-container-pill); color:var(--pv-primary); font-size:14px; text-decoration:none; white-space:nowrap; }
.pv-nav { display:flex; flex-wrap:wrap; gap:6px 8px; width:100%; flex-shrink:0; overflow:visible; }
.pv-tab { font-family:inherit; font-size:14px; font-weight:500; text-transform:none; width:auto; min-width:0; flex:0 0 calc((100% - 72px) / 10); height:32px; min-height:32px; padding:0 10px; border-radius:20px !important; box-sizing:border-box; }
.pv-tab .q-btn__content { flex-wrap:nowrap; white-space:nowrap; }
.pv-tab .q-icon { font-size:18px; }
/* キーボードフォーカスは主色の 2px 枠。Quasar の .no-outline（レイヤ内の outline:0 !important）は outline では上書きできないので、
   同じ見た目の box-shadow 輪も重ねる（<a> は outline が効く）。 */
.pv-tab:focus-visible, .pv-hbtn:focus-visible { outline:2px solid var(--pv-primary); outline-offset:2px; box-shadow:0 0 0 2px var(--pv-page), 0 0 0 4px var(--pv-primary); }
/* Use the same ring on opt-in controls and the shell's link and switch. */
:is(.pv-pill, .pv-hit28, .pv-focus-ring, .pv-conn-row .q-toggle):focus-visible,
.pv-management-input:has(input:focus-visible) .q-field__control {
  outline:var(--pv-focus-ring-outline);
  outline-offset:2px;
  box-shadow:var(--pv-focus-ring-shadow);
}
/* Quasar's button ::before draws the base style. Keep the invisible hit area on ::after. */
:where(.pv-hit28) { position:relative; }
:where(.pv-hit28):not(:disabled, .disabled, [aria-disabled=true])::after {
  content:"";
  position:absolute;
  inset:50% auto auto 50%;
  width:var(--pv-hit28-width, max(100%, var(--pv-hit28-size, 28px)));
  height:max(100%, var(--pv-hit28-size, 28px));
  transform:translate(-50%, -50%);
}
.pv-nav .q-btn.pv-tab { background:transparent !important; border:0 !important; color:var(--pv-on-surface) !important; }
.pv-nav .q-btn.pv-tab.pv-tab-active { background:var(--pv-tab-selected) !important; color:var(--pv-primary) !important; }
.pv-tab-disabled { opacity:.48; cursor:not-allowed; }
.pv-toolbar { background:var(--pv-surface); padding:16px; border-radius:12px; width:100%; display:flex; flex-wrap:wrap; align-items:center; gap:12px; flex-shrink:0; }
.pv-search { flex:1 1 240px; min-width:160px; }
.pv-toolbar .q-btn.pv-size-button { background:transparent !important; color:var(--pv-text) !important; border:1px solid transparent !important; }
.pv-toolbar .q-btn.pv-size-selected { background:var(--pv-fill) !important; color:var(--pv-on-fill) !important; border:1px solid var(--pv-border) !important; border-radius:8px; }
.pv-learning .q-btn.pv-judgment { color:var(--pv-text) !important; border:1px solid var(--pv-border) !important; border-radius:8px; }
.pv-learning .q-btn.pv-judgment-like { background:var(--pv-fill) !important; color:var(--pv-on-fill) !important; border-color:var(--pv-fill) !important; }
.pv-learning .q-btn.pv-judgment-superLike { background:rgba(0,150,250,.25) !important; border-color:#0096FA !important; }
.pv-learning .q-btn.pv-undo { background:transparent !important; color:var(--pv-muted) !important; }
.pv-counter { color:var(--pv-muted); font-size:13px; }
.pv-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(min(100%,max(170px,calc((100% - (var(--pv-max-cols, 6) - 1) * 12px) / var(--pv-max-cols, 6)))),1fr)); gap:12px; width:100%; min-width:0; }
.pv-gallery-scroll { flex:1 1 0; overflow-y:auto; overflow-x:hidden; min-height:0; max-height:none; width:100%; overflow-anchor:none; }
.pv-more-cell { min-width:0; display:flex; align-items:center; justify-content:center; border:1px solid var(--pv-border); border-radius:12px; }
.pv-gallery > .row, .pv-gallery > .pv-counter, .pv-gallery > .pv-empty { flex-shrink:0; }
.pv-card { background:var(--pv-bg); border:1px solid var(--pv-border); border-radius:12px; overflow:hidden; cursor:pointer; min-width:0; }
.pv-grid .pv-card { aspect-ratio:1 / 1; height:auto; padding:6px; display:flex; flex-direction:column; gap:3px; box-sizing:border-box; }
.pv-grid .pv-backup-card { padding:0; }
.pv-backup-tile { position:relative; width:100%; height:100%; cursor:pointer; overflow:hidden; }
.pv-grid .pv-backup-tile .pv-thumb { position:absolute; inset:0; height:100%; border-radius:0; }
.pv-backup-tile .pv-thumb img { object-fit:cover !important; }
.pv-backup-name { position:absolute; bottom:0; left:0; right:0; height:34px; padding:5px; display:-webkit-box; -webkit-box-orient:vertical; -webkit-line-clamp:2; background:rgba(0,0,0,.6); color:white; font-size:11px; font-weight:bold; text-align:center; line-height:12px; overflow:hidden; opacity:0; transition:opacity 200ms; }
.pv-backup-tile:hover .pv-backup-name, .pv-backup-tile:focus-visible .pv-backup-name { opacity:1; }
.pv-grid .pv-card-rank { flex:0 0 auto; font-size:11px; font-weight:700; line-height:1.2; color:var(--pv-text); }
.pv-grid .pv-thumb { flex:1 1 0; min-height:0; height:0; aspect-ratio:auto; border-bottom:0; border-radius:12px; }
.pv-grid .pv-card-meta { flex:0 0 auto; min-width:0; padding:0; display:flex; flex-direction:column; gap:3px; }
.pv-grid .pv-card-title { font-size:11px; font-weight:400; line-height:1.2; white-space:normal; display:-webkit-box; -webkit-box-orient:vertical; -webkit-line-clamp:2; overflow:hidden; }
.pv-bookmarks .pv-grid .pv-card-title,
[data-testid=recommend-view] .pv-grid .pv-card-title { display:block; white-space:nowrap; text-overflow:ellipsis; }
.pv-grid .pv-card-author { font-size:10px; line-height:1.2; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.pv-candidates-box { flex:1 1 0; min-width:0; position:relative; display:flex; gap:4px; }
.pv-candidate-row { flex:1 1 auto; min-width:0; overflow:hidden; padding-block:2px; }
.pv-candidate-row > .q-btn { flex:none; max-width:none; }
.pv-candidate-row .q-btn__content { white-space:nowrap; }
.pv-candidate-more { flex:none; min-width:36px; padding:0 4px; color:var(--pv-text); background:var(--pv-raised); border-radius:6px; }
.pv-candidate-menu { position:fixed; inset:0 auto auto 0; margin:0; width:max-content;
  max-width:min(360px, 80vw); max-height:40vh; overflow:auto; background:var(--pv-surface);
  color:var(--pv-text); border:1px solid var(--pv-border); border-radius:8px; padding:8px; gap:4px; }
.pv-candidate-menu .q-btn { overflow-wrap:anywhere; max-width:100%; }
.pv-candidate-menu[hidden], .pv-candidate-menu [hidden], .pv-candidate-more[hidden] { display:none !important; }
.pv-grid .pv-card-head { flex:0 0 auto; }
.pv-grid .pv-card-heart { color:var(--pv-pixiv-blue); }
body.body--light .pv-grid .pv-card-heart { color:var(--pv-fill); }
.pv-chip { display:inline-flex; align-items:center; gap:6px; padding:3px 9px; border-radius:8px; background:var(--pv-raised); border:1px solid var(--pv-border); color:var(--pv-text); font-size:11px; cursor:pointer; }
.pv-chip:hover { background:var(--pv-border); }
.pv-chip:focus-visible { outline:2px solid var(--pv-primary); outline-offset:2px; }
.pv-chip .pv-chip-x { font-size:10px; color:var(--pv-on-surface-variant); }
.pv-card:hover { background:var(--pv-border); }
.pv-menu-icon { order:-1; margin-right:12px; align-self:center; opacity:.8; }
.pv-thumb { display:block; width:100%; aspect-ratio:1 / 1; min-width:0; background:var(--pv-surface); border-bottom:1px solid var(--pv-border); overflow:hidden; }
.pv-thumb img { width:100%; height:100%; object-fit:contain !important; }
.pv-grid-image { position:relative; }
.pv-grid-image img { display:block; }
.pv-grid-cover img { object-fit:cover !important; }
.pv-grid-library-placeholder { position:absolute; top:50%; left:50%; transform:translate(-50%,-50%); font-size:32px; opacity:.55; }
.pv-grid-image-error { position:absolute; inset:0; color:white; background:rgba(0,0,0,.47); }
.pv-card-menu-anchor { position:fixed; width:0; height:0; pointer-events:none; }
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
.pv-viewer-bottom .pv-view-title { font-family:inherit; font-size:13.5px; font-weight:400; }
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
.pv-trend-layout { display:grid; grid-template-columns:minmax(0,1fr) 440px; gap:24px; align-items:start; }
.pv-trend-layout[data-mode=band], .pv-trend-layout[data-mode=fold] { grid-template-columns:minmax(0,1fr); }
.pv-trend-main, .pv-trend-side { min-width:0; }
.pv-trend-side { height:min(640px,calc(100dvh - 220px)); min-height:80px; overflow:hidden; }
.pv-trend-layout[data-mode=band] .pv-trend-side { height:192px; }
.pv-trend-layout[data-mode=fold] .pv-trend-side { position:relative; height:auto; min-height:0; overflow:visible; }
.pv-trend-viewport { flex:1 1 0; min-height:0; overflow:hidden; }
.pv-trend-rows { display:flex; flex-direction:column; gap:8px; }
.pv-trend-layout[data-mode=band] .pv-trend-rows { display:grid; grid-auto-flow:column; grid-template-rows:repeat(2,minmax(0,1fr)); grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px 24px; }
.pv-trend-layout[data-mode=fold] .pv-trend-viewport { position:fixed; top:auto; z-index:24; height:auto; overflow:auto; background:var(--pv-overlay); border:1px solid var(--pv-border); border-radius:8px; }
.pv-trend-item { height:17px; min-width:0; gap:8px; }
.pv-view-title { font-size:20px; }
.pv-description { color:var(--pv-text); font-size:13px; white-space:pre-wrap; }
.pv-card-size { font-size:9px; line-height:1.2; min-width:32px; padding:3px 5px !important; }
.pv-card-size .q-icon { font-size:16px; }
.pv-library { flex:1 1 0; min-width:0; min-height:0; overflow:hidden; display:flex; flex-direction:column; gap:6px; }
.pv-library-toolbar { display:flex; align-items:center; flex-wrap:wrap; gap:10px; width:100%; flex:0 0 auto; }
.pv-library-heading { font-size:15px; font-weight:700; }
.pv-serif-heading { font-family:"Yu Mincho", "YuMincho", serif; }
.pv-library-subviews { gap:2px; flex-wrap:nowrap; }
.pv-library .q-btn { color:var(--pv-text) !important; font-size:11px; }
.pv-library .q-btn.pv-library-subview-selected, .pv-library .q-btn.pv-size-selected { background:var(--pv-fill) !important; color:var(--pv-on-fill-lib) !important; border-radius:8px; }
.pv-library .q-btn.pv-library-scope-selected { background:var(--pv-tab-selected) !important; color:var(--pv-text) !important; }
.pv-library .q-btn.pv-library-scope-selected .q-btn__content { color:var(--pv-text) !important; }
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
.pv-shared-log-host { flex:0 0 auto; min-height:0; }
.pv-queue-log { flex:0 0 140px; height:140px; overflow-y:auto; padding:10px; border:1px solid var(--pv-border); border-radius:5px; background:var(--pv-surface); box-sizing:border-box; }
.pv-queue-log-line { font-size:13px; color:var(--pv-text); white-space:pre-wrap; user-select:text; }
.pv-queue-log-success { color:#00ff66; }
.pv-queue-log-error { color:#ff7777; }
.pv-queue-log-info { color:#9ec7f9; }
body.body--light .pv-queue-log-success { color:#006b31; }
body.body--light .pv-queue-log-error { color:#ad2020; }
body.body--light .pv-queue-log-info { color:#23578a; }
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
[data-testid=queue-view] .pv-jobs-scroll { min-height:112px; }
[data-testid=queue-view] .pv-empty { padding:12px 14px; text-align:left; }
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
""".replace("__PV_UI_FONT__", UI_FONT, 1)

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

CSS += """
[data-testid=bookmark-status] { padding:0; text-align:left; }
.pv-bookmarks .pv-bookmark-select { position:absolute; top:2px; left:2px; z-index:2; background:var(--pv-surface); border-radius:6px; }
.pv-period-chips .q-btn { border-radius:999px !important; box-shadow:none; }
.pv-period-chips { box-shadow:none; gap:3px; }
.pv-preferences-card { border-radius:16px !important; }
.pv-preferences-group { border:1px solid var(--pv-border); border-radius:12px; padding:12px; width:100%; background:var(--pv-bg); }
.pv-retry-action { border-radius:999px; background:rgba(33,150,243,.12); color:var(--pv-text); padding:6px 12px !important; }
.pv-toolbar .q-btn.pv-size-selected, .pv-library .q-btn.pv-size-selected { background:rgba(33,150,243,.12) !important; color:var(--pv-text) !important; border-color:transparent !important; }
.pv-period-chips .q-btn { background:transparent !important; color:var(--pv-text) !important; }
.pv-period-chips .q-btn[aria-pressed=true] { background:rgba(33,150,243,.12) !important; }
.pv-filter-switch { font-size:11px; color:var(--pv-muted); }
.pv-filter-switch .q-toggle__inner { font-size:32px; }
@layer quasar_importants {
  .pv-period-chips .q-btn[aria-pressed=true] { background:rgba(33,150,243,.12) !important; color:var(--pv-text) !important; }
}
"""


CSS += """
/* tone-c */
.pv-tone-toolbar { padding:8px; gap:8px; }
.pv-personal-ranking, .pv-tone-recommend, .pv-tone-dedup,
.pv-tone-learning, .pv-learning-swipe, .pv-tone-learning .pv-trend-main { gap:6px; }
.pv-personal-ranking > .pv-tone-toolbar { flex-wrap:nowrap; overflow-x:auto; }
.pv-personal-tag-entry { flex:1 1 0; min-width:min(100%,12rem); }
.pv-personal-tag-entry > .row { flex:0 1 auto; min-width:0; overflow-x:auto; }
.pv-personal-tag-entry > .row > * { flex-shrink:0; }
.pv-personal-tag-input { flex:1 0 10rem; }
.pv-personal-ranking > [data-testid=personal-chips] { flex-wrap:nowrap; overflow-x:auto; }
.pv-personal-ranking > [data-testid=personal-chips] > * { flex-shrink:0; }
.pv-personal-status { flex-shrink:0; }
.pv-personal-status [data-error] { color:var(--pv-download-danger-text); }
.pv-personal-body { flex:1 0 228px; min-height:228px; }
.pv-personal-body > .pv-library-filter-panel { flex-basis:min(280px,52%); min-width:0; }
.pv-personal-body > .pv-library-filter-panel > * { flex-shrink:0; }
.pv-personal-body > .pv-library-filter-panel > .q-field { width:100%; }
.pv-personal-body > .pv-gallery > .pv-toolbar { padding:8px; gap:6px; }
.pv-personal-body .pv-empty { padding:16px 8px; }

.pv-tone-recommend > *, .pv-tone-dedup > * { flex-shrink:0; }
.pv-recommend-shelves { gap:6px; }
.pv-recommend-shelf { gap:4px; padding:4px 0; border-top:1px solid var(--pv-border); }
.pv-recommend-shelf-header > .pv-subtitle { overflow-wrap:anywhere; }
.pv-recommend-trends { max-height:80px; overflow:hidden; align-content:flex-start; }
.pv-recommend-trends:empty, .pv-recommend-card-actions:empty { display:none; }
.pv-recommend-card-actions { gap:2px 4px; }
.pv-recommend-card-actions > .q-btn { font-size:11px; }
.pv-recommend-tile .pv-recommend-thumb-empty { display:grid; place-content:center; justify-items:center; gap:4px; }

.pv-tone-dedup [data-testid=duplicate-recovery-status]:empty,
.pv-tone-dedup [data-testid=duplicate-item-results]:empty { display:none; }
.pv-dedup-near { flex:1 0 240px; min-height:240px; }
.pv-dedup-near > * { flex-shrink:0; }
.pv-dedup-near > .pv-dedup-list { flex:1 1 0; min-height:0; overflow-y:auto; }
.pv-dedup-list > * { flex-shrink:0; }
.pv-dedup-list .pv-empty { padding:16px 8px; text-align:left; }
.pv-dedup-selection, .pv-dedup-card { padding:8px; border:1px solid var(--pv-border); background:var(--pv-surface); }
.pv-dedup-selection { border-radius:8px; }
.pv-dedup-card { display:flex; flex-flow:row wrap; align-items:flex-start; gap:12px; border-radius:12px; box-shadow:none; }
.pv-dedup-card-heading { flex:0 1 9rem; }
.pv-dedup-card-thumbnails { flex:1 1 min(100%,22rem); min-width:0; }
.pv-dedup-card-actions { flex:0 1 12rem; }
.pv-dedup-card-actions > .q-btn { max-width:100%; }
.pv-dedup-tile { max-width:min(200px,100%); overflow-wrap:anywhere; }
.pv-dedup-card .pv-card-title { white-space:normal; overflow-wrap:anywhere; }
.pv-dedup-list .pv-dedup-thumb-empty { display:grid; place-content:center; padding:24px 4px 4px; background:var(--pv-raised); text-align:center; }
.pv-dedup-tile-select { position:absolute; top:4px; left:4px; padding:2px 4px; border-radius:8px; background:var(--pv-surface); }

.pv-tone-learning .pv-trend-layout { gap:12px; position:relative; }
.pv-tone-learning .pv-trend-layout[data-mode=rail]:not(:has(.pv-view-art:not(.hidden))) { grid-template-columns:minmax(0,1fr); }
.pv-tone-learning .pv-trend-layout[data-mode=rail]:not(:has(.pv-view-art:not(.hidden))) .pv-trend-side { max-height:88px; }
.pv-tone-learning .pv-trend-layout[data-mode=rail]:has(.pv-view-art:not(.hidden)) .pv-trend-main,
.pv-tone-learning .pv-trend-layout[data-mode=rail]:has(.pv-view-art:not(.hidden)) .pv-trend-side { grid-column:2; }
.pv-tone-learning .pv-trend-layout[data-mode=rail]:has(.pv-view-art:not(.hidden)) .pv-learning-art-stage { position:absolute; inset:0 auto 0 0; width:calc(100% - 452px); max-height:var(--pv-learning-stage-height,100%); }
.pv-tone-learning .pv-trend-layout[data-mode=rail]:has(.pv-view-art:not(.hidden)) .pv-learning-art-frame { width:100%; max-width:none; height:100%; }
.pv-tone-learning .pv-trend-layout[data-mode=rail]:has(.pv-view-art:not(.hidden)) .pv-view-art { width:100%; height:100%; max-width:none; max-height:none; }
.pv-tone-learning .pv-trend-layout[data-mode=band] .pv-trend-side { height:112px; }
.pv-learning-work-title { min-width:0; max-width:calc(100% - 5rem); overflow:hidden; white-space:nowrap; text-overflow:ellipsis; }
.pv-tone-learning .pv-learning-status:empty, .pv-tone-learning .pv-counter:empty { position:absolute; }
.pv-tone-learning [data-testid=learning-reset] { flex-flow:row wrap; align-items:center; gap:4px 8px; }
.pv-tone-learning [data-testid=learning-reset-status]:empty { position:absolute; }
.pv-learning-art-frame { max-width:560px; margin-inline:auto; }
.pv-tone-learning .pv-learning-art-frame .pv-view-art { max-height:min(28dvh,420px); background:var(--pv-surface); border:1px solid var(--pv-border); border-radius:12px; }
.pv-learning-candidate-stage { min-height:160px; }
.pv-learning-empty { width:min(520px,100%); align-self:center; padding:12px 16px; color:var(--pv-text); background:var(--pv-raised); border:1px solid var(--pv-border); border-radius:12px; box-shadow:none; }
/* /tone-c */
"""

CSS += """
/* tone-b */
.pv-tone-b-notice:empty {
  position:absolute; width:1px; height:1px; padding:0; overflow:hidden; clip-path:inset(50%);
}
.pv-tone-b-error { color:var(--pv-download-danger-text); font-weight:600; }
.q-dialog__inner > .pv-tone-b-history-card {
  width:32rem; max-width:calc(100vw - 3rem); max-height:calc(100dvh - 3rem); overflow:auto; flex-wrap:nowrap;
}
.pv-tone-b-history-card > * { flex-shrink:0; }
.q-dialog__inner > .pv-tone-b-unfollowed-card { width:clamp(40rem, 50vw, 58rem); max-width:calc(100vw - 3rem); }
.pv-tone-b-list { overflow-y:auto; flex-wrap:nowrap; scrollbar-gutter:stable; }
.pv-tone-b-history-list { height:min(22.5rem, 48dvh); }
.pv-tone-b-unfollowed-list { max-height:min(64dvh, clamp(26.5rem, calc(37.5vw - 3.5rem), 42rem)); }
.pv-tone-b-unfollowed-list:empty { display:none; }
.pv-tone-b-unfollowed-card .pv-empty { padding-block:1rem; }
.pv-tone-b-following-list { max-height:12rem; }
.pv-tone-b-following-list .pv-empty { padding-block:1rem; }
.pv-tone-b-metadata-operations { display:grid; grid-template-columns:minmax(0, 2fr) minmax(0, 1fr); gap:var(--pv-gap); }
.pv-tone-b-metadata-remote { border-inline-start:1px solid var(--pv-border); padding-inline-start:var(--pv-gap); }
@media(max-width:760px) {
  .pv-tone-b-metadata-operations { grid-template-columns:minmax(0, 1fr); }
  .pv-tone-b-metadata-remote { border-inline-start:0; padding-inline-start:0; border-block-start:1px solid var(--pv-border); padding-block-start:var(--pv-gap); }
}

.pv-tone-b-history-row, .pv-tone-b-author-row {
  --pv-tone-b-thumb-size:4rem;
  display:grid; align-items:start; gap:.25rem .5rem; flex-shrink:0; width:100%;
  padding-block:.5rem; border-bottom:1px solid var(--pv-border);
}
.pv-tone-b-history-row:last-child, .pv-tone-b-author-row:last-child { border-bottom:0; }
.pv-tone-b-history-row {
  grid-template-columns:var(--pv-tone-b-thumb-size) minmax(0, 1fr) auto;
  grid-template-areas:"thumb title external" "thumb facts facts" "thumb badge badge";
}
.pv-tone-b-thumb {
  grid-area:thumb; width:var(--pv-tone-b-thumb-size); height:var(--pv-tone-b-thumb-size);
  border-radius:var(--pv-radius-control); overflow:hidden;
}
.pv-tone-b-history-placeholder { font-size:1.5rem; }
.pv-tone-b-history-row .pv-tone-b-history-title.q-btn { grid-area:title; min-width:0; min-height:2rem; padding-inline:0; }
.pv-tone-b-history-title .q-btn__content { min-width:0; text-align:start; }
.pv-tone-b-history-title-text {
  display:-webkit-box; -webkit-box-orient:vertical; -webkit-line-clamp:2; overflow:hidden; overflow-wrap:anywhere;
}
.pv-tone-b-history-facts { grid-area:facts; }
.pv-tone-b-history-badge { grid-area:badge; }
.pv-tone-b-history-external { grid-area:external; }
.pv-tone-b-author-row {
  grid-template-columns:var(--pv-tone-b-thumb-size) minmax(0, 1fr) auto auto;
  grid-template-areas:"thumb title link follow" "thumb kind notice notice" "thumb preview notice notice";
}
.pv-tone-b-author-name { grid-area:title; color:var(--pv-text); font-weight:600; overflow-wrap:anywhere; }
.pv-tone-b-author-link { grid-area:link; }
.pv-tone-b-author-follow { grid-area:follow; }
.pv-tone-b-author-notice { grid-area:notice; }
.pv-tone-b-author-kind { grid-area:kind; }
.pv-tone-b-author-preview { grid-area:preview; }
/* tone-b */
/* tone-d */
:root {
  --pv-gap:12px;
  --pv-gap-compact:6px;
  --pv-radius-control:8px;
  --pv-radius-card:12px;
  --pv-error-text:var(--pv-auth-red);
  --pv-error-bg:color-mix(in srgb, var(--pv-error-text) 10%, transparent);
  --pv-warn-text:var(--pv-auth-yellow);
  --pv-accent-tint:rgba(var(--pv-primary-rgb), .12);
  --pv-caution-fill:#ffc107;
  --pv-on-caution:#191c20;
}
.q-btn.pv-btn-primary, .q-btn.pv-btn-secondary, .q-btn.pv-btn-danger { border-radius:var(--pv-radius-control); padding-inline:10px; }
.pv-section-heading { font-family:"Yu Mincho", "YuMincho", serif; color:var(--pv-text-main); font-size:15px; font-weight:700; }
.pv-field-hint { color:var(--pv-muted); font-size:11.5px; line-height:1.4; }
.pv-alert-banner, .pv-warning-banner { padding:6px 10px; line-height:1.45; border-radius:var(--pv-radius-control); }
.pv-alert-banner { color:var(--pv-error-text); background:var(--pv-error-bg); border:1px solid currentColor; }
.pv-warning-banner { color:var(--pv-warn-text); background:var(--pv-accent-tint); }
.pv-alert-banner:empty, .pv-warning-banner:empty, .pv-compact-status:empty { display:none; }
.q-card.pv-dialog-card { width:min(100%, 540px); max-height:calc(100dvh - 48px); overflow:auto; flex-wrap:nowrap; gap:var(--pv-gap); padding:16px; border-radius:var(--pv-radius-card); }
.q-card.pv-dialog-lg { width:min(100%, 640px); max-width:640px; }
.pv-dialog-card > *, .pv-dialog-card > .pv-dialog-actions { flex-shrink:0; }
.pv-dialog-card .pv-dialog-title { font-size:20px; font-weight:700; line-height:1.35; }
.pv-dialog-actions { width:100%; justify-content:flex-end; align-items:center; gap:var(--pv-gap-compact); }
.pv-compact-toolbar.pv-toolbar { padding:var(--pv-gap-compact); gap:var(--pv-gap-compact); }
.pv-compact-upload.q-uploader { min-height:0; max-width:100%; width:100%; border-radius:var(--pv-radius-control); }
.pv-compact-upload .q-uploader__header { background:var(--pv-accent-tint); color:var(--pv-text); }
.pv-compact-upload .q-uploader__list { min-height:0; padding:0; }
.pv-compact-upload .q-uploader__subtitle { display:none; }
.q-card.pv-preferences-card { max-height:min(664px, calc(100dvh - 48px)); padding:0; gap:0; }
.pv-preferences-top { padding:12px 16px; border-bottom:1px solid var(--pv-border); }
.pv-preferences-footer { position:sticky; bottom:0; z-index:2; gap:4px; padding:6px 16px 10px; background:var(--pv-surface); border-top:1px solid var(--pv-border); }
.pv-preferences-card { scroll-padding-bottom:90px; }
.pv-preferences-footer .pv-compact-status { width:100%; }
.pv-preferences-content { padding:var(--pv-gap); gap:var(--pv-gap); }
.pv-preferences-group { padding:var(--pv-gap); gap:var(--pv-gap-compact); }
.pv-preferences-group .pv-section-heading { width:100%; border-bottom:1px solid var(--pv-border); padding-bottom:4px; }
.pv-settings-expansion .q-item { min-height:32px; padding:0 4px; }
.pv-settings-expansion > .q-expansion-item__container > .q-item .q-item__label { font-family:"Yu Mincho", "YuMincho", serif; font-weight:700; }
.pv-settings-expansion > .q-expansion-item__container > .q-expansion-item__content { padding-top:var(--pv-gap-compact); }
.pv-settings-fields { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); width:100%; gap:var(--pv-gap-compact) var(--pv-gap); }
.pv-settings-field { min-width:0; gap:2px; }
.pv-settings-wide { grid-column:1 / -1; }
.pv-preferences-card .pv-filter-switch { font-size:13px; color:var(--pv-text); }
.pv-native-profile > *, .pv-native-profile .pv-settings-field > * { max-width:100%; overflow-wrap:anywhere; }
.pv-settings-follow [data-testid=following-sync-view] { gap:var(--pv-gap-compact); }
[data-testid=native-profile-recovery] { color:var(--pv-error-text); background:var(--pv-error-bg); padding:6px 10px; border-radius:var(--pv-radius-control); }
.pv-request-status { width:100%; white-space:pre-line; overflow-wrap:anywhere; line-height:1.5; padding:4px 8px; }
.pv-retry-announcement { position:absolute; width:1px; height:1px; padding:0; margin:-1px;
  overflow:hidden; clip-path:inset(50%); white-space:pre-line; }
.pv-shared-log-host { flex-shrink:0; }
.pv-shared-log-host .pv-queue-log {
  flex-basis:124px; height:124px; border-radius:var(--pv-radius-control);
  flex-wrap:nowrap; overflow-x:hidden;
}
.pv-shared-log-host .pv-queue-log:focus-visible {
  outline-offset:-2px; box-shadow:none;
}
.pv-shared-log-host .pv-queue-log-line { line-height:1.5; overflow-wrap:anywhere; }
.pv-log-empty { color:var(--pv-muted); font-size:13px; line-height:1.5; }
.pv-migration-section { width:100%; gap:var(--pv-gap-compact); border-top:1px solid var(--pv-border); padding-top:var(--pv-gap-compact); }
.pv-migration-scroll { max-height:170px; overflow:auto; flex-wrap:nowrap; }
.pv-backup-status.pv-empty { padding:0; text-align:left; }
.pv-gallery:has([data-testid=backup-grid]:empty) .pv-backup-status:not(:empty) { padding:var(--pv-gap); text-align:center; }
@media(max-width:620px) {
  .pv-settings-fields { grid-template-columns:minmax(0, 1fr); }
  .pv-preferences-footer { padding-inline:8px; }
}
"""


CSS += """
/* tone-d-review */
:root { --pv-action-text:var(--pv-page); }
body.body--light {
  --pv-error-text:var(--pv-download-danger-text);
  --pv-error-bg:color-mix(in srgb, var(--pv-error-text) 10%, transparent);
  --pv-warn-text:var(--pv-auth-yellow);
  --pv-accent-tint:rgba(var(--pv-primary-rgb), .12);
  --pv-action-text:var(--pv-on-fill);
}
/* Keep filled actions readable inside the existing view palettes. */
.q-btn.pv-btn-primary { background:var(--pv-fill) !important; color:var(--pv-action-text) !important; }
.q-btn.pv-btn-danger { background:var(--pv-caution-fill) !important; color:var(--pv-on-caution) !important; }
.q-card.pv-dialog-sm { width:min(100%, 400px); max-width:400px; }
.q-btn:is(.pv-btn-primary, .pv-btn-secondary, .pv-btn-danger):focus-visible {
  outline:2px solid var(--pv-primary);
  outline-offset:2px;
  box-shadow:0 0 0 2px var(--pv-page), 0 0 0 4px var(--pv-primary);
}
"""

CSS += """
/* tone-c-r2 */
.pv-dedup-undecidable-empty { padding:4px 12px 0; gap:2px; text-align:left; }
.pv-dedup-undecidable-empty > [data-testid=duplicate-undecidable-empty] { color:var(--pv-text); }
@media(min-width:1120px) {
  .pv-tone-learning .pv-trend-layout[data-mode=rail]:has(
    [data-testid=learning-empty]:not(.hidden), .pv-learning-candidate-stage:not(.hidden)
  ) {
    grid-template-columns:minmax(0,1fr) minmax(0,520px) minmax(0,1fr);
  }
  .pv-trend-layout[data-mode=rail]:has(
    [data-testid=learning-empty]:not(.hidden), .pv-learning-candidate-stage:not(.hidden)
  ) > .pv-trend-main { grid-column:2; }
  .pv-trend-layout[data-mode=rail]:has(
    [data-testid=learning-empty]:not(.hidden), .pv-learning-candidate-stage:not(.hidden)
  ) > .pv-trend-side { grid-column:3; }
}
/* /tone-c-r2 */
"""

CSS += """
/* tone-c-r3 */
.pv-tone-dedup .pv-dedup-selection { padding-block:4px; }
.pv-tone-dedup .pv-dedup-card { padding-block:6px; }
.pv-dedup-caption { gap:4px; }
.pv-dedup-caption > [role=checkbox] { flex:none; }
.pv-dedup-card .pv-dedup-caption > .pv-card-title { min-width:0; white-space:nowrap; }
.pv-dedup-tile > .pv-subtitle { font-size:11px; }

.pv-learning-title-row > .pv-learning-work-title { flex:1 1 0; min-width:0; max-width:none; }
.pv-learning-title-row > .q-btn { flex:none; }
.pv-learning-summary-message { max-width:35%; overflow:hidden; white-space:nowrap; text-overflow:ellipsis; }
.pv-learning-summary-message:empty { display:none; }
.pv-learning.pv-tone-learning .pv-trend-layout[data-mode=rail]:is([data-stage=art],[data-stage=empty],[data-stage=status]) > .pv-trend-side {
  height:auto; min-height:0; max-height:none; overflow:visible;
}
.pv-tone-learning .pv-trend-layout[data-mode=rail] .pv-trend-viewport { flex:none; overflow:visible; }
.pv-tone-learning .pv-trend-layout[data-mode=rail][data-stage=art] { grid-template-rows:auto auto auto; row-gap:6px; }
.pv-tone-learning .pv-trend-layout[data-mode=rail][data-stage=art] > .pv-trend-main { display:contents; }
.pv-tone-learning .pv-trend-layout[data-mode=rail][data-stage=art] > .pv-trend-main > * { grid-column:2; }
.pv-tone-learning .pv-trend-layout[data-mode=rail][data-stage=art] .pv-learning-candidate-heading { grid-row:1; }
.pv-tone-learning .pv-trend-layout[data-mode=rail][data-stage=art] > .pv-trend-side { grid-column:2; grid-row:2; }
.pv-tone-learning .pv-trend-layout[data-mode=rail][data-stage=art] .pv-learning-judgment-controls { grid-row:3; }
.pv-learning.pv-tone-learning .pv-trend-layout[data-mode=rail][data-stage=art] .pv-learning-art-stage {
  grid-column:1 / 2; grid-row:1 / 4; position:absolute; inset:0 auto auto 0;
  width:100%; height:var(--pv-learning-stage-height,100%); max-height:none;
}
@media(min-width:1120px) {
  .pv-learning.pv-tone-learning .pv-trend-layout[data-mode=rail]:is([data-stage=empty],[data-stage=status]) {
    grid-template-columns:minmax(0,1fr) minmax(0,520px) minmax(0,1fr);
  }
  .pv-learning.pv-tone-learning .pv-trend-layout[data-mode=rail]:is([data-stage=empty],[data-stage=status]) > .pv-trend-main { grid-column:2; }
  .pv-learning.pv-tone-learning .pv-trend-layout[data-mode=rail]:is([data-stage=empty],[data-stage=status]) > .pv-trend-side { grid-column:3; }
}
/* /tone-c-r3 */
"""

CSS += """
/* ux-4 */
@media (max-height:560px) {
  .pv-library:has(.pv-bookmarks:not(.hidden)) .pv-library-body { overflow:hidden auto; }
  .pv-library:has(.pv-bookmarks:not(.hidden)) .pv-library-filter-content { min-height:300px; }
  .pv-library .pv-bookmarks { gap:4px; }
  .pv-bookmarks > [data-testid=bookmark-status] { padding:4px 8px; }
  .pv-bookmarks .pv-gallery-scroll { min-height:144px; }
}
.pv-bookmark-title-preview {
  position:fixed; inset:auto 16px 16px; margin:0 auto; width:max-content;
  max-width:min(36rem, calc(100% - 32px)); max-height:calc(100% - 32px);
  overflow:auto; overflow-wrap:anywhere; pointer-events:none;
  padding:8px 12px; border:1px solid var(--pv-border); border-radius:8px;
  background:var(--pv-surface); color:var(--pv-text); font-size:13px; line-height:1.5;
}
body.body--dark .pv-library .pv-library-subview-selected.q-btn {
  background:color-mix(in srgb, var(--pv-fill) 65%, #000) !important;
  color:#fff !important;
}
.pv-library .pv-library-error { color:var(--pv-download-danger-text); }
.pv-library .pv-library-unknown { color:var(--pv-auth-yellow); font-size:11px; }
.pv-library :is(.pv-library-filter-state, .pv-library-filter-reason, [data-testid=library-page-bookmark-count],
  [data-testid=bookmark-card-count], [data-testid=library-managed-capacity]) { font-size:11px; }
.pv-library .pv-library-profile { min-width:22px; }
.pv-viewer .pv-viewer-info { flex-wrap:nowrap; }
.pv-viewer-info .pv-view-title { flex:0 1 auto; }
.pv-viewer-info .q-btn.pv-subtitle { flex:0 0 auto; max-width:30%; }
.pv-viewer-info .q-btn.pv-subtitle .q-btn__content { display:block; overflow:hidden; text-overflow:ellipsis; }
.pv-viewer .pv-viewer-info .q-btn:not(.pv-subtitle) { flex:none; }
.pv-tone-b-history-external.q-btn { color:var(--pv-text); }
/* /ux-4 */
/* ux-1 */
[data-testid=db-status-banner] { flex-shrink:0; max-width:100%; overflow-wrap:anywhere; }
.pv-cookie-banner .pv-pill:is(:hover, :active) { opacity:.9; }
.q-tooltip { background:#666; font-size:11px; }
.pv-cookie-banner .pv-banner-body { overflow-wrap:anywhere; }
.pv-hbtn .q-badge { color:var(--pv-action-text) !important; }
.pv-hidden, .pv-hide-empty:empty { display:none !important; }

.q-notification.pv-toast { overflow-wrap:anywhere; }
.pv-toast.pv-toast-positive { background:#176d36 !important; color:white !important; }
.pv-toast.pv-toast-negative { background:#a52132 !important; color:white !important; }
.pv-toast.pv-toast-info { background:#135b88 !important; color:white !important; }
.pv-toast.pv-toast-warning { color:var(--pv-on-caution) !important; }
.q-notification.pv-toast .q-notification__actions { color:inherit !important; }
.pv-toast .q-btn { color:inherit !important; min-height:28px; }
.pv-toast .q-btn:focus-visible { outline:2px solid currentColor; outline-offset:-2px;
  box-shadow:inset 0 0 0 2px currentColor; }
.pv-toast .q-btn:focus-visible > .q-focus-helper { opacity:0; }
.pv-dialog-alert { width:100%; box-sizing:border-box; align-items:center; gap:8px; padding:8px 12px;
  border-radius:var(--pv-radius-control); }
.pv-dialog-alert.pv-toast-warning { background:var(--q-warning); }
.pv-dialog-alert-message { flex:1 1 0; min-width:0; overflow-wrap:anywhere; }

@media (max-width:540px) {
  .pv-header { flex-wrap:wrap; }
  .pv-header-main { flex-basis:100%; }
  .pv-header-actions { flex-wrap:wrap; }
  .pv-cookie-banner { flex-wrap:wrap !important; }
}
@media (max-height:560px) {
  .pv-shell-chrome { display:flex; flex:0 0 auto; flex-direction:column; gap:10px;
    width:100%; max-height:clamp(64px, calc(100dvh - 310px), 34dvh);
    overflow:hidden auto; box-sizing:border-box;
    padding:4px; scroll-padding-block:4px; }
  .pv-shared-log-host .pv-queue-log { flex-basis:min(124px, 22dvh); height:min(124px, 22dvh); }
}
@media (prefers-reduced-motion:reduce) {
  .q-notification.pv-toast { animation:none !important; transition-duration:0s !important; }
}
/* /ux-1 */
"""


CSS += """
/* ux-2 */
.pv-settings-modal :is(.q-btn:is(.disabled, :disabled), .q-toggle.disabled, .q-checkbox.disabled),
.pv-settings-modal .q-field--disabled .q-field__control > div { opacity:1 !important; }
.pv-settings-modal :is(.q-toggle.disabled .q-toggle__label,
                      .q-checkbox.disabled .q-checkbox__label, .q-field--disabled .q-field__native,
                      .q-field--disabled .q-field__label) { color:var(--pv-on-surface-variant) !important; }
.pv-settings-modal .q-btn:is(.disabled, :disabled) { color:var(--pv-text) !important; }
.pv-settings-modal .q-btn.pv-btn-primary:is(.disabled, :disabled) {
  background:color-mix(in srgb, var(--pv-fill) 25%, var(--pv-surface)) !important;
}
.pv-settings-modal .q-btn.pv-btn-danger:is(.disabled, :disabled) {
  background:color-mix(in srgb, var(--pv-caution-fill) 25%, var(--pv-surface)) !important;
}
.pv-migration-progress-value {
  background:var(--pv-surface);
  color:var(--pv-text);
  font-size:.875rem;
  border-radius:2px;
  padding-inline:4px;
}
[data-testid=stop-recovery-rate-limit-guidance] { line-height:1.5; }
/* NiceGUI imports Quasar's important rules in this layer. */
@layer quasar_importants {
  .pv-settings-modal .q-toggle.pv-hit28.disabled,
  .pv-settings-modal .q-field--disabled .q-field__control > .q-field__control-container { opacity:1 !important; }
}
.pv-settings-modal .q-toggle.pv-hit28.disabled .q-toggle__inner { opacity:.75; }
/* /ux-2 */
"""


CSS += """
/* ux-3 */
.pv-dedup-candidate { z-index:1; }
.pv-job-chips .q-btn { min-height:28px !important; }
@layer overrides {
    .pv-tone-dedup .q-btn:is([data-testid=duplicate-group-move],
                            [data-testid=duplicate-plan],
                            [data-testid=duplicate-move],
                            [data-testid=duplicate-trash],
                            [data-testid=duplicate-bulk-low]):disabled {
        background:var(--pv-surface) !important;
        color:var(--pv-muted) !important;
        opacity:1 !important;
    }
    .pv-trash-count-input.q-field--disabled .q-field__inner,
    .pv-trash-count-input.q-field--disabled .q-field__control > div {
        opacity:1 !important;
    }
    .pv-trash-count-input.q-field--disabled .q-field__label {
        color:var(--pv-muted) !important;
        opacity:1 !important;
    }
    .q-btn.pv-btn-danger[data-testid=duplicate-trash-empty]:disabled {
        background:var(--pv-surface) !important;
        color:var(--pv-muted) !important;
        opacity:1 !important;
    }
    [data-testid=duplicate-progress-bar] > .absolute-center {
        color:var(--pv-text) !important;
        background:var(--pv-surface);
        padding-inline:4px;
        border-radius:var(--pv-radius-control);
    }
}
.pv-queue-view .pv-jobs-scroll { padding-top:3px; }
.pv-queue-view .pv-job-head { overflow:visible; position:relative; z-index:1; }
.pv-queue-view .pv-job-actions .q-btn { z-index:1; }
.pv-managed-mode { background:var(--pv-raised) !important; color:var(--pv-text) !important; border:1px solid var(--pv-border); }
.pv-managed-mode[data-selected=true] { background:var(--pv-fill) !important; color:var(--pv-action-text) !important; border-color:var(--pv-fill); }
.pv-managed-star-filter { background:transparent !important; color:var(--pv-text) !important; border:1px solid var(--pv-border); }
.pv-managed-star-filter[data-selected=true] { background:var(--pv-tab-selected) !important; color:var(--pv-text) !important; border-color:var(--pv-primary); }
.pv-managed-star-btn { min-width:28px !important; min-height:28px !important; }
@layer overrides {
  .pv-managed-star-filter:disabled { opacity:1 !important; color:var(--pv-muted) !important; }
}
@layer overrides {
    .pv-metadata-work-id.q-field--disabled .q-field__inner,
    .pv-metadata-work-id.q-field--disabled .q-field__control > div { opacity:1 !important; }
    .pv-metadata-work-id.q-field--disabled .q-field__label { color:var(--pv-muted) !important; opacity:1 !important; }
}
.pv-scheduler-authors { flex:1 1 280px; min-width:min(100%, 280px); }
.pv-ud-search .q-field__label { line-height:22px; min-height:22px; top:10px; }
.pv-ud-hintrow.pv-hit28 { --pv-hit28-width:100%; }
.pv-ud-list { scroll-padding-block:4px; }
.pv-management-follow-search { flex:1 1 244px; min-width:min(100%, 244px); }
@layer overrides {
  .pv-ud-run:disabled { background:var(--pv-surface) !important; color:var(--pv-muted) !important; opacity:1 !important; }
}
@media (max-width:720px) {
  .pv-queue-view .pv-job-head .pv-job-title { min-width:0; }
  .pv-queue-view .pv-job-id-seat { min-width:0; }
  .pv-ud :is(.pv-ud-header, .pv-ud-tools) { flex:0 0 auto; flex-wrap:wrap; height:auto; }
  .pv-ud .pv-ud-body { flex:0 0 auto; flex-direction:column; }
  .pv-ud :is(.pv-ud-left, .pv-ud-right, .pv-ud-pane, .pv-ud-bm) { flex:0 0 auto; }
  .pv-ud .pv-ud-list { flex:0 0 auto; min-height:112px; max-height:210px; }
  .pv-ud .pv-ud-chips { min-height:74px; max-height:220px; }
}
/* /ux-3 */
"""


CSS += """
/* ux-5 */
:root { --pv-ux5-blue-fill:#0066aa; }
:is(.pv-ranking-view, .pv-learning, .pv-tone-recommend, [data-testid=ranking-date-dialog],
    [data-testid=learning-reset-dialog]) .q-btn.text-primary {
  color:var(--pv-primary) !important;
}
.pv-learning .q-btn.pv-judgment-like {
  background:var(--pv-ux5-blue-fill) !important;
  border-color:var(--pv-ux5-blue-fill) !important;
}
:is(.pv-ranking-view, .pv-learning, .pv-tone-recommend, [data-testid=ranking-date-dialog],
    [data-testid=learning-reset-dialog]) .q-btn:disabled,
.pv-ranking-view .pv-filter-switch[aria-disabled=true],
[data-testid=discovery-settings-panel] .q-field--disabled :is(.q-field__native, .q-field__label, .q-field__bottom) {
  color:var(--pv-muted) !important;
}
@layer quasar_importants {
  body.body--dark .pv-ranking-view .pv-gallery .pv-toolbar .q-btn.bg-primary:not(:disabled),
  [data-testid=ranking-date-picker] .q-btn.bg-primary:not(:disabled) {
    background:var(--pv-ux5-blue-fill) !important;
    color:white !important;
  }
  :is(.pv-ranking-view, .pv-learning, .pv-tone-recommend, [data-testid=ranking-date-dialog],
      [data-testid=learning-reset-dialog]) .q-btn:disabled,
  .pv-ranking-view .pv-filter-switch[aria-disabled=true],
  [data-testid=discovery-settings-panel] .q-field--disabled .q-field__control > div {
    opacity:1 !important;
  }
}
:is(.pv-learning .q-btn.pv-judgment, .pv-tone-recommend .q-btn.pv-btn-primary,
    [data-testid=ranking-date-picker] .q-btn.bg-primary,
    [data-testid=learning-reset-execute].q-btn):disabled {
  background:var(--pv-raised) !important;
  border-color:var(--pv-border) !important;
}
.pv-ranking-view .pv-period-chips .q-btn,
.pv-ranking-view .pv-filter-switch,
.pv-personal-ranking .pv-chip[role=button],
[data-testid=ranking-date-picker] .q-date__navigation .q-btn {
  min-width:28px;
  min-height:28px;
}
.pv-personal-tag-entry > .row { padding-block:2px; margin-block:-2px; }
[data-testid=personal-remove-tag].pv-hit28 { --pv-hit28-size:29px; }
.pv-learning .pv-judgment.q-btn { min-width:44px; min-height:44px; }
.pv-learning .pv-judgment .q-btn__content { flex-direction:column; }
.pv-learning .pv-judgment .q-btn__content::before {
  content:var(--pv-judgment-glyph) / "";
  font-size:10px;
  line-height:1.2;
}
/* These controls are generated by Quasar and CardGrid, beyond the Python element classes. */
:is(.pv-period-chips, [data-testid=ranking-date-picker]) .q-btn:focus-visible {
  box-shadow:var(--pv-focus-ring-shadow);
}
.pv-ranking-view .pv-grid .pv-card[role=button]:focus-visible {
  outline:var(--pv-focus-ring-outline) !important;
  outline-offset:-2px;
}
:is(.pv-learning, .pv-recommend-tile .pv-card, .pv-ranking-detail-title).pv-focus-ring:focus-visible {
  outline-offset:-2px;
  box-shadow:none;
}
:is(.pv-ranking-view, .pv-tone-recommend) .pv-grid .pv-card-title { overflow-wrap:anywhere; }
.pv-recommend-shelf .pv-card-title:not(.pv-card-meta > .pv-card-title) {
  white-space:normal;
  overflow-wrap:anywhere;
  line-height:1.5;
}
[data-testid=ranking-date-picker] { max-width:100%; }
[data-testid=ranking-date-picker] .q-date__header {
  background:var(--pv-ux5-blue-fill);
  color:white;
}
[data-testid=ranking-date-picker] .q-date__header-link { opacity:.9; }
[data-testid=ranking-date-picker] .q-date__header-link--active { opacity:1; }
[data-testid=ranking-date-picker] :is(.q-date__calendar-weekdays, .q-date__calendar-item--out) {
  color:color-mix(in srgb, var(--pv-muted) 90%, var(--pv-text));
  opacity:1;
}
[data-testid=ranking-date-picker] .q-date__calendar-weekdays > div { opacity:1; }
.pv-ranking-detail-title {
  order:-1;
  width:100%;
  overflow-wrap:anywhere;
  white-space:pre-wrap;
}
[data-testid=recommend-section-certain] .pv-grid .pv-card-author { font-size:9.5px; }
.pv-recommend-card-actions { row-gap:4px; }
.pv-ranking-view .q-field--disabled:has([data-testid=ranking-sort]) :is(.q-field__label, .q-field__native) {
  color:var(--pv-muted) !important;
}
@layer quasar_importants {
  .pv-ranking-view .q-field--disabled:has([data-testid=ranking-sort]) .q-field__control > div {
    opacity:1 !important;
  }
}
[data-testid=personal-remove-tag].q-btn { scroll-margin-inline:5px; }
/* /ux-5 */
"""

CSS += """

/* Navigation and the content's fixed controls. */
.pv-shell { padding:8px; gap:4px; }
.pv-shell-chrome { display:contents; }
.pv-header { align-items:center; gap:8px; }
.pv-header-main { flex-direction:row; align-items:center; flex-wrap:wrap; gap:4px 12px; }
.pv-title { line-height:36px; }
.pv-conn-row { min-height:28px; margin-top:0; gap:4px; }
.pv-header-actions { gap:4px; }
.pv-header-main > div:empty, .pv-automation-status:not(:has(> :not(:empty))) { display:none; }
.pv-header-main > div:has(> [data-testid$="-retry-status"]):not(:has(> :not(.hidden, .pv-retry-announcement))) { display:none; }
.pv-db-banner { width:100%; flex:none; min-width:0; overflow-wrap:anywhere; }
.pv-db-passive { color:var(--pv-muted); font-size:11px; padding-inline:4px; }
.pv-db-production { background:#b32121; color:#fff; font-size:13px; font-weight:700; }
.pv-db-compact { display:flex; justify-content:space-between; align-items:center; gap:4px; padding-inline:6px; }
.pv-db-compact > [role=status] { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.pv-db-compact .q-btn { height:28px; min-height:28px; padding-block:0; flex:none; }
.pv-nav-region { flex:none; width:100%; min-width:0; position:relative; }
.pv-nav-toggle { display:none; }
.pv-nav .pv-tab .q-btn__content { gap:2px; }
.pv-nav .pv-tab .q-icon { margin:0; }
.pv-nav .pv-tab { font-size:13px; padding-inline:2px; min-height:30px; }
@media (min-width:1280px) and (max-width:1439px) {
  .pv-nav .pv-tab .q-btn__content { flex-direction:column; font-size:12px; line-height:1.1; }
  .pv-nav .pv-tab .q-icon { font-size:15px; }
}
@media(max-width:1279px) {
  .pv-nav .pv-tab { flex-basis:calc((100% - 32px) / 5); }
}
@media(max-width:640px) {
  .pv-shell { gap:3px; }
  .pv-header-main { gap:1px 8px; flex-basis:auto; }
  .pv-header { flex-wrap:wrap; gap:2px 4px; }
  .pv-header-actions { gap:2px; }
  .q-btn.pv-hbtn, .pv-hbtn { width:32px; min-width:32px; height:32px; min-height:32px; }
  .pv-hbtn .q-icon { font-size:20px; }
  .pv-title { font-size:20px; line-height:26px; }
  .pv-auth { font-size:11px; }
  .pv-nav-toggle { display:flex; align-items:center; gap:8px; min-height:28px; padding:0 8px;
    color:var(--pv-text); background:var(--pv-surface); border:1px solid var(--pv-border); border-radius:6px; }
  .pv-nav-toggle > :first-child { font-weight:700; }
  .pv-nav-toggle > [data-nav-action] { font-size:11px; }
  .pv-nav-toggle[aria-expanded=false] + .pv-nav { display:none; }
  .pv-nav-region .pv-nav { position:absolute; inset-inline:0; top:100%; z-index:20; padding:4px;
    background:var(--pv-surface); border:1px solid var(--pv-border); border-radius:6px; gap:4px; }
  .pv-nav-region .pv-nav .pv-tab { flex-basis:calc((100% - 4px) / 2); justify-content:flex-start; }
  .pv-cookie-banner { padding:4px 8px; gap:4px; }
  .pv-cookie-banner .pv-banner-body { font-size:11px; }
  .pv-ranking-view > .pv-gallery { min-height:0; }
}
.pv-manual-toolbar { width:100%; flex:none; }
.pv-manual-toolbar > summary { display:flex; align-items:center; gap:4px; width:max-content; cursor:pointer;
  border:1px solid var(--pv-border); border-radius:6px; min-height:28px; padding-inline:8px; color:var(--pv-text); }
.pv-manual-toolbar > summary::-webkit-details-marker { display:none; }
.pv-manual-toolbar[open] > summary > .pv-expand-label, .pv-manual-toolbar:not([open]) > summary > .pv-collapse-label { display:none; }
.pv-manual-panel { min-width:0; max-height:min(30dvh, 240px); overflow:auto; scrollbar-gutter:stable;
  padding:4px 6px; gap:4px 8px; align-items:center; }
.pv-manual-panel [data-testid=bookmark-filter-toggle].q-btn {
  box-sizing:border-box; padding-block:0; min-height:24px; height:24px;
}
.pv-nav-fixed-tools { display:flex; flex-wrap:wrap; align-items:center; flex:none; gap:4px 8px; }
.pv-nav-fixed-tools .pv-library-heading { margin:0; }
.pv-content .pv-toolbar { padding:4px 8px; gap:4px 8px; }
.pv-content > .pv-gallery, .pv-content > .pv-library, .pv-content .pv-library-filter-content { gap:4px; }
.pv-content .pv-empty:empty, .pv-content .pv-subtitle:empty { display:none; }
"""

CSS += """
.pv-nav-fixed-tools { min-height:28px; }
.pv-nav-fixed-tools > .pv-manual-toolbar:not([open]) { width:auto; }
.pv-nav-fixed-tools > .pv-manual-toolbar[open] { flex:1 0 100%; min-width:0; }
.pv-tone-recommend > .pv-nav-fixed-tools, .pv-tone-dedup > .pv-nav-fixed-tools { padding:0; }
.pv-tone-recommend { overflow:hidden; }
.pv-tone-recommend > .pv-recommend-shelves { flex:1 1 0; min-height:0; overflow:auto; }
.pv-tone-recommend > .pv-nav-fixed-tools > [role=status] {
  min-width:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
}
.pv-tone-recommend > .pv-nav-fixed-tools > [role=status]:not(:empty) { flex:1 1 12ch; }
.pv-tone-recommend > .pv-nav-fixed-tools > [data-testid=recommend-notice]:is(:focus-visible,:hover) {
  white-space:normal; overflow-wrap:anywhere;
}
.pv-recommend-description {
  flex:0 1 auto; min-width:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
}
.pv-recommend-shelf-header { flex-wrap:nowrap; }
.pv-recommend-shelf-header > .q-btn { flex:none; }
.q-dialog__inner > .pv-tone-b-history-card {
  width:calc(100vw - 64px); max-width:calc(100vw - 64px);
  height:calc(100dvh - 64px); max-height:calc(100dvh - 64px); overflow:hidden;
}
.pv-tone-b-history-card > .pv-tone-b-history-list { height:auto; flex:1 1 0; min-height:0; }
.pv-bookmark-badge { font-size:11px; }
.pv-bookmark-unsaved { border:1px solid var(--pv-border); }
@media (max-width:640px) {
  .pv-shell .pv-header .pv-request-status {
    font-size:11px; line-height:16px; padding:0 4px; min-height:0;
  }
  .pv-shell .pv-header .pv-conn-row > [data-testid=shared-get-recovery-summary] {
    font-size:11px; padding:2px 4px;
  }
  .pv-personal-ranking > .pv-personal-body { flex-basis:0; min-height:0; }
  .pv-library:has(.pv-bookmarks:not(.hidden)) .pv-library-filter-content { min-height:0; }
  .pv-library .pv-bookmarks .pv-gallery-scroll { min-height:0; }
  .pv-bookmarks .pv-library-summary { gap:2px 6px; }
  .pv-gallery .pv-card-rank { font-size:11px; }
  .pv-manual-toolbar > summary { position:sticky; top:0; z-index:1; background:var(--pv-page); }
}
"""

CSS += """
@media (min-width:1024px) {
  .pv-content .pv-ud .pv-ud-left { flex:2 1 0; }
  .pv-content .pv-ud .pv-ud-right { flex:1 1 0; }
}
@media (max-width:640px) {
  .pv-shell .pv-header .pv-request-status { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .pv-header .pv-header-main { flex:1 1 100%; }
  .pv-header .pv-header-main > .pv-conn-row { min-height:28px; }
  .pv-header .pv-hbtn { height:28px; min-height:28px; }
  .pv-cookie-banner .pv-banner-icon { display:none; }
  .pv-cookie-banner .pv-banner-body { position:absolute; width:1px; height:1px; overflow:hidden; clip-path:inset(50%); }
  .pv-cookie-banner .pv-pill { padding:2px 8px; min-height:28px; gap:4px; font-size:11px; }
  .pv-shell :is(.pv-ranking-view, .pv-bookmarks) { position:relative; }
  .pv-ranking-view > .pv-manual-toolbar:not([open]), .pv-bookmarks > .pv-manual-toolbar:not([open]) { height:0; }
  :is(.pv-ranking-view, .pv-bookmarks) > .pv-manual-toolbar:not([open]) > summary {
    position:absolute; top:0; right:1px; z-index:1; font-size:11px;
  }
  :is(.pv-ranking-view > .pv-nav-fixed-tools, .pv-bookmarks > .pv-library-summary):has(+ .pv-manual-toolbar:not([open])) {
    padding-inline-end:124px;
  }
  .pv-ranking-view > .pv-gallery > .pv-counter,
  .pv-personal-body > .pv-gallery > .pv-counter { font-size:11px; line-height:16px; }
  .pv-content .pv-gallery { gap:3px; }
  .pv-content .pv-gallery-scroll { padding-block:0; }
}
"""

CSS += """
.pv-header .pv-header-request-status {
  display:flex; flex-wrap:wrap; align-items:center; gap:2px 6px; min-width:0;
}
.pv-header .pv-header-request-status :is(.pv-request-status, [data-testid=request-gate-confirm-unavailable]) {
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; min-height:0; min-width:0;
  padding-block:1px; line-height:16px; font-size:11px;
}
.pv-header .pv-header-request-status [data-testid=request-retry-status] { flex:1 1 160px; }
.pv-header .pv-header-request-status [data-testid=request-gate-confirm-unavailable] { flex:1 1 200px; }
.pv-header-actions > .pv-automation-status { flex:none; gap:2px 6px; }
[data-testid=shared-get-recovery-summary] {
  flex:1 1 130px; min-width:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
}
"""

CSS += """
@media (max-width:640px) {
  .pv-header .pv-header-request-status {
    display:grid; grid-template-columns:minmax(0,1fr) auto; gap:1px 4px;
  }
  .pv-header .pv-header-request-status [data-testid=request-retry-progress-announcement],
  .pv-header .pv-header-request-status [data-testid=request-gate-confirm-unavailable] {
    position:absolute; width:1px; height:1px; clip-path:inset(50%); overflow:hidden;
  }
  .pv-header .pv-header-request-status [data-testid=request-retry-dismiss] {
    grid-column:2; grid-row:1;
  }
  .pv-ranking-view [data-testid=ranking-notice], .pv-personal-status {
    font-size:11px; line-height:15px;
  }
  .pv-ranking-view > .pv-gallery > .pv-counter,
  .pv-personal-body > .pv-gallery > .pv-counter {
    position:absolute; width:1px; height:1px; clip-path:inset(50%); overflow:hidden;
  }
}
"""

CSS += """
@media (max-width:640px) and (max-height:420px) {
  .pv-header-actions { flex-wrap:wrap; }
  .pv-content :is(.pv-library, .pv-tone-recommend, .pv-tone-dedup) { gap:3px; }
  .pv-content .pv-tone-dedup { gap:1px; }
  .pv-tone-dedup > .pv-nav-fixed-tools { gap:4px; }
  .pv-tone-dedup > .pv-nav-fixed-tools > .pv-library-heading {
    position:absolute; width:1px; height:1px; overflow:hidden; clip-path:inset(50%);
  }
  .pv-tone-dedup [data-testid^=duplicate-mode-] {
    max-width:114px; padding-inline:4px; white-space:nowrap;
  }
  .pv-tone-dedup > .pv-nav-fixed-tools > .q-btn,
  .pv-tone-dedup .pv-nav-fixed-tools > .pv-manual-toolbar > summary {
    font-size:12px;
  }
  .pv-dedup-status { gap:4px; }
  .pv-dedup-near { flex:1 1 0; min-height:0; }
  .pv-dedup-near > .pv-dedup-selection { padding-block:0; gap:4px; }
  .pv-tone-dedup .pv-dedup-card { padding-block:4px; }
  .pv-content .pv-grid {
    grid-template-columns:repeat(auto-fill,minmax(min(100%,max(138px,
      calc((100% - (var(--pv-max-cols, 6) - 1) * 12px) / var(--pv-max-cols, 6)))),1fr));
  }
  .pv-library[data-testid=library-view] { position:relative; }
  .pv-library[data-testid=library-view] > .pv-library-toolbar:has(+ .pv-manual-toolbar:not([open])) {
    min-height:30px; padding-inline-end:124px;
  }
  .pv-library[data-testid=library-view] > .pv-manual-toolbar:not([open]) { height:0; }
  .pv-library[data-testid=library-view] > .pv-manual-toolbar:not([open]) > summary {
    position:absolute; top:0; right:1px; z-index:1; font-size:11px;
  }
  [data-testid=bookmark-host] .pv-library-body { overflow-y:auto; }
  .pv-library[data-testid=bookmark-host]:has(.pv-bookmarks:not(.hidden)) .pv-library-filter-content {
    min-height:230px;
  }
  .pv-library[data-testid=bookmark-host] .pv-bookmarks .pv-gallery-scroll { min-height:144px; }
}
"""
