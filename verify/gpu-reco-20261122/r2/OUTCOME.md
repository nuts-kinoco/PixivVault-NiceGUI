# GPU 作業 6　第2回レビュー対応

作業ブランチは `gpu-reco`。修正前に `pv-dev-baseline` の `2a78551` を取り込み、マージ `006932a` 上で先行試験を実行した。レビュー正本は `S:/Projects/PV-DEV-20261117/tmp/rx_gpu6.md`。

## 原受入との照合

| Neon の指摘 | 修正と確認 |
| --- | --- |
| High 1　OFF 復帰 | 通常再読込ではビューアを閉じた後の選択と実際の操作だけを保護。閉じた選択の順位は元順に戻る |
| High 2　ON 中の未準備 | 追い出しや別節変更と世代失効を区別。有効な保持画像がある間は、ビューア、保存、理由ダイアログの対象の位置を維持。Undo 後は元順 |
| High 3　管理保存の可視性 | 可視性ガードを通してからキャッシュを観測。投影確定前と保持画像を埋め込む直前に再確認し、失効済み画像と来歴を露出から除去 |
| Medium 4　試験の実効性 | 実 RecommendView が生成した URL をメディアレジストリで解決し、偽 transport の宛先列と件数を比較。冷、温、欠損、追い出し前後、6→12 件の6条件で追加 GET 0。モデル不一致試験は、適用すれば先頭が変わる補正を使う |
| Medium 5　UI 上の I/O | モデル・SQLite 検証と露出追記を UI ループ外で実行。描画開始時の削除 epoch を引き継いで追記時に再照合し、組み立て中または投入後の purge をまたぐ旧露出を破棄。追記失敗でも描画継続。公開直前には再試行しないマーカー確認と保存画像の可視性確認が残る |
| Medium 6　信号 A と記録 | 同じリーダー・カタログ・作品メタデータで観測済み表示画像のハッシュを棚の作り直し後も利用。保存原画は確認済みレシートまたは既取得の原画キャッシュで来歴を照合。露出に解析画像の可用性・変種・信号 A/D・世代を追記 |
| Medium 7　展開後の順位 | 初期6件の隣接スコア差を展開後も維持。2件と12件で先頭2件の事前値が同じであることを確認 |
| Low 8～10 | ETag を候補ごとに確認。閉鎖済みビュー／ピンを拒否。露出記録の `_record_event` 依存を外した。実ビューの保護・可視性・記録失敗と来歴の先行失敗を保存 |

所有範囲の製品変更は `recommendation_media.py` と `ui/recommend_view.py`。試験と通信レビュー表を更新した。作業5の公開入口と引数には変更なし。既存 lazy URL を元スロットに維持し、ドラッグ抑制は通信先を追加しないため、新しい AST 指紋を内部メディアとしてレビュー表に登録した。

## 実行と独立確認

S: の専用 venv、`-B -m pytest -p required_network_guard -p no:cacheprovider -k "not dpapi"`、S: の TEMP/TMP と個別 basetemp で単一ファイルずつ実行。

| 原証拠 | 実測 |
| --- | --- |
| [red.log](red.log) | 6 failed、1 passed。保護、管理保存の捕捉、UI上の記録失敗を再現 |
| [lineage-red.log](lineage-red.log) | 2 failed。保存原画の信号 A と不正な画像ハッシュを再現 |
| [publication-red.log](publication-red.log) | 2 failed。組み立て中の purge と公開直前の保存ページ失効を再現 |
| [green.log](green.log) | 専用 38 passed |
| [p6-network.log](p6-network.log) | 通信棚卸し 7 passed |
| [reco-certain.log](reco-certain.log) / [reco-context.log](reco-context.log) | 確実枠 5 passed、操作回帰 9 passed |
| [reco-races.log](reco-races.log) / [remote-variants.log](remote-variants.log) | 競合 10 passed、リモート画像変種 9 passed |
| [managed-media.log](managed-media.log) | 管理画像カタログ 13 passed |
| [SWEEP.md](SWEEP.md) / [cleanup.txt](cleanup.txt) | 修正前の保護全経路の棚卸し。起動した pytest の残存0。再生成可能な一時領域の削除をドライランで確認 |

独立 Sol が、保存ページの公開直前失効と描画中 purge の2経路を追加で指摘した。修正前の再現試験を残してから修正し、最終差分で両方を合格させた。Sol はデータ保全・並行処理の境界を静的に再確認した。

## 未達と次工程

- 製品受入は保留。`D6_QUIET.flag` は終了時まで存在。全体 pytest と Chromium は未実行。解除後、統合候補で Quasar の保持画像、実 lazy GET 同値、ドラッグ抑制を確認する。
- 作業7の設定・起動配線が依存。学習タブだけで観測した画像は、おすすめ側で同一画像の来歴が確認できるまで信号 A に使わない。現行ワーカーにはモデル識別と非待機確認の公開 API がないため、互換境界に内部参照が残る。
- Pixiv 操作が有効で行高が変動する画面はシャドーのみ。実 GPU、実 API、実機プロファイルを用いる製品試験は担当外。
