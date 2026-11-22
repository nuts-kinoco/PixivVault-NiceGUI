# Dots 用 再確認依頼（第 3 回）— ナビ再編の統合モックの修正確認

使い方: 「--- ここから ---」から「--- ここまで ---」までを Dots に貼る。返信ブロックを Claude Code 監督に貼る。

--- ここから ---

あなたは PixivVault（NiceGUI 版）の UI/UX の独立レビュアーです。前回（第 2 回）あなたは「直してから進める」と判定し、High 2 件（H1 検索で選択が別作品にすり替わる、H2 200% 併発時に完全表示 0 枚）と Medium 2 件（M1 125% の基準、M2 未解消／確認済みの表示）を挙げました。今回は、その修正の確認です。コード・実データ・外部サービスには触れません。

資料（GitHub 公開リポジトリ nuts-kinoco/PixivVault-NiceGUI、ブランチ pv-dev-baseline、コミット e5ab186）:
- 設計書: https://raw.githubusercontent.com/nuts-kinoco/PixivVault-NiceGUI/e5ab186/docs/design/UI_NAV_RESTRUCTURE_20261122.md
- 操作できるモック: https://htmlpreview.github.io/?https://github.com/nuts-kinoco/PixivVault-NiceGUI/blob/e5ab186/docs/design/UI_NAV_RESTRUCTURE_20261122.html
- 検証要約: https://raw.githubusercontent.com/nuts-kinoco/PixivVault-NiceGUI/e5ab186/verify/ui-nav-20261122/r2/OUTCOME.md
- 実測 JSON: https://raw.githubusercontent.com/nuts-kinoco/PixivVault-NiceGUI/e5ab186/verify/ui-nav-20261122/r2/measurements.json （H1 修正前: 同ディレクトリの h1_before.json）
- 監査仕様: https://raw.githubusercontent.com/nuts-kinoco/PixivVault-NiceGUI/e5ab186/docs/UIUX_AUDIT_SPEC_20261122.md

監督の決定: 選択の同一性が確認できないときは選択を解除して一覧の見出しへ戻す／125% の必須合格は通常と併発の畳んだ状態（展開は参考値、しきい値は製品で改修前と比べて決める）／200% は 10 タブの常時表示を必須とせず、狭幅だけ開閉式ナビ。自動処理の表示は「未解消 N」を主、「未確認 a／確認済み b」を補助に変更。

見てほしい点: (1) H1 を前回と同じ手順で再現して直っているか（作品 1・保存済みを選択 → 「pixiv本体も含む」→ 模擬検索 → マイブックマークから Enter で戻る）。(2) H2: 200% の開閉式ナビで、DB 警告・取得停止入口・失敗件数が維持され、完全表示 3 枚（畳み）・学習画像 198.5×148.9 CSS px・四択の到達が実際に成り立つか。展開時 0 枚の残りをどう扱うか（設計書に別案あり）。(3) M1: 125% の 4 状態の実測表と、必須条件の決め方は妥当か。(4) M2: 文言と読み上げの区別。(5) 新しい欠陥。(6) 製品実装に進んでよいか。

してはいけないこと: コード変更、アプリ操作、実データ・外部サービスへのアクセス、秘密情報の記載。「良いと思います」だけの返答。

形式: 総評（3 行以内）、観点ごとの判定、指摘の表（重大度・画面と要素・問題・根拠・提案）、質問。最後に次を出力して終了:

```
[Dots → Claude Code 返信 NAV3]
合言葉: 凪の砂時計-NAV3-4417
実施日時: <開始〜終了>
総評: <進めてよい／直してから進める／方針を見直す と 1 行>
判定: H1=… H2=… M1=… M2=… 新規=… 実装可否=…
High の指摘: <ID と要点>（無ければ「なし」）
主な提案: <5 件以内>
見なかった資料・確認できなかった点: <無ければ「なし」>
```

--- ここまで ---
