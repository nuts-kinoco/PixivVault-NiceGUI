# PV-B 個別保存の配置・履歴・表示GIF／再起動境界 引継ぎ

## 状態と基点

コードと回帰コードの実装納品。実装担当は pytest、製品import、構文検査、build、GUI、製品起動、実API、実データ走査を一切実行していない。自己点検はソース・原仕様・差分の静的照合のみ。機能受入、独立レビュー、P3全体完了とはしない。元152操作台帳の分母・達成数を更新しない。

- 固定271: `S:/Projects/PixivVault-NiceGUI-p3-viewer-page-save-20261028`、branch `phase11/p3-viewer-page-save-20261028`、HEAD `2cf4a7a7286dd728e8df8125094498a3622babba`、271 dirty、集合SHA `9c068bfe3718c27b1fed15c03d4cb0bc11f4f3c46d555915c04b9e2caeba4e97`。書込み終了記録と271個別SHAを照合後に専用候補を作成。CoSの実行中候補・証跡へ書いていない。
- 新候補: `S:/Projects/PixivVault-NiceGUI-p3-viewer-save-parity-20261028`、branch `phase11/p3-viewer-save-parity-20261028`、同じHEAD。ローカル基線とmanifest記載の製品差分だけを複製。CoS artifact、合成DB、利用者データは複製していない。
- 完全dirty集合・source/tool全ファイル・271からの差分は `artifacts/p3-viewer-save-parity-20261028/` の `manifest.json`, `source-manifest.json`, `changes-vs-271.json`, `changes-vs-271.diff`。最終終了証拠は `WRITE_END.json`。ここに候補集合SHAを再転記して循環hashを作らない。

## 原条件と接続

原Flet ref `3ac3aa0d47a64785251593bcb09bb2d5f6df374b` を読取り。台帳の未取得V/A行番号を推測して新しい採点単位を作らず、以下の原操作に対応させた。

1. `artbook_viewer.py:116–185,188–237,976–1005`: 個別DLを作者名(ID)/作品名先頭30文字(ID)へ配置。use_work_folder=0は既存legacy_filename（GIFは既存arcname）、1は原URL basename。作者名が未取得なら既存snapshotの作者辞書→専用projectionの辞書順先頭フォルダー→rootを使用。初回の配置先はmanifestで固定し、再訪時の物理フォルダー変化で別名へ再出力しない。元フォルダーやDBは参照・更新しない。
2. `artbook_viewer.py:875–907`, `database.py:1711–1751`, `gui.py:4497–4507`: 保存後の履歴を全作品完了とは分離。新しい個別export manifestを既存SaveExports.rootへ保存し、既存projectionへ出力後だけ履歴を公開。作者単位・最新日時・最大60作者の「直近のバックアップ」を実shellへ接続。既存共通カードサイズを使用し、保存先を開く実handlerは既存DesktopIntegrationの明示許可rootだけを使用する。全作品receipt/active pointer/差分DL/schedulerの保存済判定へ部分保存を入れない。
3. `artbook_viewer.py:1028–1095`: うごイラ個別DLは、表示に使った同じGIF bytesを保存する。既存metadata/ZIP取得と安全なGIF rendererを表示へ接続し、Mounted image.decode成功の要求世代だけを保存可能にする。保存時のZIP再取得・再変換・MP4/frames設定への置換なし。既存可変delay/画素/ZIP安全境界を再利用。GIFはviewer-pagesの個別receipt、exportは同じprojectionへ。静止画の全作品完了や専用うごイラjobを捏造しない。
4. 原仕様の「実保存成功、履歴失敗は別々に伝える」を維持。原本receiptが確定した後のprojection/manifest失敗は、保存済み原本と再試行をUIに明記。再試行は同receipt/表示GIFを使い、GETを増やさない。変更された出力の自動上書き・削除は行わない。
5. 271の拡張WindowsパスによるDB再起動不具合: `_Store._verify` のURI生成だけを `logical_path(self.path).as_uri()` へ変更。`mode=ro&immutable=1`、application_id、v1/v2テーブル照合、operations/history検証、lock/所有検証は保持。通常のnative pathによるファイルI/Oも保持。CoSの最終原票は未受領であり、親共有の対照と現物コードを根拠にした修正候補。
6. L12: `ApplicationServices.library_snapshot()` は成功済snapshotを返すだけで全作者cacheを追加復元しない。したがって先頭50作者限定の復元は51作者以上の容量sortを実際に欠落させる。LibraryViewのcache復元対象をsnapshot全作者へ変更。再計測は従来の表示中先頭50作者のまま。0/None、旧応答棄却、検索/sort/再訪・計測終了後の再登録、lease/SQLite commit契約は変更しない。

