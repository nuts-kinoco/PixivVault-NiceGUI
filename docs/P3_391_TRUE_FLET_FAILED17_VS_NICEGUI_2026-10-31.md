# P3-391 — 本物の元Flet gui.pyとNiceGUI、同一生成17件の失敗リスト実操作

2026-10-31 JST。作業branch phase11/p3-vault-cleanup-race-20261028、開始HEAD 8ec6a4595ffa989d3739f487acd1670810ceec44。今回の追加コードは tools/check_stage5_original_failed17_native.py、tests/test_stage5_original_failed17_native.py、tools/check_stage5_failed17_nicegui_matched.py、tests/test_stage5_failed17_nicegui_matched.py。生成証拠はP3-389/390の新規rootだけ。既存Flet原本・本番DB/画像/ZIP/Cookie/元設定・実アカウントは変更せず、実Pixiv API/実アカウント書込0。P3-369/370旧成功試験は再起動・上書きしていない。

## P1：元Flet固定33コピーのハードリンク経由変更を遮断

別担当worker-7は、元Fletの本物の gui.pyを合成FakeDBで起動する新ランチャーのPython path-only auditでは、保護コピー外のhardlinkから同じNTFS inodeを書換え可能なP1を指摘。実Flet新起動前に、pytest専用生成ファイルで source と outside alias のハードリンクを作り、alias.write_bytes が sourceも変えることを再現。未実装 pinned_win32_sources 呼出しはRED: 1 FAILED / exit1 (AttributeError)。本番ソースは一切変更せず、生成tmpだけで実証した。

修正はWindows CreateFileWで各コピー済みPythonソースを GENERIC_READ / FILE_SHARE_READ ONLY / OPEN_REPARSE_POINT で開いて33ハンドルをFlet起動から完全終了まで保持する。後続の書込み・直接source削除・別名hardlinkからの書込みをOSレベルで拒否し、実行前後の固定SHAをハンドル保持中に再確認。実行前に source-copy のreparse祖先と各file handleのreparse属性・production nNumberOfLinks=1 を確認し、不正なら起動前停止。併せてPython auditでos.link/os.symlink等を遮断。既設原コピーは読み取りのみ。権限恒久変更やファイル編集は行わない。

~~~powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 -m pytest -q tests/test_stage5_original_failed17_native.py -k win32_pinned_read_handle
# 修正前 1 failed / exit1。元ファイルでなくpytest生成硬リンク
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 -m pytest -q tests/test_stage5_original_failed17_native.py
# 11 passed / 0.36s / exit0
~~~

33件のOSピンを実ファイルで一度取得・解除し、33件SHA一致・実hardlink nlink !=1 は0・関連GUI起動なしで exit0 と確認。別担当worker-7のREAD-ONLY再監査は、このsource-copy書込み遮断の範囲について LIMITED APPROVE/P1/P2追加指摘なし。ただし独立source所有・GUIの終了やP1/P2製品全体承認ではない。Windowsのhardlinkの別名unlink自体が許されるケースはあり得るが、元source pathとbytesの直接変更・元パスunlinkを検査対象とし、外部別名の存在や削除を誤って保証しない。

## 元Flet本物gui.py全8タブ（inline failed）での新17件実GUI

新ランチャーはP3-368保護コピーと元依存33ファイルSHAを凍結し、FakeDB.get_failed_jobs()だけをNiceGUI製品内の typed build_failed_records_fixture() 17件から辞書型に変換して返す。元の gui.main_window(page, db=FakeDB(), scheduler=None) と、元コードのinline失敗リストUI・検索イベントを使う。元ワーカー・外部Pixiv・元DBは遮断。元ソース33件の出力SHA不変を保持する。

