# 受入セット案（2026-11-28）

これは凍結候補での CoS 依頼に向けた保守的な案。過去の状態成立や部分観測を今回の合格へ引き継がない。原条件の本文と評価手順は原依頼・原台帳を併読し、この一覧は置換しない。別の入口、空・読込中・エラーは目視に残す。自動試験のみで原条件を全て担保できる対応は今回確認できていない。

| 対象 | CoS が目視 | 自動テストのみで担保 | 除外 | 合計 |
|---|---:|---:|---:|---:|
| UI/UX 条件 | 233 | 0 | 0 | 233 |
| 機能 | 56 | 0 | 1 | 57 |

除外 F-EX-01 は本人裁定の iOS 連携、Web 連携、旧 Shaft 専用タブ、旧保管庫取込。現行対応機能は除外しない（docs/PV_DEFINITION_OF_DONE_2026-11-17.md の D1）。旧 DB 書込みも対象外という現行指示を守る。

同じ版で機能と UI/UX を試す前に、候補の固定・状態別素材・実行時来歴を確認する。目視への分類は一枚の画像だけで全操作、機能や実機受入を代替できる意味ではない。正常系・状態遷移・永続化・OS 固有操作は原条件に従い検証する。

### 撮影土台の制約と残作業

- 現行の合成画像は内部確認用であり、外部送付用の秘匿・安全性監査は未実施。実データを与えない。撮影器は実行中プロセスが読み込んだモジュールのファイルを撮影前後に SHA-256 で測定するが、Python のメモリ内バイト列を直接測定するものではない。候補固定、ソース未変更と起動時の対応は別途確認する。
- 判定器は対象画面の要素と状態信号を確認できた場合だけ成立とし、未定義の状態は不成立のままにする。画面が存在すること、画像が保存されたこと、終了コードだけでは受入としない。画像の実寸と SHA-256 は各 `metrics.json` に記録する。
- キューの作者 ID は合成ジョブに結び付いた受理済み合成記録から表示する。キュー・重複整理・学習の通常素材は用意した。各画面の空・読込中・エラーをすべて成立させる素材と判定はまだ揃っていない。既存の fixture が対応しない組合せは、状態を維持する偽 I/O 境界や画面固有の判定信号が未定義であり、除外せず目視対象として残す。カスタムランキングの読込中は初回通し撮影で信号の待機がタイムアウトした。
- H-02: 旧 `retry_capture.py` はパスを含むテキストの親要素全体を透明化し、警告本文のあるラベルにも適用し得る。旧画像では本文が消え、今回の合成キュー画像では本文が表示された。空の帯は秘匿処理に起因する有力な証拠だが、同一候補・同一画面で秘匿の有無だけを切り替えた比較は未実施。赤線の原因も未確定。今回の撮影器は警告本文を親要素ごと透明化しない。

原資料: `S:\Projects\PV-DEV-20261117\verify\cos_20261127_UIUX\FORMAL_CONDITION_LEDGER.csv`、`FINAL_REPORT.md`、`INDEPENDENT_REVIEW.md`、`INDEPENDENT_REVIEW_ADDENDUM.md`、`S:\Projects\PV-DEV-20261117\verify\cos_20261124_FULL\COS_FULL_INTERIM_20261124.md`、`COS_CURRENT_ACCEPTANCE_STATUS_20261124.md`。条件の具体的内容と前回証拠は原台帳を参照。

## UI/UX 233 条件