## 保持と未達

- 固定271/267/265/259の全manifest記載ファイルの再照合は `preservation.json`。L12 cache/lease/adapterとD15、X04/S21/L13の専用製品部品・既存fixtureはbyte同一。ただし共通LibraryView、shell、services、VerifiedImage、JobServiceは今回の影響範囲なので、以前のGUI/CPU結果を新候補の合格へ自動継承しない。
- 個別ページreceiptはv2に原URLのbasenameだけを追加。既存v1を厳密読取りし、足りないfilenameだけ必要時に正規metadata取得で補う。実URL/認証値をreceiptに保存しない。全作品receiptスキーマは不変更。
- 既存の不変CASを編集可能なhardlinkへ接続しない。個別projectionは所有コピー。ユーザー編集・未知ファイル・リンク/reparse・破損を拒否し、別作品に波及させない。既存ZIP更新/作者改名/物理隔離、遠隔更新時の既存個別成果置換は未採用のまま。
- この履歴入口は今回の個別DLが対象。原版の全媒体・過去全件を含むバックアップ一覧、旧DBのtouch_last_backup、旧保存先への配置・既存ZIPへの追記を実装済みとはしない。全作品の既存author projectionでwork-folder時もlegacy_filenameを使用する経路は今回変更していないため、個別DLの原URL basenameとの完全な同名再利用は別残差として明示する。全作品receiptに無根拠な元ファイル名を補って既存出力を改名しない。
- 書込み先は専用SaveExports.root/projectionで、実ユーザーのsave_pathを置換しない。desktop capability無しの起動では保存先を開くbuttonは無効。合成launcherは受理した相対pathを記録するだけでOS appを起動しない。native Explorer等での確認は未実施。
- GIF合成回帰は色順・frame件数・delay・表示bytesと保存SHAを検査するコード。実測済みや任意の全GIF品質保証とはしない。既存rendererの制限を新設・緩和していない。
- 先行CoS: 271 CPU112PASS/1SKIPと初回ページ保存成功・再起動失敗、267 CPU229PASSと自動容量復元、265 CPU278PASS/4SKIPと合成GUI16/16は固定候補の証拠。実データGUIの拒否操作は引き続き局所保留、再送していない。独立監査/native/live/exact-head CI/全P3横断受入は別の未達。

## 回帰コードとCoS一括確認

新 `tests/test_viewer_save_parity.py`: 原配置ON/OFF、元bytes、完全保存からの選択ページ無GET、v1原本保全、原本/出力/履歴失敗分離・実注入発火・Retry、編集保護・後続作者、深いWindows path、不正manifest、GIF色/尺/frame順・表示と保存の同一性・実Gallery handler・遅着閉鎖、実履歴button handler・作者集約、作者名不足の原fallbackとpin保持。fixtureは生成RGBとZIPだけ。

新 `tests/test_viewer_restart_boundaries.py`: native/resolve双方の285文字超DB再起動、URIの厳密期待値、同operation再受付でdispatchなし、application_id/余分テーブル拒否、62作者のcache全件復元・51人目sort先頭・0・追加表示・再計測50件維持。既存 `test_live_bookmark_integration.py` はanimation未接続の期待を、新接続と取得失敗表示・他作品継続へ変更。`test_verified_image.py` は表示GIFの短い通知identityを追加し、元のdecode/実resource照合/世代/前画像保持assertを維持。

以下はCoSのみ。新候補cwd、既存S側venvと既存pytest設定。`PYTHONUTF8=1`, `PYTHONDONTWRITEBYTECODE=1`, `PYTHONPATH=<new candidate>/src`。新しい `cos-owned-r1` を使用し、既存証拠/他候補のtmpを使い回さない。依存追加なし、OS権限変更なし。実装担当は未collect・未実行。

