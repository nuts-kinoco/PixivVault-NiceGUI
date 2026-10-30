# P3 うごイラ保存形式ラベル（CoS確認待ち）

固定228 `S:/Projects/PixivVault-NiceGUI-p3-theme-fields-20261027` / `ceda2e6a931fe1c39a00b9f6074bafd1674bd21c388c3eff49669a433fdba90f` を分離。CoSは228のCPU34/34、Edge4/4、L13実操作36・保存失敗4/4、入力9欄と無効4項目の両テーマ確認を報告。1280/1920×dark/lightでうごイラ形式selectの「保存形式」が横幅約74.56px、ラベル領域約27pxで省略されるという追加報告を受領。今回228のartifacts下にはこのCoS原票が見当たらず、数値は報告情報であり実装者自身の実測としない。

原受入 `docs/USER_ACCEPTANCE_CONDITIONS_2026-10-24.md:13` の文字可読性・クリッピング確認に対応。既存 `pv-toolbar` のflex折り返しを維持し、ugoira形式selectだけに `pv-ugoira-format` の10rem優先/最小幅と親幅上限を追加。430px固定や新しい画面・保存形式は採用しない。GIF/MP4/フォルダー、dark継承、元job操作は維持。layoutの実測やCSS有効性は未検証。

回帰: 既存の実媒体View testにクラス・3形式の検査を追加。`tools/check_theme_fields_browser.py` の実DOM checkerで、各媒体rootの表示待ちと両テーマのinput/select検査を保持し、ugoira形式欄の実ラベル文字・表示・scrollWidth/clientWidthを検証。GIF/MP4/フォルダー各選択後もラベルを再検査。画面幅/高さ引数を追加。

CoS手順: 候補manifest・固定228と223不変・所有process/portを確認。既存S隔離fixtureで `python -B -m pytest tests/test_p3_theme_field_inheritance.py tests/test_p3_theme_toggle.py tests/test_p3_card_size_shared.py tests/test_preferences.py tests/test_p3_ui_state.py -q` を実施。所有中fixture serverのloopbackに対し次を別outputで実行（PORTは実際の所有portへ置換）。

```
python -B tools/check_theme_fields_browser.py --url http://127.0.0.1:PORT --width 1280 --height 720 --output artifacts/p3-ugoira-label-20261027/cos-owned/1280
python -B tools/check_theme_fields_browser.py --url http://127.0.0.1:PORT --width 1920 --height 1080 --output artifacts/p3-ugoira-label-20261027/cos-owned/1920
```

画面結果を読み、保存形式ラベルが両サイズ・両テーマで欠けず、3選択肢と選択値が操作可能で、狭幅で欄全体や隣接要素・ボタンが重ならず、水平overflowがないことをscreenshots・computed geometryで確認。dark/light入力/無効項目、L13の3入口の同期・保存/失敗・再起動条件を変更に応じて再確認。前後hash、独立レビュー、owned processの通常終了・port解放を記録。checkerは既存fixtureを停止しない。前228のPASSは変更前根拠で自動継承しない。

試験/import/構文検査/build/GUI/実通信は実装担当未実行。S21全面受入とP3終了は未達。ZIP更新・作者改名・物理隔離に変更なし。
