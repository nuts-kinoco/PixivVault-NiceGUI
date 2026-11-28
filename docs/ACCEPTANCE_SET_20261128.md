# 受入セット案（2026-11-28 追補）

これは依頼の準備案であり製品受入ではない。233 条件と 57 F-ID の原文・手順・期待結果は維持する。元 Flet 比較、状態成立、機能操作、本人操作を別々に判定する。空・読込中・エラーと独立入口を省かない。

## 作業量（条件 ID と機能 ID の件数）

| 区分 | 件数 | 説明 |
|---|---:|---|
| UI/UX 画像で目視する候補 | 99 条件・99 枚 | 撮影時状態、画像 SHA・寸法とロード済みモジュールファイル SHA を確認。外部共有前の秘匿監査と元 Flet 比較は未実施。 |
| UI/UX 実画面で状態を再現し確認 | 134 条件 | 内訳は画像あり状態未成立 82、画像なし 52。下表と状態別手順を使う。 |
| CoS が実画面・隔離で操作する機能 | 53 F-ID | 複数操作・再起動を含む。ID 件数はクリック数ではない。本人作業を含む行は下記参照。 |
| 本人が実施する機能 | 3 F-ID | F-OS-01／02／03。実 OS・通知・拡張等の本人操作。 |
| 本人裁定で対象外 | 1 F-ID | F-EX-01 の iOS・LAN Web・旧 Shaft タブ・旧保管庫取込。現行機能と旧データ読取は残す。 |

上記の機能は混在する枝を持つ。F-ST-03 の案内保存、F-ST-05 の認証・切替、F-LE-04 の実 GPU、F-RE-03 の外部表示、F-SE-03 の削除確定、F-BO-02 の認証・保存、F-SP-03 の移行、F-HI-01／02 の実ファイル・削除などは、CoS の 53 行から本人担当部分を分ける。3 件は機能行の専任数であり、本人の操作総数ではない。原依頼の本人・隔離・通信上限と H4 の承認境界に従う。

原資料: `S:\Projects\PV-DEV-20261117\verify\cos_20261127_UIUX\FORMAL_CONDITION_LEDGER.csv`、`FINAL_REPORT.md`、`S:\Projects\PV-DEV-20261117\verify\cos_20261124_FULL\COS_FULL_INTERIM_20261124.md`、`S:\Projects\PV-DEV-20261117\work\docs\cos\COS_FULL_USECASE_PROMPT_20261124.md`。前回 PASS や部分観測を今回の候補へ自動継承しない。詳細な 22 組の状態別手順と発見は [状態の残件](H3_STATE_DISPOSITIONS_20261128.md)。

## 入口ごとにまとめる CoS の作業

同じ入口の操作を一回の作業束にし、その中で各状態・寸法・テーマは別に検査する。画像候補以外は追加の状態再現が必要。

| 入口・画面 | 原条件数 | 画像で目視する候補 | 実画面で再現 |
|---|---:|---:|---:|
| download | 16 | 8 | 8 |
| queue | 16 | 8 | 8 |
| failed | 16 | 8 | 8 |
| dedup | 16 | 16 | 0 |
| learning | 16 | 4 | 12 |
| ranking | 16 | 16 | 0 |
| personal | 16 | 11 | 5 |
| library | 16 | 8 | 8 |
| bookmarks | 16 | 8 | 8 |
| recommend | 16 | 12 | 4 |
| history | 12 | 0 | 12 |
| view-history | 12 | 0 | 12 |
| settings | 8 | 0 | 8 |
| extension | 4 | 0 | 4 |
| premium-recovery | 4 | 0 | 4 |
| automation | 5 | 0 | 5 |
| scheduler | 4 | 0 | 4 |
| metadata | 4 | 0 | 4 |
| request-limit | 4 | 0 | 4 |
| stop-details | 4 | 0 | 4 |
| queue-delete-confirm | 4 | 0 | 4 |
| failed-delete-confirm | 4 | 0 | 4 |
| view-history-delete | 4 | 0 | 4 |

機能操作は F-ID ごとに下の表で 53 行。ダウンロード→キュー→失敗、重複整理、学習、ランキング／カスタム、ライブラリ／ブックマーク、おすすめ、設定／自動処理、停止／履歴、起動／OS を作業束にする。独立した入口・別状態・別の前提を同一の合否に畳まない。

## 画像と条件 ID の対応

1 行の画像で末尾の ID を判定する。サイズ・テーマ・状態が違う条件は束ねない。状態未成立の画像は観察補助であり、その画像だけで合格としない。U-S18 と U-S18-automation は原撮影手順で同じ入口なので同じ画像へ対応する。

U-S16／U-S17 の画像は設定区画の一部のみ。U-S20 と停止詳細の画像は関連する停止画面を撮った参考であり、原条件の入口・状態との一致は未確証。これらは実画面で原条件を確認する。

**共有前の不足:** 合成画像の文字置換はパスや明示ラベル付き ID を対象とするが、重複整理などにはラベルのない合成 ID やファイル名が残る。原撮影依頼の作品 ID 非収録に対する全画像の秘匿確認は未完了であり、この成果物を外部監査用としてそのまま共有しない。旧マスク再現画像も内部専用。

画像基点: `S:\Work\_tmp\PV\h3-20261128\pilot-20261128_184057_609731`。以下のパスは基点からの相対パス。詳細な機械照合結果は `S:\Work\_tmp\PV\h3-20261128\acceptance-233.json`。

