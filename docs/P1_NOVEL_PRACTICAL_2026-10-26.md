# P1 小説実用接続 — fixture自己検証済み / 独立監査・製品受入待ち

専用候補: S:/Projects/PixivVault-NiceGUI-p1-novel-practical-20261026
branch: phase11/p1-novel-practical-20261026
HEAD: 2cf4a7a7286dd728e8df8125094498a3622babba
基準M4集合SHA: 71ac018105d702dd1962cdbf8442022678e2fdd2e7bcbe59fe2dcbce9cee5f10

前回の部品候補を継続し、取得→プレビュー→queue→bundle保存→receipt→ライブラリ→再起動の製品コードを接続した。隔離fixtureで下記の自己検証を実施した。独立監査・実サービス・配布版を含むP1製品受入完了やP2移行を意味しない。

## 原操作と接続

- `--novel-download-root` + 既存の明示 `--pixiv-cookie-file` の独立入口。rootはCookie所在と分離し、他保存モードと混在拒否。入口作成時の通信はなく、明示操作で本人照合する。この入口を実Cookieで起動する検証は別承認・未実施。
- 既存ダウンロードタブに作品ID/作者ID、作者小説一覧、公開/非公開ブックマークの選択入口。PR33 head adb6bce48012e286043b5acae46253fa2258bc7d のGET制限・認証・作品/作者照合を再利用。作者一覧は既存profile/allのnovelsを読み、256件超や不正応答は部分成功にしない。選択のみでenqueueしない。
- EPUB推奨/TXT/BOTHの確認→既存JobService。operation_id、attempt、control revision、pause/resume/stop/retry、prepublish journal、CAS、uncertain receipt照合の既存経路を使用。series intentは明示章ID順を永続化し、単一作品と別targetにする。従来intentは空の新規キーを保存しないためv1 journalを読める。
- 原Flet core.py 3ac3aa0d47a64785251593bcb09bb2d5f6df374b L1519–1607/L1943–2007 の本文のみUTF-8 BOM、題名(ID)名、ルビ、改ページ、挿絵、表紙をbundleへ。未知本文はXML escape。取得済みPNG/JPEGを有限pixel/byteでPNG正規化し、画像IDは章ID付きで衝突を避ける。元の危険なHTML結合や任意経路は再現しない。
- 画像は作者照合済み本文のmetadataから既存get_image_bytesを利用。CookieをCDNへ送らず、既存host/redirect/size/time制限に加え、累積byte、件数、全体deadline、checkpointで停止を確認。実通信は今回行っていない。
- v2 receiptは表示metadata・章順・全output名/byte/SHAを保持。TXT/EPUB・images/を同一stageに書き、全件検証→既存before_publish→rename→completion照合。別rootを作ることなくv1世代と共存し、v1ファイル/receiptを移行・改名・削除しない。未知path、symlink/reparse/hardlink、余分ファイル、hash不一致は拒否。
- ライブラリはメモリの選択一覧ではなく永続active receiptから復元。v1の従来metadata読取りを維持し、v2はreceipt metadataを使用。保存済みTXT/EPUBを文字としてページ表示し、挿絵/表紙は検証済みローカルPNGをdata URIとして表示する。EPUB内のHTMLやremote URLはブラウザーで実行しない。
- シリーズは既存PHASE11_NOVEL_SERIES_EPUB_CANDIDATE.mdの「同作者・同シリーズの明示順DTO」範囲。UIの章ID順/題名をpreviewで既存series exporterに通し、jobで再取得・再検証して保存。作者違い、series違い、重複、欠落指定、上限超過は拒否。

## 再利用と新規コード

novel_export / novel_series_export / NovelStorage / JobService / PR33 readerを再利用。
新規: novel_bundle_storage.py（v2検証）、novel_saved_reader.py（安全な保存済み読取り）。
継続: novel_practical_export.py、managed_novel_jobs.py、novel_save_presentation.py。
入口/UI: bootstrap.py、services.py、ui/novel_save_view.py、job_views.py、shell.py、theme.py。

## 回帰コード

- test_novel_practical_export.py: 原形式、BOM、ルビ/改ページ、画像欠落、XML無害化、明示シリーズ順、作者pin。
- test_novel_practical_integration.py: 3形式×単一/seriesの取得→保存→library→restart、asset失敗→同一operation/attempt2、画像取得中stop、v1/v2共存と旧receipt不変、破損拒否、不正path、CLI隔離。
- test_novel_practical_ui.py: fake認証readerを注入した一覧→preview→queue→libraryのUI wiring。
- 既存PR33 readerテスト、M3/M4ジョブ・UI・storage・seriesテストを保持。旧「assetを一律拒否」の一時テストはbundleへ渡す現在契約に変更し、取得失敗・保存前拒否はintegration回帰へ移した。

