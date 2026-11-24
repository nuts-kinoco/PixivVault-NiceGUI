# 起動保護の原因分析・設定保存の安全化（2026-11-24）

## 1. 原受入の達否

- 設定保存の原条件3件は隔離で成立。認証無効の保護中は予約0／偽GET 0、保存値と revision は再読込で保持。通常画面相当は従来どおり予約1／偽GET 1。局所401と共有STOPを扱う実装には変更を加えていない。
- 原因分析と正式復旧手順を提出。現物の再確認で、先行報告の「共有未停止」「startup形式の seal」「来歴欠落で fail-close」が訂正された。保護画面に入った当時の具体的な例外は未特定。実機復旧、10タブ表示、外向き通信の再開、全57機能・212台帳項目の受入は未達。取得停止ゲートと認証に関わる差分なので、フェーズ出口の独立検証は監督手配待ち。 [復旧手順](../../docs/STARTUP_PROTECTION_RECOVERY_20261124.md)

## 2. 原因分析

### 来歴と起動保護

- 旧2ファイルの更新時刻は 11月16日 17:39:00、旧退避の通常ツリーへのコピー作成は同日 23:09:03。記録が同一の2ファイルが removed-from-real-20261117 にあり、ファイルと親ディレクトリの作成時刻は 11月17日 02:05:59。一時期存在し、11月17日の退避を示す証拠がある。原本の削除・移動の厳密な時刻、操作者・コマンド、復元の有無を示す記録は未取得。 [旧退避](<S:/Projects/PV-DEV-20261117/backup/stop-owner-20261116-2305/removed-from-real-20261117>)
- 製品の初回起動は未使用の共有 owner に限り、ステージへ record、親へ anchor を排他的に作り、gate、seal を発行する。失敗時は保全する。通常運転のコードにこの2ファイルを消す処理は見つからない。見つかった削除は合成 owner を壊す試験と、別名ファイルである未公開のロック記録やGET intentの後始末。正式移行は別形式の migration-record と seal を発行し、既存 owner を上書きしない。 [生成と検査](../../src/pixivvault_nicegui/managed_network_stop_startup.py#L42)・[初回公開](../../src/pixivvault_nicegui/managed_network_stop_startup.py#L210)・[共有GET intentの後始末](../../src/pixivvault_nicegui/managed_network_stop_shared_scope.py#L122)・[来歴欠損試験](../../tests/test_startup_owner_lifecycle_20261116.py#L193)
- Windows Known Folder が示す対象を確認した。現 seal は 11月8日 02:12:58 更新の正式移行形式 pixivvault-managed-get-user-owner-v1。現 seal、22:23の保全、11月16日の移行形式sealはバイト一致。現在の root／DB／lock の OS identity は3/3一致した。現 seal の証明値は migration-record と一致し、旧起動用レコード、アンカーとは不一致だった。現物への製品の読み取り専用 check_startup_provenance は通過。正式移行形式で起動来歴が両方ないときは従来契約を適用する行があり、旧来歴は正常起動の前提に含まれない。 [検査](../../src/pixivvault_nicegui/managed_network_stop_startup.py#L42)・[共有ownerの検証](../../src/pixivvault_nicegui/managed_network_stop_shared_scope.py#L228)・[現物と保全の照合](forensics.txt)
- 起動ラベル 07:40・14:15・18:40 に対応する crash_log の時刻は 07:18:00・13:48:14・19:28:44 JST。stdout は各々 NiceGUI ready。通常画面と操作は監督・CoS の観察で裏付けられる。監督の立会い記録では当日も起動用2ファイルの不在が継続していた。正式移行形式の例外がその起動と整合する。21:31:28と21:42:41 にも起動記録があり、後者の CoS 観測で configuration-required／local_unavailable の表示が確認された。出力ログは待受開始までで、例外名は残していない。 [Workログ](<S:/Projects/PV-DEV-20261117/logs>)・[crash_log の保全](<S:/Projects/PV-DEV-20261117/backup/realstate-20261124-2215-broken/profile-native/logs/crash_log.txt>)・[CoSの起動観測](<S:/Projects/PV-DEV-20261117/verify/cos_20261124_FULL/COS_RESUME_AFTER_USER_STARTUP_20261124.md>)・[監督記録](<S:/Projects/PV-DEV-20261117/tmp/OVERNIGHT_PLAN_20261121.md:269>)
- local_unavailable の起動表示は、共有 owner の検証、ゲート・ロック・SQLの読取り、選択済み媒体の履歴検査、局所STOP転記準備などからの汎用エラーに対応する。起動順は Native のプロファイル・局所ゲート・保存領域、共有ゲート、通常ページ登録。今回の合成再現では、正式移行形式＋起動来歴なし＋両401＋実装と同じGET intentで、両参照NULLと参照復元済みの双方が通常ルートを登録し、偽GET 0。起動来歴欠落、両NULL、両401のどれも今回の既知の組み合わせでは保護画面の十分条件にならない。21:42起動時の例外、当時のゲートのI/O・ファイルアクセスと選択済み保存元、および実際にロードされたコードの記録が必要。監督記載の18:40候補 923dead0、bd98f04f、今回の元HEADの src tree は同一。 [起動順と分岐](../../src/pixivvault_nicegui/bootstrap.py#L1034)・[保護画面への登録](../../src/pixivvault_nicegui/bootstrap.py#L1222)・[隔離再現](startup-replay.txt)

### 設定 revision

- 朝の r10 は設定DB revision 28・両参照あり。退避のコピー時刻は 03:18:24–03:18:28 JSTで、07:40 は起動ラベル。22:23の保全版は revision 38・両参照NULL、設定DBの更新は 21:32:39.612。両方のDBは整合検査に合格した。
- 保全版の採用履歴は revision 30〜37。revision 30 は 06:31:18、最終採用の revision 37 は 19:14:58 に更新された世代と対応する。revision 29 の中身は現存履歴から確定できない。拡張受信・ブラウザー取込は候補 Cookie のみをCASで採用し、保存先親を維持する。採用世代の切替と世代の後始末も両参照をNULLにしない。両参照を同じ保存で書く既存経路は Native 設定の保存。2つの「選択解除」は下書きに反映し、次の「保存」は両NULLを1回のCASで確定できる。revision 37から38、更新時刻、保存先親までNULLになった点と整合する最有力経路。通常設定と起動設定で保存実装を共用しており、画面、操作者、クリックの順序は未特定。 [画面の選択解除と保存](../../src/pixivvault_nicegui/ui/native_profile_view.py#L324)・[CASと履歴](../../src/pixivvault_nicegui/native_profile.py#L447)・[取込確定](../../src/pixivvault_nicegui/extension_cookie_store.py#L1049)
- 追加確認の現物は 22:55:09.180 更新の revision 39・両参照あり。保存先親はr10、Cookie参照は保全版の最終採用世代と一致し、採用履歴に追加なし。変更者・操作は記録から確定できない。この担当の操作はファイル読取りとS:への検査コピーのみ。 [照合結果](forensics.txt)

### HTTP 401 の時刻と経路

- 保全版と現物から採った対象ゲート2DBは各々バイト一致。いずれも sidecar なし、製品の schema と gate 行の検査を通り、局所・共有の双方が blocked=true / status=401 / revision=1。共有 DB は旧保全の未停止DBと異なる。共有送信中記録も標準バイト列と一致し、現物と保全の双方に残る。先行診断の共有未停止という観測とは一致しない。 [照合結果](forensics.txt)
- 20:28:36.132 に共有GET intent、同 .372 に共有DB、同 .385 に局所DBが更新され、CoSは20:29:30.552に停止表示を初観測。標準のGETを始める直前にintentを永続化し、401の応答で共有→局所の順に永続化し、intentを保持する実装と一致する。付与は20:28:36頃と強く裏付けられる。個別のリクエスト、認証GETか他の取得GETか、送信元、実際に使われた資格は現在の証拠で断定できない。Cookie世代の更新から約73分後の401は資格の失効・無効化を疑う材料になる。 [二重ゲート](../../src/pixivvault_nicegui/managed_network_stop_shared_transport.py#L35)・[intent](../../src/pixivvault_nicegui/managed_network_stop_shared_scope.py#L106)・[401の永続化](../../src/pixivvault_nicegui/managed_network_stop_gate.py#L620)・[CoS時系列](<S:/Projects/PV-DEV-20261117/verify/cos_20261124_FULL/COS_AUDIT_TIMELINE_20261124.md>)

### 監督による当日作業の実状態への接触

| 作業 | 判定と根拠 |
| --- | --- |
| cos_isolated_up／down、launch_test_owner、CoS fixture | **その実行経路からの実 profile・共有STOP 書込み／削除を否定。** 固定の隔離ポートと新規 run。TEMP/TMP・LOCALAPPDATA・APPDATA・NiceGUI記憶領域を切替え、専用ワークスペースのマーカーで所有ルートを置換する。CoS fixture は Python の書込み先を配下に制限。down はポート、親プロセス所有と起動時刻を検査して当該フィクスチャを終了する。 [up](../../tools/cos_isolated_up.ps1#L13)・[down](../../tools/cos_isolated_down.ps1#L7)・[起動](../../tools/launch_test_owner.py#L16)・[隔離所有ルート](../../src/pixivvault_nicegui/test_owner_runtime.py#L29)・[fixture](../../tools/cos_isolated_fixture.py#L212) |
| UIUX 隔離起動 | **確認したUIUX実行経路からの実 profile・共有STOP 書込み／削除を否定。** 元アプリは合成DBと複製、移植版は合成サービス・レシピ・STOP reader、ブラウザー用AppDataも作業領域。通常製品の暗黙の owner を選ぶ起動経路を使わず、子の後始末は起動元が所有するプロセスに限る。 [UIUX](../../tools/uiux_audit.py#L602)・[合成サービス](../../tools/p5_design_evidence.py#L325)・[子起動](../../tools/p5_design_evidence.py#L433) |
| C:監視付き全体 pytest | **監視ラッパー自体の変更／削除経路は否定。子テストによる実共有STOPへの到達は肯定。** 環境の LOCALAPPDATA を変えても Known Folder は実物を指す。保存先所有権の既存試験5件は verified_storage の代替照合から実共有 owner に到達する。追加監査で実ownerディレクトリの mkdir(exist_ok) 試行6回と、その共有DBへの書込可能な接続6回を捕捉し、書込みを拒否した。既存ロックは接続に先立って取得し得る。対象の既存ゲートを開く経路にSTOP trip・来歴削除はなく、このフォールバックでは実プロファイルを選ばない。過去のラッパーのC:新規パス差分は既存ファイルの更新・削除も、子テストの実行時に同等の拒否が働いたかも立証しない。 [ラッパー](../../tools/p6_c_drive.py#L77)・[Known Folder](../../src/pixivvault_nicegui/managed_network_stop_shared_scope.py#L392)・[フォールバック](../../src/pixivvault_nicegui/managed_stop_migration_binding.py#L9)・[書込可能接続](../../src/pixivvault_nicegui/managed_network_stop_gate.py#L380)・[追加監査](pytest-audit-attribution.txt) |
| launch_d10.ps1 起動試験 | **実 LocalAppData の既存sealへの読み取り接触を肯定。** 監督記録は既存sealのガード通過、子起動、既存単一インスタンスへの復帰／二重起動拒否。非隔離分岐は OS Known Folder の印を読み、既定ProfileRoot は CodeRoot 配下の profile、任意指定可。Native が既存インスタンスを復帰させる分岐はサービス・共有ゲート・GETの準備より早い一方、その前に profile の peek/open があり、ProfileRoot が実機なら実設定DBを開く可能性がある。当時の引数とI/O履歴を捕捉した原記録が無いため実profileの書込みは未証明。隔離分岐は指定領域以外を拒否。いずれのランチャー分岐にも実来歴2ファイルの削除はない。 [D10](../../tools/launch_d10.ps1#L66)・[既存インスタンス分岐](../../src/pixivvault_nicegui/bootstrap.py#L928)・[監督記録](<S:/Projects/PV-DEV-20261117/tmp/OVERNIGHT_PLAN_20261121.md:247>) |

## 3. 修正と検証

- 元HEAD 4daeb24dd2a9cb67b91ffc58e85e9597491a0063、ブランチ startrecover-20261124。認証無効な NativeProfileView に保存後の on_saved を登録しない。従来 busy 画面だけで外していた処理をビュー作成側に集約し、再確認後に明示認証ボタンだけを復帰できる従来の案内を維持。既存の request_cookie_check、通常起動、STOP、CAS・保存値検証とは同じ実装を通る。 [差分箇所](../../src/pixivvault_nicegui/native_startup.py#L257)
- red は2失敗・1成功・6選択外、3.56秒。ゲートが開いた対照ケースで偽GET 1、局所401＋共有未接続を模したケースで偽GET 0・予約1。通常側は予約1・偽GET 1。試験は NiceGUI の設定ビューの保存メソッド、実ストア、局所・共有ゲート付きリーダーを通し、最終の送信だけを偽装。 [red](pytest-red.txt)・[試験](../../tests/test_stage5_r1_native_auth_stop_boundary.py#L102)
- green 99件、45.15秒。追加の回復・保存先・隔離・通信棚卸し34件、12.09秒。到達先を分類した同じ34件の再実行は12.76秒、計数用プラグインの既読警告1件。通信棚卸しの既存fingerprint全件一致。全体 pytest は実行していない。 [green](pytest-green.txt)・[追加試験](pytest-recovery-boundary.txt)・[書込み監査](python-write-audit.json)・[到達先の分類](pytest-audit-attribution.txt)
- Known Folder の未上書きフォールバック先を S: 配下の隔離 AppData に固定して同じ対象を最終確認し、133件通過、56.87秒。書込み監査の C: 拒否0件。子プロセス1件はゲート競合試験が自分の一時ロックを保持する Python。pytest 各回の報告時間合計は約130.43秒。 [最終実行](pytest-isolated-confirmation.txt)・[最終書込み監査](python-isolated-write-audit.json)

## 4. 残る問題と次の承認済み作業

- 監督が独立レビューに回す。担当はレビューで指摘があれば候補に反映して再検証する。その後、監督が現在の候補と revision、対象の全取得者を照合し、本人承認の下で復旧手順の再起動判定へ進む。通常画面の確認と401を保全したままの取得拒否、実通信の受入は別の項目。
- 起動時の例外が現物で続く場合、候補と当該分岐を特定できる証拠を保全して再調査する。現在の正式移行は既存 owner を拒否し、結果不明の回復は既存401を拒否する。復旧手順は今回の停止をクリアする製品操作を確定していない。旧来歴2ファイルを戻す根拠もない。
- 全体試験を再実行する担当には実 Known Folder を隔離へ向ける試験設定と書込み監査を引き継ぐ。既存テストのフォールバック隔離の修正は今回の製品差分外。

### 逸脱日誌

- 先行CoS診断と読取結果が不一致。保全DB・現物読み取りコピー・製品の来歴検査・旧退避を照合して監督に通知。観測時点の原因となった例外と先行診断で使われた実際の参照先は未確認。
- 追加試験の C:監査で実共有STOPへのフォールバックを発見。書込み可能なSQLite接続は監査で拒否。既存共有ロックに一時接触し得る経路であり、以後の確認は Known Folder を合成先に差し替えた。実機アプリの操作、実Pixiv、設定・STOP・来歴への書込みは実施していない。
- 後始末は担当の S:\Work\_tmp\PV\startrecover-20261124 に限定。検証用コピーを含む3,293ファイル、1,526ディレクトリ、9,709,787 bytes を列挙し、所有プロセス残存0、リンクなしを確認。シェルの実削除は実行環境のポリシーで拒否された。実削除0、残存は列挙分。監督の片付け対象として引き継ぐ。コミット候補と verify の証拠は所定の作業ツリーに保全する。
