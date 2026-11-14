"""Value-free, per-operation request retry progress on every application tab."""
from nicegui import ui


def retry_status_text(records):
    messages=[]
    for record in records:
        phase=record['phase'];attempt=record['attempt'];limit=record['limit'];status=record['status']
        reason=f'HTTP {status}' if status is not None else '通信エラー'
        if phase=='waiting':
            messages.append(f'{reason}のため再試行待機中: 試行 {attempt}/{limit}・残り約{record["remaining_seconds"]}秒（待機設定 {record["wait_seconds"]:g}秒）')
        elif phase=='requesting' and attempt>1:
            messages.append(f'取得を再試行中: 試行 {attempt}/{limit}')
        elif phase=='stopped' and status==429:
            messages.append(f'HTTP 429: 試行 {attempt}/{limit}で上限に達したため、この起動中の取得を停止しました。再起動後に再試行できます。')
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