1. P3-389 r1: NiceGUI専用venvにFletが存在せず ModuleNotFoundError、実GUI前exit1。保存済み artifacts/p3-389-real-flet-failed17-r1/generated-failed-17-source-guard.json SHA256=C2EE5C505BD9FB30BBD988802636BA52F33F7C0484AF085EC45CED8C9FE7F1CF。元33コピーSHA不変、関連PID0。同じ実行を無根拠に再送しない。
2. 既設のstenv-flet1 PythonにはFlet/Flet Desktop/PILがあることを限定import確認（元Flet受入用既設環境）。別の新規 root artifacts/p3-389-real-flet-failed17-r2 に対して実行。ログ P3_368_REAL_ORIGINAL_FULL_GUI_READY は app_pid=100716、元 gui.py本物、全タブ、source_workers_started=0。Desktop UIAは本人所有のPixivVaultウィンドウ HWND62134374 を識別。
3. 実Fletウィンドウで「失敗リスト」タブを実クリック。カテゴリ表示は「すべて17／通信3／見つからない3／権限・年齢制限3／保存先3／形式3／その他2」。先頭行は failure-0001、通信、リトライ回数0、元エラー全文2行。
4. 実検索欄へ failure-0001 を入力すると、表示行は1件（ボタン文言「1件を再試行」）となり、先頭2行理由を維持。さらに no-such-generated-work と入力すると「一致する失敗はありません」、「検索を消す」が表示される。実「検索を消す」クリックで元の17件一覧へ復帰。危険な「再試行」「キューから削除」「CSV出力」「全件クリア」等は一切クリックしていない。
5. 所有FletウィンドウのネイティブPrintWindowで検索中と解除後の2枚を撮影。Win32 GetDpiForWindow=168 (175%)、client1770×884、画像3098×1547、source PID100716/誕生ID134378826473403286、HWND62134374は両画面で同一。旧P3-369のDPI96受入条件とは異なる今回の実モニタ条件であり、旧結果へ遡及合格を付けない。
6. タイトルバーの実閉じるボタンで通常終了。CoS session exit0、Fletランチャーのsource_unchanged=true、元コピー33 SHA一致、元原本 gui.py SHA不変、関係PID再列挙0。個別の再試行・保存実処理は未検証。

証拠：

- artifacts/p3-389-real-flet-failed17-r2/actual-original-full-gui-ready.json SHA256=0E6B1EDBEC6363B1E3F9F85D7F50BBBB6985D93192D1E880B32D30FC77DAD97E
- generated-failed-17-source-guard.json SHA256=A33B1BC7E023F111738BED0661BF6C39A3484432D3028D1A74408A0B478CBCB6
- original-full-failed-native.png（検索1件）SHA256=5FFEDD4F69D5244750F60B9551E3DDADAFEAA5429E3A986D931971834D9DB01E
- unfiltered-state/original-full-failed-native.png（解除後17件）SHA256=F3CD56ADB1BAFDCDD96F30F8ADD0AD59FB9538379BF329DFF31CBEFB8FCF686A
- 両PrintWindow JSONの共通ready_sha256=0e6b1edbec6363b1e3f9f85d7f50bbbb6985d93192d1e880b32d30fc77dad97e、同一PID/birth/HWND/DPI。検索結果の実UIA観測はCoS Desktopの入力と事後accessibility応答に紐づく担当内実操作であり、ランチャーJSONの actual_failed_search_ui_observed=false は意図的に未自動結合のまま。単なる生成fixture開始successを正式検索PASSに書き換えていない。

元Fletオリジナル gui.py SHA256=731E680452ED89CF35DAF4FA6F1DD220D2DBA7D592BE61FE1CD6ACBECD84E939。生成入力17件の共通正本フィンガープリント SHA256=711C0BD7F743688AB34F3DBB83F2B4E0CB3319C4266A29F9F1419A7B6AD41AE0。

## NiceGUIの現実装で同じ17→1→0→17実操作

既存のP3-331実GUI受入証拠 artifact p3-331-failure-removal-edge-r1/result.json SHA256=D290412151DEDE76A76AE0E9DCAE599E0F25DBD7B581AAC34AA02C1D629AA2E7 は成功済みとして継承。実証対象の failed_records_fixtures.py、failed_records_state.py、ui/failed_records_view.py の3ファイルは現在のSHAとその実行時SHA一致。既存のCSV/2client/削除・再起動を繰り返さない。

