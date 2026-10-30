# P3 非依存残差の接続候補（正式受入未完）

## 基線と所有

作業先は `S:/Projects/PixivVault-NiceGUI-p3-residuals-20261026`、branch `phase11/p3-residuals-20261026`、HEAD `2cf4a7a7286dd728e8df8125094498a3622babba`。
固定した前P3のdirty84、集合SHA `6356e216d17493c618cf0f34003f2a27842c3fb742bbceb2e4d0382f49b2d807`を独立checkoutへ複製し、84/84の入力hashを照合して開始した。前P3/P1/P2/M3/M4は読取りのみ。commit/push/main統合なし。
rootがservices/bootstrap/shellと共通保存を所有し、作者/scheduler、発掘、推薦、設定/cache、外部連携を別ファイル所有で進めた。担当freeze後の共通修正はrootが引継ぎ、先行担当hashと最終候補hashは別に記録する。これは自己点検であり独立監査ではない。

## 原操作との対応

原版は `3ac3aa0d47a64785251593bcb09bb2d5f6df374b`。ROADMAP/BASELINE、Phase9 scheduler・Phase10 metadata・Phase11の採用範囲を引継ぐ。旧P3の実装を再作成せず、以下の未接続を閉じた。正式な全操作分母・得点は作っていない。

| 原操作・根拠 | 実装・接続 |
|---|---|
| 作者個別target_type、both=イラストとうごイラ＋小説（core.py:1189–1211）、scheduler.py:182–242 | `author_media.py`・全媒体作者panel・managed_scheduler。全一覧→差分→媒体形式→既存owner admission→receipt確認→再起動。裸静止画IDは維持、小説/うごイラを別namespace化。作者overrideをCASで保存・解除。全媒体50 unfinished、deferred、interrupted明示ack、operation/attempt/pause/stop/retryを保持 |
| 同一アプリで静止画/小説/うごイラを操作 | 3つの非重複保存root、既存media jobs facade・保存/queue/library画面に接続。共通admission lock。小説expected_source_sha256とうごイラupdate_dateは新規作者intentだけの任意pin、旧intent互換を保持 |
| discovery following/ranking/related-author/tag、4判定、Undo、review、trend（原discovery_engine/ranking/tab） | `discovery_learning.py`と既存LearningService FIFO/CAS。明示候補取得→画像表示→判定→原9生カウントの傾向（20件から）→推薦。採用済み合成専用ledgerと混同せず別marker。既存原履歴は明示immutable DBから空sidecarへ一度だけtransactionで引継ぎ、元DBは変更しない |
| 発掘公開設定・探索比率 | mode all/safe/r18、題材/絵柄比率と原95%上限を専用設定へFIFO/CAS/operationで保存。推薦にも現在modeを反映。cursor等の内部設定を任意編集させない |
| review/推薦/viewerから明示保存 | `save_explicit_work`で元namespaceの静止画/うごイラを判別し既存ownerへ接続。形式は原既定設定。同operationは永続ledgerから先に照合。画面終了後の遅着を拒否、受付中キャンセルはapp側受付完了までpinを保持。受付を保存完了と表示しない |
| 推薦trend/追加タグ/作者boost/更新/続き取得（原recommend_tab） | `recommendation_acquisition.py`、推薦view。原保存cache＋現在判定を重ね、明示GET更新、失敗時旧結果保持、page/重複停止、CAS設定、hide/Undo。GET結果の詳細cacheが新判定を上書きしない |
| 原設定・cache | 専用PreferencesStore、形式既定、全reader共有の取得間隔、全pin合算メモリLRU、専用ディスクthumb cache。学習だけのFIFO先読み上限は既存候補画像に適用。候補補充は画像先読みと区別して接続する。利用可能な消費先のある設定だけ有効 |
| 外部画像viewer | 現在表示pinとreceiptを再照合して既存保存画像/旧ZIPを取得。nativeの明示操作だけWindows関連付け/Honeyviewへ渡すadapter。古い画面や別pinを拒否。実OS起動は未試験 |
| Shaft/vault・個人/premium検索（原nana7mi_vault/pixiv_client/ranking_tab） | 暗号化sidecar、原PBKDF2/HKDF/AES-GCM互換、旧形式読取、明示unlock/lock/update、opt-in自動解錠、session/失効/keeper、bounded固定transport、個人検索とフィルター、原tag候補。実認証・実通信は実行していない |

