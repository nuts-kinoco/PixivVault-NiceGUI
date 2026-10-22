# 旧ライブラリ・マイブックマークの読取統合

基準はPR #16統合後の`48c718fe6f8702606496f44e5ed7a9d24cd72050`。編集先は独立した`phase4/library-adapter-integration`。先行`library-read-integration`の確認済み14ファイルをSHA-256一致で移管し、元worktreeと他担当差分は保持した。取得値・Cookie・実画像・DB・スクリーンショットをGitへ含めない。

## 今回の操作単位

旧SQLiteの作者/作品・閲覧履歴・容量キャッシュ、保存済みフォルダ/ZIP画像、永続ブックマークキャッシュをUIへ接続する。任意キャッシュ破損を作品一覧の読取失敗と混同しない。作者名変更やNULL作者ID、イラストと小説の同番号を保持し、小説は`novel:<id>`へ正規化する。ブックマークの検索、選択数、50件追加、共有ビューア、I/Tabの詳細切替、戻るフォーカス、タブ再訪を対象にする。

未DL作品もviewerの作品集合へ含める。旧DB cacheにページ数がない作品は既知1枚とは区別し、必要な場合だけ作品選択時にメタデータを非同期取得する。未接続の小説本文・うごイラ再生・DL/ブクマ書込/フォローは有効な操作に見せない。

## 整合性と安全境界

DBをUIスレッドで読まず、成功した読取結果だけを公開する。remote/local bookmark情報は一つのSQLiteトランザクションから得る。ライブ成功後のDLフラグ読取は壊れた旧remote cacheに依存させない。DBのSQL更新・schema移行を行わないが、WAL利用時のSQLite共有メモリsidecarまで無変更とは保証しない。本番ファイルを使わず合成SQLiteで検査した。

画像は世代ごとのcatalogを画面と進行中HTTPが参照カウントで保持する。別clientの更新で古いviewerの画像を差し替えない。`catalog`クエリはsnapshot識別であり、要求ごとに変えるcache-busterではない。従来の画像イベントgeneration/serialはURL fragmentのまま維持した。HTTPのlocal/remote画像は有効なsnapshot tokenが必要で、tokenなしは合成画像だけ。HEADは未取得remote画像のGETを開始しない。

保存画像では作者/作品/ページを厳密照合し、同名旧形式・欠落・異なるページを推測で代用しない。ID付き保存・作品フォルダ・ブクマdirect・同一inode hardlinkを扱う。ZIP traversal/リンク/重複/過大エントリ、decodeサイズ、並行decode数を制限し、索引中にUI-facing lockを保持しない。close後の待機中decodeを拒否する。

`LivePixivReadClient`は標準ライブラリによるGET専用。Cookieは明示されたファイルからメモリへ読み期限を守り、警告に値を出さず原本を保存しない。認証根拠はホームの`meta-global-data.userData.id`とextra APIの成功/ID整合で、Cookie文字列のID候補だけを認証成功にしない。宛先/パスを限定しproxy/redirect/retryを無効化。APIとCDNを分離しCDNにはCookieを付けない。レスポンス本文や認証値を例外/ログへ含めず、応答byte数・ページ数・所要時間に上限を設ける。

## 独立レビュー

DB担当と画像担当を分け、互いの実装とprimeのUI/registryを独立レビューした。HTTP認証クライアントは第三担当が実装し別担当が監査した。レビューで見つかった任意cacheによる起動阻害、hybrid更新復旧不能、novel/illust交差ID、未知ページ誤認、remote-only viewer欠落、型2のmetadata巻き添え、close後リクエスト、token省略による画像取得を回帰試験付きで修正した。実装者自身のレビューを独立承認とは扱わない。

## 検証記録

専用worktree内のignored `artifacts/phase4-completion-20261022/` に合成証拠を保持する。`full-final-02.xml`は製品コード凍結時516 pytest成功。`live-edge-final-02/result.json`は1280×720/1920×1080で未DL viewerの実decodeとページ画素、検索/選択/戻り/詳細キー、取得失敗と復旧、2client保持を確認し、page error・外部host通信は0、port解放・検査前後source一致。`default-edge-final-02`は既存4browser経路、`readonly-edge-final-02`は旧schema SQLite/保存画像/永続ブクマviewerと読取失敗復旧が成功。最初の再実行でcheckboxの非同期loopback更新前にPlaywright.checkが失敗したため、単一click後に実checked状態を待つ検証へ修正し、失敗試行も保持した。

開発実行のnative再診断では最小化/復帰のGDI全client画素差0を確認したが、配布exeの最終受入とは分ける。配布ビルドは専用srcのimport先を明示確認し、`build-inputs.json`の41入力がビルド前後で一致、配布1050ファイルのコピーSHAが一致。exe SHA-256は`a624e83c98cb34826dad2601b4763754d30e87f29b0057dfb3ab1e26a64e6778`。

同じコピーexeの`native-acceptance-03/native.json`で、旧DB→保存画像p1/p2→共有viewer、永続ブクマのDL印・検索・選択・viewer・戻り・タブ往復、I/Oの通常終了を検査。`ui_inspection.passed`・`restore_gate.passed`・`normal_exit`・`port_released`は全てtrue、観測8プロセス終了、forced cleanup・console/page/process-log errorは0。全client実GDIは復帰前後で安定連続2標本、平均画素差0.542237（許容上限1.0）、DPI175%。同一top/child HWNDのPID/生成時刻、寸法、位置、DPIも維持。画像送り性能の値ではない。

検証器の診断・所有窓前面化を含む最終`full-final-04.xml`は **520 passed / failure 0 / skipped 0**（Starlette TestClient非推奨警告1件）。compileall・pip check・git diff --checkも成功。PyInstallerのAnalysis-00.tocから製品36モジュールが全て当該専用src由来であることを確認し、ビルド入力41件と配布コピー1050件のSHAを受入後に再照合して不変を確認した。検証器のみの改修で製品exeを置換していない。

先行したコピーexe試行2回は最小化前のbaselineで不合格。診断でサンプル位置を別アプリのウィンドウが覆っていたと確定し、外部ウィンドウへは操作せず、検証用の所有top HWNDだけを`SetWindowPos`の初期前面化対象にした。元topmostフラグ復帰は従来NOACTIVATEのまま。全サンプルの所有PID/生成時刻、GDI、geometryの合格条件は緩めていない。`owned_top_foreground=false`でも画素取得点が全て所有ウィンドウに属すことを独立に要求する。検証器変更を独立レビューし、最初の不合格記録も保持した。製品exeは再ビルドせず同一物を使用。

## 未受入を残す

実Cookie原本の確認は接続ツールの安全性チェックでブロックされ、別経路で取得していない。今回の実認証成功・実アカウント画像/キャッシュ表示を主張しない。`check_live_bookmark_browser.py`はproductionのUI/サービス経路へ**合成のAPI代替**を注入する試験であり、pixivに接続する試験ではない。

フェーズ4全体は未完。実API受入、うごイラ再生・小説本文、類似/おすすめ/高度な絞込等が残り、他DPI/別PC/長時間運用、ダウンロード・キュー・失敗/重複処理はそれぞれ後続ゲート。PR #7の限定warm画像送り結果は変更せず、この変更から追加の改善率を算出しない。
