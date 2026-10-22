# PixivVault-NiceGUI

Windows版PixivVaultの段階移植用リポジトリです。現在は**架空データだけの開発デモ**で、元アプリの置換版ではありません。

見た目・操作順・データ互換を維持し、画面更新や画像表示の待ちを減らすことを目指します。速度改善と外観の一致は、実機比較が終わるまで未確認として扱います。

## 専用環境

Python 3.13を使います。既存PixivVaultの環境には追加インストールしません。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pixivvault_nicegui --check
```

## デモ起動

```powershell
.\.venv\Scripts\python.exe -m pixivvault_nicegui
```

ブラウザを手動で `http://127.0.0.1:8089/` に接続します。ブラウザは自動で開きません。終了は起動した端末でCtrl+C。
既定のデモは本番DB・画像フォルダ・Cookie・pixiv APIに接続せず、120件の架空作品とコードで生成した診断PNGを使います。
検索、50件単位の追加表示、カード大中小、作品詳細、作品内のページ送り、学習4判定とUndoの検証経路があります。
学習は画面内のメモリ台帳だけに記録します。実データへの保存・学習アルゴリズムの本番接続は未実装です。
ダウンロード、キュー、失敗リスト、重複整理、ライブラリ、おすすめの6画面はまだ無効です。

Windowsの専用窓は `pip install -e ".[native]"` の後に `python -m pixivvault_nicegui --native` で起動できます。
開発機のWebView2とonedir exeでは、画像表示、一覧/学習の操作、最小化・復元・通常終了とプロセス/ポート解放を検証しています。
別PC、Windowsの全DPI条件、トレイ、URI連携、長時間運用は未検証です。配布手順は [Windows配布検証](docs/WINDOWS_PACKAGING.md) にあります。

## 実ブラウザの回帰試験

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[test,browser]"
.\.venv\Scripts\python.exe tools/run_browser_checks.py --channel msedge
```

ランナーが専用の一時ポートにデモを起動し、Edgeで一覧・学習・画像故障/遅着を操作した後、自分のプロセスを停止します。一覧は1280×720と1920×1080で確認し、学習は操作試験に加えて画面サイズ別の画像・ボタン配置を確認します。
Chromiumを使う場合は `python -m playwright install chromium` を実行し、`--channel chromium` を指定します。
結果と合成画像のスクリーンショットは `artifacts/browser-ci` に保存されます。`check_browser_suite.py` は同じランナーへの互換入口です。pytestだけの成功を実画面の成功とは扱いません。

## 既存データとの接続範囲

`ReadonlyLibraryRepository` は既存schemaの読取アダプタです。明示された既存DBを `mode=ro` / `query_only` で開き、ファイルが無ければ作成せず失敗します。
検証には一時DBだけを使っています。既定のUIに本番DBへの自動接続はありません。詳細は [読取契約](docs/LIBRARY_ADAPTER.md) を参照してください。

## 計画と証拠

- [フェーズ別計画](docs/ROADMAP.md)
- [基準と操作台帳](docs/BASELINE.md)
- [進捗・未完・検証結果](docs/PROGRESS.md)
- [作業規約](AGENTS.md)

NiceGUI 3.18.0を直接依存として固定しています。開発機で検証したWindows依存一覧は `requirements-lock.windows.txt`。別PCでの同一環境再構築と配布の受入は未完です。
