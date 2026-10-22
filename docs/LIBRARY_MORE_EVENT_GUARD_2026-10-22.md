# ライブラリ「もっと見る」遅延イベントの防御

2026-10-22。PR #11 マージ後 main `7ad811820bd84f72a018d3e41126af4fa1aa78e4` を基点とする `phase4/library-more-guard` の限定修正。

## 不具合条件と対応

旧作者詳細で押された「もっと見る」のキュー済みイベントが、詳細を閉じて別の作者詳細（または同じ作者の新セッション）を開いた後に届くと、現在表示中の作者のページを意図せず追加できる。従来 `more()` は active / closed のみを確認し、ボタンを作った詳細との同一性を検査しなかった。

`_clear_pages()` で世代オブジェクト `_more_session` を更新し、「もっと見る」ボタンの callback にその世代を束縛する。`more(session)` はセッション不一致なら拒否する。50→98件への正常な追加では `_clear_pages()` を呼ばないため、既存カードとボタンは引き続き有効。プログラム内部からの `more()` は従来と同様に利用できる。

## 限定受入

- 専用worktreeの明示 `PYTHONPATH` で全pytest **360 passed**。旧ボタンの実イベントhandlerを保持し、作者詳細を閉じて再入場後には新しいページを追加できないこと、現ボタンでは正常に追加できること、append中のボタン同一性、reload後の遅延通知拒否を確認。
- MS Edge 実DOM `tools/run_browser_checks.py --channel msedge` は gallery / learning / image races / library の4系統が終了コード0。libraryは1280×720と1920×1080、ページ追加・共有ビューア・戻り・画面状態維持を検査。`source_changed=false`、専用port解放。ignored証拠 `artifacts/library-more-guard-20261022/browser-msedge`。
- `git diff --check` 成功、`pip check` は `No broken requirements found`。
- 実装担当以外による独立コードレビューでは重大blocking指摘なし。補足されたreload/append時の直接イベント検証を追加した。

元PixivVault、実DB、Cookie、実API、本番画像には触れていない。この変更はイベントの誤適用を防ぐ限定単位であり、段階4全体や他DPI/別PCの受入を意味しない。headとマージ後mainのCIは別に確認する。
