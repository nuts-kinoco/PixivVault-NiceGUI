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
この初期版はDB・画像フォルダ・Cookie・pixiv APIに接続せず、120件の架空作品を使います。画像枠は表示用の代用品です。
検索・追加表示・カードサイズ・作品詳細の経路を実装中です。他タブは未移植を表示し無効にします。

`--native` は専用ウィンドウのための入口ですが、pywebview導入・Windows配布・実機操作試験は未完です。
エージェントによる画面クリックやキー操作にはユーザーの許可が必要です。

## 計画と証拠

- [フェーズ別計画](docs/ROADMAP.md)
- [基準と操作台帳](docs/BASELINE.md)
- [進捗・未完・検証結果](docs/PROGRESS.md)
- [作業規約](AGENTS.md)

NiceGUI 3.18.0を直接依存として固定しています。間接依存全体の固定と再現可能なWindows配布は、配布検証時の成果物です。
