"""右クリックメニューから使う、明示操作だけの Pixiv 書き込み・保存キュー積み込み。

どれもユーザーがメニュー項目を押した 1 回につき 1 件だけ動く。Pixiv への書き込みは
既存の ApplicationServices.pixiv_actions（検証済みアカウント・取得停止ゲート・状態台帳）を
そのまま通し、ここでは追加の通信経路を作らない。「ブックマークする」は追加専用で、
既に登録済みなら何も送らない（解除はビューアのブクマ導線だけ）。
"""
from __future__ import annotations

import re
from collections.abc import Callable
from uuid import uuid4

from ..pixiv_actions import ActionBusy, ActionError, ActionNeedsReconciliation
from ..pixiv_write_client import WriteIntent


def _id(value) -> bool:
    return type(value) is str and re.fullmatch(r'[1-9][0-9]{0,19}', value) is not None


class CardActions:
    """1 つの画面が持つ、カード用の明示操作。`say` は結果文言の出力先。"""

    def __init__(self, services, say: Callable[[str], None], is_current: Callable[[], bool]):
        self.services = services
        self._say = say
        self._current = is_current
        self._busy: set[tuple[str, str]] = set()
        self._save_operations: dict[str, str] = {}

    # 現在の画面が生きているときだけ文言を出す。
    def _notify(self, message: str) -> None:
        if self._current():
            self._say(message)

    @property
    def actions(self):
        return getattr(self.services, 'pixiv_actions', None)

    def can_write(self) -> bool:
        return self.actions is not None

    def can_enqueue(self) -> bool:
        return (getattr(self.services, 'managed_downloads', None) is not None
                and callable(getattr(self.services, 'save_explicit_work', None)))

    async def bookmark(self, work_id: str, author_id: str, author_name: str = '') -> bool:
        return await self._write('bookmark', work_id, author_id, author_name)

    async def follow(self, author_id: str, author_name: str = '') -> bool:
        return await self._write('follow', author_id, author_id, author_name)

    async def _write(self, kind: str, target: str, author_id: str, author_name: str) -> bool:
        actions = self.actions
        noun = 'ブックマーク' if kind == 'bookmark' else 'フォロー'
        if actions is None or not _id(target) or not _id(author_id):
            self._notify(f'{noun}に失敗しました。')
            return False
        key = (kind, target)
        if key in self._busy:
            return False
        self._busy.add(key)
        entity = 'illust' if kind == 'bookmark' else 'user'

        def cancelled():
            return not self._current() or bool(getattr(self.services, 'closing', False))

        try:
            account = actions.cached_verified_account_id
            state = actions.state(account, entity, target) if account else None
            if state is not None and state.pending:
                self._notify('この対象は別の画面で処理中です。完了を待ってください。')
                return False
            if state is not None and state.unknown:
                self._notify('先の送信結果が未確認です。ビューアで結果を確認してください。')
                return False
            if state is not None and state.observed is True:
                self._notify(f'Pixivでは既に{noun}済みです。')
                return True
            self._notify('Pixivの状態を確認しています…')
            if account is None:
                account = await actions.verify_account(cancelled=cancelled)
            if cancelled():
                return False
            intent = WriteIntent(uuid4().hex, account, target,
                                 'bookmark_add' if kind == 'bookmark' else 'follow',
                                 'illust', author_id, 0)
            outcome = await actions.execute(intent, author_name=author_name, cancelled=cancelled)
            messages = {
                'confirmed': f'{noun}しました。',
                'already': f'Pixivでは既に{noun}済みです。',
                'observed': 'Pixivの現在の状態を確認しました。',
                'indeterminate': '送信結果を確認できません。結果を確認してから次の操作を行ってください。',
                'rejected': f'{noun}に失敗しました。',
                'cancelled': '送信前に操作を取り消しました。',
            }
            self._notify(messages.get(outcome.status, '結果を確認できませんでした。'))
            return outcome.status in ('confirmed', 'already', 'observed')
        except ActionNeedsReconciliation:
            self._notify('先の送信結果が未確認です。ビューアで結果を確認してください。')
        except ActionBusy:
            self._notify('この対象は別の画面で処理中です。完了を待ってください。')
        except ActionError:
            self._notify('認証または接続を確認できませんでした。再操作できます。')
        finally:
            self._busy.discard(key)
        return False

    async def enqueue(self, work_id: str) -> bool:
        """保存キューへ 1 件積む（既存の明示保存。通信は受理された作業の中で行う）。"""
        if not self.can_enqueue():
            self._notify('保存の準備ができていないため、キューへ積めません。')
            return False
        key = ('queue', work_id)
        if key in self._busy:
            return False
        self._busy.add(key)
        operation = self._save_operations.setdefault(work_id, uuid4().hex)
        try:
            job = await self.services.save_explicit_work(work_id, operation, is_current=self._current)
            if job is not None:
                self._notify('ダウンロードキューへ積みました。')
                return True
        except Exception:
            self._notify('保存受付を確認できません。同じ操作で再確認できます。キューも確認してください。')
        finally:
            self._busy.discard(key)
        return False
