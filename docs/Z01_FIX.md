# PV-Z01-FIX

破損を旧Vaultのパスワード不一致として認証成功へ送っていた連鎖を修正した。実機条件1〜4は成立。条件5は診断と実装の不一致として切り分け、統括の最新指示に従い新機能を追加していない。台帳・手元版は変更していない。

## 修正
- `src/pixivvault_nicegui/external_vault.py`: 破損・欠損・スキーマ異常の3コードを定義。保存済み派生鍵での復号・正規化失敗を `unlock-failed` に変換しない。
- `src/pixivvault_nicegui/premium_gate.py`: 認証の旧Vault継続経路はパスワード不一致だけに限定。破損時はOFF・確認待ち解除・OFFフェンス保存。confirmでも破損状態を拒否し、起動時の保存鍵復帰でも理由を保持。
- `src/pixivvault_nicegui/ui/premium_gate_view.py`: 破損とパスワード不一致を平易な理由表示へ分離。
- `src/pixivvault_nicegui/ui/external_vault_view.py`: gateの破損理由と手動解錠の失敗理由を再描画で保持。手動経路で破損した場合も借用OFFにする。
- `tests/test_z01_vault_integrity.py`: AES-GCM破損、欠損、異常ペイロード、派生鍵経路、保存鍵復帰、GUI再描画、手動旧Vault解錠、暗号化旧JSONペイロード正規化の13試験を追加。
- `tests/test_external_vault_ui.py`・`tests/test_n45a_f26a_recovery.py`: 意図した日本語の不一致理由を検証するよう各1箇所の期待文言を更新。入力消去・失敗拒否の検証は維持。
- `docs/Z01_FIX.md`: 今回の修正と確認範囲を記録。

## 確認
|条件|結果・回数|証拠|
|---|---|---|
|1 暗号文破損|2/2で理由表示・借用OFF・再描画後も同じ理由。保存選択へ進まずセッション消去。|corrupt-1/2-after-render.ax.txt/png、gui-events.jsonl|
|2 暗号文欠損|2/2で同条件を満たす。生成した vault.enc を .held へ退避した試験。|missing-1/2-after-render.ax.txt/png、gui-events.jsonl|
|3 誤パスワード|1/1で「パスワードが一致しません」・借用OFF・入力消去。|wrong-after-render.ax.txt/png|
|4 正常Vault|解錠1/1、登録1件の状態表示1/1、Z02明示ロック1/1でOFF・Closed・鍵/セッション消去。UID値の直接表示ではなく、既存UIの「登録 1」表示と合成UIDの内部照合。|normal-open / normal-registered-slot / normal-locked のax.txt/png、gui-events.jsonl|
|5 診断の平文JSON初回自動移行|診断条件と実装実態の不一致。候補と元製品の認証・解錠・移行入口を照合した範囲に該当経路なし。新機能は追加せず、統括判断待ち。|CONDITION5.md、condition5-code-evidence.json|
|別枠: 暗号化旧Vaultの明示取込保全|空入力拒否1/1、誤入力拒否1/1（いずれもCURRENTなし）、正常取込1/1・直後Closed1/1、手動解錠1/1、通常終了後の再起動・再解錠1/1。|legacy-*.ax.txt/png、gui-events.jsonl、legacy-phase1-exit.json、legacy-restart-witness.md|

対象回帰164 PASS・0 FAIL・0 SKIP。全回帰1561 PASS・0 FAIL・2 SKIP。SKIPは既存のWindowsシンボリックリンク権限条件（test_managed_downloads.py:252、test_managed_network_stop_gate.py:263）。既存の test_premium_f2_session_keys.py は無修正で5/5 PASS。

元候補の398対象ファイルの内容SHA256は `231ac0e4722332ca449cd5b0c298c7c6af8f8425b9a328c176682f06bfdc14b7`。メタデータを含む400ファイルの開始時・終了時SHA一致。

全試験は合成保管庫・合成通信。実Cookie・実通信・実Vault・原本DB・実設定は使用していない。候補の採用・台帳更新・手元版更新は本工程の範囲外。
