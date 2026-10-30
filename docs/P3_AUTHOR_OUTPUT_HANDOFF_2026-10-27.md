# P3 旧マスター所属・作者保存後出力接続（未試験）

専用候補 S:/Projects/PixivVault-NiceGUI-p3-author-output-20261027、branch phase11/p3-author-output-20261027、HEAD 2cf4a7a7286dd728e8df8125094498a3622babba。固定author-layout191 / SHA08a2c698e17d8a816b5fbaa8aac28ae9aff69bb8dd3302de204b108dff51d99fを開始時個別hash照合して別候補へコピー。旧191とClaude検証中zip-init-io189への編集/プロセス操作なし。CoS/Claude artifacts・利用者データはコピーしない。製品実行/pytest/import/構文検査/GUIは実施しない。

## 原操作と実装接続

原core.py2869–2898の実フォルダー所属判定、作者保存が先にマスターを作りbookmarkが後からmirrorする順序を維持。作者登録やCAS上の作者存在だけでheavyへ分類しない。

bookmark_projection.legacy_membershipは、既に明示設定されたlibrary_image_root直下の作者ID末尾folderだけを読取専用で確認。既存local_media_adapter._safe_fileを再利用し、bookmark wrapperはマスター所属とみなさず、symlink/reparse・複数folder/ZIP候補は拒否。旧ファイル本文を出力元にせず、所属名だけを新規export intentへ固定。新専用projection rootに、今回検証済みCASから所有copyを作る。元PV/旧DB/フォルダー/画像/ZIPを変更しない。旧ZIP所属は未採用更新契約に依存するため固定文言で拒否し、自動採用・削除・移動しない。

AuthorMediaService.completed_projection_sourcesは、既存admissions台帳とreconcileのjob固有fingerprint/現在receipt照合結果からだけ出力候補を返す。未受理・未done・世代不一致は含めない。新DB/別管理台帳は追加しない。SaveExports.scan_authorsは同じ既存export JobServiceにauthor-projectionを受付。既存identity/operation_id/attempt/明示Retry/再起動を使用し、元作者受付のproofを固定して照合後の別世代への差替えを防ぐ。source jobは成功したまま、派生出力失敗は別jobのerrorとして表示する。

静止画・小説TXT/EPUB/BOTH・うごイラGIF/MP4/framesは既存snapshot/所有copy/hardlink/manifestを再利用して作者フォルダー（use_work_folderを反映）へ出力。bookmarkアカウント指定は不要で、架空アカウントIDやbookmark名を生成しない。作者名/作品ID命名と元bytes/相対参照を維持し、transcode/再レンダリングしない。作者マスター出力を先にsettleさせてからbookmarkの物理所属を受付時固定する。通常のbookmark-only保存を作者保存と推定しない。

SaveExportsの本文・intent・manifestのread/writeと所有bytes検証・ZIP bytes検証・診断を既存native_io_pathへ揃え、公開論理root/相対receiptとSQLite台帳を維持。既存ZIP exporterの拒否/排他的公開は変更しない。

## 回帰コードと未検証

新tests/test_author_output_connection.py（5件）: 実fixtureの作者3媒体preview→永続受付→保存→作者出力（bookmark accountなし）→再起動/再GETなし、旧マスターの読取り不変・wrapper除外・ZIP/曖昧候補拒否、旧所属名の新projection引継ぎ、author admissionなしの単なるdone除外、os.linkの実発火確認付き出力失敗→同じjob ID/attempt2 Retry/CAS proof不変。前段191のheavy/light・全形式・相対参照・編集拒否・旧intent互換の回帰も維持。

全形式のコードを接続したが、新規結合回帰はPNG/BOTH/framesの合成fixtureであり、codec付きMP4等の原受入成功を意味しない。旧39PASSや未回収の統合検証結果、191の検証結果を新候補へ無条件継承しない。有限な全原操作分母は未確定。

チャット/承認済みClaude担当はfinal/manifest.jsonの個別/集合hashと旧191を照合し、既存S venv、PYTHONPATH=<candidate>/src、PYTHONUTF8=1、PYTHONDONTWRITEBYTECODE=1、新S専用TEMP/TMP/深いbasetemp/logで次を実行。

```powershell
& S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B -m pytest tests/test_author_output_connection.py tests/test_author_projection_layout.py tests/test_author_media.py tests/test_author_new_only.py tests/test_save_exports.py tests/test_bookmark_media_projection.py tests/test_bookmark_media_deep_path.py tests/test_direct_projection_references.py tests/test_p3_residuals_fixtures.py tests/test_save_exports_gui_fixture.py --basetemp <fresh-deep-S-temp> --junitxml <fresh-S-log>/author-output.xml
```

期待: 元作者save receipt成立→作者projection3件done/フォルダー配置、同世代scan重複なし、bookmarkアカウントなしで成功、旧マスター所属名保持/旧bytes不変、無関係bookmark-onlyを作者へ誤分類しない、失敗は明示Retry後同一job/attempt2、再起動jobs/設定/元CAS不変・reader callsゼロ。MP4は既存codec条件を使用しskipを成功としない。前段成功後、既存GUI checkerの設定/保存/queue/failed Retry/projection reader/再起動4ケースと作者保存UIからの新派生job表示を別々に検証する。実データ/ネットワーク/OS設定で回避せず、8089/PID37168/EMAに非干渉、所有process通常終了/port解放/前後hashを回収。

## P3保留と必要判断

今回の非依存実装対象である旧マスターfolder所属引継ぎとbookmark以外の作者全媒体出力はコード接続済み、未試験。ZIP所在の作者スキップ/旧ZIP追記・更新・作者改名/原画像削除・重複物理隔離/ZIP部分削除は不変receipt/CASと既存拒否契約に関わるため解除しない。

必要判断は、更新可能ZIPの所有証明範囲、作者改名時の旧名/新名公開、中断時のZIP+manifest整合、物理隔離後の旧receipt/再取得抑止。推奨は新専用rootでmanifest/SHAにより所有を証明できる派生ZIPだけを更新対象とし、旧PV/未知ZIPを保護すること。これは未採用案であり勝手に適用しない。folder所属の読取りと現在CASの派生出力を進めるために、この判断待ちを一括停止理由にはしていない。

独立監査/native配布/live/exact-head CI/総合UX/原全機能受入/P3全完/P4移行は未達。write-end/hash/最小差分/旧191保全は専用artifacts final。書込み担当が終了してから検証を開始する。
