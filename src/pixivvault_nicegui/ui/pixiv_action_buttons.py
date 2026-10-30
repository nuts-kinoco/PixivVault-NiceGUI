"""Explicit Pixiv actions bound to the displayed work and this client.

Rendering/polling reads only app-owned cached state. Browser events carry an
opaque render token; an old card/page/account never authorizes a new target.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import re
from uuid import uuid4
from weakref import WeakKeyDictionary

from nicegui import ui

from ..pixiv_actions import ActionBusy, ActionError, ActionNeedsReconciliation
from ..pixiv_write_client import WriteIntent

_HUBS = WeakKeyDictionary()
_UNKNOWN = object()


def _id(value):
    return type(value) is str and re.fullmatch(r'[1-9][0-9]{0,19}', value) is not None


@dataclass(frozen=True)
class _Selection:
    token: str
    epoch: int
    account: str | None
    action: str
    target: str
    work_kind: str
    author: str
    author_name: str
    prior_intent: WriteIntent | None = None


class _ClientActions:
    def __init__(self, services, client):
        self.services = services
        self.actions = getattr(services, 'pixiv_actions', None)
        self.client = client
        self.bindings = set()
        self.closed = False
        # Shelf contents can be cleared/rebuilt while this client-wide hub
        # survives. Its timer belongs to the client layout, never a card slot.
        self.timer = None
        if self.actions is not None:
            with client.layout:
                self.timer = ui.timer(.5, self.refresh, active=False)
        client.on_delete(self.close)

    def refresh(self):
        if not self.closed:
            for binding in tuple(self.bindings):
                binding.refresh()

    def add(self, binding):
        self.bindings.add(binding)
        if self.timer is not None:
            self.timer.activate()

    def remove(self, binding):
        self.bindings.discard(binding)
        if not self.bindings and self.timer is not None:
            self.timer.deactivate()

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.timer is not None:
            self.timer.deactivate()
            self.timer.cancel()
        for binding in tuple(self.bindings):
            binding.close()
        self.bindings.clear()
        hubs = _HUBS.get(self.client)
        if hubs is not None:
            hubs.pop(id(self.actions), None)
            if not hubs:
                _HUBS.pop(self.client, None)
        # The app owns in-flight results, writer, reader and following store.


def _client_hub(services, client):
    actions = getattr(services, 'pixiv_actions', None)
    hubs = _HUBS.setdefault(client, {})
    key = id(actions)
    hub = hubs.get(key)
    if hub is None or hub.closed:
        hub = _ClientActions(services, client)
        hubs[key] = hub
    return hub


class PixivActionButtons:
    """Reusable viewer/card controls borrowing one app-wide coordinator."""
    def __init__(self, services, *, bookmark_button=None, follow_button=None,
                 notice=None, prefix='pixiv-action'):
        self.services = services
        self.client = ui.context.client
        self.actions = getattr(services, 'pixiv_actions', None)
        self.hub = _client_hub(services, self.client)
        self.bookmark = bookmark_button if bookmark_button is not None else ui.button(
            'ブクマ', icon='favorite_border', color=None).props('flat dense no-caps')
        self.follow = follow_button if follow_button is not None else ui.button(
            'フォロー', icon='person_add', color=None).props('flat dense no-caps')
        self.notice = notice if notice is not None else ui.label().classes('pv-subtitle')
        self.bookmark.props(f'data-testid={prefix}-bookmark')
        self.follow.props(f'data-testid={prefix}-follow')
        self.notice.props(f'role=status aria-live=polite data-testid={prefix}-status')
        self.notice.set_visibility(False)
        self._closed = False
        self._epoch = 0
        self._target = None
        self._current = lambda: False
        self._busy = None
        self._selections = {}
        self._signatures = {}
        self._last_account = _UNKNOWN
        for kind, button, handler in (
                ('bookmark', self.bookmark, self._bookmark_clicked),
                ('follow', self.follow, self._follow_clicked)):
            button.on('click', handler, js_handler=f"""event => {{
                const button = document.getElementById('{button.html_id}');
                if (button) emit(button.dataset.actionToken);
            }}""")
        if self.hub is not None:
            self.hub.add(self)
        self.refresh()

    def bind(self, work_id, author_id, *, author_name='', work_kind='illust', is_current=None):
        self._epoch += 1
        self._target = (work_id, author_id, author_name, work_kind)
        self._current = is_current if is_current is not None else lambda: True
        self._selections.clear()
        self._signatures.clear()
        self._message('')
        self.refresh()

    def clear(self):
        self._epoch += 1
        self._target = None
        self._selections.clear()
        self._signatures.clear()
        if not self._closed:
            self._message('')
            self.refresh()

    def _visible(self, epoch=None):
        return bool(not self._closed and self._target is not None
                    and not getattr(self.services, 'closing', False)
                    and getattr(self.services, 'pixiv_actions', None) is self.actions
                    and (epoch is None or self._epoch == epoch)
                    and self._current())

    def _message(self, message):
        if self._closed:
            return
        self.notice.set_text(message)
        self.notice.set_visibility(bool(message))

    def refresh(self):
        if self._closed:
            return
        account = self.actions.cached_verified_account_id if self.actions is not None else None
        if self._last_account is not _UNKNOWN and account != self._last_account:
            self._message('')
        self._last_account = account
        target = self._target
        valid = self.actions is not None and self._visible()
        for kind, button in (('bookmark', self.bookmark), ('follow', self.follow)):
            label = 'ブクマ' if kind == 'bookmark' else 'フォロー'
            selection = None
            enabled = False
            state = None
            if valid and target is not None:
                work_id, author_id, author_name, work_kind = target
                metadata_ok = (_id(author_id) and work_kind in ('illust', 'novel')
                               and (kind == 'follow' or _id(work_id)))
                if metadata_ok:
                    entity_kind = 'user' if kind == 'follow' else work_kind
                    target_id = author_id if kind == 'follow' else work_id
                    state = self.actions.state(account, entity_kind, target_id) if account else None
                    pending = bool(state and state.pending)
                    unknown = bool(state and state.unknown)
                    observed = state.observed if state is not None else None
                    prior = None
                    if unknown:
                        outcome = self.actions.result(state.operation_id)
                        if (outcome is not None and outcome.intent.account_id == account
                                and outcome.intent.target_id == target_id):
                            prior = outcome.intent
                        label = 'ブクマ結果を確認' if kind == 'bookmark' else 'フォロー結果を確認'
                        action = 'reconcile'
                    elif kind == 'bookmark':
                        label = 'ブクマ済み' if observed is True else 'ブクマ'
                        action = 'bookmark_add'
                    else:
                        label = 'フォロー解除' if observed is True else 'フォロー'
                        action = 'unfollow' if observed is True else 'follow'
                    if pending or self._busy == kind:
                        label = '確認中…' if pending and unknown else '処理中…'
                    enabled = (not pending and self._busy is None
                               and (not unknown or prior is not None)
                               and not (kind == 'bookmark' and observed is True and not unknown))
                    signature = (self._epoch, account, action, target_id, work_kind,
                                 author_id, author_name, state, enabled)
                    if signature == self._signatures.get(kind):
                        selection = self._selections.get(kind)
                    else:
                        selection = _Selection(uuid4().hex, self._epoch, account, action,
                            target_id, work_kind, author_id, author_name, prior)
                        self._signatures[kind] = signature
            if selection is None:
                self._selections.pop(kind, None)
                self._signatures.pop(kind, None)
                token = ''
            else:
                self._selections[kind] = selection
                token = selection.token
            # Avoid sending identical element updates on the periodic poll.
            if button.text != label:
                button.set_text(label)
            if button.enabled != enabled:
                button.set_enabled(enabled)
            if button.props.get('data-action-token') != token:
                button.props['data-action-token'] = token
                button.update()

    async def _bookmark_clicked(self, event):
        return await self.click('bookmark', event.args)

    async def _follow_clicked(self, event):
        return await self.click('follow', event.args)

    async def click(self, kind, token):
        self.refresh()
        selection = self._selections.get(kind)
        button = self.bookmark if kind == 'bookmark' else self.follow
        if (type(token) is not str or selection is None or token != selection.token
                or not button.enabled or not self._visible(selection.epoch) or self._busy is not None):
            return False

        def cancelled():
            return (not self._visible(selection.epoch)
                    or (selection.account is not None
                        and self.actions.cached_verified_account_id != selection.account))

        self._busy = kind
        self._message('Pixivの状態を確認しています…')
        self.hub.refresh()
        try:
            account = selection.account
            if account is None:
                account = await self.actions.verify_account(cancelled=cancelled)
            if cancelled():
                return False
            if selection.prior_intent is not None:
                outcome = await self.actions.reconcile(selection.prior_intent,
                    author_name=selection.author_name, cancelled=cancelled)
            else:
                request = WriteIntent(uuid4().hex, account, selection.target, selection.action,
                    selection.work_kind, selection.author, 0)
                outcome = await self.actions.execute(request,
                    author_name=selection.author_name, cancelled=cancelled)
            if not cancelled():
                messages = {
                    'confirmed': 'Pixivへの変更を確認しました。',
                    'already': 'Pixivでは既にこの状態でした。',
                    'observed': 'Pixivの現在の状態を確認しました。',
                    'indeterminate': '送信結果を確認できません。結果を確認してから次の操作を行ってください。',
                    'rejected': '変更できませんでした。状態や接続を確認してから再操作できます。',
                    'cancelled': '送信前に操作を取り消しました。',
                }
                message = messages.get(outcome.status, '結果を確認できませんでした。')
                if outcome.local_warning:
                    message += ' ローカルのフォロー記録は更新できませんでした。設定からフォローを同期してください。'
                self._message(message)
            return outcome.status in ('confirmed', 'already', 'observed')
        except ActionNeedsReconciliation:
            if not cancelled():
                self._message('先の送信結果が未確認です。結果を確認してください。')
            return False
        except ActionBusy:
            if not cancelled():
                self._message('この対象は別の画面で処理中です。完了を待ってください。')
            return False
        except ActionError:
            if not cancelled():
                self._message('認証または接続を確認できませんでした。再操作できます。')
            return False
        finally:
            self._busy = None
            if self.hub is not None:
                self.hub.refresh()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._epoch += 1
        self._target = None
        self._selections.clear()
        if self.hub is not None:
            self.hub.remove(self)
