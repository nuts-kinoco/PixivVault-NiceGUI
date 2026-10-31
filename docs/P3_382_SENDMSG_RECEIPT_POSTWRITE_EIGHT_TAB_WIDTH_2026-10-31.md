# P3-382 — P2 sendmsg と S07 receipt後通信試行、S07 EPUB/BOTH、8タブ幅別受入

2026-10-31 JST。開始HEAD 5eaa8a4847ff81dbba4b6be3b36fae93fc68ddf7、branch phase11/p3-vault-cleanup-race-20261028。開始時staged0、stash空、対象PIDなし。AGENTS.md、b104390、P3_370/P3_375/P3_379の継続要求を保持。旧P3-376・P3-369/370の証拠SHA照合済み。他担当dirtyを変更・stageせず、原DB・画像・ZIP・Cookie・実ワーカーと外部通信に介入しない。

## 1. P2 — sendmsg の外向きUDP宛先漏れ

従来フェンスは connect/connect_ex/create_connection/getaddrinfo/sendto のみ。Windows 11現Pythonでは socket.sendmsg が存在しないため、ネットワークへ送信しない SimulatedSendmsg のみを一時的に導入して、Linux等のAPI有効環境の遮断漏れを負例再現。修正前は外向き 203.0.113.55:9999 の送信試行にPermissionErrorが出なかった。現在は sendmsg存在時に限ってラップし、実送信前に外向き宛先を拒否・attemptsに socket.sendmsg を追加。loopbackは許容。自身のメソッド/継承メソッドの違いを区別し、通常終了と例外終了で元参照を厳密復元。Linux実socket.sendmsg試験は**未実施**。

## 2. P2 — S07 receipt書込み途中のfalse GREEN

従来 finalize_s07_run は write_func より前に network.attempt_count を確定していた。write_funcが外部DNSを試み、通信拒否例外を捕捉して通常終了した時に receiptも success=true となることを負テストで確認。修正後は最初のreceiptを必ず success=false で書き、write_func完了後のnetwork.cleanと試行件数・cleanup_errors・主処理成否を見て、cleanな時だけ別一時ファイルの完全JSONを置換して最終GREEN発行。試行があれば保存receiptはREDで件数・offline_parent_fenceを保持。原レシート/フォールバック書込み異常、services.close/SHA確認異常、outer with によるsocket復元、TXT 45byte/UTF-8 BOM/42字/作者91作品501/本文/receipt SHA厳密一致は弱めていない。

修正前のコマンド（新規故障注入テスト、実外部通信0）：

~~~powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 -m pytest -q tests/test_stage5_offline_parent_fences.py tests/test_stage5_s07_cleanup_restore.py -k 'sendmsg or caught_dns_attempt_during_receipt_write'
~~~

実出力：F.F / 2 failed, 1 passed, 14 deselected、P2_RED_EXIT=1。sendmsg未遮断と、DNS拒否捕捉後のS07誤success=trueをそれぞれ確認。

修正後のコマンドと実出力：

~~~powershell
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 -m pytest -q tests/test_stage5_offline_parent_fences.py tests/test_stage5_s07_cleanup_restore.py
# 17 passed in 6.12s; P2_LOCAL_EXIT=0
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 -m pytest -q tests/test_stage5_offline_parent_fences.py tests/test_stage5_s07_cleanup_restore.py tests/test_stage5_s07_txt_content_guard.py tests/test_stage5_s08_gif_default_content.py tests/test_p3_residuals_fixtures.py tests/test_media_coexistence.py
# 38 passed in 10.26s; P2_SHARED_FULL_REGRESSION_EXIT=0
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 tools/check_stage5_s07_next_saved_txt.py artifacts/p3-380-s07-postreceipt-r1
# success=true, steps=3, failure=null, formal_pass_added=0, exit0
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 tools/check_stage5_s08_frames_next_save.py --output artifacts/p3-380-s08-sendmsg-fence-r1
# success=true, steps=4, failure=null, remaining=0, formal_pass_added=0, exit0
~~~

新S07レシートSHA256 b4cce9d46180084f2f8db5ac7a1581484385475408c723a6e57654257ca2c059、本文一致、親外向き試行0。新S08 SHA256 ea651ff869a5f363c9d994711d4fde05855713501832bb3f4f9367b7b35a2408、親外向き試行0、自然exit0/子孫0/port解放/source・harness SHA不変。両件とも担当内成功で、独立P2解消レビューは未実施。旧証拠は上書きしていない。

## 3. 原 v8-S07-01 — EPUB/BOTH の次回生成保存内容

以前のTXT GUI設定→次回TXT内容一致は成功済みとして継承。今回は新 tests/test_stage5_s07_epub_both_saved_content.py で、専用生成PreferencesStoreへEPUB/BOTHを設定し、実製品作者小説501ジョブの次回保存を実測。EPUB ZIP破損なし、XHTMLに既知の2段落、OPFに題名・作者、receipt作品501/作者91/形式・ファイル実SHA/bytes一致。BOTHはTXTのBOM付き全文も一致。新GUIクリックを再実行した試験ではない。

