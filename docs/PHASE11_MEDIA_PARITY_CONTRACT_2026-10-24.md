# Phase11 — 小説・うごイラ移植の実装契約（2026-10-24）

## 位置づけ

- PixivVault Flet→NiceGUI完全移植の**残存する元機能**を段階的に扱う。新規のiPhone/iOSアプリ連携は**対象外**（`docs/MIGRATION_SCOPE_AMENDMENT_2026-10-24.md`）。既存の`docs/BASELINE.md`と旧版コードは履歴として変更しない。
- Phase10の独立レビュー、最新UX受入、Windows native確認、exact-head CIはPR #31に保存されている。Phase11はPR #31の内容を前提に分離して実装し、Phase10未マージのうちはmainへ統合しない。
- 実Cookieの利用は既存の明示Cookie読取経路・本人が許可した限定的な実Pixiv検証のみ。新しい認証設定、権限変更、Pixivへの書込、Cookie値のGit/ログ/会話への出力はしない。

## 原版で実装済みの内容と現在のギャップ

| 分野 | 原版Fletの操作契約 | NiceGUI現状 | 未達 |
|---|---|---|---|
| 小説一覧 | `pixiv_client.py:944-1013` 作者profile/all→48件単位details、`1546-1607` 公開/非公開ブックマーク | `pixiv_read_client.py:711-712` 公開/非公開のブックマーク一覧を読める | 小説の本文、保存UI・ジョブ・検証 |
| 小説本文 | `pixiv_client.py:1015-1028` `GET /ajax/novel/{id}`、`get_novel_info:1071-1089` | 小説カードのサムネイルのみ | 本文と作者ID照合、シリーズ・挿絵の安全な処理 |
| 小説出力 | `core.py:1519-1607, 1943-2007` TXT / EPUB / BOTH、`epub_builder.py:6-132` | isolated managed JobServiceはイラスト向け | 出力形式、再開・重複・検証、ライブラリ表示 |
| うごイラ情報 | `pixiv_client.py:1652-1656` `GET /ajax/illust/{id}/ugoira_meta`、ZIP URLとフレーム遅延 | `remote_media.py:34-35` アニメーションviewを拒否、サムネイルは可 | 枠付きのZIP/frames GET、再生 |
| うごイラ保存 | `core.py:937-1065` ZIPからGIF/MP4/フォルダー出力（UI設定は`gui.py:1251-1261`） | managed original downloadは`illustration_type=0,1`のみ（`managed_downloads.py:101-106`） | うごイラ独立ジョブ・変換・再生・受入 |

## 守るべき安全性とユーザー体験

1. **原版の危険な実装を機械的に複製しない。** `core.py:989-994` はZIPの`frames[*].file`をそのまま`os.path.join`して書き出すため、ZIP経路逸脱やZIP爆弾を拒否する独立検証ゲートが必要。検証前の展開や、旧画像フォルダーへの書込みは禁止。
2. **小説本文をそのままHTML/XMLに埋め込まない。** 旧`epub_builder.py:49-105`は題名・作者・本文を文字列結合する。新実装ではXMLエスケープ、タグ正規化、リソースURLの許可リスト、章・挿絵の検証を行い、未知表記を勝手にHTMLとして実行しない。
3. **アニメーションの時間精度。** 旧MP4変換は平均フレームdelayを固定FPSへ近似する（`core.py:1023-1031`）。GIFとMP4でフレーム間隔が変わる可能性がある。黙ってMP4→GIFへ切り替えず、変換不可なら利用者に失敗理由と代替形式を知らせる。実装前に可変delayの仕様をテストで決める。
4. **既存データの更新なし。** 専用管理root内だけに新しいreceipts、出力、原子的なindex更新を書き込む。既存のイラスト用receipt schema・CAS・failed/unknown判断は壊さず、novel/ugoira用namespace・型を分ける。完了表示は書込みとintegrity検証を経た後のみ。
5. **秘密・通信境界。** 許可されたGETのみ、明示的な作品と作者の同一性、画像CDNのホスト確認、リダイレクト/外部ホストの拒否、HTTPの上限時間・サイズ・件数。秘密値、本文、実作品名、CDN署名付きURLを画面以外のログ/Git/証跡へ保存しない。
6. **操作感。** ダウンロード一覧・形式選択→キュー投入→結果→ライブラリ→小説閲覧/うごイラ再生/再起動の実導線をEdge 1280×720・1920×1080、Windows nativeで検証。タブの可読性、スクロール・フォーカス、失敗表示、ウォーム操作のp50/p95、バックエンド処理時間を分けて実測し、見た目の問題は明記する。

## 段階ゲート

- **M1 (offline contract):** 原版の型・形式・失敗境界をread-only監査。小説本文/うごイラフレームの**純粋パーサー**を偽データのみで検証し、未知・不正ID、作者不一致、危険パス、重複フレーム、巨大数値・URL、非対応型を拒否。実HTTP・DB変更・出力なし。

  M1の検査契約：小説 `textEmbeddedImages:null` は空mapとして扱うが、配列・101件超は拒否。うごイラはファイル名のcasefold重複、Windowsデバイス名、URLの不正ホスト・制御文字、1〜3000フレーム、1フレーム1〜60000ms、合計3時間を境界テスト。原文やCDN URLはDTOのreprに含めない。保存/抽出/再生はM1に含まない。