今回新たに tools/check_stage5_failed17_nicegui_matched.py を作成し、上記元Flet生成fixture SHA/元gui実起動/元コピー不変を前提検査して、NiceGUI現製品の失敗リストを同じ生成SQLite17件から実起動。loopback限定のHTTP・WebSocket監視、service worker無効化、JavaScript/console error、owner parent/listener/childと正常終了を検査する。画面サイズは元Fletのclient1770×884 CSSpx/DPR1.75で画像3098×1547に揃えるが、**NiceGUI撮影はCDPであり元FletのGDI/PrintWindowと同じ撮影方式ではない**。この状態で同一物理画像サイズを正式視覚同等性とは扱わない。

- P3-390 r1 はHTTP listenerがserver子PIDであり、当初の checker は親PID一致を要求して拒否。GUI操作前に failure=RuntimeError、exit1/forced cleanup/owned0。result SHA256=FEA1CEC65BCA9061238AE9BFEF0AE4D8471096E8A3A50281E4F45594E434AC77。原因は検査器の所有親子同一視であり、製品UI不具合の証拠ではない。
- r2 はlistener PIDをOwnedProcessTreeの観測済み子孫として照合して成功。実NiceGUIの失敗タブをクリックし17件表示、failure-0001検索で1件かつ2行の理由、no-such-generated-workで空状態表示、検索解除で17件へ復帰。DPR1.75・1770×884 CSSpx、3098×1547物理画像、document.scrollWidth1770で全体横はみ出しなし。
- r2 result.json SHA256=C7E2359C75F7667B6D4D60ADEEB0627756611EB3CC5CD91F50067A426B939075。success=true/steps4/外部HTTP0/外部WS0/JS0/console0/元生成DB SHA不変/source_changed=false/harness_changed=false、normal_exit=true/forced=false/parent126240/listener127564/owner観測3/残存0/port解放true、checker exit0。
- CDP画像 search-one-cdp.png SHA256=8DCE8AFAB8C598B8536E0E15F48997AFD0D552FFDDEEFE3E912B4444DDCF5EAD。再表示 restored-seventeen-cdp.png SHA256=702DA67C3A1AC522D69AF3AA9A605B1D5920C850C4F31E8B295F982585D6124D。

局所CPUテスト：python -X utf8 -m pytest -q tests/test_stage5_original_failed17_native.py tests/test_stage5_failed17_nicegui_matched.py → 初回16 passed in 0.64s / exit0。偽の元Flet source/PIN/fixtureを持つ4場合はNiceGUIのサーバー起動前に拒否し、新rootさえ生成しない。

## P390次回実行の証拠信頼境界を追加

独立限定レビューは初回P3-389/390について新確定P1/P2なし、生成入力・実GUI4操作に限るLIMITED APPROVE。ただし元Fletのnative_gui_main_returned=true・fixture SHAという自己申告だけで、実画面画像が存在しなくても今後のNiceGUI比較を開始できる余地があった。これをレビュー前の追加改善として、tools/check_stage5_failed17_nicegui_matched.pyへ verify_flet_real_evidence を追加。元Fletの依存33ソース現物SHA、gui.py固定SHA、実ready・実終了後source guard、2枚のPrintWindow PNG/JSON実SHA、同一のPID/birth/HWND、DPI168、client1770×884、画像3098×1547、元DB/Cookie不使用、元worker0を照合してからNiceGUIを起動する。存在しない元画面証拠・改ざんPNG・元Flet失敗状態での比較を、新しい専用プロセスやフォルダ作成前に拒否する。

追加した負例は、同検証関数が存在しない旧コードのRED pytest 1 FAILED/exit1 (AttributeError)。修正後は元Flet実2枚を含む限定証拠確認と、missing/tampered/native SHA、偽の元Flet成功自己申告拒否を追加し、両テストファイル合計 **19 passed in 0.98s / exit0**。これらは保存済みP3-389/P3-390の情報を読取りで再検証したもので、成功済みGUIを再起動していない。P3-390 r2のsuccess=trueは追加改修**前のchecker SHA**で採取された履歴結果であり、更新後のcheckerの新しいGUI成功を偽って引き継がない。追加改修は次回実行用のfail-closed証拠事前検証として区分し、独立再レビュー待ちの段階では最終承認とはしない。

