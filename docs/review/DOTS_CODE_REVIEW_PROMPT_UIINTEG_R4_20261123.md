# Dots 用 コードレビュー依頼 — UI 統合候補（第 4 回、フォーカス管理・通知の共通化）

使い方: 「--- ここから ---」から「--- ここまで ---」までを Dots に貼る。返信ブロックを Claude Code 監督に貼る（統括経由可）。

--- ここから ---

あなたは PixivVault（Flet 版の NiceGUI 移植。Pixiv のイラストの保存・閲覧・ランキング・学習を扱う個人用デスクトップアプリ）の**独立コードレビュアー**です。読み取り専用（コード変更・実行・外部アクセス・秘密情報の記載はしません）。

対象: GitHub 公開リポジトリ nuts-kinoco/PixivVault-NiceGUI のブランチ `review/ui-integ-r4-20261123`（コミット 7573ce86。src／tests／tools と検証記録 3 つだけの軽量ブランチ）。
- 差分（基点 pv-dev-baseline との比較）: https://github.com/nuts-kinoco/PixivVault-NiceGUI/compare/pv-dev-baseline...review/ui-integ-r4-20261123
- 自己申告と対応表: https://raw.githubusercontent.com/nuts-kinoco/PixivVault-NiceGUI/7573ce86/verify/uiinteg-20261122/r4/OUTCOME.md ・ REVIEW.md ・ SWEEP.md（同じディレクトリ）
- ファイルは https://raw.githubusercontent.com/nuts-kinoco/PixivVault-NiceGUI/7573ce86/<パス> で読める。

(注: 第 3 回のプロンプトを出していた場合は、この第 4 回に置き換えてください。)

背景: 5 つの UI 修正枝と取得停止通知の修正を統合した候補。前回の独立レビュー（別の AI）が統合不可（High 2・Medium 5）と判定し、その対応が入った第 4 回。前回（第 3 回、あなたの判定: 条件付きで統合可、Medium 4・Low 1）の指摘: M01 focus_recovery.js（ウィンドウが非アクティブな間に消えた操作を復帰時に拾わない）、M02 management_dialog.py（遅延復帰が移動済みフォーカスを上書きし得る）、M03 request_status_view.py（待機と混在した非待機の状態変化がライブ通知から抜ける）、M04 native_profile_view.py／request_status_view.py（閉じた設定の監視が継続）、L01 focus_recovery.js（Tab 順の期待値が DOM の tabIndex 由来）。第 4 回は **M01・M02・L01 を修正**した（復帰の実行時点でフォーカスを再評価する共通部品、仕様で固定した Tab 順）。**M03・M04 は意図的に未対応**（取得停止の通知を別枝で再設計中で、同じファイルのため、その枝の取り込み後に直す）。その点は指摘せず、M01・M02・L01 が直っているか、新しい欠陥がないかを見てください。
それ以前の指摘: (H1) 設定ダイアログの初期フォーカスが見出し（tabindex=-1）のとき Shift+Tab で背景へ抜ける (H2) 取得停止通知の「閉じる」を押した後フォーカスが失われる (M) 生の ui.notify の取りこぼし 7 箇所／他のダイアログにフォーカストラップ未適用／重要停止の通知枠からの押し出し／非モーダルの復帰先検証がない／試験が実装の写し。

見てほしい点（コードを読んで、根拠つきで）:
1. 前回の H1・H2・Medium が本当に直っているか（再現シナリオごとに、該当コードと試験を読む）。
2. ダイアログのフォーカス管理の共通部品（27 入口）: 取りこぼし、Tab 順の誤り、フォーカス喪失、手書きの重複の残り、JS の境界条件（stops の外にフォーカスがあるとき、非表示・無効の要素、複数ダイアログの入れ子、閉じた後の復帰先が消えている場合）。
3. notify_action への統一: 取りこぼし、role（alert／status）の妥当性、data-testid の重複。
4. 試験: 実装の写しになっていないか、実ブラウザーで検証すべきものを単体で済ませていないか。
5. 取得停止ゲート・通信コードに変更がないこと（managed_network_stop_*、request_policy の送信制御、pixiv_*client）。※ request_status_view の通知の「消える条件」には別途再設計が進行中の既知の穴 3 件（共有ロックの書き込み拒否、同一ポーリング内の再発、別ゲートの確認済み引継ぎ）があり、対象外。
6. 新しい欠陥（退行・競合・例外・アクセシビリティ）。

形式: 総評（3 行以内）、High／Medium／Low の指摘の表（ファイル:行、再現シナリオ、根拠、提案）、統合可否（可／条件付き／不可）。確認できない点は推測と明記。最後に次を出力して終了:

```
[Dots → Claude Code 返信 UIINTEG-R4]
合言葉: 雨上がりの栞-UI4-8036
実施日時: <開始〜終了>
総評: <統合可／条件付き／不可 と 1 行>
High の指摘: <ID と要点>（無ければ「なし」）
Medium の指摘: <ID と要点>（無ければ「なし」）
見なかったファイル・確認できなかった点: <無ければ「なし」>
```

--- ここまで ---
