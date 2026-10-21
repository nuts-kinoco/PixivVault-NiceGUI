# 移植進捗

更新: 2026-10-22 03:00 JST。今回の作業単位: `bootstrap/phase0-shell`。

## 状態

公開pushと新アプリの実画面操作は承認済み。初回 `500902a` と、画像配信・学習状態・読取アダプタの `160dbbe` を公開済み。
この後続単位では、合成データによる一覧/ビューア/学習の実ブラウザ操作と、Windowsの最小配布物を検証した。
元のPixivVaultの作業ツリー・DB・画像・Cookie・環境は変更していない。UIは依然として架空120作品のみで動く。

**全面移植・外観一致・高速化の受入は未完。** ランキングと学習の代表経路は動くが、他6画面、実データ書込み、旧Fletとの同条件性能比較は未実施。

## 担当

- worker: 画像/資源寿命、学習、読取互換、ブラウザ検証、Windows終了監視の限定実装と独立レビュー。1ファイルの編集を担当範囲に分ける。
- prime: 接続・画像イベントの切分け、ブラウザ/WebViewの実操作、ビルド、統合確認、記録、Git。
- 上限8workerの承認はあるが、この再検証では通常2件並列で分担した。利用枠の消費量は未計測。

## 検証

環境: Windows / Python 3.13.11 / NiceGUI 3.18.0 / pytest 8.4.2 / Playwright 1.64.0 / Edge・WebView2 155.0.4283.62。
新repoの専用venvを使用。バージョン一覧は `requirements-lock.windows.txt`。全間接依存の固定一覧を保存したが、別PCでの再構築は未検証。

| コマンド | 実測結果 |
|---|---|
| `python -X utf8 -m pytest -q --junitxml=artifacts/final-pytest.xml` | 2026-10-22 02:55頃、122 passed、4.25秒、exit 0 |
| `python -m pip check` | No broken requirements found、exit 0 |
| `python -m compileall -q src tests tools` | exit 0 |
| `python tools/check_browser_suite.py --channel msedge --output artifacts/browser-suite-final` | 02:51–02:52 JST、一覧2サイズ・学習13項目×2・画像故障/遅着7項目・2ページ独立性を通過、予期しないconsole/page error 0 |
| `python tools/run_browser_checks.py --channel msedge --output artifacts/browser-integrated-final` | 一覧/学習/画像競合の3scriptが全てexit 0、試験用サーバーのポート解放を確認 |
| PyInstaller `--onedir --noconsole` の専用出力ビルド | exit 0。独自画像コンポーネントのJS同梱・元ソースとの一致を確認 |
| 配布exe `--check --check-output <絶対JSONパス>` | demo / synthetic / native requested・available true、exit 0 |
| `python tools/check_native_window.py --exe artifacts/restart-standalone/native-app/PixivVault-NiceGUI.exe --inspect-ui --output artifacts/restart-standalone/native-acceptance.json` | 02:55 JST、別フォルダーへコピーしたexeの表示・画像・ビューア・学習/Undo・最小化/復元・通常終了・子プロセス/ポート解放を通過、強制cleanupなし |
| `git diff --check` | exit 0 |

ブラウザの比較条件は1280×720と1920×1080。50サムネイル、50→100件で既存DOM保持、カード大中小、検索/clear、作品内のページ送り、詳細とEsc二段、一覧へ戻るscrollTop保持を確認した。
学習は4判定、Undo、古い世代の拒否、通常ショートカット、repeat/IME/入力/ダイアログ/非activeでの抑止を確認した。
故障注入した画像のHTTP503/404だけを期待エラーとして記録し、他のconsole/page errorは失敗にする。
試験前後の製品ソースハッシュは一致している。生の報告・画像はignoredの `artifacts/` に保存し、gitには同梱しない。
122件の実行時間やPlaywrightの待機時間は、アプリのp95性能値ではない。

native実測のWebViewクライアント領域は1907×1044、`devicePixelRatio=1.75`。これを100/125/150%の全条件合格へ読み替えない。
本番ジョブ・トレイ・URI・別PCへのインストール・長時間運用は今回の最小配布試験に含まない。

実証して修正した箇所:
- Undo時、キャッシュ画像の `currentSrc` が以前のfragmentを返し、画像readyが回復しない不具合。要求ごとの実DOMノードとdecode完了、HTTP資源の一致を照合し、捕捉した要求URLを通知する。サーバー側の世代照合は緩めていない。
- Quasarの既定文字色と選択中タブ背景が共に青になる不具合。ナビゲーションの既定colorを外して既存トークンで制御し、computed styleとスクリーンショットで読める状態を確認した。
- 並行closeによる資源の二重解放。closeを排他し、並行実行試験で一度だけ解放されることを確認した。
- native終了監視で、プロセスの照会失敗を終了済みと誤認する経路。PID/作成時刻・列挙失敗・待機戻り値を確認し、不明を監視失敗として出す。故障注入試験と実exeの終了確認を行った。
- 並行統合で重複したCIジョブキー。ブラウザジョブを1つに統合し、重複を検知する回帰試験を追加した。

GitHub ActionsではUbuntu/Windowsのpytestと、専用Chromiumによる実ブラウザ試験を別ジョブで実行する。
**この記録時点では後続UIコミットのCIは未実行。** 最新コミットの結果はPR #1のChecksで確認する。古いコミットの成功で代用しない。

## 製品受入の未完

旧Fletと同じ画像・件数・画面条件での速度/メモリ比較、全画面の外観一致、実ライブラリ接続、実学習の保存、ダウンロード/停止/再開、トレイ/URI/全DPIは未完。
読取アダプタは一時fixture DBでの互換確認まで。任意の本番DBを暗黙に開く入口は作っていない。
後続の旧Flet隔離ベースライン案は `LEGACY_BASELINE_PREPARATION.md`。調査だけで、旧アプリを起動した性能値はない。

## 次の受入

この代表経路の検証済み実装を小単位でコミットし、PRのWindows/Ubuntu/Chromium CIを確認する。
次は同一fixtureによる旧Fletとの比較入口と、残り閲覧系の契約を確定する。比較測定と全体の視覚確認を通す前に、既定UIの置換や本番書込みへ進まない。
