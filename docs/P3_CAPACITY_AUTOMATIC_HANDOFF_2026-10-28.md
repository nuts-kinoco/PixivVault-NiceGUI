# PV P3 未計測作者の自動容量計測 — CoS引継ぎ

## 状態・固定基線

実装と回帰コードの納品。pytest・collect・製品import・構文検査・build・GUI・アプリ起動・実API・実データ走査は実装担当未実行。独立監査、機能受入、L12全体・P3完了とはしない。元152操作台帳のL12原行未取得は保留し、分子・分母を更新しない。

固定285は `S:/Projects/PixivVault-NiceGUI-p3-viewer-parity-fix-20261028`、HEAD `2cf4a7a7286dd728e8df8125094498a3622babba`、branch `phase11/p3-viewer-parity-fix-20261028`、dirty285、集合SHA `782c6d4a307654de7f86ab10ff7b4a7f53f568a3e8cbf916e428d855494f4004`。WRITE_ENDと全285ファイルを照合し、前操作完了・該当process不在を確認後に複製した。

新候補は `S:/Projects/PixivVault-NiceGUI-p3-capacity-auto-20261028`、branch `phase11/p3-capacity-auto-20261028`、同HEAD。専用候補以外へ書込まず、旧CoS artifact/合成DB/実データを複製していない。関連AGENTSを読取り、指示ファイルは不変更。checkout内の関連skillsは見つからなかった。

固定285のCoS原票 `artifacts/p3-viewer-parity-fix-20261028/cos-owned-summary-r1/P3_285_CPU_GUI_INDEPENDENT_ACCEPTANCE.json` SHA `ddc267455c94524500e4025d5465b8ec38d7703610b2222ad5731ac841c97aab` を照合。CPU246 PASS、通常GUI4/4、309文字root初回/再起動2/2、所有終了・hash保全の証拠を引き継ぐ。長パスの旧assert2式は変更され、構造・復元・実SQLite read-only/書込拒否で確認された限定合格であり、元assert全文不変更とはしない。今回変更したLibraryViewとfixtureは別検証が必要。

## 原条件と今回閉じるコード差

原Flet固定ref `3ac3aa0d47a64785251593bcb09bb2d5f6df374b` の `docs/design/LIBRARY_TAB_REDESIGN_SPEC_2026-08-16.md:133–168,198–214` と `library_tab.py:409–479,2259–2260` を読取り照合した。

- 保存済cacheの復元と未計測作者のディスク計測は別経路。285には前者と明示再計測だけがあった。原版の_renderはshownに対して_ensure_capacityを呼ぶ。今回もまず全作者の既存cacheを復元して容量ソートを保ち、その後、**表示中の未計測作者だけ**を1人ずつバックグラウンド処理する。初回50件を恒久上限にせず、「さらに表示」や検索・ソートで表示対象になった51人目以降も処理する。非表示作者の全フォルダー走査を初回から追加しない。
- キャッシュ済み作者は0を含め再走査しない。新専用cacheの成功値を優先し、元read-only DBのlibrary_capacity_cache由来の既存値も再走査防止に使う。作品単位capacity_bytesのヒントだけを「計測済み」と誤認しない。元DBのcache行を移行・更新・削除する機構は追加していない。
- 1作者ずつ、既存LegacyCapacityService→既存LegacyCapacityReader（選択ZIP本体または選択folder全files）→MeasurementLease→CapacityCache.commit_batchで保存。成功の都度画面の通常容量・容量ソートへ反映する。managedの別容量表示へ加算せず、保存先の新しい混合規則を作らない。
- 保存先なしの成功は0、失敗はNoneとしてcacheへ書かない。失敗作者を飛ばして正常作者を継続。試行済集合は同じタブ表示中の検索・sort・reload・再描画で保持し、失敗を周期的に再試行しない。タブ再訪/マイブックマークから作者一覧への再訪では未計測を再試行できる。既存の明示再計測も維持。
- 1つのviewでは自動workerと明示再計測を重ねない。I/O/SQLite待ちはto_threadへ出す。世代/epoch/state/サービスidentityを確認し、検索・sort・reload・作者詳細・タブ非表示・closeで失効した結果は画面へ適用しない。失効は既存leaseへ取消を要求する。worker終了までbusyを保持し、未終了を終了済みに見せない。終了後に現在画面の必要な復元/未計測だけを再登録する。
- 取消成立前の値は永続化しない。既存leaseのSEALEDはcommit決定であり成功ではない。決定後取消の物理commitは既存254以降の契約を保持し、成功確定前の表示・失効画面への適用をしない。他writerの成功が先ならCASを負けた側が上書きせず、その確定cacheを読んで表示する。cache/lease/保存契約自体は不変更。
- 自動計測の進行・失敗は `library-legacy-capacity-auto-status` に表示。明示再計測とmanaged計測のstatusを上書きしない。失効時には旧進行表示を消す。

