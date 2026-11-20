"""学習タブの通知帯。元 discovery_tab.py:248-290 の `discovery_toast`、1528-1700 の帯の出し入れ、853-896 の通知枠。

帯は1枚だけ。理由2択・「判定しました／取り消す」・軽い諦め・重い諦めが同じ座標で入れ替わる。
- 重い知らせは「閉じる」を押すまで消えない。軽いものにも譲らない（元 1573-1576, 1654-1656）。
- 残り時間は細い線で示す。数字は出さない。
"""
from __future__ import annotations

import asyncio
import time

from nicegui import ui

UNDO_SECONDS = 4.0     # original discovery_tab.py:1540 _TOAST_SECONDS_UNDO (the original is correct)
REASON_SECONDS = 6.0   # 元 discovery_tab.py:1539 _TOAST_SECONDS_REASON

_BAND_STYLE = ("position:absolute;right:16px;bottom:12px;z-index:20;width:320px;max-width:calc(100% - 32px);"
               "background:var(--pv-overlay);border:1px solid var(--pv-border);border-radius:12px;"
               "padding:10px 14px;box-sizing:border-box;display:flex;flex-direction:column;gap:6px;")
_CHIP_STYLE = ("background:var(--pv-raised) !important;border:1px solid var(--pv-border);border-radius:999px;"
               "color:var(--pv-text) !important;font-size:11.5px;min-height:0;padding:2px 12px;")
_ROW_STYLE = 'display:flex;flex-wrap:wrap;align-items:center;justify-content:center;gap:8px;'