| 画像 | 条件 ID | 状態 |
|---|---|---|
| `empty/1280x800-dark-100/download/port.png` | U-S01_1280x800_dark_empty | 成立・目視候補 |
| `normal/1280x800-dark-100/download/port.png` | U-S01_1280x800_dark_normal | 成立・目視候補 |
| `empty/1280x800-light-100/download/port.png` | U-S01_1280x800_light_empty | 成立・目視候補 |
| `normal/1280x800-light-100/download/port.png` | U-S01_1280x800_light_normal | 成立・目視候補 |
| `empty/1920x1080-dark-100/download/port.png` | U-S01_1920x1080_dark_empty | 成立・目視候補 |
| `normal/1920x1080-dark-100/download/port.png` | U-S01_1920x1080_dark_normal | 成立・目視候補 |
| `empty/1920x1080-light-100/download/port.png` | U-S01_1920x1080_light_empty | 成立・目視候補 |
| `normal/1920x1080-light-100/download/port.png` | U-S01_1920x1080_light_normal | 成立・目視候補 |
| `empty/1280x800-dark-100/queue/port.png` | U-S02_1280x800_dark_empty | 成立・目視候補 |
| `normal/1280x800-dark-100/queue/port.png` | U-S02_1280x800_dark_normal | 成立・目視候補 |
| `empty/1280x800-light-100/queue/port.png` | U-S02_1280x800_light_empty | 成立・目視候補 |
| `normal/1280x800-light-100/queue/port.png` | U-S02_1280x800_light_normal | 成立・目視候補 |
| `empty/1920x1080-dark-100/queue/port.png` | U-S02_1920x1080_dark_empty | 成立・目視候補 |
| `normal/1920x1080-dark-100/queue/port.png` | U-S02_1920x1080_dark_normal | 成立・目視候補 |
| `empty/1920x1080-light-100/queue/port.png` | U-S02_1920x1080_light_empty | 成立・目視候補 |
| `normal/1920x1080-light-100/queue/port.png` | U-S02_1920x1080_light_normal | 成立・目視候補 |
| `empty/1280x800-dark-100/failed/port.png` | U-S03_1280x800_dark_empty | 成立・目視候補 |
| `normal/1280x800-dark-100/failed/port.png` | U-S03_1280x800_dark_normal | 成立・目視候補 |
| `empty/1280x800-light-100/failed/port.png` | U-S03_1280x800_light_empty | 成立・目視候補 |
| `normal/1280x800-light-100/failed/port.png` | U-S03_1280x800_light_normal | 成立・目視候補 |
| `empty/1920x1080-dark-100/failed/port.png` | U-S03_1920x1080_dark_empty | 成立・目視候補 |
| `normal/1920x1080-dark-100/failed/port.png` | U-S03_1920x1080_dark_normal | 成立・目視候補 |
| `empty/1920x1080-light-100/failed/port.png` | U-S03_1920x1080_light_empty | 成立・目視候補 |
| `normal/1920x1080-light-100/failed/port.png` | U-S03_1920x1080_light_normal | 成立・目視候補 |
| `empty/1280x800-dark-100/dedup/port.png` | U-S04_1280x800_dark_empty | 成立・目視候補 |
| `error/1280x800-dark-100/dedup/port.png` | U-S04_1280x800_dark_error | 成立・目視候補 |
| `loading/1280x800-dark-100/dedup/port.png` | U-S04_1280x800_dark_loading | 成立・目視候補 |
| `normal/1280x800-dark-100/dedup/port.png` | U-S04_1280x800_dark_normal | 成立・目視候補 |
| `empty/1280x800-light-100/dedup/port.png` | U-S04_1280x800_light_empty | 成立・目視候補 |
| `error/1280x800-light-100/dedup/port.png` | U-S04_1280x800_light_error | 成立・目視候補 |
| `loading/1280x800-light-100/dedup/port.png` | U-S04_1280x800_light_loading | 成立・目視候補 |
| `normal/1280x800-light-100/dedup/port.png` | U-S04_1280x800_light_normal | 成立・目視候補 |
| `empty/1920x1080-dark-100/dedup/port.png` | U-S04_1920x1080_dark_empty | 成立・目視候補 |
| `error/1920x1080-dark-100/dedup/port.png` | U-S04_1920x1080_dark_error | 成立・目視候補 |
| `loading/1920x1080-dark-100/dedup/port.png` | U-S04_1920x1080_dark_loading | 成立・目視候補 |
| `normal/1920x1080-dark-100/dedup/port.png` | U-S04_1920x1080_dark_normal | 成立・目視候補 |
| `empty/1920x1080-light-100/dedup/port.png` | U-S04_1920x1080_light_empty | 成立・目視候補 |
| `error/1920x1080-light-100/dedup/port.png` | U-S04_1920x1080_light_error | 成立・目視候補 |
| `loading/1920x1080-light-100/dedup/port.png` | U-S04_1920x1080_light_loading | 成立・目視候補 |
| `normal/1920x1080-light-100/dedup/port.png` | U-S04_1920x1080_light_normal | 成立・目視候補 |
| `empty/1280x800-dark-100/learning/port.png` | U-S05_1280x800_dark_empty | 未成立・実画面で再現 |
| `normal/1280x800-dark-100/learning/port.png` | U-S05_1280x800_dark_normal | 成立・目視候補 |
| `empty/1280x800-light-100/learning/port.png` | U-S05_1280x800_light_empty | 未成立・実画面で再現 |
| `normal/1280x800-light-100/learning/port.png` | U-S05_1280x800_light_normal | 成立・目視候補 |
| `empty/1920x1080-dark-100/learning/port.png` | U-S05_1920x1080_dark_empty | 未成立・実画面で再現 |
| `normal/1920x1080-dark-100/learning/port.png` | U-S05_1920x1080_dark_normal | 成立・目視候補 |
| `empty/1920x1080-light-100/learning/port.png` | U-S05_1920x1080_light_empty | 未成立・実画面で再現 |
| `normal/1920x1080-light-100/learning/port.png` | U-S05_1920x1080_light_normal | 成立・目視候補 |
| `empty/1280x800-dark-100/ranking/port.png` | U-S06_1280x800_dark_empty | 成立・目視候補 |
| `error/1280x800-dark-100/ranking/port.png` | U-S06_1280x800_dark_error | 成立・目視候補 |
| `loading/1280x800-dark-100/ranking/port.png` | U-S06_1280x800_dark_loading | 成立・目視候補 |
| `normal/1280x800-dark-100/ranking/port.png` | U-S06_1280x800_dark_normal | 成立・目視候補 |
| `empty/1280x800-light-100/ranking/port.png` | U-S06_1280x800_light_empty | 成立・目視候補 |
| `error/1280x800-light-100/ranking/port.png` | U-S06_1280x800_light_error | 成立・目視候補 |
| `loading/1280x800-light-100/ranking/port.png` | U-S06_1280x800_light_loading | 成立・目視候補 |
| `normal/1280x800-light-100/ranking/port.png` | U-S06_1280x800_light_normal | 成立・目視候補 |
| `empty/1920x1080-dark-100/ranking/port.png` | U-S06_1920x1080_dark_empty | 成立・目視候補 |
| `error/1920x1080-dark-100/ranking/port.png` | U-S06_1920x1080_dark_error | 成立・目視候補 |
| `loading/1920x1080-dark-100/ranking/port.png` | U-S06_1920x1080_dark_loading | 成立・目視候補 |
| `normal/1920x1080-dark-100/ranking/port.png` | U-S06_1920x1080_dark_normal | 成立・目視候補 |
| `empty/1920x1080-light-100/ranking/port.png` | U-S06_1920x1080_light_empty | 成立・目視候補 |
| `error/1920x1080-light-100/ranking/port.png` | U-S06_1920x1080_light_error | 成立・目視候補 |
| `loading/1920x1080-light-100/ranking/port.png` | U-S06_1920x1080_light_loading | 成立・目視候補 |
| `normal/1920x1080-light-100/ranking/port.png` | U-S06_1920x1080_light_normal | 成立・目視候補 |
| `empty/1280x800-dark-100/personal/port.png` | U-S07_1280x800_dark_empty | 成立・目視候補 |
| `error/1280x800-dark-100/personal/port.png` | U-S07_1280x800_dark_error | 成立・目視候補 |
| `loading/1280x800-dark-100/personal/port.png` | U-S07_1280x800_dark_loading | 未成立・実画面で再現 |
| `normal/1280x800-dark-100/personal/port.png` | U-S07_1280x800_dark_normal | 成立・目視候補 |
| `empty/1280x800-light-100/personal/port.png` | U-S07_1280x800_light_empty | 成立・目視候補 |
| `error/1280x800-light-100/personal/port.png` | U-S07_1280x800_light_error | 成立・目視候補 |
| `loading/1280x800-light-100/personal/port.png` | U-S07_1280x800_light_loading | 未成立・実画面で再現 |
| `normal/1280x800-light-100/personal/port.png` | U-S07_1280x800_light_normal | 成立・目視候補 |
| `empty/1920x1080-dark-100/personal/port.png` | U-S07_1920x1080_dark_empty | 成立・目視候補 |
| `error/1920x1080-dark-100/personal/port.png` | U-S07_1920x1080_dark_error | 成立・目視候補 |
| `loading/1920x1080-dark-100/personal/port.png` | U-S07_1920x1080_dark_loading | 未成立・実画面で再現 |
| `normal/1920x1080-dark-100/personal/port.png` | U-S07_1920x1080_dark_normal | 未成立・実画面で再現 |
| `empty/1920x1080-light-100/personal/port.png` | U-S07_1920x1080_light_empty | 成立・目視候補 |
| `error/1920x1080-light-100/personal/port.png` | U-S07_1920x1080_light_error | 成立・目視候補 |
| `loading/1920x1080-light-100/personal/port.png` | U-S07_1920x1080_light_loading | 未成立・実画面で再現 |
| `normal/1920x1080-light-100/personal/port.png` | U-S07_1920x1080_light_normal | 成立・目視候補 |
| `empty/1280x800-dark-100/library/port.png` | U-S08_1280x800_dark_empty | 成立・目視候補 |
| `normal/1280x800-dark-100/library/port.png` | U-S08_1280x800_dark_normal | 成立・目視候補 |
| `empty/1280x800-light-100/library/port.png` | U-S08_1280x800_light_empty | 成立・目視候補 |
| `normal/1280x800-light-100/library/port.png` | U-S08_1280x800_light_normal | 成立・目視候補 |
| `empty/1920x1080-dark-100/library/port.png` | U-S08_1920x1080_dark_empty | 成立・目視候補 |
| `normal/1920x1080-dark-100/library/port.png` | U-S08_1920x1080_dark_normal | 成立・目視候補 |
| `empty/1920x1080-light-100/library/port.png` | U-S08_1920x1080_light_empty | 成立・目視候補 |
| `normal/1920x1080-light-100/library/port.png` | U-S08_1920x1080_light_normal | 成立・目視候補 |
| `empty/1280x800-dark-100/bookmarks/port.png` | U-S09_1280x800_dark_empty | 成立・目視候補 |
| `normal/1280x800-dark-100/bookmarks/port.png` | U-S09_1280x800_dark_normal | 成立・目視候補 |
| `empty/1280x800-light-100/bookmarks/port.png` | U-S09_1280x800_light_empty | 成立・目視候補 |
| `normal/1280x800-light-100/bookmarks/port.png` | U-S09_1280x800_light_normal | 成立・目視候補 |
| `empty/1920x1080-dark-100/bookmarks/port.png` | U-S09_1920x1080_dark_empty | 成立・目視候補 |
| `normal/1920x1080-dark-100/bookmarks/port.png` | U-S09_1920x1080_dark_normal | 成立・目視候補 |
| `empty/1920x1080-light-100/bookmarks/port.png` | U-S09_1920x1080_light_empty | 成立・目視候補 |
| `normal/1920x1080-light-100/bookmarks/port.png` | U-S09_1920x1080_light_normal | 成立・目視候補 |
| `empty/1280x800-dark-100/recommend/port.png` | U-S10_1280x800_dark_empty | 未成立・実画面で再現 |
| `error/1280x800-dark-100/recommend/port.png` | U-S10_1280x800_dark_error | 成立・目視候補 |
| `loading/1280x800-dark-100/recommend/port.png` | U-S10_1280x800_dark_loading | 成立・目視候補 |
| `normal/1280x800-dark-100/recommend/port.png` | U-S10_1280x800_dark_normal | 成立・目視候補 |
| `empty/1280x800-light-100/recommend/port.png` | U-S10_1280x800_light_empty | 未成立・実画面で再現 |
| `error/1280x800-light-100/recommend/port.png` | U-S10_1280x800_light_error | 成立・目視候補 |
| `loading/1280x800-light-100/recommend/port.png` | U-S10_1280x800_light_loading | 成立・目視候補 |
| `normal/1280x800-light-100/recommend/port.png` | U-S10_1280x800_light_normal | 成立・目視候補 |
| `empty/1920x1080-dark-100/recommend/port.png` | U-S10_1920x1080_dark_empty | 未成立・実画面で再現 |
| `error/1920x1080-dark-100/recommend/port.png` | U-S10_1920x1080_dark_error | 成立・目視候補 |
| `loading/1920x1080-dark-100/recommend/port.png` | U-S10_1920x1080_dark_loading | 成立・目視候補 |
| `normal/1920x1080-dark-100/recommend/port.png` | U-S10_1920x1080_dark_normal | 成立・目視候補 |
| `empty/1920x1080-light-100/recommend/port.png` | U-S10_1920x1080_light_empty | 未成立・実画面で再現 |
| `error/1920x1080-light-100/recommend/port.png` | U-S10_1920x1080_light_error | 成立・目視候補 |
| `loading/1920x1080-light-100/recommend/port.png` | U-S10_1920x1080_light_loading | 成立・目視候補 |
| `normal/1920x1080-light-100/recommend/port.png` | U-S10_1920x1080_light_normal | 成立・目視候補 |
| `empty/1280x800-dark-100/history/port.png` | U-S11-backup_1280x800_dark_empty | 未成立・実画面で再現 |
| `error/1280x800-dark-100/history/port.png` | U-S11-backup_1280x800_dark_error | 未成立・実画面で再現 |
| `normal/1280x800-dark-100/history/port.png` | U-S11-backup_1280x800_dark_normal | 未成立・実画面で再現 |
| `empty/1280x800-light-100/history/port.png` | U-S11-backup_1280x800_light_empty | 未成立・実画面で再現 |
| `error/1280x800-light-100/history/port.png` | U-S11-backup_1280x800_light_error | 未成立・実画面で再現 |
| `normal/1280x800-light-100/history/port.png` | U-S11-backup_1280x800_light_normal | 未成立・実画面で再現 |
| `empty/1920x1080-dark-100/history/port.png` | U-S11-backup_1920x1080_dark_empty | 未成立・実画面で再現 |
| `error/1920x1080-dark-100/history/port.png` | U-S11-backup_1920x1080_dark_error | 未成立・実画面で再現 |
| `normal/1920x1080-dark-100/history/port.png` | U-S11-backup_1920x1080_dark_normal | 未成立・実画面で再現 |
| `empty/1920x1080-light-100/history/port.png` | U-S11-backup_1920x1080_light_empty | 未成立・実画面で再現 |
| `error/1920x1080-light-100/history/port.png` | U-S11-backup_1920x1080_light_error | 未成立・実画面で再現 |
| `normal/1920x1080-light-100/history/port.png` | U-S11-backup_1920x1080_light_normal | 未成立・実画面で再現 |
| `empty/1280x800-dark-100/view-history/port.png` | U-S12-history_1280x800_dark_empty | 未成立・実画面で再現 |
| `error/1280x800-dark-100/view-history/port.png` | U-S12-history_1280x800_dark_error | 未成立・実画面で再現 |
| `normal/1280x800-dark-100/view-history/port.png` | U-S12-history_1280x800_dark_normal | 未成立・実画面で再現 |
| `empty/1280x800-light-100/view-history/port.png` | U-S12-history_1280x800_light_empty | 未成立・実画面で再現 |
| `error/1280x800-light-100/view-history/port.png` | U-S12-history_1280x800_light_error | 未成立・実画面で再現 |
| `normal/1280x800-light-100/view-history/port.png` | U-S12-history_1280x800_light_normal | 未成立・実画面で再現 |
| `empty/1920x1080-dark-100/view-history/port.png` | U-S12-history_1920x1080_dark_empty | 未成立・実画面で再現 |
| `error/1920x1080-dark-100/view-history/port.png` | U-S12-history_1920x1080_dark_error | 未成立・実画面で再現 |
| `normal/1920x1080-dark-100/view-history/port.png` | U-S12-history_1920x1080_dark_normal | 未成立・実画面で再現 |
| `empty/1920x1080-light-100/view-history/port.png` | U-S12-history_1920x1080_light_empty | 未成立・実画面で再現 |
| `error/1920x1080-light-100/view-history/port.png` | U-S12-history_1920x1080_light_error | 未成立・実画面で再現 |
| `normal/1920x1080-light-100/view-history/port.png` | U-S12-history_1920x1080_light_normal | 未成立・実画面で再現 |
| `error/1280x800-dark-100/settings/port.png` | U-S15-settings_1280x800_dark_error | 未成立・実画面で再現 |
| `normal/1280x800-dark-100/settings/port.png` | U-S15-settings_1280x800_dark_normal | 未成立・実画面で再現 |
| `error/1280x800-light-100/settings/port.png` | U-S15-settings_1280x800_light_error | 未成立・実画面で再現 |
| `normal/1280x800-light-100/settings/port.png` | U-S15-settings_1280x800_light_normal | 未成立・実画面で再現 |
| `error/1920x1080-dark-100/settings/port.png` | U-S15-settings_1920x1080_dark_error | 未成立・実画面で再現 |
| `normal/1920x1080-dark-100/settings/port.png` | U-S15-settings_1920x1080_dark_normal | 未成立・実画面で再現 |
| `error/1920x1080-light-100/settings/port.png` | U-S15-settings_1920x1080_light_error | 未成立・実画面で再現 |
| `normal/1920x1080-light-100/settings/port.png` | U-S15-settings_1920x1080_light_normal | 未成立・実画面で再現 |
| `normal/1280x800-dark-100/extension/port.png` | U-S16-settings_1280x800_dark_normal | 未成立・実画面で再現 |
| `normal/1280x800-light-100/extension/port.png` | U-S16-settings_1280x800_light_normal | 未成立・実画面で再現 |
| `normal/1920x1080-dark-100/extension/port.png` | U-S16-settings_1920x1080_dark_normal | 未成立・実画面で再現 |
| `normal/1920x1080-light-100/extension/port.png` | U-S16-settings_1920x1080_light_normal | 未成立・実画面で再現 |
| `normal/1280x800-dark-100/premium-recovery/port.png` | U-S17-settings_1280x800_dark_normal | 未成立・実画面で再現 |
| `normal/1280x800-light-100/premium-recovery/port.png` | U-S17-settings_1280x800_light_normal | 未成立・実画面で再現 |
| `normal/1920x1080-dark-100/premium-recovery/port.png` | U-S17-settings_1920x1080_dark_normal | 未成立・実画面で再現 |
| `normal/1920x1080-light-100/premium-recovery/port.png` | U-S17-settings_1920x1080_light_normal | 未成立・実画面で再現 |
| `normal/1280x800-dark-100/automation/port.png` | U-S18_1280x800_dark_normal, U-S18-automation_1280x800_dark_normal | 未成立・実画面で再現 |
| `normal/1280x800-light-100/automation/port.png` | U-S18-automation_1280x800_light_normal | 未成立・実画面で再現 |
| `normal/1920x1080-dark-100/automation/port.png` | U-S18-automation_1920x1080_dark_normal | 未成立・実画面で再現 |
| `normal/1920x1080-light-100/automation/port.png` | U-S18-automation_1920x1080_light_normal | 未成立・実画面で再現 |
| `normal/1280x800-dark-100/scheduler/port.png` | U-S18-scheduler_1280x800_dark_normal | 未成立・実画面で再現 |
| `normal/1280x800-light-100/scheduler/port.png` | U-S18-scheduler_1280x800_light_normal | 未成立・実画面で再現 |
| `normal/1920x1080-dark-100/scheduler/port.png` | U-S18-scheduler_1920x1080_dark_normal | 未成立・実画面で再現 |
| `normal/1920x1080-light-100/scheduler/port.png` | U-S18-scheduler_1920x1080_light_normal | 未成立・実画面で再現 |
| `normal/1280x800-dark-100/metadata/port.png` | U-S19-metadata_1280x800_dark_normal | 未成立・実画面で再現 |
| `normal/1280x800-light-100/metadata/port.png` | U-S19-metadata_1280x800_light_normal | 未成立・実画面で再現 |
| `normal/1920x1080-dark-100/metadata/port.png` | U-S19-metadata_1920x1080_dark_normal | 未成立・実画面で再現 |
| `normal/1920x1080-light-100/metadata/port.png` | U-S19-metadata_1920x1080_light_normal | 未成立・実画面で再現 |
| `error/1280x800-dark-100/request-limit/port.png` | U-S20_1280x800_dark_error | 未成立・実画面で再現 |
| `error/1280x800-light-100/request-limit/port.png` | U-S20_1280x800_light_error | 未成立・実画面で再現 |
| `error/1920x1080-dark-100/request-limit/port.png` | U-S20_1920x1080_dark_error | 未成立・実画面で再現 |
| `error/1920x1080-light-100/request-limit/port.png` | U-S20_1920x1080_light_error | 未成立・実画面で再現 |
| `normal/1280x800-dark-100/stop-details/port.png` | U-S20-stop-details_1280x800_dark_normal | 未成立・実画面で再現 |
| `normal/1920x1080-dark-100/stop-details/port.png` | U-S20-stop-details_1920x1080_dark_normal | 未成立・実画面で再現 |
| `normal/1280x800-dark-100/queue-delete-confirm/port.png` | U-S26-bulk-confirm_1280x800_dark_normal | 未成立・実画面で再現 |
| `normal/1280x800-light-100/queue-delete-confirm/port.png` | U-S26-bulk-confirm_1280x800_light_normal | 未成立・実画面で再現 |
| `normal/1920x1080-dark-100/queue-delete-confirm/port.png` | U-S26-bulk-confirm_1920x1080_dark_normal | 未成立・実画面で再現 |
| `normal/1920x1080-light-100/queue-delete-confirm/port.png` | U-S26-bulk-confirm_1920x1080_light_normal | 未成立・実画面で再現 |
| `normal/1280x800-dark-100/failed-delete-confirm/port.png` | U-S27-row-confirm_1280x800_dark_normal | 未成立・実画面で再現 |
| `normal/1280x800-light-100/failed-delete-confirm/port.png` | U-S27-row-confirm_1280x800_light_normal | 未成立・実画面で再現 |
| `normal/1920x1080-dark-100/failed-delete-confirm/port.png` | U-S27-row-confirm_1920x1080_dark_normal | 未成立・実画面で再現 |
| `normal/1920x1080-light-100/failed-delete-confirm/port.png` | U-S27-row-confirm_1920x1080_light_normal | 未成立・実画面で再現 |
| `normal/1280x800-light-100/view-history-delete/port.png` | U-S40-delete-guard_1280x800_light_normal | 未成立・実画面で再現 |
| `normal/1920x1080-light-100/view-history-delete/port.png` | U-S40-delete-guard_1920x1080_light_normal | 未成立・実画面で再現 |