## 変更範囲と回帰意図

製品変更は `src/pixivvault_nicegui/ui/library_view.py` のみ。新しい管理service/DB/schema/保存方式は追加しない。新回帰 `tests/test_capacity_automatic.py` は実LibraryView・合成旧folder/ZIP・実SQLiteを使い、次を判別するコードを収める。

1. 62作者、既存cache正値/0、未計測の順次処理、途中のUI操作、50→62表示、全件cache復元と再起動で再走査なし、元合成bytes不変。
2. 1作者の失敗はNoneのまま、後続/0成功、同じvisitの検索/sort/reloadで再試行なし、次回visitで復帰。
3. inactive/subview/author/search/sort/reload/closeで止めた旧workerの特異な値999999がcache/画面へ入らない、後続に同時workerがない。
4. cache出版失敗の実注入発火、他作者継続、再訪後の再試行。実SQLite writer lock下の決定前取消、決定後待機中のSEALEDと成功/画面不反映の区別。
5. 同一viewの再active/明示クリックで重複workerなし、他writerのCAS勝ちを保持、サービス閉鎖時の旧結果・再登録拒否。
6. 通常ApplicationServices(readonly DB + 旧image root + owned preferences)からのZIP優先・folder合計・保存先なし0・元DB既存cache0保持と再起動。元DB/合成ファイルSHA保全。

既存fixture3ファイルの前提も合わせた。`test_capacity_reactivation` の未計測作者には合成OSErrorを返す（新契約では自動試行されるため）。cached作者の走査は呼出履歴とassertで禁止し、元の値/revision/古いcache読取の拒否を維持。`test_capacity_lease_sqlite` のUI決定後取消は実際に起動される自動taskを待つ。`test_legacy_capacity_connected` は自動取消→失敗した自動試行→既存明示再計測→cache復元を通り、元の0/None・revision1・画面不反映assertを保持。task件数の固定期待は、実taskの完了を追う前提へ変更した。callback no-op、skip、timeout延長、成功assert削除で通していない。

`tools/serve_capacity_auto_fixture.py` はCoS専用の合成起動コード。通常readonly構成と実DemoShell/LibraryViewを使い、実API/実Cookie/実画像は使わない。fixture初期化は新専用rootにのみ行う。元成果のviewer/JobService/desktop、L12 reader/cache/lease、D15/S21/X04/L13製品は285とbyte同一。完全な対応はsource-manifest.jsonへ記録する。

## CoS一括CPU入口（未実行）

既存S側venvと既存pytest設定を使用。新候補cwd、新cos-owned領域のみ。新依存・OS権限変更なし。

```powershell
Set-Location S:/Projects/PixivVault-NiceGUI-p3-capacity-auto-20261028
$env:PYTHONUTF8='1'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH='S:/Projects/PixivVault-NiceGUI-p3-capacity-auto-20261028/src'
& S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_capacity_automatic.py tests/test_capacity_reactivation.py tests/test_capacity_lease_sqlite.py tests/test_capacity_cancel_nonblocking.py tests/test_capacity_cache_injected.py tests/test_capacity_normal_configuration.py tests/test_capacity_selection.py tests/test_legacy_capacity_connected.py tests/test_capacity_display.py tests/test_managed_capacity.py tests/test_viewer_restart_boundaries.py tests/test_library_view.py tests/test_library_state.py tests/test_library_readonly_integration.py tests/test_library_filter_ui.py tests/test_library_bookmark_integration.py --basetemp=artifacts/p3-capacity-auto-20261028/cos-owned-r1/tmp --junitxml=artifacts/p3-capacity-auto-20261028/cos-owned-r1/impact.xml
```

