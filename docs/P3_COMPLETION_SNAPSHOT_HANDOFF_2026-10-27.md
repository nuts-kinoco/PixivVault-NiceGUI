# P3 completion-snapshot: 回帰fixture前提補正（未実行）

## 対象と原因

固定216 `S:/Projects/PixivVault-NiceGUI-p3-binding-closure-20261027`、集合SHA `43cd535c737ea6cb3928119b626d5f7162903dfc60e8457ea5aa7020f1ae2eea` から別候補へ分離。
新候補 `S:/Projects/PixivVault-NiceGUI-p3-completion-snapshot-20261027`、branch `phase11/p3-completion-snapshot-20261027`、HEAD `2cf4a7a7286dd728e8df8125094498a3622babba`。書込み担当は既存PV Codexタスクのみ。

CoSの影響回帰は227 PASS / 9 FAIL / 1 SKIP（237件、128.24秒）。9件とも `tests/test_p3_binding_boundaries.py` の58 / 83 / 131行で受付snapshotを保持したまま、59 / 84 / 132行の `ex.root / first.result` が `None` と連結してTypeError。元の拒否・保護assertには未到達。
`JobSnapshot` は frozen dataclass、`wait_state`（`tests/test_export_completion_history.py:23`）は条件待機後にJobServiceから新しいsnapshotを返す。3か所でこの戻り値を `first` に受け取り、完了jobのresultを使用する。固定sleep、mock、シム、例外抑制、待機延長、製品変更はない。

## 維持する原受入

- v1退避完了manifestの不正証拠拒否4条件：source-only / bad-path / bad-sha / duplicate-source。元のValueError、operation数不増、破損bytes保全を保持。
- v1完了記録と編集可能projection3条件：none / edited / deleted。再起動後の同一job再受付、operation数不増、編集bytes・削除状態の保全を保持。
- autoZIP2条件：正規完了manifestの再利用 / 不正archive名拒否。再出力しないこと、operation数不増、元archive bytes不変を保持。
- 同ファイルの不正target、作者行隔離、TXT/frames direct相対参照metadataの既存検査は変更しない。

## 原票（固定216を読取り、複製しない）

`artifacts/cos-p3-216-20261027/`：

- `logs/binding-closure-impact.xml` SHA `a3030d87e6a4af3e6e74d06805a8b84fa828d360ff89ed90e6b0005731a99357`
- `P3_COS_216_VERIFICATION.json` SHA `5f1a589a686100cebff3e6cc49ee39fdb7e82daa58d1dcab37dbf6cccb7e935d`
- `P3_COS_216_HANDOFF.md` SHA `c12337ecbee978360e86b144114093bccc2fe2f80ddcbe512378a93c0eba8a98`

上記3件は実読/hash一致。新native binding31件、作者journal隔離6件、212原本8件、作者GUI4/4・保存GUI4/4は216のCoS結果。変更後の新しいPASSには置換しない。製品・toolsの全ファイル対応は `final/product-equivalence.json` に個別SHAを記録する。GUI等の既存証拠を継承する判断は検証担当/親担当が行う。

## CoS最小再確認（Codexでは未実行）

1. 所有writer終了、HEAD/branchと `final/manifest.json` のdirty全ファイル/hash、固定216と旧7候補不変を照合。既存サーバーや他候補へ書き込まない。
2. 既存のCoS用S環境・隔離fixture領域を用い、候補cwdから次を実行する。新規導入や権限変更は不要。元のdeep S path条件を短縮しない。

```powershell
Set-Location -LiteralPath 'S:/Projects/PixivVault-NiceGUI-p3-completion-snapshot-20261027'
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe' -B -m pytest tests/test_p3_binding_boundaries.py -q --basetemp artifacts/p3-completion-snapshot-20261027/cos-owned-r1/tmp --junitxml artifacts/p3-completion-snapshot-20261027/cos-owned-r1/binding-boundaries.xml
```

3. 同ファイル15ケース（ソース上の予定数、未collect）が対象。元失敗9条件がmanifest検査・v1再起動・拒否/編集保護・operation数・archive bytesの後続assertまで到達すること。残り6条件も保持。失敗時はtest名・最初の失敗assert・原票SHAを残し、期待緩和や再送で隠さない。
4. 実行前後の全候補hashと所有process終了を確認する。製品とtoolsは固定216と全hash同一のため、今回新たなGUI/native再実行を必要条件として増やさない。未達の独立再レビュー・元受入は別途保持。

## 範囲と残件

変更はテスト1ファイルの3代入、PROGRESS追記、本引継ぎだけ。試験/製品import/構文検査/build/GUI/nativeは未実行。自己点検は原票・ソース・差分・bytes hashの静的照合であり、独立監査ではない。
既存の同一作品新世代projection更新、旧logical identity欠損、v1退避bindingの証明限界、診断総容量方針、ZIP更新・作者改名・物理隔離の未採用契約は変更しない。元データ/OS設定/依存/main/公開は変更しない。P3全完/P4移行は宣言しない。
最終識別値・旧候補保全・所有process・書込み終了は専用artifact `final/` を参照。
