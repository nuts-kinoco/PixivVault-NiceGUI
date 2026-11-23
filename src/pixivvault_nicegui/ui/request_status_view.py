"""Value-free, per-operation request retry progress on every application tab."""
import asyncio

from nicegui import ui

from ..request_policy import is_pinned_notice, is_rate_stop_notice
from .action_controls import secondary_button, status_label


def _displayed_records(records, acknowledged):
    messages = [record for record in records
                if record['phase'] != 'requesting' or record['attempt'] > 1]

    def priority(record):
        if is_pinned_notice(record):
            return 2 if record.get('notice_id') in acknowledged else 3
        return int(is_rate_stop_notice(record))

    ranked = sorted(enumerate(messages), key=lambda entry: (priority(entry[1]), entry[0]), reverse=True)
    ordered = sorted(ranked[:3], key=lambda entry: (-priority(entry[1]), entry[0]))
    visible = [record for _, record in ordered]
    overflow = [record for _, record in sorted(ranked[3:])]
    return visible, overflow


def retry_status_text(records, *, acknowledged=(), limit=3):
    messages=[]
    for record in records:
        if is_pinned_notice(record) and record.get('notice_id') in acknowledged:
            if record.get('stop_cause') == 'durable_gate':
                messages.append(f'確認済み　通信停止中（HTTP {record["status"]}）')
            elif record.get('stop_cause') == 'gate_busy':
                messages.append('確認済み　同時に実行中の処理があり、取得を見送りました。')
            elif record.get('gate_problem') == 'uncertain':
                messages.append('確認済み　前の通信結果は不明です。')
            else:
                messages.append('確認済み　通信の状態を確認できません。' + _storage_authority_text(record))
            continue
        phase=record['phase'];attempt=record['attempt'];attempt_limit=record['limit'];status=record['status']
        reason=f'HTTP {status}' if status is not None else '通信エラー'
        if phase=='waiting':
            message=f'{reason}のため再試行待機中: 試行 {attempt}/{attempt_limit}・残り約{record["remaining_seconds"]}秒（待機設定 {record["wait_seconds"]:g}秒）'
            if status==429:
                message += '。今回の429は、初回を含む設定回数まで再試行します。再起動後に残る停止として記録しません。'
            messages.append(message)
        elif phase=='requesting' and attempt>1:
            messages.append(f'取得を再試行中: 試行 {attempt}/{attempt_limit}')
        elif phase=='stopped' and record.get('stop_cause')=='durable_gate':
            guidance = {
                401: 'ログイン情報を確認してください。',
                403: 'アクセスが拒否されました。ログイン情報とアクセス権を確認してください。',
                429: 'アクセスが制限されました。取得停止の理由を確認してください。',
            }.get(status, '取得停止の理由を確認してください。')
            messages.append('通信を止めています。' + guidance)
        elif record.get('stop_cause') == 'gate_busy':
            messages.append('別の通信が実行中か確認と競合したため、取得できる状態を確認できません。')
        elif phase=='stopped' and record.get('stop_cause')=='gate_unverified':
            subject = '前の通信結果' if record.get('gate_problem') == 'uncertain' else '通信の状態'
            messages.append(f'{subject}を確認できないため、取得を止めています。'
                            '取得停止の理由を確認してください。' + _storage_authority_text(record))
        elif phase=='stopped' and status==429:
            cause=record.get('stop_cause')
            if cause=='shared_limit' or cause is None and attempt<attempt_limit:
                message=(f'今回のHTTP 429: この操作の試行 {attempt}/{attempt_limit}（この回は送信前に停止）。'
                         '別の取得処理が429の試行上限に達したため、通信を止めています。'
                         'この操作自身は試行上限に達していません。')
            elif cause=='cleanup_429':
                message=(f'HTTP 429 の応答を受けました。試行 {attempt}/{attempt_limit}。'
                         '通信の終了を確認できなかったため、再試行を中止しました。')
            elif cause=='shared_cleanup_429':
                message=(f'試行 {attempt}/{attempt_limit} は送信前に停止しました。'
                         '別の取得処理が HTTP 429 の応答後に終了を確認できなかったため、通信を止めています。')
            elif cause=='own_limit':
                message=(f'今回のHTTP 429: この操作の試行 {attempt}/{attempt_limit}。'
                         'この操作自身が429の試行上限に達したため、通信を止めています。')
            else:
                message=(f'今回のHTTP 429: この操作の試行表示 {attempt}/{attempt_limit}。429の試行上限に達したため、通信を止めています。'
                         'この記録には停止原因の処理が残っていないため、この操作自身か別の取得処理かは特定できません。')
            messages.append(message+'同じ通信の仕組みを使う取得は、この起動中は停止しています。'
                            '今回の停止は再起動後に残る停止として記録しません。'
                            'アプリを起動し直した後、必要な取得を手動でやり直してください。'
                            '再起動後も、過去に記録した通信停止は残ります。自動再送は行いません。')
        elif phase in ('stopped','failed'):
            messages.append(f'{reason}: 試行 {attempt}/{attempt_limit}で取得を終了しました。')
        elif phase=='cancelled':
            messages.append('取得または待機を中止しました。必要な場合は、もう一度お試しください。')
    return '\n'.join(messages if limit is None else messages[-limit:])


def _storage_authority_text(record):
    return {('local',): '（この端末側）', ('shared',): '（共有側）'}.get(record.get('gate_owners'), '')


