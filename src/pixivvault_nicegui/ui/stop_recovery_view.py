"""Offline stop-history guidance and explicit verified migration entry."""
import asyncio
from nicegui import ui

STEPS = (
    '1. 保存先の設定を確認し、以前使った保存元も控えてください。元DB・Cookie・停止履歴を削除しないでください。',
    '2. 下の確認ボタンで、現在の設定にある停止履歴を読み取ります。認証不要・通信なしで使えます。確認はSTOP解除や移行の証明ではありません。',
    '3. 保存元の正式移行が必要な場合は、「正式移行を実行する」で以前のすべての保存元を追加し、内容を確認してから明示的に開始してください。アプリがロック・コピー・照合を行います。正式移行は履歴と所有情報を引き継ぐ操作で、STOP解除ではありません。履歴や所有ファイルを自分で作り直さないでください。',
    '4. 正式移行が完了したらアプリを終了し、起動し直してください。起動時の検証が通るまで認証・取得は無効のままです。記録済みのSTOPは移行・再起動後も維持されます。STOPが記録されている場合は、元データを保持して確認を依頼してください。',
)

RATE_LIMIT_GUIDANCE = (
    '過去のHTTP 429による永続STOP履歴と、今回の429の設定待機・再試行は別です。'
    '現在のGET再試行ポリシーが扱う429は、設定した総試行回数（初回を含む）と待機時間に従い、共有STOP履歴には記録しません。'
    '上限に達すると、同じポリシーを共有する取得をこの起動中は停止します。'
    '再起動後に取得を明示的にやり直せますが、過去の永続STOP履歴は残ります。自動解除・自動再送は行いません。'
)


class StopRecoveryPanel:
    def __init__(self, binding):
        self.binding, self.closed, self.busy = binding, False, False
        self.migration_view = None
        self.help_text = '\n'.join((RATE_LIMIT_GUIDANCE, *STEPS))
        with ui.column().classes('w-full gap-2') as self.root:
            self.entry = ui.button('停止履歴の確認と移行手順を開く', on_click=self.open).props('data-testid=stop-recovery-open')
            with ui.dialog() as self.dialog, ui.card().classes('max-w-2xl'):
                ui.label('通信を再開する前の確認').classes('text-h6')
                ui.label(RATE_LIMIT_GUIDANCE).props('data-testid=stop-recovery-rate-limit-guidance')
                for step in STEPS:
                    ui.label(step)
                self.result = ui.label('まだ確認していません。').props('role=status data-testid=stop-recovery-result')
                self.check_button = ui.button('選択済み保存元の停止履歴を確認する', on_click=self.check).props('data-testid=stop-recovery-check')
                ui.button('正式移行を実行する', on_click=self.open_migration).props('data-testid=stop-recovery-migration')
                ui.button('閉じる', on_click=self.dialog.close)
    def open(self):
        if not self.closed:
            self.dialog.open()
    def open_migration(self):
        if self.closed:return
        if self.migration_view is None:self.migration_view=self.binding.create_migration_view()
        self.migration_view.open()
    async def check(self):
        if self.closed or self.busy:
            return
        self.busy = True
        self.check_button.disable()
        try:
            observations = await asyncio.to_thread(self.binding.inspect_stop_history)
            names = {'native':'起動設定', 'images':'画像保存先', 'novel':'小説保存先', 'ugoira':'うごいら保存先', 'configured':'保存先設定'}
            lines = []
            for item in observations:
                owner = names.get(item.get('owner'), '保存元')
                state = item.get('state')
                if state == 'observed_stopped' and item.get('status_code') in (401,403,429):
                    value = f"停止履歴あり（HTTP {item['status_code']}）"
                    if item['status_code'] == 429:
                        value += '。過去の永続STOP履歴です。今回の429の再試行待機とは別で、正式移行・再起動でも解除されません'
                elif state == 'observed_unblocked':
                    value = 'この保存元の読取時点ではSTOPなし'
                else:
                    value = '履歴を確認できません'
                lines.append(owner + ': ' + value)
            message = ' / '.join(lines) + '。以前の全保存元を網羅した確認ではありません。通信停止・認証無効を維持しています。'
        except Exception:
            message = '停止履歴を確認できません。元データを保持し、通信を停止したまま正式な移行の確認を依頼してください。'
        finally:
            self.busy = False
        if not self.closed:
            self.result.set_text(message)
            self.check_button.enable()
    def close(self):
        self.closed = True
        if self.migration_view is not None:self.migration_view.close()