元のlease/表示/managed/原意味論と新自動処理を同時確認する16モジュール。collect未実行なのでPASS数を予告しない。285の246 PASSをそのまま変更後の実結果にしない。Eventの5秒は故障/停止を検出する試験上限で、製品の新しい応答基準ではない。旧拒否の実データGUI・追加監査原文の回収はこの手順へ含めない。

## CoS合成GUI入口（未起動）

既存8089/他所有processを触らず、CoSが空き・所有を確認した専用portを使う。例52403。launcherは**新候補tools原本の位置**から起動する（__file__で許可rootを決めるため、別artifactへコピーしない）。

```powershell
& S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B tools/serve_capacity_auto_fixture.py --port 52403 --root S:/Projects/PixivVault-NiceGUI-p3-capacity-auto-20261028/artifacts/p3-capacity-auto-20261028/cos-owned-r1/gui-1280/data --pause-first --fail-author 1003
```

1. 1280×720/darkの通常画面からライブラリを開く。初期50作者のカードが表示されたまま、`/_fixture/state` のcalls先頭1人/active1となること。UIが使えることを通常カードサイズ操作で確認する。まだ「再計測」「DB更新」は押さない。
2. 別のloopbackタブ `/_fixture/control` の「合成計測の停止を解除」を通常クリック。故障注入制御だけで製品保存APIを代行しない。作者ごとに容量が埋まり、1002は保存先なし0、1003は未計測のまま、他の初期作者は成功すること。cacheは成功revision1、1003は[0,null]、max_active1。ZIPの1001はZIP本体、他作者はfolder全filesの合計であることを合成rootの実サイズと比較する。
3. 「さらに表示」で51〜62人目へ進む。先の作者のcallsを増やさず、新たな表示対象だけ計測されること。検索/容量sort/タブ内再描画では1003の失敗を繰り返さない。元DB/画像/ZIPのhash不変を記録する。
4. controlの合成失敗checkboxをOFF、製品で別タブ→ライブラリを通常操作。1003だけ再試行して成功し、全62作者の成功値を得ること。以後同じタブ再訪でcallsは増えない。
5. 所有serverを通常終了してPID/port解放を確認。**同じdata root**・同じlauncherを `--pause-first` / `--fail-author` なしで再起動。明示DB更新/再計測なしにcache容量が表示され、calls空のまま、51人目以降もcache復元され、容量sortが正しいこと。
6. 1920×1080/lightは別新規root `gui-1920/data` と製品の既存theme操作を使い、初回・再起動を確認する。進行/失敗ラベルとカードのクリッピング・横はみ出し・重なり、console/page error、外部request0を記録。取消とSQLite競合の細かな境界はCPU原票と区別し、GUIで未確認の条件を合格にしない。

`/_fixture/state` は観測専用で、呼出履歴/並行数/cache値を返す。GETして計測/保存を代行する機能はない。実サービスや本番rootへの接続はしない。終了時は所有server/browserだけ通常後始末し、候補/固定285以下のmanifest、process/port残存0、画面とログを残す。失敗を短root/固定sleep/期待値緩和で隠さない。

## 成果物と未達

`artifacts/p3-capacity-auto-20261028/manifest.json` が全dirty集合とSHA、`source-manifest.json` がsrc/tools全体、`changes-vs-285.json` / `.diff` が今回差分、`original-contract.json` が原固定refの照合範囲、`review-ranges.json` が正確なレビュー用行範囲、`preservation.json` が固定285/283/271/267/265/259の保全、`WRITE_END.json` が書込み終了記録。hash循環を避け、最終集合SHAは本文へ再転記しない。

今回の自動計測CPU/GUI・独立レビューは未実施。全L12原行対応、全P3受入、native/live/CI、旧ZIP更新/作者改名/物理隔離の未採用契約は別境界のまま。実装の出口を満たすコードと検証入口を納品するが、局所PASSや過去の合格だけで製品完成としない。追加相談待ちや拒否迂回は行っていない。