## 自己検証結果と出口ゲート

2026-10-26 17:55 JST の本人承認に従い、隔離fixture回帰と所有loopback検証を実施した。実API/Cookie/GPUは使用していない。

- 対象回帰: 282 PASS / 1 SKIP、修正追加回帰: 46 PASS。最終全pytest: **2146 PASS / 2 SKIP / 0 FAIL**、exit 0。SKIPはWindows symlink権限で、権限変更なし。既存非推奨警告・重複ZIP合成fixture警告の2件。
- 新しいv2 bundleでCAS不一致・同じoperation再送・rename後fsync失敗の照合・再起動順序を確認。PR33 LivePixivReadClientも合成transportと生成した偽Cookie fixtureだけで取得→CDNヘッダー境界→bundle保存→閲覧へ接続し、外部GETなし。
- Headless Edge、1280×720/1920×1080、初回/再起動 **4/4成功**。作者一覧・公開/非公開bookmark選択、単一3形式、明示章順series、pause/resume、retry、stop、保存済み画像/長文ページ送り/閉じた後のfocusを実クリック。再起動後の本文/画像閲覧でsource呼出0、全receipt/file SHA一致。browser console/page error・外部origin要求0。所有server正常終了・全port解放。
- 初回browser-r1はchecker本文が8000文字未満のためページ送り期待が誤り。fixtureを長文化。browser-r2は保存順とjournalキー順でライブラリ順が再起動後変化する製品不具合を検出し、sorted target列挙に修正。失敗証跡は保持。browser-r3 4/4成功後、原契約の時間計測を加えたbrowser-finalも4/4成功。

- Edge 1280×720 初回: warm p50 65.7 ms / p95 114.8 ms（20切替）
- Edge 1280×720 再起動: warm p50 91.3 ms / p95 131.2 ms（20切替）
- Edge 1920×1080 初回: warm p50 84.3 ms / p95 132.6 ms（20切替）
- Edge 1920×1080 再起動: warm p50 79.0 ms / p95 144.0 ms（20切替）

時間はDOMの遷移完了までの観測値。描画・バックエンド完了と同義ではない。保存バックエンド時間は各result.jsonのbackend_durationsに別記。合成データ・同条件Flet比較ではなく性能優劣は判定しない。

残る出口ゲートを分離する:
1. 全pytest: 上記自己検証済み。
2. 独立監査: dot回収待ち。M3の監査完了を代替しない。
3. Edge初回/再起動: 上記隔離fixtureで自己検証済み。
4. 配布native build→全file SHA copy→初回/復帰/終了: P1未実施。共有前面操作はしていない。
5. 必要なlive GET: 未実施・今回の承認範囲外。
6. exact-head Windows/Ubuntu/Chromium CI: 未実施、commit/push/公開なし。

再現: 既存Sのvenv、PYTHONPATH=<候補>/src、PYTHONDONTWRITEBYTECODE=1、TEMP/TMP=<候補内の新規temp>で `python -m pytest -q -p no:cacheprovider --basetemp <新規path>`。画面は `python tools/check_novel_practical_browser.py --channel msedge --output <未作成の候補内artifact path>`。新規dataと動的空きportを自動選択し、正常終了を検査する。証跡pathを再使用しない。

## 未採用仕様と残る判定

シリーズAPIからの自動全件発見・完全性保証は原シリーズ契約にない。現実装は利用者が明示した章順を保存し、全シリーズ取得済みとは表示しない。この全件自動化を採用するなら、取得根拠・欠章時操作を別途確定する必要がある。
候補の256作品、画像100/32MiB、series100章等の上限は既存候補・資源予算で、製品の実データ受入値ではない。大量作品/画像・変換性能の受入確認が必要。
readerはHTMLをそのまま表示せず、ルビは括弧付き文字、画像は本文の下の保存画像領域で表示する。正確な組版・本文中位置の視覚互換はUX受入で判定する。

独立監査・実サービス・配布版で修正が必要になる可能性は残る。P1のfixture自己検証済み接続候補として引き渡し、P2移行はdot判断まで保留する。
旧M3/M4・監査束・既存8089・EMA・他所有processへの変更、commit/push/公開/main統合、依存導入、認証/OS変更は行っていない。
最新の差分/SHA/保全/書込み終了は artifacts/p1-validation-20261026-r1/final/ を参照。以前の部分候補証跡は保持する。
画面自己点検: 既存dark theme内で保存・ライブラリ・readerが使用可能。1280幅のシリーズ章IDラベルは末尾が省略表示される。本文中の画像位置・ルビ組版を含め、P4総合UXの比較項目として残す。