| 条件 ID | 分類 |
|---|---|
| U-S01_1280x800_dark_empty | CoS が目視 |
| U-S01_1280x800_dark_error | CoS が目視 |
| U-S01_1280x800_dark_loading | CoS が目視 |
| U-S01_1280x800_dark_normal | CoS が目視 |
| U-S01_1280x800_light_empty | CoS が目視 |
| U-S01_1280x800_light_error | CoS が目視 |
| U-S01_1280x800_light_loading | CoS が目視 |
| U-S01_1280x800_light_normal | CoS が目視 |
| U-S01_1920x1080_dark_empty | CoS が目視 |
| U-S01_1920x1080_dark_error | CoS が目視 |
| U-S01_1920x1080_dark_loading | CoS が目視 |
| U-S01_1920x1080_dark_normal | CoS が目視 |
| U-S01_1920x1080_light_empty | CoS が目視 |
| U-S01_1920x1080_light_error | CoS が目視 |
| U-S01_1920x1080_light_loading | CoS が目視 |
| U-S01_1920x1080_light_normal | CoS が目視 |
| U-S02_1280x800_dark_empty | CoS が目視 |
| U-S02_1280x800_dark_error | CoS が目視 |
| U-S02_1280x800_dark_loading | CoS が目視 |
| U-S02_1280x800_dark_normal | CoS が目視 |
| U-S02_1280x800_light_empty | CoS が目視 |
| U-S02_1280x800_light_error | CoS が目視 |
| U-S02_1280x800_light_loading | CoS が目視 |
| U-S02_1280x800_light_normal | CoS が目視 |
| U-S02_1920x1080_dark_empty | CoS が目視 |
| U-S02_1920x1080_dark_error | CoS が目視 |
| U-S02_1920x1080_dark_loading | CoS が目視 |
| U-S02_1920x1080_dark_normal | CoS が目視 |
| U-S02_1920x1080_light_empty | CoS が目視 |
| U-S02_1920x1080_light_error | CoS が目視 |
| U-S02_1920x1080_light_loading | CoS が目視 |
| U-S02_1920x1080_light_normal | CoS が目視 |
| U-S03_1280x800_dark_empty | CoS が目視 |
| U-S03_1280x800_dark_error | CoS が目視 |
| U-S03_1280x800_dark_loading | CoS が目視 |
| U-S03_1280x800_dark_normal | CoS が目視 |
| U-S03_1280x800_light_empty | CoS が目視 |
| U-S03_1280x800_light_error | CoS が目視 |
| U-S03_1280x800_light_loading | CoS が目視 |
| U-S03_1280x800_light_normal | CoS が目視 |
| U-S03_1920x1080_dark_empty | CoS が目視 |
| U-S03_1920x1080_dark_error | CoS が目視 |
| U-S03_1920x1080_dark_loading | CoS が目視 |
| U-S03_1920x1080_dark_normal | CoS が目視 |
| U-S03_1920x1080_light_empty | CoS が目視 |
| U-S03_1920x1080_light_error | CoS が目視 |
| U-S03_1920x1080_light_loading | CoS が目視 |
| U-S03_1920x1080_light_normal | CoS が目視 |
| U-S04_1280x800_dark_empty | CoS が目視 |
| U-S04_1280x800_dark_error | CoS が目視 |
| U-S04_1280x800_dark_loading | CoS が目視 |
| U-S04_1280x800_dark_normal | CoS が目視 |
| U-S04_1280x800_light_empty | CoS が目視 |
| U-S04_1280x800_light_error | CoS が目視 |
| U-S04_1280x800_light_loading | CoS が目視 |
| U-S04_1280x800_light_normal | CoS が目視 |
| U-S04_1920x1080_dark_empty | CoS が目視 |
| U-S04_1920x1080_dark_error | CoS が目視 |
| U-S04_1920x1080_dark_loading | CoS が目視 |
| U-S04_1920x1080_dark_normal | CoS が目視 |
| U-S04_1920x1080_light_empty | CoS が目視 |
| U-S04_1920x1080_light_error | CoS が目視 |
| U-S04_1920x1080_light_loading | CoS が目視 |
| U-S04_1920x1080_light_normal | CoS が目視 |
| U-S05_1280x800_dark_empty | CoS が目視 |
| U-S05_1280x800_dark_error | CoS が目視 |
| U-S05_1280x800_dark_loading | CoS が目視 |
| U-S05_1280x800_dark_normal | CoS が目視 |
| U-S05_1280x800_light_empty | CoS が目視 |
| U-S05_1280x800_light_error | CoS が目視 |
| U-S05_1280x800_light_loading | CoS が目視 |
| U-S05_1280x800_light_normal | CoS が目視 |
| U-S05_1920x1080_dark_empty | CoS が目視 |
| U-S05_1920x1080_dark_error | CoS が目視 |
| U-S05_1920x1080_dark_loading | CoS が目視 |
| U-S05_1920x1080_dark_normal | CoS が目視 |
| U-S05_1920x1080_light_empty | CoS が目視 |
| U-S05_1920x1080_light_error | CoS が目視 |
| U-S05_1920x1080_light_loading | CoS が目視 |
| U-S05_1920x1080_light_normal | CoS が目視 |
| U-S06_1280x800_dark_empty | CoS が目視 |
| U-S06_1280x800_dark_error | CoS が目視 |
| U-S06_1280x800_dark_loading | CoS が目視 |
| U-S06_1280x800_dark_normal | CoS が目視 |
| U-S06_1280x800_light_empty | CoS が目視 |
| U-S06_1280x800_light_error | CoS が目視 |
| U-S06_1280x800_light_loading | CoS が目視 |
| U-S06_1280x800_light_normal | CoS が目視 |
| U-S06_1920x1080_dark_empty | CoS が目視 |
| U-S06_1920x1080_dark_error | CoS が目視 |
| U-S06_1920x1080_dark_loading | CoS が目視 |
| U-S06_1920x1080_dark_normal | CoS が目視 |
| U-S06_1920x1080_light_empty | CoS が目視 |
| U-S06_1920x1080_light_error | CoS が目視 |
| U-S06_1920x1080_light_loading | CoS が目視 |
| U-S06_1920x1080_light_normal | CoS が目視 |
| U-S07_1280x800_dark_empty | CoS が目視 |
| U-S07_1280x800_dark_error | CoS が目視 |
| U-S07_1280x800_dark_loading | CoS が目視 |
| U-S07_1280x800_dark_normal | CoS が目視 |
| U-S07_1280x800_light_empty | CoS が目視 |
| U-S07_1280x800_light_error | CoS が目視 |
| U-S07_1280x800_light_loading | CoS が目視 |
| U-S07_1280x800_light_normal | CoS が目視 |
| U-S07_1920x1080_dark_empty | CoS が目視 |
| U-S07_1920x1080_dark_error | CoS が目視 |
| U-S07_1920x1080_dark_loading | CoS が目視 |
| U-S07_1920x1080_dark_normal | CoS が目視 |
| U-S07_1920x1080_light_empty | CoS が目視 |
| U-S07_1920x1080_light_error | CoS が目視 |
| U-S07_1920x1080_light_loading | CoS が目視 |
| U-S07_1920x1080_light_normal | CoS が目視 |
| U-S08_1280x800_dark_empty | CoS が目視 |
| U-S08_1280x800_dark_error | CoS が目視 |
| U-S08_1280x800_dark_loading | CoS が目視 |
| U-S08_1280x800_dark_normal | CoS が目視 |
| U-S08_1280x800_light_empty | CoS が目視 |
| U-S08_1280x800_light_error | CoS が目視 |
| U-S08_1280x800_light_loading | CoS が目視 |
| U-S08_1280x800_light_normal | CoS が目視 |
| U-S08_1920x1080_dark_empty | CoS が目視 |
| U-S08_1920x1080_dark_error | CoS が目視 |
| U-S08_1920x1080_dark_loading | CoS が目視 |
| U-S08_1920x1080_dark_normal | CoS が目視 |
| U-S08_1920x1080_light_empty | CoS が目視 |
| U-S08_1920x1080_light_error | CoS が目視 |
| U-S08_1920x1080_light_loading | CoS が目視 |
| U-S08_1920x1080_light_normal | CoS が目視 |
| U-S09_1280x800_dark_empty | CoS が目視 |
| U-S09_1280x800_dark_error | CoS が目視 |
| U-S09_1280x800_dark_loading | CoS が目視 |
| U-S09_1280x800_dark_normal | CoS が目視 |
| U-S09_1280x800_light_empty | CoS が目視 |
| U-S09_1280x800_light_error | CoS が目視 |
| U-S09_1280x800_light_loading | CoS が目視 |
| U-S09_1280x800_light_normal | CoS が目視 |
| U-S09_1920x1080_dark_empty | CoS が目視 |
| U-S09_1920x1080_dark_error | CoS が目視 |
| U-S09_1920x1080_dark_loading | CoS が目視 |
| U-S09_1920x1080_dark_normal | CoS が目視 |
| U-S09_1920x1080_light_empty | CoS が目視 |
| U-S09_1920x1080_light_error | CoS が目視 |
| U-S09_1920x1080_light_loading | CoS が目視 |
| U-S09_1920x1080_light_normal | CoS が目視 |
| U-S10_1280x800_dark_empty | CoS が目視 |
| U-S10_1280x800_dark_error | CoS が目視 |
| U-S10_1280x800_dark_loading | CoS が目視 |
| U-S10_1280x800_dark_normal | CoS が目視 |
| U-S10_1280x800_light_empty | CoS が目視 |
| U-S10_1280x800_light_error | CoS が目視 |
| U-S10_1280x800_light_loading | CoS が目視 |
| U-S10_1280x800_light_normal | CoS が目視 |
| U-S10_1920x1080_dark_empty | CoS が目視 |
| U-S10_1920x1080_dark_error | CoS が目視 |
| U-S10_1920x1080_dark_loading | CoS が目視 |
| U-S10_1920x1080_dark_normal | CoS が目視 |
| U-S10_1920x1080_light_empty | CoS が目視 |
| U-S10_1920x1080_light_error | CoS が目視 |
| U-S10_1920x1080_light_loading | CoS が目視 |
| U-S10_1920x1080_light_normal | CoS が目視 |
| U-S11-backup_1280x800_dark_empty | CoS が目視 |
| U-S11-backup_1280x800_dark_error | CoS が目視 |
| U-S11-backup_1280x800_dark_normal | CoS が目視 |
| U-S11-backup_1280x800_light_empty | CoS が目視 |
| U-S11-backup_1280x800_light_error | CoS が目視 |
| U-S11-backup_1280x800_light_normal | CoS が目視 |
| U-S11-backup_1920x1080_dark_empty | CoS が目視 |
| U-S11-backup_1920x1080_dark_error | CoS が目視 |
| U-S11-backup_1920x1080_dark_normal | CoS が目視 |
| U-S11-backup_1920x1080_light_empty | CoS が目視 |
| U-S11-backup_1920x1080_light_error | CoS が目視 |
| U-S11-backup_1920x1080_light_normal | CoS が目視 |
| U-S12-history_1280x800_dark_empty | CoS が目視 |
| U-S12-history_1280x800_dark_error | CoS が目視 |
| U-S12-history_1280x800_dark_normal | CoS が目視 |
| U-S12-history_1280x800_light_empty | CoS が目視 |
| U-S12-history_1280x800_light_error | CoS が目視 |
| U-S12-history_1280x800_light_normal | CoS が目視 |
| U-S12-history_1920x1080_dark_empty | CoS が目視 |
| U-S12-history_1920x1080_dark_error | CoS が目視 |
| U-S12-history_1920x1080_dark_normal | CoS が目視 |
| U-S12-history_1920x1080_light_empty | CoS が目視 |
| U-S12-history_1920x1080_light_error | CoS が目視 |
| U-S12-history_1920x1080_light_normal | CoS が目視 |
| U-S15-settings_1280x800_dark_error | CoS が目視 |
| U-S15-settings_1280x800_dark_normal | CoS が目視 |
| U-S15-settings_1280x800_light_error | CoS が目視 |
| U-S15-settings_1280x800_light_normal | CoS が目視 |
| U-S15-settings_1920x1080_dark_error | CoS が目視 |
| U-S15-settings_1920x1080_dark_normal | CoS が目視 |
| U-S15-settings_1920x1080_light_error | CoS が目視 |
| U-S15-settings_1920x1080_light_normal | CoS が目視 |
| U-S16-settings_1280x800_dark_normal | CoS が目視 |
| U-S16-settings_1280x800_light_normal | CoS が目視 |
| U-S16-settings_1920x1080_dark_normal | CoS が目視 |
| U-S16-settings_1920x1080_light_normal | CoS が目視 |
| U-S17-settings_1280x800_dark_normal | CoS が目視 |
| U-S17-settings_1280x800_light_normal | CoS が目視 |
| U-S17-settings_1920x1080_dark_normal | CoS が目視 |
| U-S17-settings_1920x1080_light_normal | CoS が目視 |
| U-S18_1280x800_dark_normal | CoS が目視 |
| U-S18-automation_1280x800_dark_normal | CoS が目視 |
| U-S18-automation_1280x800_light_normal | CoS が目視 |
| U-S18-automation_1920x1080_dark_normal | CoS が目視 |
| U-S18-automation_1920x1080_light_normal | CoS が目視 |
| U-S18-scheduler_1280x800_dark_normal | CoS が目視 |
| U-S18-scheduler_1280x800_light_normal | CoS が目視 |
| U-S18-scheduler_1920x1080_dark_normal | CoS が目視 |
| U-S18-scheduler_1920x1080_light_normal | CoS が目視 |
| U-S19-metadata_1280x800_dark_normal | CoS が目視 |
| U-S19-metadata_1280x800_light_normal | CoS が目視 |
| U-S19-metadata_1920x1080_dark_normal | CoS が目視 |
| U-S19-metadata_1920x1080_light_normal | CoS が目視 |
| U-S20_1280x800_dark_error | CoS が目視 |
| U-S20_1280x800_light_error | CoS が目視 |
| U-S20_1920x1080_dark_error | CoS が目視 |
| U-S20_1920x1080_light_error | CoS が目視 |
| U-S20-stop-details_1280x800_dark_normal | CoS が目視 |
| U-S20-stop-details_1280x800_light_normal | CoS が目視 |
| U-S20-stop-details_1920x1080_dark_normal | CoS が目視 |
| U-S20-stop-details_1920x1080_light_normal | CoS が目視 |
| U-S26-bulk-confirm_1280x800_dark_normal | CoS が目視 |
| U-S26-bulk-confirm_1280x800_light_normal | CoS が目視 |
| U-S26-bulk-confirm_1920x1080_dark_normal | CoS が目視 |
| U-S26-bulk-confirm_1920x1080_light_normal | CoS が目視 |
| U-S27-row-confirm_1280x800_dark_normal | CoS が目視 |
| U-S27-row-confirm_1280x800_light_normal | CoS が目視 |
| U-S27-row-confirm_1920x1080_dark_normal | CoS が目視 |
| U-S27-row-confirm_1920x1080_light_normal | CoS が目視 |
| U-S40-delete-guard_1280x800_dark_normal | CoS が目視 |
| U-S40-delete-guard_1280x800_light_normal | CoS が目視 |
| U-S40-delete-guard_1920x1080_dark_normal | CoS が目視 |
| U-S40-delete-guard_1920x1080_light_normal | CoS が目視 |

