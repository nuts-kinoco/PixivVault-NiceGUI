# Windows配布ソース凍結ゲート — P3-318

## 理由と適用範囲

移植本体のdirtyな作業ツリーからそのままPyInstallerを実行すると、並列修復中の異なるソース世代を1つのexeへ混在させ、過去のpytest結果と配布exeの対応を失う。`tools/freeze_windows_candidate.py` は現在の製品/ビルド入力を**書換えず**新規専用領域へバイト単位で固定し、`source-manifest.json` のハッシュと実コピーを全数照合する。

- 対象：`src/pixivvault_nicegui` 内のPython/JS/CSSと、`pyproject.toml`、`README.md`、`tools/build_windows.ps1`、`tools/native_entry.py`、検証器自身の`tools/freeze_windows_candidate.py`。`__pycache__`/`.pyc`は入力とせず、未知の拡張子が製品package下にあればfail-closed。
- 送元/送先のpath componentはsymlink/reparseを拒否。実ファイルはsingle-link regular fileのみ。各ファイルのlstat/fstat・バイト数・SHAを読取時とコピー後に照合し、集合の前後も再走査する。コピー先はこの作業ツリーの`artifacts/`以下にある既存でないフォルダーのみ。
- Git HEAD/branchはラベルであり、未コミットソースの証明ではないことをmanifestに明記。ハッシュはdirtyな実バイトを固定する。途中変更やエラー時は成功manifestを作成せず、部分出力を自動削除・再利用しない。
- 旧Flet、Cookie、実画像、旧SQLite、実アカウントの通信はこの処理に一切関係しない。

## 実施済み検証

- `artifacts/p3-318-freeze-gate/test-r1/result.xml`: 初回5 PASS。出力先不変、未知ファイル拒否、凍結途中変更、reparse注入、manifest全数照合。
- `artifacts/p3-318-freeze-gate/test-r2/result.xml`: 追加した出力先境界を含め6 PASS／0 FAIL／0 SKIP（0.31秒）。JUnit SHA256 `cd4ac6ea865c46c60a397d7f8617ae54180b18421f8e89b9960d87d5657bee34`。
- 追加：検証器そのものを凍結入力に含め、`--verify`で保存済みmanifestと実コピーを改めて全数照合できるようにした。改ざんしたコピーと重複キーを含むJSON manifestはfail-closed。`test-r3`は重複キーのエラー区別が失われていたため**1 FAIL/7 PASS**として保持し、エラー処理のみ補正。`artifacts/p3-318-freeze-gate/test-r4/result.xml` は**8 PASS/0 FAIL/0 SKIP**（0.42秒）。
- 実ツリーを対象とした手順検査 `artifacts/p3-318-freeze-gate/live-preflight-r1/`：**161入力の凍結と、その凍結コピー自身の検証器による再読取が成功**。source集合SHA256 `23c6bd4d886bdf77112df4eb18bfccf4b1d14377d44cd6c6d1b192713d1a908d`、manifest SHA256 `eec14a1c71c78b7877fdbc02300558bfe90dc38b20abb9d2ced410adffc88d75`。生成後の別回帰・配布build・独立レビューは未実施なので、これは**操作手順のpreflight**であり最終候補ではない。
- 製品ツリーの**読取だけ**のpreflight：160入力、合計2,514,824 bytes、読み取った瞬間の集合SHA256 `930a022518aef079b6dd28c5faf31b36b832686dd78e66865450460368bcad0b`。この値は進行中の並列変更により無効になる可能性があり、最終候補版のSHAと呼ばない。
- 先行r2時点のツールSHA256：`3a1d6d0ad8b9fa1549799958a729433f2476fa1e90bbbdfe9fa7d82fc5514404`、合成試験SHA256：`deec319ade31e8bcc7e6f9883d2b9125fcc4ebb8e449c77e12ded626be337540`。**現行r4変更後のハッシュではない。**

## 最新修復後の使用順序

1. 先行する修復、独立レビュー、最新全回帰と対象Edgeが安定してから、**新しい出力名**を指定する。実行例：`python -B tools/freeze_windows_candidate.py --output artifacts/p3-319-native-build/frozen-candidate-01`。同じ名前の再利用は禁止。
2. `frozen-candidate-01/source-manifest.json` が `status=verified`であることを確認。**凍結コピーを作業ディレクトリにして**`python -B tools/freeze_windows_candidate.py --verify .`を実行し、全ファイルSHAとbytesを再照合。元ワークツリーの検証器から`--verify artifacts/p3-319-native-build/frozen-candidate-01`でも照合可能。旧source manifestや旧exeのSHAは持ち込まない。
3. 凍結した別rootを作業ディレクトリとし、**そのコピーの**`tools/build_windows.ps1`でfresh `dist/build/spec` を生成。`-Python`には検証済みの配布依存を持つ絶対Python pathを渡す。live rootのビルドはこの凍結候補の証拠にならない。
4. 同梱JS/codec/依存metadataとexeのSHA、完全なonedirファイル一覧、別フォルダー全ファイルSHA、privacy scanner、native WebView実操作・最小化/GDI復帰・確認/トレイ/URI・子PID/port・再起動/保存receiptを受け入れる。
5. Fletとの実画像・DPI同条件ABBA、正式152原操作、実Cookie GET境界、独立監査、CI/PRを完了して初めてGoalを判定する。

**現状は凍結ツールの限定CPUゲートのみ成功。現ソースの凍結・Windows再ビルド・配布受入を行ったという意味ではない。** 並列担当のr5全回帰には失敗ケースがあり、修復後の全回帰を優先する。