class LearningToast:
    def __init__(self, *, clock=time.monotonic) -> None:
        self._clock = clock
        self.kind: str | None = None
        self.heavy = False
        self.heavy_key: str | None = None
        self.reason_active = False
        self.undo_button = None
        self._undo_action = None
        self._deadline: float | None = None
        self._expiry = None
        self._meter_variant = False
        # Alternate identical keyframes to restart even for consecutive toasts
        # with the same duration, without replacing the progress element.
        ui.add_css("""
            @keyframes pv-learning-meter-a { from {transform:scaleX(1)} to {transform:scaleX(0)} }
            @keyframes pv-learning-meter-b { from {transform:scaleX(1)} to {transform:scaleX(0)} }
            [data-testid=learning-toast-meter] .q-linear-progress__model {
                animation: var(--pv-meter-animation, none) var(--pv-meter-duration, 0s) linear forwards;
            }
        """)
        self.band = ui.element('div').style(_BAND_STYLE).props(
            'role=status aria-live=polite data-testid=learning-toast')
        with self.band:
            self.row = ui.element('div').style(_ROW_STYLE)
            self._undo_row = ui.element('div').style(_ROW_STYLE)
            self.meter = ui.linear_progress(value=1.0, show_value=False, size='2px').props(
                'rounded data-testid=learning-toast-meter')
        self.band.set_visibility(False)
        self._undo_row.set_visibility(False)

    # ---- 出し入れ -------------------------------------------------------
    def _chip(self, label: str, *, testid: str, on_click=None, js_handler=None):
        button = ui.button(label, color=None).props(f'flat dense no-caps data-testid={testid}').style(_CHIP_STYLE)
        if on_click is not None:
            if js_handler is not None:
                button.on('click', on_click, js_handler=js_handler)
            else:
                button.on('click', on_click)
        return button

    def _show(self, build, *, timeout: float, heavy: bool = False, key: str | None = None, kind: str = '') -> bool:
        # 重い知らせが出ている間は軽いものに譲らない（元 toast_decision）。
        if self.heavy and not heavy:
            return False
        self.heavy = heavy
        self.heavy_key = key if heavy else None
        self.kind = kind
        self.reason_active = False
        self.row.clear()
        self.row.set_visibility(kind != 'undo')
        self._undo_row.set_visibility(kind == 'undo')
        if build is not None:
            with self.row:
                build()
        self.meter.set_visibility(bool(timeout))
        if timeout:
            self._meter_variant = not self._meter_variant
            suffix = 'a' if self._meter_variant else 'b'
            self.meter.style(f'--pv-meter-animation:pv-learning-meter-{suffix};--pv-meter-duration:{timeout}s')
        self.band.set_visibility(True)
        self._cancel_expiry()
        self._deadline = self._clock() + timeout if timeout else None
        if timeout:
            self._expiry = asyncio.get_running_loop().call_later(timeout, self._expire)
        return True

    def show_undo(self, undo_js, on_undo, props: str) -> bool:
        """判定直後の「判定しました／取り消す」。理由2択が生きている間は出さない。"""
        if self.reason_active or self.heavy:
            return False

        if self.undo_button is None:
            with self._undo_row:
                ui.label('判定しました').classes('pv-subtitle').style('font-size:11.5px;')
                self.undo_button = self._chip('取り消す', testid='learning-toast-undo',
                                             on_click=self._undo_clicked, js_handler=undo_js)
        self._undo_action = on_undo
        self.undo_button.props(props)
        return self._show(None, timeout=UNDO_SECONDS, kind='undo')

    def _undo_clicked(self, event) -> None:
        if self.kind == 'undo':
            self._undo_action(event)

    def set_undo_props(self, props: str) -> None:
        if self.kind == 'undo' and self.undo_button is not None:
            self.undo_button.props(props)

    def show_reason(self, on_pick) -> bool:
        """「合わない」の理由2択。「取り消す」は置かない（元 1750-1755）。"""
        def build():
            ui.label('何が合いませんでしたか？').style('font-size:11.5px;color:var(--pv-text);')
            self._chip('絵柄', testid='learning-reason-style', on_click=lambda e: on_pick('style'))
            self._chip('ジャンル', testid='learning-reason-genre', on_click=lambda e: on_pick('genre'))
        shown = self._show(build, timeout=REASON_SECONDS, kind='reason')
        self.reason_active = bool(shown)
        return shown

    def show_gave_up(self, message: str, recovery: str, *, heavy: bool, key: str | None = None) -> bool:
        """諦めの通知。軽いものは1行で自動消滅、重いものは2行で「閉じる」まで残る。"""
        if not heavy:
            def build_light():
                ui.icon('info_outline').style('font-size:15px;color:var(--pv-muted);')
                ui.label(message).style('font-size:11.5px;color:var(--pv-text);')
                ui.label(recovery).style('font-size:11.5px;color:var(--pv-muted);')
            return self._show(build_light, timeout=4.0, kind='light')

        def build_heavy():
            with ui.element('div').style('display:flex;flex-direction:column;gap:6px;width:100%;'):
                with ui.element('div').style('display:flex;align-items:flex-start;gap:8px;'):
                    ui.icon('warning_amber').style('font-size:15px;color:var(--pv-text);')
                    ui.label(message).props('data-testid=learning-toast-message').style(
                        'font-size:11.5px;color:var(--pv-text);flex:1 1 0;min-width:0;')
                with ui.element('div').style('display:flex;align-items:center;gap:8px;'):
                    ui.label(recovery).props('data-testid=learning-toast-recovery').style(
                        'font-size:11px;color:var(--pv-muted);flex:1 1 0;min-width:0;')
                    self._chip('閉じる', testid='learning-toast-close', on_click=lambda e: self.hide(force=True))
        return self._show(build_heavy, timeout=0, heavy=True, key=key, kind='heavy')

    def hide(self, force: bool = False) -> bool:
        """帯が消える唯一の経路。重い知らせは `force`（「閉じる」）以外では消えない（元 1654-1656）。"""
        if self.heavy and not force:
            return False
        self.heavy = False
        self.heavy_key = None
        self.reason_active = False
        self.kind = None
        self._undo_action = None
        self._deadline = None
        self._cancel_expiry()
        self.row.clear()
        self._undo_row.set_visibility(False)
        self.band.set_visibility(False)
        return True

    def clear_heavy(self, key: str) -> None:
        """その知らせの原因が解消したとき（移植版の再試行が成功した等）に、嘘を残さないため外す。"""
        if self.heavy and self.heavy_key == key:
            self.hide(force=True)

    def tick(self) -> None:
        if self._deadline is not None and self._clock() >= self._deadline:
            with self.band.client:
                self.hide()

    def _expire(self) -> None:
        self._expiry = None
        self.tick()

    def _cancel_expiry(self) -> None:
        if self._expiry is not None:
            self._expiry.cancel()
            self._expiry = None

    def close(self) -> None:
        self._deadline = None
        self._cancel_expiry()