class RequestStatusView:
    def __init__(self,policy):
        self.policy = policy
        self._closed = False
        self._dismissed = set()
        self._acknowledged = {}
        self._shown_important = set()
        self._shown_pinned = {}
        self._dismiss_revision = 0
        self._expanded = False
        self._confirming = False
        with ui.element('div').classes('w-full') as self.root:
            self.label = status_label(testid='request-retry-status', classes='pv-subtitle pv-request-status')
            self.more_button = secondary_button('', self.toggle_more, testid='request-retry-more')
            self.details = status_label(testid='request-retry-details', classes='pv-subtitle pv-request-status')
            self.confirm_button = secondary_button('取得停止を再確認', self.confirm_gate,
                                                   testid='request-gate-confirm')
            self.confirm_status = status_label(testid='request-gate-confirm-result')
            self.confirm_status.set_visibility(False)
            self.unrecheckable_status = status_label(testid='request-gate-confirm-unavailable')
            self.unrecheckable_status.set_text('停止は維持中です。確認に使える取得経路がないため、ここから再確認できません。')
            self.dismiss_button = secondary_button('閉じる', testid='request-retry-dismiss')
            self.dismiss_button.props['aria-label'] = '取得の通知を閉じる'
            self.dismiss_button.on(
                'click', self._dismiss_rendered,
                js_handler=f"() => emit({{revision:document.getElementById('{self.dismiss_button.html_id}')?.dataset.noticeRevision}})",
            )
        self.refresh()
        self.timer = ui.timer(.2, self.refresh)
        self._gate_timer = (ui.timer(2, self.recheck_gate)
                            if hasattr(policy, 'should_watch_gate') else None)

    async def recheck_gate(self):
        if not self._closed and self.policy.should_watch_gate():
            await asyncio.to_thread(self.policy.recheck_gate_notices)
        if not self._closed:
            self.refresh()

    async def confirm_gate(self):
        if self._closed or self._confirming or not self.policy.can_confirm_gate_notices():
            return
        self._confirming = True
        self.confirm_button.set_enabled(False)
        self.confirm_status.set_text('取得できる状態か確認しています…')
        self.confirm_status.set_visibility(True)
        try:
            cleared = await asyncio.to_thread(self.policy.confirm_gate_notices)
            if not self._closed:
                self.confirm_status.set_visibility(not cleared)
                if not cleared:
                    self.confirm_status.set_text('取得停止の原因が解消したことを確認できませんでした。')
        finally:
            self._confirming = False
            if not self._closed:
                self.confirm_button.set_enabled(True)
                self.refresh()

    def refresh(self):
        records = self.policy.progress_snapshot()
        current_ids = {record.get('notice_id') for record in records}
        self._dismissed.intersection_update(current_ids)
        owners = {record['notice_id']: frozenset(record.get('gate_owners', ()))
                  for record in records if 'notice_id' in record and is_pinned_notice(record)}
        self._acknowledged = {key: confirmed for key, confirmed in self._acknowledged.items()
                              if owners.get(key) == confirmed}
        records = [record for record in records if record.get('notice_id') not in self._dismissed]
        displayed, overflow = _displayed_records(records, self._acknowledged)
        self._expanded = self._expanded and bool(overflow)
        visible = displayed + (overflow if self._expanded else [])
        important = [record for record in visible
                     if is_pinned_notice(record) or record['phase'] in ('stopped', 'failed')]
        shown_important = {record['notice_id'] for record in important
                           if 'notice_id' in record and record['notice_id'] not in self._acknowledged}
        shown_pinned = {record['notice_id']: owners[record['notice_id']] for record in important
                        if 'notice_id' in record and is_pinned_notice(record)}
        if (shown_important, shown_pinned) != (self._shown_important, self._shown_pinned):
            self._dismiss_revision += 1
            self.dismiss_button.props['data-notice-revision'] = str(self._dismiss_revision)
        self._shown_important, self._shown_pinned = shown_important, shown_pinned
        message = retry_status_text(displayed, acknowledged=self._acknowledged)
        tone = 'pv-alert-banner' if important or any(map(is_pinned_notice, overflow)) else 'pv-warning-banner'
        self.label.classes(remove='pv-alert-banner pv-warning-banner', add=tone)
        self.label.set_text(message)
        self.label.set_visibility(bool(message))
        pins = sum(is_pinned_notice(record) for record in overflow)
        summary = f'ほか {len(overflow)} 件' + (f'（停止 {pins} 件）' if pins else '')
        self.more_button.set_text('明細を閉じる' if self._expanded else summary)
        self.more_button.props['aria-expanded'] = str(self._expanded).lower()
        self.more_button.set_visibility(bool(overflow))
        self.details.set_text(retry_status_text(overflow, acknowledged=self._acknowledged, limit=None)
                              if self._expanded else '')
        self.details.set_visibility(self._expanded)
        self.dismiss_button.set_visibility(bool(self._shown_important))
        check = getattr(self.policy, 'can_confirm_gate_notices', None)
        can_confirm = check is not None and check()
        self.confirm_button.set_visibility(can_confirm)
        if not can_confirm:
            self.confirm_status.set_visibility(False)
        self.unrecheckable_status.set_visibility(
            not can_confirm and any(is_pinned_notice(record) or is_rate_stop_notice(record) for record in records))
        self.root.set_visibility(bool(message))

    def toggle_more(self):
        self._expanded = not self._expanded
        self.refresh()

    def _dismiss_rendered(self, event):
        self.refresh()
        if event.args == {'revision': str(self._dismiss_revision)}:
            self.dismiss()

    def dismiss(self):
        self._acknowledged.update((key, owners) for key, owners in self._shown_pinned.items()
                                  if key in self._shown_important)
        self._dismissed.update(self._shown_important - self._shown_pinned.keys())
        self.refresh()

    def close(self):
        self._closed = True
        self.timer.deactivate()
        if self._gate_timer is not None:
            self._gate_timer.deactivate()
