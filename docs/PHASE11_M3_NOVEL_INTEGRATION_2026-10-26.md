# Phase11 M3 単一小説 JobService 接続 — CoS 引継ぎ (2026-10-26)

状態: **実装・回帰コード準備済み、CoS 実行検証待ち**。Phase11 / 全移植 / M3 全範囲の受入完了ではない。

## 固定位置と所有

- 専用 checkout: `S:/Projects/PixivVault-NiceGUI-m3-novel-integration-20261026`
- branch: `phase11/m3-novel-integration-20261026`
- HEAD / 採用依存: PR #34 `2cf4a7a7286dd728e8df8125094498a3622babba`
- 共通 base: `31aad331846fc3d4c3aceac4f7a90fb61f05b111` (merge-base 読取で一致)
- PR #33 `adb6bce48012e286043b5acae46253fa2258bc7d`、PR #35 `50f8ae658a112f4db260ee979af97ee71e5cce93` の追加取込みなし。この範囲は PR34 にある parser/exporter/storage で実装。
- 実装所有者はこの Codex 1名。開始前は候補未作成。CIM 標準権限読取拒否後、承認された読取で PixivVault 関連 Python 2件を検出したが、対象候補を指すものは0件。候補の存在を再確認して独立 clone を作成。元 checkout の Git metadata を共有しない。
- 最初の実ファイル作成は clone/checkout 後の `src/pixivvault_nicegui/novel_storage.py` 実在で確認。その時点は実装着手であり完成報告ではない。
- 原版 PV、利用者データ、他候補、PR34 凍結 staging は変更していない。commit / push / PR / main 統合なし。C に開発ファイルを作成していない。

## 製品接続

`ApplicationServices(novel_fixture_root=専用絶対パス, novel_fixture_source=FixtureNovelSource(...))`
→ `enqueue_novel_export(NovelJobIntent(...), operation_id)`
→ 既存 `services.jobs` (`JobService` 永続 SQLite)
→ fixture の本文取得・作品/作者照合
→ `NovelExportStore.save` (TXT / EPUB / BOTH)
→ 保存直前の receipt path + SHA journal
→ 全ファイル再照合
→ `JobContext.publish` 内の世代 CAS
→ 既存 JobService の永続 done
→ close / 再生成時の同じ operation・receipt 照合。

`current_novel_export(work_id, format)` は active receipt と出力 bytes を照合する。UI スレッド外で呼ぶ API であり、画面や CLI 起動引数は今回追加していない。

- kind=`novel-export`、target=`<work_id>:<TXT|EPUB|BOTH>`。作品名・本文・作者名を JobSnapshot/operation fingerprint/journal に入れない。
- root marker=`.pixivvault-novel-jobs.json`、`jobs.sqlite3`、`novel-journal.json`、`exports/`。イラスト/うごイラ用 receipt/schema/index を変更しない。
- JobService の操作・履歴・operation tombstone と `attempt` をそのまま使う。メモリ map を永続 ledger の代わりにしない。別 operation の未完同一 intent は同一 job に束ね、作者等の異なる intent は拒否。
- journal の accepted fingerprint は job_id + canonical intent。pending proof に保存元 attempt を記録し、古い context は新 attempt / Stop / delete / close に対して publish できない。
- 初回は predecessor なし。更新は caller が取得済みの **path と SHA の両方**を指定。receipt 本体が同じ SHA でも別 generation path は古い CAS になる。破損した predecessor は通常更新で置換しない。
- storage の `before_publish` hook は検証済み stage の receipt を rename 前に journal 化。停止が rename 後に勝った場合、immutable 出力と pending proof を保存しつつ active/done にしない。
- restart の `interrupted` だけを復旧対象とする。accepted identity + exact proof + 全出力 integrity + predecessor CAS 一致時のみ done。不足・不整合は error/interrupted として明示 retry を要求。明示 Stop や delete は成功に書き換えない。
- journal replace/fsync の成否が曖昧なら writer を fence し、再openまで書かない。破損した done 出力や不正 journal は startup 拒否、元 bytes を保持。
- journal は最大8MiB・各map最大10000件、失敗 journal stage は256件/合計16MiB予算。出力は PR34 の256世代/1024ファイル/256MiB予算を継承。失敗 stage の自動削除なし。
- `FixtureNovelSource` は JSON 化・コピーした有限の合成データだけ。HTTP、Cookie、実 Pixiv/nana7mi への fallback はない。他の live/read-only/download/scheduler 設定との混在を拒否し、Phase10 の metadata/scheduler admission を迂回しない。