## 機能 57 件

前回結果は各機能の全分岐の合否ではない。最新の追補は原資料で照合する。

| 機能 ID | 分類 | 前回の区分・観測（原資料の記述） |
|---|---|---|
| F-ST-01 | CoS が目視 | 判定不能（既往部分観測・現起動BLOCKED）：21時以前は本番DB要約と詳細→Escを観測。本人再起動後は22386が起動設定の保護画面、通常起動の開始条件未達 |
| F-ST-02 | CoS が目視 | 判定不能（既往部分観測・現起動BLOCKED）：21時以前はログイン有効、借用ONと10タブを観測。現稼働はconfiguration-requiredでこれらを再証明できない |
| F-ST-03 | CoS が目視 | 判定不能（部分観測）：ダーク→ライト→ダーク復元確認。初回案内の発生条件は未確認 |
| F-ST-04 | CoS が目視 | 判定不能（当該分岐未実施）：隔離・本人・既存自動試験の実証と原期待結果への照合が未完了 |
| F-ST-05 | CoS が目視 | 判定不能（部分観測）：有効ログイン画面のみ観測。期限・別アカウント分岐未確認 |
| F-DL-01 | CoS が目視 | 判定不能（部分観測）：作者一覧426人9頁、名前・数字検索→復元、前後ページ1→2→1、並びメニューと☆フィルタを操作。全並び条件未確認 |
| F-DL-02 | CoS が目視 | 判定不能（部分観測）：表示中全選択→全解除を2巡。作者1行選択でヘッダーaria-checked=mixedを確認し解除。☆保存等未確認 |
| F-DL-03 | CoS が目視 | 判定不能（部分観測）：22709合成作者1件から作品単位詳細、差分取得失敗とキュー追加無効を確認。正常取得・受付は未証明 |
| F-DL-04 | CoS が目視 | 判定不能（部分観測）：22709合成作品ID確認が取得失敗し、誤受付しない表示を観測。保存形式・媒体別実取得は未証明 |
| F-DL-05 | CoS が目視 | 判定不能（当該分岐未実施）：隔離・本人・既存自動試験の実証と原期待結果への照合が未完了 |
| F-DL-06 | CoS が目視 | 判定不能（当該分岐未実施）：隔離・本人・既存自動試験の実証と原期待結果への照合が未完了 |
| F-QU-01 | CoS が目視 | 判定不能（部分観測）：本番ローカル一覧に加え22709合成ジョブを再開→実行中→21/30で一時停止。全状態・再起動未証明 |
| F-QU-02 | CoS が目視 | 判定不能（部分観測）：ログ表示切替と22709合成ジョブ一時停止・再開。行削除・確認取消・永続化未実証 |
| F-FA-01 | CoS が目視 | 判定不能（部分観測）：本番空状態と22705合成失敗17件の分類/検索0件/クリア/ページ境界をGUI操作。全分岐未証明 |
| F-FA-02 | CoS が目視 | 判定不能（部分観測）：22705長文・分類・非数字ID再試行無効。全17件クリアと1件削除の確認入口→それぞれ取消で17/17件保持。CSV入口あり、実出力/削除確定/実再試行は未実証 |
| F-DE-01 | CoS が目視 | 判定不能（部分観測）：本番入口と22709合成スキャン→近似1組→画質選択→確認取消→移動を観測。広域正常系未達 |
| F-DE-02 | CoS が目視 | 判定不能（部分観測・復元未達）：22709合成1枚227B隔離とごみ箱1件を確認。legacy物理移動がmanaged限定復元候補へ出ず復元不可 |
| F-LE-01 | CoS が目視 | 判定不能（部分観測）：本番学習で既判定29件/総候補840、現在画像読み込み失敗と判定ボタン無効を観測。新規判定未実施 |
| F-LE-02 | CoS が目視 | 判定不能（部分観測）：本番振り返りで肯定/否定一覧、検索/絞込/ページ送り入口を観測。判定取消・Undo保存は未実施 |
| F-LE-03 | CoS が目視 | 判定不能（当該分岐未実施）：隔離・本人・既存自動試験の実証と原期待結果への照合が未完了 |
| F-LE-04 | CoS が目視 | 判定不能（当該分岐未実施）：隔離・本人・既存自動試験の実証と原期待結果への照合が未完了 |
| F-RK-01 | CoS が目視 | 判定不能（部分観測）：22701合成429 STOP回復説明と22703合成不正応答の取得失敗表示をGUI確認。実正常取得は未実証 |
| F-RK-02 | CoS が目視 | 判定不能（部分観測）：22703合成ランキング取得失敗と未取得の区別を観測。4期間正常カード等は既存証拠範囲のみ |
| F-RK-03 | CoS が目視 | 判定不能（部分観測）：22709合成ランキングカード右クリックで5入口とブクマdisabledを確認、Esc閉鎖。メニュー確定・非表示復帰・ビューア正常は未実施 |
| F-PR-01 | CoS が目視 | 判定不能（部分観測）：22702合成カスタムランキングの送信結果不明→追加通信停止と保管データ保持説明を確認。正常結果未実証 |
| F-PR-02 | CoS が目視 | 判定不能（部分観測）：22709合成14条件に関連する内側絞り込みパネルの各入口表示と開閉を確認。入力・保存・再取得の結果は未証明 |
| F-LI-01 | CoS が目視 | 判定不能（部分観測）：本番ライブラリ1作者1作品を表示し、合成不一致文字検索0→クリア1に復帰。閲覧・複数選択未証明 |
| F-LI-02 | CoS が目視 | 判定不能（部分観測）：本番媒体切替で小説保存0・うごイラ保存0の空状態を観測。正常媒体保存例・編集未実証 |
| F-BM-01 | CoS が目視 | 判定不能（部分観測）：22704合成空0件と選択DL disabled、同時に取得失敗表示を観測。正常な空取得は未証明 |
| F-BM-02 | CoS が目視 | 判定不能（当該分岐未実施）：隔離・本人・既存自動試験の実証と原期待結果への照合が未完了 |
| F-RE-01 | CoS が目視 | 判定不能（部分観測）：22709合成おすすめ更新成功・カード表示を確認。実借用のカード欠落と保存結果不明は未解消 |
| F-RE-02 | CoS が目視 | 判定不能（部分観測）：22709合成カード3→非表示2→復帰3、理由未選択取消を実証。前結果/再起動保存は未実証 |
| F-RE-03 | CoS が目視 | 判定不能（部分観測）：22709合成出さない一覧の作品1件→復帰して一覧から消滅。旧Flet非空/作者比較と永続性未実証 |
| F-VW-01 | CoS が目視 | 判定不能（部分観測）：22708合成履歴行→画集ビューア（1/1、画像読込失敗）、詳細失敗→Escで詳細/ビューアを段階閉鎖。正常画像・ページ送り未証明 |
| F-VW-02 | CoS が目視 | 判定不能（当該分岐未実施）：隔離・本人・既存自動試験の実証と原期待結果への照合が未完了 |
| F-AU-01 | CoS が目視 | 判定不能（部分観測）：定期・メタデータが取得停止中の要約、最終・次回、ログと詳細入口を確認 |
| F-AU-02 | CoS が目視 | 判定不能（当該分岐未実施）：隔離・本人・既存自動試験の実証と原期待結果への照合が未完了 |
| F-AU-03 | CoS が目視 | 判定不能（当該分岐未実施）：隔離・本人・既存自動試験の実証と原期待結果への照合が未完了 |
| F-SE-01 | CoS が目視 | 判定不能（部分観測）：設定グループ・現在値・一部無効表示を読取。全キーとTab順の全量照合は未達 |
| F-SE-02 | CoS が目視 | 判定不能（部分観測）：22707合成操作可能な視覚学習設定を変更→保存失敗通知→画面内入力保持を確認。永続化未実証 |
| F-SE-03 | CoS が目視 | 判定不能（当該分岐未実施）：隔離・本人・既存自動試験の実証と原期待結果への照合が未完了 |
| F-BO-01 | CoS が目視 | 判定不能（部分観測）：ヘッダー借用ONを読み取り。借用枠・障害分岐は未確認 |
| F-BO-02 | CoS が目視 | 判定不能（当該分岐未実施）：隔離・本人・既存自動試験の実証と原期待結果への照合が未完了 |
| F-SP-01 | CoS が目視 | 判定不能（部分観測）：回復入口で過去HTTP401停止・解除不可・再送しない説明を観測。解除未実施 |
| F-SP-02 | CoS が目視 | 判定不能（部分観測）：22701合成HTTP429記録済みSTOPと解除不可、22702合成結果不明で追加通信停止をGUI確認 |
| F-SP-03 | CoS が目視 | 判定不能（部分観測）：22701の429永続STOPと本番401 STOPの表示を区別。正式復旧や認証切替は未実証 |
| F-NO-01 | CoS が目視 | 判定不能（部分観測）：通信停止通知を観測。既読化・確認は操作せず |
| F-HI-01 | CoS が目視 | 判定不能（部分観測）：直近バックアップと閲覧履歴を開く。識別値や作品は出力・保存せず |
| F-HI-02 | CoS が目視 | 判定不能（部分観測）：22708合成history/backup複数カード表示・Esc閉鎖を実操作。履歴保存・再起動未実証 |
| F-OS-01 | CoS が目視 | 判定不能（当該分岐未実施）：隔離・本人・既存自動試験の実証と原期待結果への照合が未完了 |
| F-OS-02 | CoS が目視 | 判定不能（当該分岐未実施）：隔離・本人・既存自動試験の実証と原期待結果への照合が未完了 |
| F-OS-03 | CoS が目視 | 判定不能（当該分岐未実施）：隔離・本人・既存自動試験の実証と原期待結果への照合が未完了 |
| F-ER-01 | CoS が目視 | 判定不能（部分観測）：本番STOP、22702結果不明・22703不正応答・22704空失敗・22706整合エラー・22707書込失敗を合成UI観測。全例外未実施 |
| F-EX-01 | 除外（本人裁定の4範囲） | 本人裁定で対象外（4範囲）：iOS・LAN Web・旧Shaft専用タブ・旧保管庫取込の4件。対応現行機能は別途確認 |
| F-SC-01 | CoS が目視 | 判定不能（当該分岐未実施）：隔離・本人・既存自動試験の実証と原期待結果への照合が未完了 |
| F-SC-02 | CoS が目視 | 判定不能（部分観測）：ダウンロード→キュー→失敗→重複の一部閲覧。全導線と再起動は未達 |
| F-SC-03 | CoS が目視 | 判定不能（当該分岐未実施）：隔離・本人・既存自動試験の実証と原期待結果への照合が未完了 |

