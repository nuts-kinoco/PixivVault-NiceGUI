# P3-345 U 対象選択／作者設定：実装とCPU局所受入（2026-10-30 JST）

## 所有境界と原仕様

対象は原 `S:/Projects/PixivVault/docs/UI_SWEEP_CHECKLIST_2026-09-03.md` の U-3/U-4/U-5/U-6/U-13/U-14、及び `docs/P3_343_REMAINING_85_FUNCTIONAL_EVIDENCE_2026-10-30.md` の U01/U03/U04/U05/U06/U10/U11。現在の `managed_download_view` と既存 `author_media_view`／`author_media`／`save_exports` を接続した。

本ワーカー変更は以下6本のみ。`services.py` は prime 所有で、`followed_download_authors(owner)` が返す `AuthorTarget` の `is_favorite: bool|None`、`follow_order: int|None` を prime が別途結線した。`author_media.py`、`save_exports.py`、`shell.py`、原 Flet／DB／画像／Cookie は編集・実操作していない。

| 原要求 | 実装契約 |
|---|---|
| U01 ブックマーク／フォロー作者切替 | ダウンロード入口にモードボタンと `data-target-mode`、それぞれの独立パネル。切替時にepochを更新し、表示外の候補は選択状態を保持。 |
| U03 作者昇順／降順 | フォロー登録順・名前順・作者ID順、昇／降切替。作者一覧の検索と選択集合は別状態。 |
| U04 ☆作者filter | `is_favorite is True` のみ一致。同期・正規元情報が欠ける `None` は非☆と断定せず、フィルタと一括操作を不可にして理由を表示。 |
| U05 全選択／全解除 | ブックマークは表示中の作品候補、フォロー作者は検索／☆条件に合致する**全作者**を対象。画面の48/50件表示窓から選択上限を切り離した。作者選択チップは48件ずつ表示、421作者全件選択をCPUで確認。既存作品受付128件の制約は維持し超過を明示。 |
| U06 ☆作者一括追加 | フォロー一覧全体の ☆True 作者を `AuthorTarget` として一括選択。由来 `id` と `follow` の同一作者重複は先行選択を保持。受付ジョブ作成はプレビュー→明示submitのみ。 |
| U10 作者ごとの媒体 | `AuthorMediaView` で選択作者ごとの媒体設定を既存 CAS (`author_target`／`configure_author_target`) で読込／保存／解除。通常プレビューは `target_type=None` → `author_media.resolve_author_target` を作者単位で評価し、イラスト・うごイラ・小説の正規ownerへ `enqueue_author_media` で受付。今回限りの全作者共通対象指定も維持。 |
| U11 作者ごとのZIP | 選択作者ごとに ZIP継承／有効／無効を既存 CAS (`author_zip_setting`／`configure_author_zip`) で読込／保存。実保存後の `save_exports.py:522-526` がこの設定を参照し、条件を満たす静止画の `auto-zip` へ接続。設定を変えた時は旧差分プレビューを失効。 |

作者選択の上限 `20,000` は読取クライアントの公開／非公開フォロー上限 `10,000×2` に合わせた保護範囲。無制限の原入力は許さない。作者一括で 421 件を選択してもページ窓48件で切り捨てない。媒体差分は全eligibleを選択対象に置き、DOM表示は50件ずつ。実ジョブ受付の総合容量50で途中に失敗した場合、受付済み件数・未受付残件を別々に表示し、同じ operation ID での明示再試行を保持する。受付後の保存成功はジョブ・receiptで別途観測する。

## CPU結果（実行済み）

S専用現worktree `src` を `PYTHONPATH` として、既存 `S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B`、`PYTHONDONTWRITEBYTECODE=1`、`-p no:cacheprovider`、専用 `--basetemp` で実行。GUI・サーバー・ネットワークは起動していない。