## 画像がない条件

撮影できなかった状態も原条件として保持する。画面別の原因と再現手順は状態の残件表を参照する。

| 条件 ID | 方針 |
|---|---|
| U-S01_1280x800_dark_error | 実画面で状態を再現・確認 |
| U-S01_1280x800_dark_loading | 実画面で状態を再現・確認 |
| U-S01_1280x800_light_error | 実画面で状態を再現・確認 |
| U-S01_1280x800_light_loading | 実画面で状態を再現・確認 |
| U-S01_1920x1080_dark_error | 実画面で状態を再現・確認 |
| U-S01_1920x1080_dark_loading | 実画面で状態を再現・確認 |
| U-S01_1920x1080_light_error | 実画面で状態を再現・確認 |
| U-S01_1920x1080_light_loading | 実画面で状態を再現・確認 |
| U-S02_1280x800_dark_error | 実画面で状態を再現・確認 |
| U-S02_1280x800_dark_loading | 実画面で状態を再現・確認 |
| U-S02_1280x800_light_error | 実画面で状態を再現・確認 |
| U-S02_1280x800_light_loading | 実画面で状態を再現・確認 |
| U-S02_1920x1080_dark_error | 実画面で状態を再現・確認 |
| U-S02_1920x1080_dark_loading | 実画面で状態を再現・確認 |
| U-S02_1920x1080_light_error | 実画面で状態を再現・確認 |
| U-S02_1920x1080_light_loading | 実画面で状態を再現・確認 |
| U-S03_1280x800_dark_error | 実画面で状態を再現・確認 |
| U-S03_1280x800_dark_loading | 実画面で状態を再現・確認 |
| U-S03_1280x800_light_error | 実画面で状態を再現・確認 |
| U-S03_1280x800_light_loading | 実画面で状態を再現・確認 |
| U-S03_1920x1080_dark_error | 実画面で状態を再現・確認 |
| U-S03_1920x1080_dark_loading | 実画面で状態を再現・確認 |
| U-S03_1920x1080_light_error | 実画面で状態を再現・確認 |
| U-S03_1920x1080_light_loading | 実画面で状態を再現・確認 |
| U-S05_1280x800_dark_error | 実画面で状態を再現・確認 |
| U-S05_1280x800_dark_loading | 実画面で状態を再現・確認 |
| U-S05_1280x800_light_error | 実画面で状態を再現・確認 |
| U-S05_1280x800_light_loading | 実画面で状態を再現・確認 |
| U-S05_1920x1080_dark_error | 実画面で状態を再現・確認 |
| U-S05_1920x1080_dark_loading | 実画面で状態を再現・確認 |
| U-S05_1920x1080_light_error | 実画面で状態を再現・確認 |
| U-S05_1920x1080_light_loading | 実画面で状態を再現・確認 |
| U-S08_1280x800_dark_error | 実画面で状態を再現・確認 |
| U-S08_1280x800_dark_loading | 実画面で状態を再現・確認 |
| U-S08_1280x800_light_error | 実画面で状態を再現・確認 |
| U-S08_1280x800_light_loading | 実画面で状態を再現・確認 |
| U-S08_1920x1080_dark_error | 実画面で状態を再現・確認 |
| U-S08_1920x1080_dark_loading | 実画面で状態を再現・確認 |
| U-S08_1920x1080_light_error | 実画面で状態を再現・確認 |
| U-S08_1920x1080_light_loading | 実画面で状態を再現・確認 |
| U-S09_1280x800_dark_error | 実画面で状態を再現・確認 |
| U-S09_1280x800_dark_loading | 実画面で状態を再現・確認 |
| U-S09_1280x800_light_error | 実画面で状態を再現・確認 |
| U-S09_1280x800_light_loading | 実画面で状態を再現・確認 |
| U-S09_1920x1080_dark_error | 実画面で状態を再現・確認 |
| U-S09_1920x1080_dark_loading | 実画面で状態を再現・確認 |
| U-S09_1920x1080_light_error | 実画面で状態を再現・確認 |
| U-S09_1920x1080_light_loading | 実画面で状態を再現・確認 |
| U-S20-stop-details_1280x800_light_normal | 実画面で状態を再現・確認 |
| U-S20-stop-details_1920x1080_light_normal | 実画面で状態を再現・確認 |
| U-S40-delete-guard_1280x800_dark_normal | 実画面で状態を再現・確認 |
| U-S40-delete-guard_1920x1080_dark_normal | 実画面で状態を再現・確認 |

