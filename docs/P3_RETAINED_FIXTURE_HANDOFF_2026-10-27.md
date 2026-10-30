# P3 退避Retry GUI回帰 fixture 修正（未実行）

候補: S:/Projects/PixivVault-NiceGUI-p3-retained-fixture-20261027
branch: phase11/p3-retained-fixture-20261027
HEAD: 2cf4a7a7286dd728e8df8125094498a3622babba
固定基線203: S:/Projects/PixivVault-NiceGUI-p3-history-recovery-20261027
基線SHA: 0dc9248c0a982309df34c0192bfab1ab2bb8117a0902cd7a367455961ef31644

開始時に前回write-endと全203ファイルhash、branch/HEAD、新候補不存在、対象候補writer不在を照合。203を使うCoS GUI・独立レビューには書込/停止/再実行なし。固定200/198/196も不変更。新候補は基線の製品差分をコピーし、旧artifacts/TEMPを混ぜていない。

## 元の唯一のFAILと修正理由

親からのCoS原結果は影響12テストファイルで203 PASS / 1 FAIL / 1 SKIP。唯一のFAILは tests/test_export_completion_history.py::test_real_failed_view_renders_retained_retry_and_dispatches_its_handler。QueueView constructor→refresh→_matching（ui/job_views.py:420）が self.services.artworks を列挙するが、テストのSimpleNamespaceはjobs/save_exportsだけだったためAttributeErrorとなった。製品の退避Retry handlerに未到達であり、製品不具合の再現ではない。SKIPはWindows symlink権限不足で変更しない。上記は固定203のCoS結果であり新候補のPASSへ継承しない。

QueueViewの現直接依存はartworks、jobs、save_exports。今回のfixtureへ artworks=() を追加した。export targetはdigestで、author検索文字列のカタログ対応を必要としないため、空カタログは正規の入力となる。jobsは実JobService、save_exportsは実SaveExportsのまま。製品callbackやrefreshをmockで素通りさせない。テスト内の合成失敗注入を解除した後、本物QueueViewが生成するretained-export-retryボタンの通常handlerを使って明示Retryする。

保持失敗提示、元job IDとbutton.enabled、handler呼出後に提示が消えること、同jobのdone/attempt2/同target、closeを維持。提示columnの可視性とボタン文言のassertを追加し、表示を検査する。期待値削除・待機延長・強制click・内部APIによるGUI代行を加えていない。同ファイルの他のテストは変更なし。

## 変更と同一性

実装差分はテストfixture1か所（説明コメントと表示assertを含む）。文書は本handoffとPROGRESS追記。src/とtools/は基線203と同一。final/product-equivalence.jsonに全対応path・旧新SHAと集合SHAを記録する。final/manifest.jsonはdirty全ファイル、final/diff-from-203.patchは今回の3ファイルだけの差分。製品の履歴/診断/容量契約は変更しない。診断総容量の配分/保持方針、独立レビューと実機受入の未達は基線のまま。

## CoS 限定再確認（Codexは一切未実行）

先にfinal/manifest.jsonの全hash、branch/HEAD、product-equivalence、固定旧候補保全を照合。新しい候補専用S TEMP/TMP、深いbasetemp、logを使用。既存のvenv/依存で実施し、旧候補の所有processやportは触らない。

```powershell
$env:PYTHONPATH = '<candidate>/src'
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
& S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B -m pytest tests/test_export_completion_history.py::test_real_failed_view_renders_retained_retry_and_dispatches_its_handler --basetemp <fresh-deep-S-temp> --junitxml <fresh-S-log>/retained-fixture.xml
```

期待はQueueView constructorを通過し、退避失敗の可視な通常ボタンから実handler→明示Retry→同job ID/targetでattempt2 done。続けて同ファイル全体を新しい別basetemp/logで実行すれば、他の履歴/再起動/JSON正規化回帰への影響も確認できる。新候補での実結果を記録し、親が必要と判断した場合に既存12ファイルを同条件で再確認する。製品・GUI checker全hashが203と同一であることは継承判断の根拠として渡すが、CodexがGUI合格や監査合格を宣言しない。製品hash同一でも新規の試験fixtureは未確認である。

pytest/import/構文検査/build/GUI/native/通信はCodex未実行。旧fixtureのAttributeErrorを静的に修正した候補で、実Retry経路の成否はCoSの結果待ち。前後hashと候補所有process終了を回収する。最終SHA/所有process/write-endは専用artifacts final。P3閉鎖/P4移行、容量配分の新方式は今回採用しない。
