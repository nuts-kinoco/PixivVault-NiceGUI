# P3 保存export GUI 修正候補（実行前）

基線: p3-intent-io-20261027 / dirty182 / SHA cdddfa18f58749ad564712e30c6dbcad08f98c7fdf3b32d157f4891bb9fd21ca。専用後続: S:/Projects/PixivVault-NiceGUI-p3-export-ui-fix-20261027、branch phase11/p3-export-ui-fix-20261027、HEAD 2cf4a7a7286dd728e8df8125094498a3622babba。開始時に新候補不存在・候補writerなしを確認。旧182をhash照合し、manifest対象だけコピー。CoS artifacts/fixture tempはコピーせず固定保持。

## 原票と原因の切り分け

旧候補 artifacts/p3-intent-io-20261027/cos-owned-20261027-0246-r1 のcpu.xml SHA3C65AE2E28EB97BD18B702990F3012DBF724472B9FC736F3A0AE20963DDAA07D、gui-msedge-r1/result.json SHA4309B6167856FDE4BDC8223CA12EDF1178ECCA00E0E6E1F418F4ECA8309859F3を現物照合。各初回result.json/failure-dom.htmlとfailure.png両サイズを実読した。

1280: Locatorはnovel-catalog-502のvisible/enabled/stableを確認しているが、c187説明文・c182 toolbar・c173親viewがpointerを遮る。DOMではcatalog columnがmax-height160/overflow:autoのflex item、親pv-jobs-viewはflex列で高さ制約とスクロールを持ち、toolbar/subtitleだけは既存CSSでshrink禁止。画像は候補行が潰れた状態で後続説明・toolbarが続く。製品側の縮み/重なりを修正対象とした。pv-novel-download直下要素とcatalog内ボタンをflex-shrink:0にし、親の既存overflow-y:autoで通常スクロールできるようにする。catalog最大高さ160は維持。z-index/強制click/JS dispatch/固定sleep/タイムアウト延長は追加しない。実ヒットテストの改善はCoS未確認であり、画像だけで修正成功としない。

1920: DOMのjob-state実UnicodeはU+30A8 U+30E9 U+30FC（エラー）。画像も同じ表示。job_views.pyの既存error表示も同じ。checkerソースはU+5931 U+6557（失敗）を期待していた。これは表示仕様と検査期待語の不一致で、ログencodingだけの問題ではない。製品の状態文言は変えない。checkerを正しい完全一致へ修正し、data-state=error検査を残す。Retry後もattempt2/errorの永続snapshotだけでなく同じ行の状態属性と表示を再確認する。Unicode escape表記を使い、シェル表示encodingに期待値を依存させない。

## 差分と回帰

- ui/theme.py: 小説downloadだけの縮み防止2ルール。
- ui/novel_save_view.py: catalog列へ限定CSS class追加。
- tools/check_p3_save_exports_browser.py: 通常catalogクリック後の入力ID一致確認、正しいerrorラベル完全一致、Retry後のDOM状態/表示再確認。両実ボタン・preview/save・形式・件数・後続reader/restartは保持。
- tests/test_export_ui_layout_contract.py: CSS適用範囲とcatalog接続の静的回帰。ブラウザgeometry合格の代替ではない。
- 本書とPROGRESSに未実行/CoS手順を記録。

managed intent/receipt/CAS/export本体/fixtureデータは本候補で変更しない。前基線CPU25PASSと旧fixture2件PASSはその基線の結果であり、変更後の実行結果に加算/継承しない。静的読み戻しのみ、試験/構文検査/GUI起動なし。

## CoS 限定再試験と終了条件

final/manifest.jsonの個別SHA/集合SHAと旧182保全を照合。新しいS専用TEMP/TMP、PYTHONPATH=<candidate>/src、PYTHONUTF8=1、PYTHONDONTWRITEBYTECODE=1、既存S venvを使用。

```powershell
& S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B -m pytest tests/test_export_ui_layout_contract.py tests/test_save_exports_gui_fixture.py tests/test_novel_save_view.py --basetemp <fresh-S-temp> --junitxml <fresh-S-log>/cpu.xml
& S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B tools/check_p3_save_exports_browser.py --channel msedge --output <candidate>/artifacts/<fresh-owned-run>
```

前段成功後にGUI4ケース（1280/1920初回+再起動）。1280で公開501保存後の非公開502を通常clickし入力ID一致、重なりなくフォーム/preview/submitにスクロール到達できること。1920でsource4/projection4とZIP done/refused、errorラベル完全一致、通常Retry→attempt2/error表示。両方でTXT/画像のprojection reader・参照hash・再起動preferences/jobs一致まで元期待を維持。スクリーンショット/DOM/ログ/結果保存、外部request0・正常所有終了/port解放・前後source一致。8089/他所有processへ非干渉。成否不明の操作を照合前に再送しない。

原受入のRetry/projection reader/再起動は依然未確認。独立監査/native/live/exact-head CI/総合UX/P3閉鎖/P4判断も未達。書込み終端・全hash・差分・旧182保全はfinal配下。所有app/test/serverは起動していない。