## 機能 ID と担当

同じ機能の観察・隔離操作・本人操作は一つの作業束にする。原手順の順序や安全境界は省かない。

| 機能 ID | 主担当 | 前回の観測 |
|---|---|---|
| F-ST-01 | CoS（本人分岐は本人） | 判定不能（既往部分観測・現起動BLOCKED）：21時以前は本番DB要約と詳細→Escを観測。本人再起動後は22386が起動設定の保護画面、通常起動の開始条件未達 |
| F-ST-02 | CoS（本人分岐は本人） | 判定不能（既往部分観測・現起動BLOCKED）：21時以前はログイン有効、借用ONと10タブを観測。現稼働はconfiguration-requiredでこれらを再証明できない |
| F-ST-03 | CoS（本人分岐は本人） | 判定不能（部分観測）：ダーク→ライト→ダーク復元確認。初回案内の発生条件は未確認 |
| F-ST-04 | CoS（本人分岐は本人） | 判定不能（当該分岐未実施）：隔離・本人・既存自動試験の実証と原期待結果への照合が未完了 |
| F-ST-05 | CoS（本人分岐は本人） | 判定不能（部分観測）：有効ログイン画面のみ観測。期限・別アカウント分岐未確認 |
| F-DL-01 | CoS（本人分岐は本人） | 判定不能（部分観測）：作者一覧426人9頁、名前・数字検索→復元、前後ページ1→2→1、並びメニューと☆フィルタを操作。全並び条件未確認 |
| F-DL-02 | CoS（本人分岐は本人） | 判定不能（部分観測）：表示中全選択→全解除を2巡。作者1行選択でヘッダーaria-checked=mixedを確認し解除。☆保存等未確認 |
| F-DL-03 | CoS（本人分岐は本人） | 判定不能（部分観測）：22709合成作者1件から作品単位詳細、差分取得失敗とキュー追加無効を確認。正常取得・受付は未証明 |
| F-DL-04 | CoS（本人分岐は本人） | 判定不能（部分観測）：22709合成作品ID確認が取得失敗し、誤受付しない表示を観測。保存形式・媒体別実取得は未証明 |
| F-DL-05 | CoS（本人分岐は本人） | 判定不能（当該分岐未実施）：隔離・本人・既存自動試験の実証と原期待結果への照合が未完了 |
| F-DL-06 | CoS（本人分岐は本人） | 判定不能（当該分岐未実施）：隔離・本人・既存自動試験の実証と原期待結果への照合が未完了 |
| F-QU-01 | CoS（本人分岐は本人） | 判定不能（部分観測）：本番ローカル一覧に加え22709合成ジョブを再開→実行中→21/30で一時停止。全状態・再起動未証明 |
| F-QU-02 | CoS（本人分岐は本人） | 判定不能（部分観測）：ログ表示切替と22709合成ジョブ一時停止・再開。行削除・確認取消・永続化未実証 |
| F-FA-01 | CoS（本人分岐は本人） | 判定不能（部分観測）：本番空状態と22705合成失敗17件の分類/検索0件/クリア/ページ境界をGUI操作。全分岐未証明 |
| F-FA-02 | CoS（本人分岐は本人） | 判定不能（部分観測）：22705長文・分類・非数字ID再試行無効。全17件クリアと1件削除の確認入口→それぞれ取消で17/17件保持。CSV入口あり、実出力/削除確定/実再試行は未実証 |
| F-DE-01 | CoS（本人分岐は本人） | 判定不能（部分観測）：本番入口と22709合成スキャン→近似1組→画質選択→確認取消→移動を観測。広域正常系未達 |
| F-DE-02 | CoS（本人分岐は本人） | 判定不能（部分観測・復元未達）：22709合成1枚227B隔離とごみ箱1件を確認。legacy物理移動がmanaged限定復元候補へ出ず復元不可 |
| F-LE-01 | CoS（本人分岐は本人） | 判定不能（部分観測）：本番学習で既判定29件/総候補840、現在画像読み込み失敗と判定ボタン無効を観測。新規判定未実施 |
| F-LE-02 | CoS（本人分岐は本人） | 判定不能（部分観測）：本番振り返りで肯定/否定一覧、検索/絞込/ページ送り入口を観測。判定取消・Undo保存は未実施 |
| F-LE-03 | CoS（本人分岐は本人） | 判定不能（当該分岐未実施）：隔離・本人・既存自動試験の実証と原期待結果への照合が未完了 |
| F-LE-04 | CoS（本人分岐は本人） | 判定不能（当該分岐未実施）：隔離・本人・既存自動試験の実証と原期待結果への照合が未完了 |
| F-RK-01 | CoS（本人分岐は本人） | 判定不能（部分観測）：22701合成429 STOP回復説明と22703合成不正応答の取得失敗表示をGUI確認。実正常取得は未実証 |
| F-RK-02 | CoS（本人分岐は本人） | 判定不能（部分観測）：22703合成ランキング取得失敗と未取得の区別を観測。4期間正常カード等は既存証拠範囲のみ |
| F-RK-03 | CoS（本人分岐は本人） | 判定不能（部分観測）：22709合成ランキングカード右クリックで5入口とブクマdisabledを確認、Esc閉鎖。メニュー確定・非表示復帰・ビューア正常は未実施 |
| F-PR-01 | CoS（本人分岐は本人） | 判定不能（部分観測）：22702合成カスタムランキングの送信結果不明→追加通信停止と保管データ保持説明を確認。正常結果未実証 |
| F-PR-02 | CoS（本人分岐は本人） | 判定不能（部分観測）：22709合成14条件に関連する内側絞り込みパネルの各入口表示と開閉を確認。入力・保存・再取得の結果は未証明 |
| F-LI-01 | CoS（本人分岐は本人） | 判定不能（部分観測）：本番ライブラリ1作者1作品を表示し、合成不一致文字検索0→クリア1に復帰。閲覧・複数選択未証明 |
| F-LI-02 | CoS（本人分岐は本人） | 判定不能（部分観測）：本番媒体切替で小説保存0・うごイラ保存0の空状態を観測。正常媒体保存例・編集未実証 |
| F-BM-01 | CoS（本人分岐は本人） | 判定不能（部分観測）：22704合成空0件と選択DL disabled、同時に取得失敗表示を観測。正常な空取得は未証明 |
| F-BM-02 | CoS（本人分岐は本人） | 判定不能（当該分岐未実施）：隔離・本人・既存自動試験の実証と原期待結果への照合が未完了 |
| F-RE-01 | CoS（本人分岐は本人） | 判定不能（部分観測）：22709合成おすすめ更新成功・カード表示を確認。実借用のカード欠落と保存結果不明は未解消 |
| F-RE-02 | CoS（本人分岐は本人） | 判定不能（部分観測）：22709合成カード3→非表示2→復帰3、理由未選択取消を実証。前結果/再起動保存は未実証 |
| F-RE-03 | CoS（本人分岐は本人） | 判定不能（部分観測）：22709合成出さない一覧の作品1件→復帰して一覧から消滅。旧Flet非空/作者比較と永続性未実証 |
| F-VW-01 | CoS（本人分岐は本人） | 判定不能（部分観測）：22708合成履歴行→画集ビューア（1/1、画像読込失敗）、詳細失敗→Escで詳細/ビューアを段階閉鎖。正常画像・ページ送り未証明 |
| F-VW-02 | CoS（本人分岐は本人） | 判定不能（当該分岐未実施）：隔離・本人・既存自動試験の実証と原期待結果への照合が未完了 |
| F-AU-01 | CoS（本人分岐は本人） | 判定不能（部分観測）：定期・メタデータが取得停止中の要約、最終・次回、ログと詳細入口を確認 |
| F-AU-02 | CoS（本人分岐は本人） | 判定不能（当該分岐未実施）：隔離・本人・既存自動試験の実証と原期待結果への照合が未完了 |
| F-AU-03 | CoS（本人分岐は本人） | 判定不能（当該分岐未実施）：隔離・本人・既存自動試験の実証と原期待結果への照合が未完了 |
| F-SE-01 | CoS（本人分岐は本人） | 判定不能（部分観測）：設定グループ・現在値・一部無効表示を読取。全キーとTab順の全量照合は未達 |
| F-SE-02 | CoS（本人分岐は本人） | 判定不能（部分観測）：22707合成操作可能な視覚学習設定を変更→保存失敗通知→画面内入力保持を確認。永続化未実証 |
| F-SE-03 | CoS（本人分岐は本人） | 判定不能（当該分岐未実施）：隔離・本人・既存自動試験の実証と原期待結果への照合が未完了 |
| F-BO-01 | CoS（本人分岐は本人） | 判定不能（部分観測）：ヘッダー借用ONを読み取り。借用枠・障害分岐は未確認 |
| F-BO-02 | CoS（本人分岐は本人） | 判定不能（当該分岐未実施）：隔離・本人・既存自動試験の実証と原期待結果への照合が未完了 |
| F-SP-01 | CoS（本人分岐は本人） | 判定不能（部分観測）：回復入口で過去HTTP401停止・解除不可・再送しない説明を観測。解除未実施 |
| F-SP-02 | CoS（本人分岐は本人） | 判定不能（部分観測）：22701合成HTTP429記録済みSTOPと解除不可、22702合成結果不明で追加通信停止をGUI確認 |
| F-SP-03 | CoS（本人分岐は本人） | 判定不能（部分観測）：22701の429永続STOPと本番401 STOPの表示を区別。正式復旧や認証切替は未実証 |
| F-NO-01 | CoS（本人分岐は本人） | 判定不能（部分観測）：通信停止通知を観測。既読化・確認は操作せず |
| F-HI-01 | CoS（本人分岐は本人） | 判定不能（部分観測）：直近バックアップと閲覧履歴を開く。識別値や作品は出力・保存せず |
| F-HI-02 | CoS（本人分岐は本人） | 判定不能（部分観測）：22708合成history/backup複数カード表示・Esc閉鎖を実操作。履歴保存・再起動未実証 |
| F-OS-01 | 本人 | 判定不能（当該分岐未実施）：隔離・本人・既存自動試験の実証と原期待結果への照合が未完了 |
| F-OS-02 | 本人 | 判定不能（当該分岐未実施）：隔離・本人・既存自動試験の実証と原期待結果への照合が未完了 |
| F-OS-03 | 本人 | 判定不能（当該分岐未実施）：隔離・本人・既存自動試験の実証と原期待結果への照合が未完了 |
| F-ER-01 | CoS（本人分岐は本人） | 判定不能（部分観測）：本番STOP、22702結果不明・22703不正応答・22704空失敗・22706整合エラー・22707書込失敗を合成UI観測。全例外未実施 |
| F-EX-01 | 除外（本人裁定） | 本人裁定で対象外（4範囲）：iOS・LAN Web・旧Shaft専用タブ・旧保管庫取込の4件。対応現行機能は別途確認 |
| F-SC-01 | CoS（本人分岐は本人） | 判定不能（当該分岐未実施）：隔離・本人・既存自動試験の実証と原期待結果への照合が未完了 |
| F-SC-02 | CoS（本人分岐は本人） | 判定不能（部分観測）：ダウンロード→キュー→失敗→重複の一部閲覧。全導線と再起動は未達 |
| F-SC-03 | CoS（本人分岐は本人） | 判定不能（当該分岐未実施）：隔離・本人・既存自動試験の実証と原期待結果への照合が未完了 |

## 検証の限界

- 最終通し撮影器は対応済み 204 条件を計画し 99 成立、105 未成立。原台帳 233 条件への対応は画像あり 181・なし 52、状態成立 99。撮影器の計画には台帳外の補助条件も含まれるので両分母を混ぜない。
- 実行時の SHA はプロセスがロードしたモジュールのファイルの撮影前後測定であり、メモリ内コードの直接測定ではない。
- 写真、Flet 比較、状態遷移・再起動、ネイティブ操作、本人による実機受入は別の証拠。前記 99 は撮影状態の機械判定であり、UI/UX・機能の合格件数ではない。