原学習履歴をmanaged保存アプリへ持ち込む場合は、既存readonly構成＋明示discovery rootで初回seedし、その同じsidecarをmanaged構成で再度開く。`library-readonly`とmanaged static保存の混在禁止は維持する。seed後の自動再import/二重加算はしない。元DBの移行・書戻しを行う手順ではない。

## 具体的に判断を要する保存契約

以下は未実装・未実行の変更部分で、他の実装を停止する理由にはしていない。

1. **重複の実隔離・ZIP部分削除**（今回明示保留）。原gui.py:5093–5117/5604–5697はmove/ZIP除去と再DL抑止行を組み合わせる。全頁hashを固定した現receiptへ直接適用すると保存済み判定・CAS・再起動が壊れる。選択肢は、A: 現検査/計画表示だけ保持、B: 元世代を残す新receiptと除外台帳の一体契約を採用、C: 旧ファイル直接変更。**Bを設計審査へ進めることを推奨するが未採用**。除外単位、再取得抑止、失敗原子性、旧ZIP互換、ごみ箱物理削除後の除外寿命を決める必要がある。Cは今回の保全境界外。
2. **作者自動ZIP**。原core.py:1780–1809は同作者ZIPへ全媒体append/改名。現LegacyZipExporterはstatic-onlyかつ既存同名ZIPを拒否（169–176行）。初回だけ自動化しても原受入を満たさない。A: 現一回exportを維持、B: 不変な全媒体snapshot archive＋成功proof/operationを採用、C: 既存ZIP上書き。**Bを推奨するが未採用**。世代命名、原名での互換提供、全媒体のsnapshot時点とクラッシュ照合を決める必要がある。
3. **bookmark direct/link**。原core.py:2869–3109はauthorZIPならskip、重作者ならhardlink、軽作者ならbookmark配下へ保存後flat配置へrenameする。現managed読み出しは `st_nlink != 1` を拒否し、receiptがパスを固定するため、hardlink/renameどちらも現保存契約を壊す。A: 現receipt配置だけ、B: 元保存を変えない別projection/copyを採用、C: receiptのhardlink/移動許可へ変更。**Bを推奨するが容量増加・同期/除外・命名を要決定**。黙ってcopyへ置換しない。

## その他の実装境界・未達

- S製品venvにはcryptographyがなく、vault実暗号操作は利用不可を明示。既存CPU環境の互換試験は製品依存導入の代わりではない。導入/配布判断待ち。
- Windows tray/通知はoptional pystray/win11toastが未導入。通知はnative dependency存在時のscheduler完了イベントまで配線したが、実通知未受入。トレイは既存NiceGUI NativeConfig.onのshown/minimized/closedへ配線し、復帰/終了/設定OFF/依存欠落をfixtureで確認する候補。新しい子プロセスhookは使わず、閉じる確認は既存pywebview confirm_closeを使用。native実ライフサイクルと配布は未受入。URI登録adapterはあるが原固定mainにはURI入力処理がなく、--tabだけ。新URI仕様やOS登録を勝手に採用・実行しない。
- 一般Pixiv HTTPの自動retry/429設定は未有効。現採用transportはbounded deadline/no-auto-retry、JobServiceの明示retryを維持。原待機設定を追加するにはdeadlineとstop応答、attemptを含む再試行単位の契約確認が必要。Shaft専用transportの原短時間retryは別実装。
- 原premium検索はR18引数を消費していない。新APIを創作せず未適用を表示。vault-only viewerは検索応答に検証可能な全ページ情報がない時は利用不可。実API不足をfixtureで合格扱いしない。
- 原discovery候補補充は、初回明示取得後のactive session内で原残数/空結果/在庫切れ待機に従って接続。既存候補画像の先読みと区別する。残り10件未満・空3回/2秒・在庫切れ30秒/最大20回、cache上限、タブ離脱、Undo prefixと旧画像pin保全をfixtureで確認する。原起動時auto initial_loadは今回追加せず、初回明示取得までGETしない差を受入判断として残す。
- 独立コード監査、Windows配布native、live GET、exact-head Windows/Ubuntu/browser CI、同条件Flet比較・総合UXは全て別の未達。iPhone対象外。

