# PV（PixivVault NiceGUI 移植）— 実装・検証担当への指示

目的: 元リポ `S:\Projects\PixivVault`（Flet 版）の移植＋上位互換。元が正。デザインの変更・機能の退行は不可。iOS 連携・Web 連携・Shaft 連携タブ・旧保管庫の取込・旧 DB への書き込みは対象外（本人裁定）。
最初に読む: `docs/PV_DEFINITION_OF_DONE_2026-11-17.md`（完了の定義と事前確定した判断）、`docs/FEATURE_LEDGER_2026-11-17.md` と `docs/FEATURE_LEDGER_ADDENDA_2026-11-17.md`（元→移植版の台帳）、`docs/progress_manual.json`（現在地）。

## 絶対に守る（違反は不合格）
- 新規ファイル（作業コピー・ログ・一時・キャッシュ・TEMP/TMP）は S: に置く。C ドライブに作らない。
- 実機プロファイル `S:\Projects\COS-PV433-STARTUP-20261116-r1`、ポート 22386 の実機アプリ、実 Cookie、実 Pixiv へ触れない。試験は隔離データ・偽クライアントのみ。実機の起動・再起動はしない（Claude 側が行う）。
- **python.exe / node などのプロセスを、名前や条件で一括停止しない**（Stop-Process / taskkill / pkill）。他の担当の試験を巻き込む。止めてよいのは、自分が起動した PID だけ。
- 1 つの作業ツリー（git worktree）には 1 書き手。自分に割り当てられた作業ツリー以外を編集しない。`git stash` / `git reset --hard` / `git clean` を使わない。
- Cookie・トークン・固定の開発パスワードを、ログ・記録・出力に出さない。
- 取得停止ゲート（「取得・更新」と同じゲート）を通さない自動通信を作らない。自動実行の機能は既定を自動、設定で止められる。
- 試験の期待値・閾値を緩めて合格にしない。失敗は原因を直す。

## 作業の約束
- 先に失敗するテストを書き、失敗（red）を確認してから実装し、通る（green）まで進める。
- 実行: `S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe -B -m pytest -p required_network_guard`、環境変数 PYTHONPATH に作業ツリーの `src` と `tests`、`-k "not dpapi"`、TEMP/TMP は S: 配下。
- 改行コード（CRLF/LF）は元のファイルに合わせる。Python で読み書きするときは bytes か `newline=''`。`git diff --stat` が実変更だけを示すこと。PowerShell 5.1 向けの .ps1 は ASCII のみ。
- 1 つのまとまりごとに日本語で git commit。コミットメッセージの末尾に `Co-Authored-By` の行を付ける。GitHub へは push しない（ローカル bare のみ）。
- 終了コード 0 や「試験が通った」を、製品の合格と同一視しない。実際に実行した試験と結果、原証拠のパス、残る問題、想定外の発見（逸脱日誌: 発見・証拠・実施した行動・残るリスク）を最終回答に書く。
- データ保全（保存領域・索引・削除・ごみ箱・ジャーナル）、取得停止ゲート、認証・Cookie、並行処理・ロックに触れる差分は、独立検証（Sol）の対象。完了を宣言する前に、その旨を最終回答に明記する。

## 返答形式
1. 原受入の達否 2. 変更したこと 3. 実行した検証と実測結果 4. 未達・依存・必要な判断 5. 原証拠と次の作業
