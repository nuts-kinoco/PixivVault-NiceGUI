# P3 全152受入・314起点の追加実装ログ（2026-10-28）

## 基準と判断基準

起点: `S:/Projects/PixivVault-NiceGUI-p3-vault-cleanup-race-20261028` の保存済み候補314。原314 branch `phase11/p3-vault-cleanup-race-20261028`、HEAD `2cf4a7a7286dd728e8df8125094498a3622babba`、保存時集合SHA `d32a1b6a9718c4765572062dfd6a01b8f83fe1be5a1818dbe729899c71ce5afa`。原manifest `artifacts/p3-vault-cleanup-race-20261028/manifest.json` は不変の証跡であり、以下の後続編集を混ぜて314固定SHAと呼ばない。

正式152原ID索引: `docs/P3_FULL_ACCEPTANCE_152_INDEX_2026-10-28.md`。正式の現在認定数は **33完了／43未達／76未確認** から無証拠で加点しない。旧216本文の30/46/76を現在値にしない。D15は振り返り好意行からのローカルDLであり、CSV履歴出力ではない。保存済み表示は原 #57 の状態契約。iPhone/iOS連携のみ除外。元DB・画像・ZIP・Cookie・実アカウントは変更していない。

## 保存済み原候補の回収

- 候補314の接続後始末の試験 `artifacts/p3-vault-cleanup-race-20261028/cos-owned-r1/impact.xml` は **81 PASS**（旧308の80例とD1合成競合1例）。既存real TLS localhost証拠、所有process/port後始末を監査で確認。
- 313のL12は合成容量計測・取消で部分成功。ただし元SW L-1のすべてのケースや314への全適用を証明しない。313のD15は保存済み描分けのGUI fixture不足で未受入だった。

## 新規検証と変更

| 領域 | 実装または追加したGUI操作 | 実際に得られた証拠 | 残る境界 |
| --- | --- | --- | --- |
| Vault close異常 | `SocketFence.require_healthy`・failclosedラッチ、強参照した登録context、閉鎖失敗と失効を直列化。失敗後はDNS/socket生成前に遮断 | `artifacts/p3-314-close-proof/r1/red.xml` 修正前FAIL、`r9/vault.xml` 85 PASS。独立監査は今回P2資源増加封じ込め限定APPROVE | 実OS close失敗、秘密/外部通信、nativeの全系統は未受入 |
| D15 | 振り返り好意行→管理DL、保存済み表示と未保存フィルタ、再起動復元をGUIで照合 | `artifacts/p3-314-d15-review-r3/result.json` 合成Edge 1280/1920 × 初回/再起動 4/4、source_changed=false | error/stopped/Retry・個別の実データ/全UI契約 |
| R05 | おすすめ節の既定順へ明示復元、CAS保存 | `artifacts/p3-314-r05-r08-recommend-r3/result.json` 4/4操作成功 | source_changed=true、最終候補再検証 |
| R08 | 作品非表示の理由 genre/style/なし、保存と取り消し、空一覧 | 同 r3 GUI 4/4操作成功。disabledのUndoを発見・修正 | source_changed=true、作者側の全条件・復帰後再訪 |
| V07/V08 | 画集viewerのマウスホイール・ボタン・キー拡大、pointerパン、4倍上限・ページ変更/closeで復元 | `artifacts/p3-viewer-zoom-pan-20261028/worker2-tests-verified/impact.xml` 117 PASS、合成Edge 1280dark/1920light 各6チェック PASS | 独立レビュー・native/性能/最終候補 |
| L12 | 原条件の合成GUI検査器 `tools/check_p3_l12_full_browser.py` を整備 | `artifacts/p3-capacity-auto-20261028/p3-314-full-l12-r2/result.json` 4/4操作成功。初回各62作者、0B、容量降順、明示50再計測、故障時旧値保持、再起動後新規計測0件 | source_changed=true、固定候補/旧画面全条件 |
| D08 | 傾向パネルの開閉と判定保存成功後の自動閉鎖 | `artifacts/p3-315-d08-trend/r1/impact.xml` 25 PASS、`artifacts/p3-315-d08-theme-recommend-r2/result.json` 合成Edge4/4操作成功 | source_changed=true、原画面全条件 |
| S21 | 暗／明テーマ切替で実CSS背景色が変化・元へ復元するブラウザ条件 | 同 D08 GUI4/4の合成Edge操作成功 | source_changed=true、Windows native |
| S22 | ヘッダーへ `Pixivを開く` の外部リンクを追加 | DOM href/targetのGUI観測は同D08 GUI受入中に実施 | Windows既定ブラウザの実起動は未検証 |
| H01 | 元 `works.last_backup` 由来の直近作者履歴・最大60を移植中 | 担当作業中 | 変更後レビュー・原DB read-only・サムネ/ZIP/空の原条件 |

以上の `source_changed=true` は並列担当が同じ候補の別ファイルを編集したためで、各試験は機能的には成功している。保存済み結果の条件を消したり、失敗した試行をPASSへ書き換えたりしない。所有環境が静止した後に必要最小限のGUI差分受入を再取得する。

## 継続ゴール

Stage 1で全元採用機能を実装し、Stage 2で正式152原IDの実操作を対応づけ、Stage 3でL12/D15/保存・取消・停止と競合を確認。Stage 4で全体UX/p95等のFlet同条件比較・Windows native/DPI/別PC、Stage 5で独立監査とexact-head CI、Stage 6で全152 + 別枠ゲートの全証拠が成立した場合だけGoal宣言。

原本を上書き・削除・移動しない。外部Pixiv書込み、main統合・push・公開・権限変更・課金・実データ削除は既存個別承認境界を維持。現時点でこれらを新たに行っていない。