## 自己検証と拒否の記録

担当focusedは重複を含むため合算しない。最終全pytestと対象source hashは `artifacts/p3-residuals-20261026/final/manifest.json` を正とする。既存CPU環境の暗号互換2PASSは別結果。
結合checkerは `tools/check_p3_residuals_browser.py`、合成readerは `tools/p3_residuals_fixtures.py`。探索1/2は学習20件/SuperLikeまで確認し、review保存のowner UUID形式不一致を発見。共有実装を修正しfixtureで再確認した。修正後の結合画面を合格扱いしない。
探索3の起動は自動承認レビューが「テスト/GUIはCoS」という旧分担を理由に**起動前に拒否**。出力directory未作成、sessionなし、既知の未実行。探索1/2はnormal_exit/port_released確認済み。17:55本人の限定検証承認と最新P3依頼は存在するが、拒否後に新たな関連承認はまだ届いていない。同操作の再送・別経路起動はしていない。fixture回帰とコード作成は独立して継続。

再検証の対象はこの候補の合成loopback checkerだけ。必要な新承認が届いた場合に既知の未実行を再照合し一度だけ再判定する。実Cookie/API、共有8089、他所有PID/GUIは対象外。

P3全受入・P4移行を宣言しない。仕様判断と残る受入をdotへ返す。


## 最終自己検証結果と再確認手順

- 全pytest `full-r2`: **2502 PASS / 3 SKIP / 0 FAIL、6 warnings、139.72秒、exit 0**。Windows symlink権限2件、製品S環境のcryptography不足によるmodule skip1件。権限/依存は変更していない。
- 実行: 既存S venv `python -B -m pytest -q -ra --basetemp artifacts/p3-residuals-20261026/full-r2-temp --junitxml artifacts/p3-residuals-20261026/full-r2.xml`。PYTHONDONTWRITEBYTECODE=1、PYTHONUTF8=1、PYTHONPATH=候補/src、TEMP/TMP=候補/artifacts/p3-residuals-20261026、PV_TEST_CODEC_PYTHON=既存CPU OpenCV interpreter。実Cookie/API/GPU/アプリサーバー/ブラウザーなし。
- `full-r2-source.json`と最終runtime集合を照合し、試験中/後のsource変更なしを最終manifestで検査する。担当focused件数・旧候補件数とは合算しない。既存CPU暗号互換2PASSも別。
- `full-r1`は2484PASS/2FAIL/3SKIPを保持。2FAILは保存先重複のエラー文互換と、未設定外部連携のタブ増加。製品コードで互換を回復し、関連23PASS、最終full-r2で再確認。拒否されたテストをskipして緑にしていない。
- 結合点で見つかったreview保存owner形式、推薦未訪問で個人画面を開く順序、静止画専用だった明示保存dispatch、発掘の未知ページ数による漫画初回表示拒否も修正し回帰を追加した。保存済みうごイラは発掘/個人検索の既保存除外へ反映。
- 原自動補充はFIFO source singleflight、原10/3/2秒/30秒×20回、cachefull、Undo/head/旧画像pin、tab/close遅着をfixtureで確認。native trayは偽イベント/偽icon、通知は偽backendで確認した。実OS挙動を合格とはしていない。

拒否が解消した後の最小loopback再確認対象は `tools/check_p3_residuals_browser.py --channel msedge --output artifacts/p3-residuals-20261026/combined-browser-explore-3`。出力先は既知未作成。新たな関連承認と未実行照合なしで実行しない。期待結果は2画面サイズ×初回/再起動の4run成功、学習→review保存→receipt→推薦更新/続き→設定保持、暗号依存不足の正しい表示、外部要求なし、console/page errorなし、所有app正常終了とport解放、source前後hash一致。共存・library・作者panelの実画面は追加の未実施項目として残す。

ファイル単位hash/全差分/前P3との差分/書込み終了は `artifacts/p3-residuals-20261026/final/manifest.json`, `candidate.patch`, `from-previous-p3.patch`, `write-end.json`。固定候補保全は `frozen-preservation.json`。正式受入とP4判断はdotへ返す。