~~~powershell
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 -m pytest -q tests/test_stage5_s07_epub_both_saved_content.py
# 2 passed in 1.40s, S07_EPUB_BOTH_CONTENT_EXIT=0
~~~

S08のMP4実出力はcv2/codec前提が満たせず未実測。S07/S08の全形式・元実Fletとの完全等価は未達。

## 4. 実8タブの保存済み元Flet比較と新NiceGUI幅別実測

別担当P3_369_370で、元 gui.py と保護された実依存33ソースのSHA、元Flet8タブPrintWindow画像8枚、NiceGUI8タブ画像8枚のSHAは全一致照合済み。元Flet原本は変更・再起動せず、実gui.pyの全8タブが安全な合成FakeDBで実表示・クリックされた成果だけ継承。ただし双方1768x882で開始データ（Flet空FakeDB対NiceGUI管理画像1件）と撮影方式（PrintWindow対CDP）が不一致なので full parity=false。

新 tools/check_stage5_eight_tabs_width.py は実NiceGUIの8タブが全て有効な生成管理rootで1サーバーを正常起動、720x900と1280x720 CSSpx / DPR1で全8タブ実クリック、aria-current、documentElement.scrollWidth、body boundsを測定。横方向の全画面はみ出しなし。16枚を個別SHAで保存。旧L13及び1768px成功試験は反復していない。

~~~powershell
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 tools/check_stage5_eight_tabs_width.py artifacts/p3-381-eight-tabs-width-r1
# success=true, screens=16, failure=null, remaining=0, formal_pass_added=0, exit0
# WIDTH 720 COUNT 8 MAX_DOCUMENT_SCROLL 720
# WIDTH 1280 COUNT 8 MAX_DOCUMENT_SCROLL 1280
~~~

新レシートSHA256 2fabc333afcfef2707fc9c02cb4e1739e221fc5bc23e224d46e65f120d04085d。ブラウザ外部通信0/JSエラー0、自然終了exit0/所有子孫0/port解放、ソース不変。これはNiceGUI側の狭幅担当内受入。元Flet狭幅全タブ/同一データ/同一撮影方式/重なりの視覚全判定/30回p95等の正式比較ではない。

## 5. 未達・推奨案・独立ゲート

正式152操作479参照の分母・不足索引は変更/再生成せず、S07=v8-S07-01、S08=v8-S08-01への合成限定証拠に留める。全体 Stage5=4 FAIL/12 UNVERIFIED、formal=0 PASS/152 INCOMPLETE、release_ready=false。K14/K02/K04/K08/L13等既成功試験は再実行していない。

推奨する残作業は、元Flet保護コピーの各実タブへ安全な同一生成データ・FakeDB能力を与え、NiceGUIと同じ初期状態/画面幅/物理DPI/操作順/撮影backendで比較すること。元DB・Cookie・画像・ZIP・workerを解放せず実装する。実Cookie限定受入、全機能差/エラー復帰/UI UX、規定試験・CI/配布・独立P1/P2レビュー・本人受入は別の未完了工程。仕様変更案や外向き通信/アカウント変更・main統合・pushは未実施。今回所有差分だけ作業branchへ選択commitする。

## 6. 継続時の所有境界・限定コミット手順

圧縮後の再確認（2026-10-31 JST）：HEAD 5eaa8a4847ff81dbba4b6be3b36fae93fc68ddf7、指定branch、開始staged0 / stash空、関連Python/Fletプロセス検索0。既存 P3-382 の実体はこのファイル名（ANDなし）で確認し、別名で二重作成しない。既存P3-380 S07/S08とP3-381全8タブのresult SHAは上記値と再一致したので再実行しない。

選択コミット対象は tools/stage5_offline_parent_fence.py、tools/check_stage5_s07_next_saved_txt.py、tests/test_stage5_offline_parent_fences.py、tests/test_stage5_s07_cleanup_restore.py、tests/test_stage5_s07_epub_both_saved_content.py、tools/check_stage5_eight_tabs_width.py と本書の7パスのみ。操作は git add -- [この7パス]、git diff --cached --name-status、git diff --cached --check、git commit -m "test: close sendmsg and post-receipt egress gaps with GUI width evidence" の順に限定する。コミットが成功したことは実コマンドの終了コードと git show の照合までは主張しない。他担当 dirty はstageせず保持し、push/main統合は実施しない。

制約：S07の二段階receiptは最初にREDを永続化し、カスタムwriter後の通信試行を検査してからGREENを公開する。最後のatomic公開そのものの障害とfallback書込障害は未解消の運用上の例外経路として区別し、レシート不在を成功と解釈しない。Linux native sendmsg、S08 MP4、同一データ同一撮影のFlet比較は引き続き未検証。
