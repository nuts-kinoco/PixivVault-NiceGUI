"""Value-free, per-operation request retry progress on every application tab."""
from nicegui import ui


def retry_status_text(records):
    messages=[]
    for record in records:
        phase=record['phase'];attempt=record['attempt'];limit=record['limit'];status=record['status']
        reason=f'HTTP {status}' if status is not None else '通信エラー'
        if phase=='waiting':
            message=f'{reason}のため再試行待機中: 試行 {attempt}/{limit}・残り約{record["remaining_seconds"]}秒（待機設定 {record["wait_seconds"]:g}秒）'
            if status==429:
                message += '。今回の429は設定した総試行回数（初回を含む）の範囲で再試行します。共有STOP履歴には記録しません。'
            messages.append(message)
        elif phase=='requesting' and attempt>1:
            messages.append(f'取得を再試行中: 試行 {attempt}/{limit}')
        elif phase=='stopped' and status==429:
            cause=record.get('stop_cause')
            if cause=='durable_gate':
                messages.append(f'HTTP 429 の永続STOPにより取得を停止しました。試行 {attempt}/{limit}')
                continue
            if cause=='shared_limit' or cause is None and attempt<limit:
                message=(f'今回のHTTP 429: この操作の試行 {attempt}/{limit}（この回は送信前に停止）。'
                         '別の取得処理が同じ再試行ポリシーで429の試行上限に達したため、共有停止しました。'
                         'この操作自身は試行上限に達していません。')
            elif cause=='own_limit':
                message=(f'今回のHTTP 429: この操作の試行 {attempt}/{limit}。'
                         'この操作自身が429の試行上限に達したため、共有停止しました。')
            else:
                message=(f'今回のHTTP 429: この操作の試行表示 {attempt}/{limit}。共有ポリシーの429上限停止が記録されています。'
                         'この記録には停止原因の処理が残っていないため、この操作自身か別の取得処理かは特定できません。')
            messages.append(message+'このポリシーを共有する取得をこの起動中は停止しています。共有STOP履歴には記録しません。'
                            'アプリを終了して起動し直した後、取得を明示的にやり直してください。'
                            '再起動後も過去の永続STOP履歴は残ります。自動再送は行いません。')
        elif phase in ('stopped','failed'):
            messages.append(f'{reason}: 試行 {attempt}/{limit}で取得を終了しました。')
        elif phase=='cancelled':
            messages.append(f'取得または待機を中止しました（制限時間・停止状態を確認してください）。試行 {attempt}/{limit}')
    return '\n'.join(messages[-3:])


class RequestStatusView:
    def __init__(self,policy):
        self.policy=policy
        self.label=ui.label('').classes('whitespace-pre-line').props('role=status data-testid=request-retry-status')
        self.label.set_visibility(False)
        self.timer=ui.timer(.2,self.refresh)

    def refresh(self):
        message=retry_status_text(self.policy.progress_snapshot())
        self.label.set_text(message);self.label.set_visibility(bool(message))

    def close(self):
        self.timer.deactivate()