```powershell
& S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_viewer_save_parity.py tests/test_viewer_restart_boundaries.py tests/test_viewer_page_save.py tests/test_viewer_save.py tests/test_verified_image.py tests/test_live_bookmark_integration.py tests/test_library_media_registry.py tests/test_ugoira_gif.py tests/test_job_service.py tests/test_native_job_history_compat.py tests/test_native_operation_bindings.py tests/test_capacity_reactivation.py tests/test_p3_viewer_author.py tests/test_artwork_viewer.py tests/test_gallery_pages.py tests/test_library_view.py tests/test_p3_card_size_shared.py --basetemp=artifacts/p3-viewer-save-parity-20261028/cos-owned-r1/tmp --junitxml=artifacts/p3-viewer-save-parity-20261028/cos-owned-r1/impact.xml
```

失敗時にtimeout延長、callbackのno-op化、短pathへの移転、失敗assert削除で通さない。元のEvent待ちは検査の停止検知で、製品応答時間の新しい閾値ではない。全pytest/全媒体JobService/native検証は別ゲートとして結果と実行範囲を区別する。

## CoS合成Edge（新規入口、通常クリック）

`serve_viewer_save_parity_fixture.py` は実services/export/settings/shell/viewerを使用。`/_fixture/state` はread-onlyで、保存APIを代行しない。static原画像とpreviewを別bytesにし、うごイラは合成metadata+ZIP。取得の実transportを生成しない。取得失敗checkboxは合成fixture限定。

```powershell
# CoSが空きと所有を確認した専用port例。8089/他所有PIDは触らない。
& S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B tools/serve_viewer_save_parity_fixture.py --port 52387 --root S:/Projects/PixivVault-NiceGUI-p3-viewer-save-parity-20261028/artifacts/p3-viewer-save-parity-20261028/cos-owned-r1/gui-1280/data
# 別のCoS所有shell。起動成功・所有PID確認後にだけ実行。
& S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B tools/check_viewer_save_parity_browser.py --url http://127.0.0.1:52387 --output S:/Projects/PixivVault-NiceGUI-p3-viewer-save-parity-20261028/artifacts/p3-viewer-save-parity-20261028/cos-owned-r1/gui-1280/initial --width 1280 --height 720
```

- 初回: 123の2/3を通常保存、再押下で原画像取得はindex1の1回だけ。settingsからuse_work_folder ONを保存し3/3を保存。作者/作品/原basenameとbytes SHA確認。299を通常表示し、実DOMの表示GIF hashと保存GIFを比較、ZIP取得1回。履歴2作者の表示と実buttonからの保存先opening要求（fake launcher記録）を確認。complete receipt/jobは0のまま。
- CoSが所有serverを通常終了してPID/port解放を確認し、**同じdata root** で同じlauncherを再起動。checkerに `--restart`、別output `.../restart` を指定。履歴が取得なしで表示され、3/3再押下でもoriginal_image=0・export manifest/hash不変を確認。HTTP起動成功だけでは再起動合格にしない。
- 1920×1080/lightは別の `gui-1920/data` を初回から使用し、checkerへ `--width 1920 --height 1080 --light`。再起動にも `--light --restart`。以上2サイズ×初回/再起動4ケース。新履歴・設定・個別保存のクリッピングと重なり、初回/再訪の表示をスクリーンショットで確認する。旧成功動線の全面やり直しではない。
- 各実行はresult.json、history.png、GIF表示画像を出力。失敗時はfailure.pngとfailure-dom.html。console/page error/外部requestは0が期待。固定sleep、強制click、JS dispatch、内部API保存代行なし。JSは画像hashとread-only完了観測だけ。
- 手動失敗確認は専用の新規fixture rootで合成取得失敗をONにし、未保存ページまたは未表示299を通常操作。保存済と表示されず再試行可能、OFF後に正常復帰すること。出力/履歴のI/O失敗はCPUの実handler故障注入で別に検証する。
- L12は上記CPU62作者回帰と既存267再起動fixtureを新候補で確認。CoSが必要と判断した実画面では51人目以降のcacheが容量sort前に適用されることを別記録する。cache/leaseや元データを新規計測して証明しない。
- 終了時は所有server/browserだけ通常後始末。PID/port残存0、候補全manifest/固定271/267/265/259の前後hashを記録。今回コードと既存先行証拠の成功を混在させない。

## 独立レビューの入口

`review-ranges.json` に今回変更した関数と正確な現行行番号を保存。特に部分/全体保存の分離、v1ページreceipt互換、出力pin/故障回復、GIF source世代と実decode、原filename、全作者cache復元の失効、JobService URIと厳密検証の保持を確認する。設計・実装者の自己点検を独立監査に数えない。