- **M2 (isolated media):** 小説TXT/EPUB（本文/シリーズ/挿絵）と、うごイラZIPフレームの安全な取り出し・変換を、有限のサイズ・枚数・時間予算内で実装。`zip-slip`、膨張、画像pixel上限、同名エントリー、壊れたJPEG等をRED→GREEN試験。旧DB/画像を参照せず独立rootへ原子保存。
- **M3 (managed integration):** 受入済みジョブキュー/ledger/receiptと世代CAS、エラー/停止/再開の型契約を利用し、イラスト型との衝突を防ぐ。作業終了待ちやダウンロード優先ゲートは既存Phase10と矛盾させない。
- **M4 (UI/UX):** 入力→プレビュー→形式指定→キュー→保存・失敗→ライブラリ閲覧/再生を実装。長時間変換もUIスレッドをブロックしない。iPhone連携タブ/権限/同期は作らない。
- **M5 (acceptance):** 全pytest、独立read-onlyコードレビューP1/P2ゼロ、ソースSHA固定、Edge初回/再起動・画面サイズ、native build→全file SHA copy→native UI/restore/exit、既存Cookieによる必要最小限の実Pixiv read-only GET、PR exact-head Windows/Ubuntu/Chromium CI。必要な既存承認境界を越える行為は行わない。

## 設計上申（今は無条件に変えない）

- 動画の可変delayをMP4で近似する際に何を優先するか（最小尺誤差かFPS固定か）を将来の受入仕様として判定してもらう。黙って品質を落とすのは不可。
- 既存のGIF・MP4・フォルダー選択のほかに、自動生成の最大容量と巨大作品での挙動（メモリ制限・中止導線）をUXの受入条件へ追加する必要がある。既存イラストの同期・保存・Cookie設定を変更する承認ではない。

## 2026-10-25 M2 オフライン実装の受入状況（部分達成）

- Phase10 PR #31 はhead `47f8357a016c40dffd6bbfd44ed9711e8f7f0a6e`、merge commit `27e903216020c02407f040ca3c737e1b759ca9b9` でmainに統合済み。上記「Phase10未マージ」は本書作成時点の履歴として保持する。Phase11ブランチはPhase10の同じ内容を基点とし、mainへは未統合。
- `novel_export.py` は既に検証された単一小説のTXT/EPUBをオフライン・有限サイズで生成する。本文をHTMLとして実行せずXML文字種とエスケープを検証し、決定的なEPUB ZIPを作成する。**シリーズ複数章と埋込み画像は未実装**であり、形式互換完了と主張しない。
- `ugoira_archive.py` はZIP64や重複・局所領域重複、ZIP爆弾、危険パス、シンボリックリンク、画像ピクセル予算を事前検証する。新設 `ugoira_storage.py` は明示された専用managed rootのmarker・単独writer・予算制限を使い、stage検証→no-replace publish→completion proofでフレームを保存・再照合する。元Fletデータ/画像/Cookieは参照も変更もしない。
- 修正済みの既存証跡 `artifacts/phase11-lock-race-fix-full-corrected-20261025.xml` は **1913 passed / 1 skipped / errors=failures=0**。独立Claude Sonnet read-onlyソースレビューは **APPROVE / P1=0 / P2=0**。最終テストの後で試験コードだけにZIP64の偽終端レコードを追加した。実Python 3.13.11の`zipfile._EndRecData64`がlocatorの実offsetを参照することを確認し、合成ZIPに別の偽 `PK\x06\x06` を埋めた回帰試験は **1 passed**（`artifacts/phase11-zip64-fake-eocd-review-20261025.xml`）。
- この追加試験を含む**最新全pytest 1914 passed / 1 skipped / failures=errors=0**（`artifacts/phase11-m2-independent-review-full-20261025.xml`）。warningは既存Starlette/httpxの非推奨通知と、悪意ある重複ZIPメンバーを作る合成試験の意図的なPython警告。Windows symlink privilegeで1SKIP。これはM2オフラインコードの成果であり、M3ジョブ・実保存UI・GIF/MP4変換・native/Edge最終受入やPhase11のexact-head CI成功を意味しない。
- 残P3: Windowsではディレクトリfsyncに電源断保証がない、失敗stageを自動削除しないため予算に達すると明示的な運用判断が必要、現時点では小説/うごイラ全処理をUIスレッドから隔離する統合が未実装、TXTに一部Unicode改行類がヘッダとして見える可能性がある。いずれも原版や利用者データの無断変更で解消しない。
- `artifacts/phase11-managed-ugoira-gif-20261025/` のGIF/managed job候補は**別のcandidate-only試案**として保存済み。まだ製品コードにも本ブランチのM2検証済み範囲にも取り込まない。可変delay、停止/再開、GIFのフレーム単位変換、強制終了時の残証跡と容量の上限を独立レビュー・RED→GREENで確認してから導入する。