## 追加P2：実UIA状態とGDI画像の結合（新規P3-389/390 r3）

初回の独立再レビューは、PNG/JSONが同じowner・dimensions・SHAで整合していても、異なる名前に同じ画像をコピーしてメタデータSHAを付け直せば「検索1件」「解除後17件」を誤って両方PASSにできるP2を指摘。生成tmpに1件画像を複製してSHAを付け直す負例は修正前1FAIL/exit1（検査器が拒否しないことを確認）。修正後は2画面PNGのSHAが互いに異なることを必要条件へ追加し、20件局所PASS。ただし画像が異なるだけでは操作結果を示せないため、さらにUIAの原観測を各画像へ結合した。

元Fletを追加の証拠取得条件で新しい専用root artifacts/p3-389-real-flet-failed17-r3 に一度だけ起動。本物の元gui.pyの全8タブシェル、元ワーカー0、実原本/Cookie/外部通信なし。CoS Desktopの同じowned PixivVaultウィンドウ HWND8133840 で、失敗タブ17件→実検索failure-0001で1件・2行理由→no-such-generated-workで「一致する失敗はありません」→実「検索を消す」で17件へ戻る操作を再確認した。各状態のget_window_state返却アクセシビリティツリーを直接保存した。画像だけをコピーしたr2とは別の新規受入条件であり、旧GUI結果は再実行していない。

- uia-initial17.json SHA256=F229B7AC7DD9166625F3EC2756C336D2D4770927899F14DE9478AE723275DA8E
- uia-filtered-one.json SHA256=159ECC0A591DEB3DE649517A64660FB48AA75002D42D373182EF7BA016012E8D
- uia-no-match.json SHA256=A8FC0CAADC01AEC0FE7E8BB4AD6F9FC699011E38130766BB8345CDB3BB8A9816
- uia-restored17.json SHA256=C581D7EBC092A009DC706BD2FA3217949B6C3D65840C720AD28BD062090C8D5F
- r3 ready JSON SHA256=F3E91F03C6DFA0E05C6FFEBCE57F6A2E6F5941D4C5577C17AC796C1840C84AF0
- r3終了後 source-guard JSON SHA256=A33B1BC7E023F111738BED0661BF6C39A3484432D3028D1A74408A0B478CBCB6
- filtered-one実Flet PrintWindow PNG SHA256=20A8B42F2A87C57AEED66ED7CB1A42AAA71E935A571D77A742039A9294DB73B2
- restored-seventeen実Flet PrintWindow PNG SHA256=07E8B8F08DF21F94A088A7FE769D406532A97F4BF02B967D614710B9D7948C12

元Flet両画像は実DPI168、1770×884 client、3098×1547物理画素、owner PID/birth/HWND同一で、ツリーの検索表示と結合。終了は所有タイトルバーから通常クローズ、専用実行exit0/元33ソースSHA不変、関連所有PID0。今回のUIAはCoS Desktopから直接得た生情報だが、第三者署名を持つ独立trust rootではない。自作の状態ラベルだけで正式152 PASSを認めない。

さらに追加したREDは「2枚の実画像があってもUIA生記録4状態が無ければ4操作の証拠にしてはならない」：旧P3-389 r2を渡すと以前のcheckerが拒否しないため、1FAIL/exit1を再現。修正後の tools/check_stage5_failed17_nicegui_matched.py の verify_flet_real_evidence は、元33ソース、元ready、終了後guard、2枚の異なる画像、同一owner/DPIに加えて、4つのUIA状態記録のphase・生ツリー文字列・window ID・frame連番順序と両PNG SHAを照合する。状態を混ぜた偽記録や欠落・改ざんを拒否する局所負例を追加。更新後の両テストファイルは **22 passed in 1.12s / exit0**。