## CoS 最小手順 (この担当は未実行)

まず最終 SHA manifest と実ファイルを照合。以下は CoS だけが実行する。既存承認済み venv に必要依存が存在する場合に限り使用し、依存導入はしない。

```powershell
Set-Location -LiteralPath 'S:\Projects\PixivVault-NiceGUI-m3-novel-integration-20261026'
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
$env:PYTHONDONTWRITEBYTECODE = '1'
$python = 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe'
$run = Join-Path (Get-Location) ('artifacts\cos-m3-' + (Get-Date -Format 'yyyyMMdd-HHmmss-ffff'))
if (Test-Path -LiteralPath $run) { throw 'Choose a fresh evidence directory' }
New-Item -ItemType Directory -Path $run | Out-Null
& $python -X utf8 -m pytest -q tests/test_managed_novel_jobs.py --basetemp "$run\tmp-focused" --junitxml "$run\focused.xml"
```

期待: 全case PASS、FAIL/ERRORなし。テストは一時rootだけを使い、アプリサービス経由の3形式 enqueue→保存→done→再open、同一operation再投入で世代増加なし、CAS旧path拒否、pause/Stop/delete/retry、古いattempt、通常close、実子プロセスの保存直前/直後/active確定直後終了、fsync障害、破損データ、二重owner、live混在拒否、照合vs保存ロック競合を含む。単体部品だけの PASS を受入にしない。case件数・所要時間は実測後に記録する。

focused成功後、JobService/storage/service変更の回帰を確認:

```powershell
& $python -X utf8 -m pytest -q tests/test_job_service.py tests/test_novel_storage.py tests/test_novel_export.py tests/test_novel_series_export.py tests/test_services.py tests/test_download_services.py tests/test_managed_download_services.py tests/test_author_v2_services_restart.py tests/test_metadata_view_service.py tests/test_managed_scheduler.py --basetemp "$run\tmp-regression" --junitxml "$run\regression.xml"
```

上記成功後、受入担当の判断で既存全pytestを同じ source SHA 固定で実施。新fixtureテストの子プロセスは合成rootへの書込みと意図的 `os._exit(73)` のみ。GPU・GUI・実通信は必要ない。テスト出力を過去の凍結証跡へ上書きしない。

## 未検証・残件

- この担当は pytest / lint / syntax compile / アプリ / browser / native / GPU を**一切起動していない**。差分・コードの読み戻しと SHA 取得のみ。PASS件数・性能・完成受入を主張しない。
- CoS のfocused/regression/full結果、Windows/Ubuntu、独立レビューP1/P2、exact-source記録待ち。自動CI設定の追加は今回なし、公開操作なし。
- M4の入力・形式選択・キュー操作画面、ライブラリ/reader統合、native/Edge実導線、ライブ小説取得、シリーズ/挿絵統合、うごイラ同居、実Cookie、iPhoneは未接続または今回対象外。
- Windowsディレクトリfsyncの電源断保証は PR34 と同じ限界。初期markerの途中書込みや不正ledgerは安全側で起動拒否する。出力/失敗stage/operation tombstone の容量回収UIはない。
- `JobContext.publish` はbounded local journal IO中に control lock を保持する。snapshotは既存のimmutable tuple読取を継承するが、操作待ち時間・UIレスポンスはCoS実測待ち。
- active更新中の `current_novel_export` は再照合後に世代が変わっていれば固定エラーを返し、callerの明示再読取を要求する。照合失敗をmissing成功へ変えない。
- 元仕様や追加承認の不足による実装停止はなし。今回の境界はユーザー指定のfixture実装/未実行引継ぎ。S: 書込みはsandboxの明示承認経由で行い、拒否の迂回はしていない。

最終ファイルSHA・差分patch・書込み終了時刻は `artifacts/m3-novel-integration-20261026/` の manifest / write-end に記録する。以後のCoS検証が完了するまで受入済みへ昇格しない。