### 合成画面の素材・判定状況（1280×800 dark の試行）

成立は撮影状態の機械判定であり、画面の人による合否ではない。未試行は該当する状態を保持する fixture 境界が未定義。未成立は個別の証拠を参照する。各状態は CoS の対象から除外しない。

| 画面 | 通常 | 空 | エラー | 読込中 |
|---|---|---|---|---|
| download | 成立 | 未成立（判定信号未定義） | 未試行（境界未定義） | 未試行（境界未定義） |
| queue | 成立 | 成立 | 未試行（境界未定義） | 未試行（境界未定義） |
| failed | 成立 | 未成立（判定信号未定義） | 未試行（境界未定義） | 未試行（境界未定義） |
| dedup | 成立 | 未成立（判定信号未定義） | 成立 | 成立 |
| learning | 成立 | 未成立（判定信号未定義） | 未試行（境界未定義） | 未試行（境界未定義） |
| ranking | 成立 | 未成立（判定信号未定義） | 成立 | 成立 |
| personal | 成立 | 未成立（判定信号未定義） | 成立 | 未成立（状態待機タイムアウト） |
| library | 成立 | 未成立（判定信号未定義） | 未試行（境界未定義） | 未試行（境界未定義） |
| bookmarks | 成立 | 未成立（判定信号未定義） | 未試行（境界未定義） | 未試行（境界未定義） |
| recommend | 成立 | 未成立（判定信号未定義） | 成立 | 成立 |

表の未試行は 12 組合せ、判定・状態の未成立は 10 件。空状態はキュー以外の判定が未実装であり、素材が空でも表示の成立を証明していない。カスタムランキングの読込中は 4 秒の偽応答遅延では状態表示の捕捉に至らず、待機を制御できる境界と画面信号の追補が必要。スクリーン別の実装可否はこの試行だけから断定しない。

今回の一通しの原証拠: `S:\Work\_tmp\PV\h3-20261128\pilot-20261128_182435_057404`、実行ログ `S:\Work\_tmp\PV\h3-20261128\capture-final2.log`。設定画面・Flet 比較・その他撮影経路の画像への同じ来歴記録は今回確認していない。