同条件の新しいNiceGUI証拠は artifacts/p3-390-nicegui-failed17-matched-r3/result.json SHA256=64F8D3583FA6AD92D6829F571A2C6BBB9AA5C0E82B4F72CDD2BF690C69542C33。保存済み元Flet r3 UIA四状態＋GDI画像2枚への検証を通過してから、生成17件の現NiceGUI実失敗タブで同じ17→1→該当なし→17を実操作。success=true、steps=4、生成入力SHA一致、CSS1770×884/DPR1.75で3098×1547、外部HTTP0・WS0・JS0・console0、元生成DB/source/harness不変、server正常終了・owned子0・port解放、exit0。画像はCDPで撮影しているため **same_capture_backend_as_flet=false、full_product_feature_parity=false、formal_pass_added=0** のまま。今回のr3証拠と、旧r2の旧checker SHAでの成功は混同しない。

実装者以外のworker-7によるP3-389/P3-390 r3コード・UIA/GDI実証拠・NiceGUI r3保存票の独立read-only再レビュー：**LIMITED APPROVE（生成17件の実GUI四状態のみ）／新確定P1/P2なし**。先行P2の同一画像使い回し・UIA欠落についてdistinct GDI PNG SHA、owner PID/birth/HWND/DPI、4 UIA段階/ツリー/フレーム順序/画像結合の検証を確認。workerは今回のpytest/アプリを起動せず、コードと保存済みUIA/結果を読み取ったため、22PASS自体は実装担当の実実行結果として区別する。署名済み独立trust rootではなく、GDI対CDPの撮影差も残り、完全パリティ・152正式PASSは認定していない。

固定v8元原IDへの今回の限定対応：**Q13**「旧失敗リストを表示する」参照 v8-Q13-01〜04：同一17件の一覧/理由全文/見た目（ただし53px行、画面占有、全状態は未測定）。**Q14**「失敗理由カテゴリで絞る」参照 v8-Q14-01〜03：カテゴリごとの件数表示は原画面で確認したが、6カテゴリ全ての実クリック・AND条件の同一比較は未検証。**Q15**「失敗リストをタイトル・ID検索する」参照 v8-Q15-01〜04：実ID検索1件/0件/解除17件をFletとNiceGUIで確認、タイトル検索・カテゴリとのAND、頁/状態保持、独立原ソース逐語expectedと全scope対応は未検証。元v8のこれらのsource-only参照には完全なliteral_expectationが供給されておらず、現在の観測を勝手な逐語expectedや正式PASSへ換算しない。固定152/479台帳は無変更。

正規のGDI同士の2製品比較・全タブ全機能、元画面の状態保持/失敗復帰、正式152逐語expected・独立信頼rootが未達である点は維持する。

## 判定と未達

**新規限定成功:** 本物の元Flet gui.py実8タブシェル上のinline失敗リストとNiceGUI実失敗一覧で、同一生成入力17件・4種類の実操作効果が一致。元Fletの2件のPrintWindow物理画像、NiceGUIの2件のCDP画像、実所有/終了/元データ保護はそれぞれ固定した。操作差分・表示不具合の観測値はまだない。

**未達:** 両方のcapture backend同一、同じ製品native window、初期画面・周辺UIの完全一致、元Flet全8タブの同内容操作、設定・状態持続・再起動/障害復帰、実Cookie・通信断・429、30回同条件p95/メモリ、Windows最新配布、元DB/画像/ZIP/Cookie全bytes前後同一・独立最終レビュー、正式152操作の全参照、CI/PR/本人受入。P3-384 fail-closed gateは正式原ID 0 PASS/152 INCOMPLETE/479参照、Stage5は4 FAIL/12 UNVERIFIED、release_ready=false。全ゲート前のmain統合・製品完成認定・pushは行わない。

今回の独立段階再レビューはworker-7への読取依頼を回収し、未解決P1/P2があれば再現試験を先に書いて修正する。自分の新ファイルのみ選択stage/commit、他担当dirtyは保全。元Fletソース、生成証拠旧root、Cookie秘密値、実画像・本番IDはGitへ含めない。
