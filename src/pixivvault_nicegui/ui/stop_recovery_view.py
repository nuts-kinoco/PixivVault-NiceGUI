"""Offline stop-history guidance and explicit verified migration entry."""
import asyncio
from nicegui import ui

STEPS = (
    '1. 保存先の設定を確認し、以前使った保存元も控えてください。元DB・Cookie・停止履歴を削除しないでください。',
    '2. 下の確認ボタンで、現在の設定にある停止履歴を読み取ります。認証不要・通信なしで使えます。確認はSTOP解除や移行の証明ではありません。',
    '3. 「正式移行を実行する」で以前のすべての保存元を追加し、内容を確認してから明示的に開始してください。アプリがロック・コピー・照合を行います。履歴や所有ファイルを自分で作り直さないでください。',
    '4. 正式移行が完了したらアプリを終了し、起動し直してください。起動時の検証が通るまで認証・取得は無効のままです。',
)


class StopRecoveryPanel:
    def __init__(self, binding):
        self.binding, self.closed, self.busy = binding, False, False
        self.migration_view = None
        self.help_text = '\n'.join(STEPS)
        with ui.column().classes('w-full gap-2') as self.root:
            self.entry = ui.button('停止履歴の確認と移行手順を開く', on_click=self.open).props('data-testid=stop-recovery-open')
            with ui.dialog() as self.dialog, ui.card().classes('max-w-2xl'):
                ui.label('通信を再開する前の確認').classes('text-h6')
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