- 初回新pure／既存作者DL：12 PASS。`artifacts/p3-345-u-worker12-pytest-r1/tmp`（結果表示のみ）。
- 関連5ファイル（media owner、pins、output、author view、managed view）：40 PASS。`artifacts/p3-345-u-worker12-impact-r2/tmp`。
- 新U＋既存AuthorMediaView：16 PASS。`artifacts/p3-345-u-worker12-targets-r3/results.xml`。421作者全件選択、70媒体中50件受付／残20件、途中1件の応答喪失→同じoperationで継続、クライアントclose後の新規受付遮断を検証。
- 統合関連9ファイル **76 PASS / 0 FAIL**。`artifacts/p3-345-u-worker12-fullcpu-r1/results.xml`。対象は `test_author_target_selection.py`、`test_author_media_view.py`、`test_author_download_view.py`、`test_managed_download_view.py`、`test_author_media.py`、`test_author_media_pins.py`、`test_author_output_connection.py`、`test_followed_target_metadata.py`、`test_save_exports.py`。
- 広範囲試験の初回 `r1` は `--basetemp` 親ディレクトリ未作成による23件 setup ERROR（製品FAILではない）。親を明示作成した `r2` は40PASS。以降の `r3` と統合試験もPASS。

## GUI checker（**準備のみ、実行していない**）

`artifacts/p3-345-u-worker12-checker/check_u_target_browser.py` は既存 `tools/check_p3_residuals_browser.py` の owned loopback Playwright harness と `tools/p3_residuals_fixtures.py` の合成Readerを再利用。構文AST解析のみPASS。primeの実行権／競合しないGUI環境確認が済んでから、S専用隔離環境で次の1回を実行する想定。

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe' -B `
  artifacts/p3-345-u-worker12-checker/check_u_target_browser.py `
  --channel msedge `
  --output artifacts/p3-345-u-worker12-edge-r1
```

checkerの想定受入は2サイズ（1280×720、1920×1080）×初回／再起動。合成2作者（☆1件）で対象切替／名前順／降順／☆絞り込み／☆一括／全解除／全候補選択、作者別媒体・ZIP CAS保存、通常プレビュー、3媒体への実enqueue、managed receipt／novel receipt／ugoira owner完了／実ZIPの物理CRCとSHA、再起動で設定・ジョブ・receipt・ZIP同一性、起動時暗黙GETゼロを検査。既存 harness により原src SHA before/after、外部browser URL遮断、nonceシャットダウン、正常exit、port解放、スクリーンショット・result.jsonを取得する設計。**これらは未実行の予想検査であり、PASS証拠はまだ無い。** 実PIDの子プロセス0件は別途prime側で所有記録と照合すること。

## 独立レビュー・未観測

静的UI結線とCPU合成は完了。ブラウザ実DOM／2解像度／実保存全件の受入、実ZIP生成、再起動、旧Flet操作の画面整合、独立レビューは prime／別担当が担当し、現時点では未検証。既存 `tools/check_author_download_browser.py` は改修前の同時表示レイアウトを前提に `managed-download-author-id` に直接操作するため、新U01モード切替を挟む必要がある（ツールファイルはworker所有外・不変更）。

## このワーカーのコードSHA-256（作業終了前観測）

| Path | SHA256 |
|---|---|
| `src/pixivvault_nicegui/author_download.py` | `8F6FCE31F2782D3C97CE20CD999902933421C2CA580386CD6A1F2285D9BE705C` |
| `src/pixivvault_nicegui/author_target_selection.py` | `6D4BB19D8E72C3E3199B9A4AE22494A266916E4F816210195D16933FC7E89870` |
| `src/pixivvault_nicegui/ui/managed_download_view.py` | `2451EB9C9E4DCC3D7BE6B61FD42DFF588E55DC6B977EC14BF5AE441CA2656D29` |
| `src/pixivvault_nicegui/ui/author_media_view.py` | `B75DB5951F8721D22AA837BCED186C15954C8EFFCCF1772E197720EE4D47CD95` |
| `tests/test_author_target_selection.py` | `1DC9B7DB0271CA1E74B9EB6EB9430B0DEA9CE29830486677B33B04AF925BAB07` |
| `artifacts/p3-345-u-worker12-checker/check_u_target_browser.py` | `97835AB32B2E4BC1C09B18D5C2484BD02E71E63AC14ED36A5DD6CAE323D9006E` |

Git commit・push・clean・reset・stash、実Pixivアクセス、原DB／Cookie／画像操作は実施していない。dirtyな他担当差分は保持。
