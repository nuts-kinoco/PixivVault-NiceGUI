"""ランキング（世間・自分）のカード右クリックと「出さない一覧」の管理。

元 ranking_tab.py:632-790（右クリック 5 項目）と 2505-2580（管理ボタン・一覧）に対応する。
「出さない」はおすすめと同じ非表示データ（hidden_works.py）を使う。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from nicegui import ui

from ..hidden_works import HideUnavailable, read_hidden, set_hidden
from ..viewer_metadata import pixiv_url
from .card_actions import CardActions
from .card_context_menu import MenuEntry, build_context_menu, open_from_keyboard

HIDE_ERROR = '出さない設定を保存できませんでした。再読込してから再試行してください。'
HIDE_UNAVAILABLE = '出さない設定の保存先が未指定のため、変更できません。'


class RankingCardMenus:
    """RankingView / PersonalRankingView 共通。__init__ を通らない派生でも動くよう遅延初期化する。"""

    # ---- 状態 --------------------------------------------------------
    def _hm(self):
        state = self.__dict__.get('_hide_state')
        if state is None:
            state = self.__dict__['_hide_state'] = SimpleNamespace(
                hidden=frozenset(), writable=False, loaded=False, undo=None, busy=False,
                titles={}, actions=None, task=None)
        return state

    def hidden_work_ids(self) -> frozenset[str]:
        return self._hm().hidden

    def _card_actions(self) -> CardActions:
        state = self._hm()
        if state.actions is None:
            state.actions = CardActions(self.services, self._say_action,
                                        lambda: self.active and not self.closed)
        return state.actions

    def _say_action(self, message: str) -> None:
        label = self.__dict__.get('action_status')
        if label is not None and not label.is_deleted:
            label.set_text(message)
            label.set_visibility(bool(message))
        else:
            self.notice.set_text(message)

    # ---- 画面部品 ----------------------------------------------------
    def _build_hide_manager(self) -> None:
        """ツールバー右端の管理ボタン（元の PopupMenuButton＋バッジ）。ツールバーの `with` 内で呼ぶ。"""
        ui.space()
        self.manage_button = ui.button(icon='tune', color=None).props(
            'flat round dense aria-label=出さない一覧の管理 data-testid=ranking-hide-manage')
        with self.manage_button:
            self.manage_badge = ui.badge('0').props('floating data-testid=ranking-hide-badge')
            self.manage_badge.set_visibility(False)
            ui.tooltip('出さない一覧の管理')
            with ui.menu().props('data-testid=ranking-hide-manage-menu'):
                self.manage_item = ui.menu_item(on_click=self.open_hidden_overlay).props(
                    'data-testid=ranking-hide-manage-open')
                with self.manage_item:
                    self.manage_label = ui.item_section('出さない一覧（0件）')

    def _build_hide_status(self) -> None:
        """通知行の下に、右クリック操作の結果と「元に戻す」を置く。"""
        with ui.row().classes('items-center gap-2'):
            self.action_status = ui.label('').props('role=status aria-live=polite data-testid=ranking-action-status')
            self.action_status.set_visibility(False)
            self.undo_button = ui.button('元に戻す', on_click=self.undo_last_hide).props(
                'flat dense no-caps data-testid=ranking-hide-undo')
            self.undo_button.set_visibility(False)
        with ui.dialog().props('data-testid=ranking-hidden-dialog') as self.hidden_dialog:
            with ui.card().classes('pv-hidden-card').style('min-width:min(520px,90vw)'):
                with ui.row().classes('items-center w-full'):
                    ui.label('出さない一覧').classes('pv-view-title')
                    ui.space()
                    ui.button(icon='close', on_click=self.hidden_dialog.close, color=None).props(
                        'flat round dense aria-label=閉じる data-testid=ranking-hidden-close')
                self.hidden_empty = ui.label('出さない登録された作品はありません。').classes('pv-subtitle').props(
                    'data-testid=ranking-hidden-empty')
                self.hidden_rows = ui.column().classes('gap-1 w-full')

    def _update_manage_button(self) -> None:
        manage = self.__dict__.get('manage_button')
        if manage is None or self.closed:
            return
        count = len(self._hm().hidden)
        self.manage_label.set_text(f'出さない一覧（{count}件）')
        self.manage_badge.set_text(str(count))
        self.manage_badge.set_visibility(bool(count))

    def _titles(self) -> dict[str, str]:
        titles = dict(self._hm().titles)
        page = getattr(self, 'page', None)
        if page is None:
            items = ()
        elif hasattr(page, 'items'):
            items = page.items
        else:
            # PersonalPage keeps rows as {'item': ...} mappings (no `.items`).
            items = tuple(row['item'] for row in getattr(page, 'rows', ()) if isinstance(row, dict) and 'item' in row)
        for item in items:
            artwork = item.record.as_artwork()
            titles.setdefault(artwork.work_id, artwork.title)
        return titles

    def _rebuild_hidden_list(self) -> None:
        rows = self.__dict__.get('hidden_rows')
        if rows is None or self.closed:
            return
        titles = self._titles()
        hidden = sorted(self._hm().hidden, key=int)
        rows.clear()
        self.hidden_empty.set_visibility(not hidden)
        writable = self._hm().writable
        with rows:
            for work_id in hidden:
                with ui.row().classes('items-center gap-2 w-full').props(f'data-testid=ranking-hidden-row-{work_id}'):
                    ui.label(titles.get(work_id) or f'（タイトル不明・{work_id}）').classes('pv-card-title')
                    ui.space()
                    undo = ui.button('取り消す', on_click=lambda w=work_id: self.undo_hide(w)).props(
                        f'flat dense no-caps data-testid=ranking-hidden-undo-{work_id}')
                    undo.set_enabled(writable and not self._hm().busy)

    def _refresh_hide_ui(self) -> None:
        self._update_manage_button()
        self._rebuild_hidden_list()

    # ---- 読み込み・描画連動 ------------------------------------------
    def _load_hidden_now(self) -> None:
        """保存済みの出さない集合を読む（通信なし）。読めなければ前の集合を保つ。"""
        state = self._hm()
        try:
            state.hidden, state.writable = read_hidden(self.services)
            state.loaded = True
        except Exception:
            pass

    async def refresh_hidden(self) -> bool:
        """他タブ（おすすめ等）での変更を取り込む。集合が変わったら True。"""
        before = self._hm().hidden
        await asyncio.to_thread(self._load_hidden_now)
        if self.closed:
            return False
        changed = self._hm().hidden != before
        self._refresh_hide_ui()
        if changed:
            self._after_hidden_change()
        return changed

    def _after_hidden_change(self) -> None:
        """出さない集合が変わったあとの再描画。自分のランキングは文脈の再読込を経由する。"""
        self._refresh_hide_ui()
        if getattr(self, 'page', None) is not None:
            self.render()

    # ---- 出さない・元に戻す ------------------------------------------
    def _usable(self) -> bool:
        return self.active and not self.closed

    async def hide_work(self, work_id: str, title: str = '') -> bool:
        state = self._hm()
        if not self._usable() or state.busy:
            return False
        state.busy = True
        state.titles[work_id] = title
        try:
            hidden = await asyncio.to_thread(set_hidden, self.services, work_id, True)
        except HideUnavailable:
            self._say_action(HIDE_UNAVAILABLE)
            return False
        except Exception:
            self._say_action(HIDE_ERROR)
            return False
        finally:
            state.busy = False
        if self.closed:
            return False
        state.hidden, state.writable, state.loaded = hidden, True, True
        state.undo = work_id
        self._say_action('次回から表示しません。')
        self.undo_button.set_visibility(True)
        self._after_hidden_change()
        return True

    def adopt_hidden(self, work_id: str, title: str, hidden) -> None:
        """ビューアの右クリックで出さないにした結果を取り込み、一覧を描き直す。"""
        if self.closed:
            return
        state = self._hm()
        state.hidden, state.writable, state.loaded = frozenset(hidden), True, True
        state.titles[work_id] = title
        state.undo = work_id
        self._say_action('次回から表示しません。')
        self.undo_button.set_visibility(True)
        self._after_hidden_change()

    async def undo_hide(self, work_id: str) -> bool:
        state = self._hm()
        if not self._usable() or state.busy:
            return False
        state.busy = True
        try:
            hidden = await asyncio.to_thread(set_hidden, self.services, work_id, False)
        except HideUnavailable:
            self._say_action(HIDE_UNAVAILABLE)
            return False
        except Exception:
            self._say_action(HIDE_ERROR)
            return False
        finally:
            state.busy = False
        if self.closed:
            return False
        state.hidden, state.writable = hidden, True
        if state.undo == work_id:
            state.undo = None
            self.undo_button.set_visibility(False)
        self._say_action('元に戻しました。')
        self._after_hidden_change()
        return True

    async def undo_last_hide(self) -> bool:
        work_id = self._hm().undo
        return await self.undo_hide(work_id) if work_id else False

    def open_hidden_overlay(self) -> None:
        if not self._usable() or self.__dict__.get('hidden_dialog') is None:
            return
        self._load_hidden_now()
        self._refresh_hide_ui()
        self.hidden_dialog.open()

    async def boost_author(self, author_id: str, author_name: str) -> bool:
        """この作者をもっと見る。おすすめの「もっと見る」登録と同じ設定へ追加する。"""
        recommendations = getattr(self.services, 'recommendations', None)
        if not self._usable() or recommendations is None or not author_id:
            return False

        def work():
            try:
                state = recommendations.snapshot()
            except Exception:
                state = recommendations.refresh()
            if not state.get('writable'):
                raise HideUnavailable()
            return recommendations.boost(author_id, author_name, True, state['revision'])

        try:
            await asyncio.to_thread(work)
        except HideUnavailable:
            self._say_action('もっと見る登録の保存先が未指定のため、追加できません。')
            return False
        except Exception:
            self._say_action('もっと見るに追加できませんでした。')
            return False
        self._say_action(f'「{author_name or author_id}」をもっと見るに追加しました。')
        return True

    # ---- カードの右クリック ------------------------------------------
    def build_card_menu(self, art, card) -> None:
        """元 ranking_tab.py:763-790 の 5 項目。`with` 内（カードの子）で呼ぶ。"""
        actions = self._card_actions()
        recommendations = getattr(self.services, 'recommendations', None)
        author_id = art.author_id or ''
        url = pixiv_url(art.work_id)
        entries = [
            MenuEntry('bookmark', 'ブックマークする', 'bookmark_add',
                      lambda: self._guarded(actions.bookmark(art.work_id, author_id, art.author)),
                      enabled=actions.can_write() and bool(author_id)),
            MenuEntry('queue', 'ダウンロードキューへ積む', 'download_for_offline',
                      lambda: self._guarded(actions.enqueue(art.work_id)),
                      enabled=actions.can_enqueue()),
            MenuEntry('hide', '出さない', 'visibility_off',
                      lambda: self._guarded(self.hide_work(art.work_id, art.title)),
                      enabled=recommendations is not None),
            MenuEntry('boost', 'この作者をもっと見る', 'person_add_alt',
                      lambda: self._guarded(self.boost_author(author_id, art.author)),
                      enabled=recommendations is not None and bool(author_id)),
            MenuEntry('pixiv', 'pixivで作品ページを開く', 'open_in_new', href=url, enabled=url is not None),
        ]
        menu, _ = build_context_menu(entries, testid=f'ranking-ctx-{art.work_id}')
        open_from_keyboard(card, menu)

    async def _guarded(self, coroutine):
        if not self._usable():
            coroutine.close()
            return None
        return await coroutine
