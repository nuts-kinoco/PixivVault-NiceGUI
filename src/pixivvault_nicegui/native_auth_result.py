"""Bounded native authentication diagnostics; never includes remote text."""
from dataclasses import dataclass


@dataclass(frozen=True)
class NativeAuthResult:
    code: str
    status_code: int | None = None

    @property
    def ok(self):
        return self.code == 'verified'

    @property
    def message(self):
        return {
            'verified': '認証を確認できました。Cookieと保存先を保存し、再起動すると利用できます。',
            'cookie_unreadable': 'Cookieを読み込めません。選択したファイルの形式・有効期限・読取権限を確認してください。通信は行っていません。',
            'owner_absent': '共有STOPの初期設定が完了していません。アプリを再起動してください。認証通信は行っていません。',
            'local_unavailable': 'STOP保護または設定の保存状態を確認できません。記録を保持したまま設定画面で確認してください。Pixivの認証失敗ではありません。',
            'uncertain': '前回の通信結果が不明なため停止しています。共有GETの復旧画面で記録を確認してください。再送していません。',
            'busy': '別の通信または設定処理が実行中です。完了を待ってから確認してください。',
            'stop_rechecked': '通信記録を確認できました。設定画面から認証を確認できます。通常画面を開くにはアプリを再起動してください。',
            'profile_changed': '起動後に設定が変わりました。新しい設定を反映するにはアプリを再起動してください。',
            'stopped': '既存のSTOP記録により通信を停止しています。停止理由を確認してください。Cookieの再選択では解除されません。',
            'remote_denied': 'Pixivが認証確認の通信を拒否しました。追加通信を止めています。ブラウザーで認証状態を確認してください。',
            'identity_unverified': 'Pixivの応答からログイン状態を確認できません。ブラウザーで認証・ログイン要求を確認してください。',
            'rate_limited': 'Pixivから429が返されました。通信を止めて待機しています。時間を置いて状態を確認してください。',
            'communication_failed': '通信を完了できませんでした。接続状態を確認してください。認証拒否かどうかは未確認です。',
            'closed': '終了処理中のため認証確認を中止しました。',
        }.get(self.code, '認証確認の結果を取得できませんでした。')
