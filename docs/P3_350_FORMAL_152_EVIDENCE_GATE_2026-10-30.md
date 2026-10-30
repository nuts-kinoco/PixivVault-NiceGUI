# P3-350: 正式152操作の証拠結合ゲート（2026-10-30 JST）

## 対象・保全

- `tools/check_formal_152_coverage.py` と `tests/test_formal_152_coverage.py`、CIの `formal-152-acceptance` ジョブを追加。元Flet、DB、画像、ZIP、Cookie、実Pixiv APIには触れない。既存P3-348の製品修正とテストは別担当の未コミット差分として保護。
- 原v8台帳 `docs/P3_FORMAL_152_OPERATIONS_LEDGER_2026-10-29_v8.json` は**書換えず**、固定SHA256 `487065a9a52228266d540e36d3e3b83ea34a38bdde4d1fdb54e275d725e55bb4` で照合。
- v8内の固有参照 `original_reference_bindings_v8[].id` 479個を採用。同じ `reference_id` が何度出現しても代用不可。元参照と原文選択行SHA、実候補の開始条件、操作・観測、実行receiptとそのSHA、現製品・checker SHA、独立レビューの対象proof SHA・原文節の承認を対応付ける。

## 実測・現状

- 実行：`S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -X utf8 -m pytest -q tests/test_formal_152_coverage.py`。合成専用fixtureで漏れ・重複・偽参照・SHA変化・証拠改ざん・レビュー範囲差および**現物v8の未合格状態**などをテスト。**15 PASS / exit 0（12.30秒）**。結果は局所結果のみであり、実GUI/実Pixiv成功には換算しない。
- 実行：`S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -X utf8 tools/check_formal_152_coverage.py --output artifacts/p3-350-formal-gate/current-evidence-bounded-r1.json`。現時点の入力はv8原票のみで、候補の完全な正式証拠manifestは未作成。**152 INCOMPLETE / 0 PASS / 479原参照 / exit 1**となった。JSON証拠はGit追跡外の専用 `artifacts/` 内。最新の本ゲートを再実行するときは別の証跡ファイル名を使う。
- CIでは `formal-152-acceptance` を通常pytest・ブラウザとは独立したジョブにした。**実証拠が揃うまで意図的にRED**とする。偽の空欄埋めや、合成操作を実データの成功として扱うことを禁止。pytestがGREENでも正式受入はREDのまま。

## 今後の候補manifestとレビュー要件

- 既定入力 `artifacts/formal-152/current-candidate.json`。`version=1`、`ledger_sha256`、`source_sha256`、`source_before_sha256`、`source_after_sha256` と、**152原IDを重複なしに含む** `records` が必要。各原IDに `status=PASS`、`run_id`、`start_condition`、実操作と実観測の `steps`、checker path/SHA、全ての `references`、独立reviewが必要。
- 各参照はv8固有 `binding_id`、元選択行SHA、原期待の逐語内容または独立復元した期待、観測、`verdict`、検証可能なJSON receiptの相対path/SHAに紐づく。receiptには同一run ID、実行前後source SHA、checker SHA、操作event、原ID・binding ID別 assertion 結果を収める。
- 独立review JSONには、実装とは別の担当者、現ソースSHA、run ID、対象原ID、承認した **binding ID別receipt SHA**、元選択行SHA、承認した期待本文SHAと原条件の網羅確認を記録する。これを実装者の自己申告だけで済ませない。
- 原v8の `literal_expectation` が空の参照が233あり、全479参照で `full_original_scope_reconciled=false`。これらは元Fletソース・元実画面・原チェックリストを独立して突き合わせ、曖昧さや仕様変更案を分けてから承認する。v8本体を結果で書き換えない。
- **現在の検査は証拠形式と相互整合性を検査するもの。** 書き込み可能なJSONの真偽や実GUI操作の発生自体を暗号学的には証明できない。最終製品の完成認定には、実装者とは別の担当が原本receipt・GUI・ネイティブ・実Cookie・性能・CIを直接監査する独立ゲートがなお必要。

## 未完了の機能受入

- 最初の候補D01は、手動取得・在庫0表示・再取得の原操作を、同じ条件で実際に動かすGUI証拠が不足。合成単体の成功は正式全条件を満たさない。
- 152件と479参照はすべて現在版の再認定待ち。歴史的集計33完了/43未達/76未確認は維持。既存UI機能が現在のソースに実装されている場合も、原ソースとの差分や現候補の実動作を独立に確認してから認定する。
- P3-348 P1/P2修正の全ゲート、実Cookie、Windows onedir実起動、Flet/NiceGUI同条件p50/p95/メモリ、原本before/after SHA、CI head一致、独立最終レビューは引き続き未完。main統合・製品完成宣言は禁止。
