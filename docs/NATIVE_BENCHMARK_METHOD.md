# Native Window Pixel Transition Benchmark

対象は **native message → 中央画像変化の観測時間（capture overheadを含む）**。平均FPSでも画面全体の入力遅延でもない。Flet / pywebview で同じ指定のcapture modeとWin32 `PostMessageW`（WM_MOUSEMOVE / WM_LBUTTONDOWN / WM_LBUTTONUP）、共通ROI・閾値を使う。既定の`--capture-mode window`は従来どおりPillow `ImageGrab.grab(window=HWND)`。`--capture-mode screen`は物理client領域を`GetClientRect`＋`ClientToScreen`で計算し、`ImageGrab.grab(bbox=screen_bbox, all_screens=True)`で取得する。2方式は混用せずmetadataの`capture_mode`/`screen_bbox`を校正・測定間で厳密照合する。DPI awareness v2は既存どおり座標照会より前に設定する。

`--capture-mode screen-gdi` は同じ物理client矩形をGDIのtop-down DIBへ直接取得する追加方式。以下のscreenに関する所有者・位置・被覆・寸法・一様画像の検査をすべて適用する。GDIの初期化/コピー/復元/解放に失敗した場合は例外で拒否する。3方式の校正を混用しない。同一合成画面での20組の画素一致・取得負荷と、故障注入/独立レビューの記録は `GDI_CAPTURE_REVIEW_2026-10-22.md`。

screen modeは専用top HWNDが可視・非最小化・`WS_EX_TOPMOST`であることを要求する。primeが事前にtopmostを設定・解除する（計測ツール自身はforegroundもtopmostも変更しない）。client bboxは仮想スクリーン領域に完全包含させ、初期bboxから移動・resizeしたら拒否。root PIDのbirthと初期process snapshotから列挙した子孫のPID+birthを記録し、各capture直前と直後にbbox内3×3＝9点の`WindowFromPoint`の所有PIDが記録済みroot/子孫で現在も同じbirthであることを検査する。PID照会不能・unknown ancestry・snapshot失敗は拒否する。**9点検査は完全被覆の証明ではなく、点の間や瞬間的な被覆は検出できない。** 全白・全黒など一様なframeは成功にしない。

`--input-interval-ms` の既定値は600。独立した1操作を測るため、直前のnative入力バッチからこの時間が経つまで次の入力を待つ。待機はverifyと計測開始より前で、応答時間には含めない。両backendに同値を使い、calibration/resultのmetadataへ保存する。`--input-interval-ms 0` は間隔なしの連続入力ストレスとして別に実行できる。2026-10-22の初回間隔なし試行ではFletは6遷移目でtimeout、WebViewは校正中に次の遷移を観測できず失敗した。これを成功値として捨てたり連打性能の合格と扱わない。30遷移すべて成功しなければ集約p50/p95はnullにし、個々の観測と失敗を残す。

2026-10-22の既存window modeはWebViewのtop HWNDの`ImageGrab(window=...)`が全白だった失敗記録を保持。paced/unpacedの失敗も保持し、600ms間隔を入力取りこぼしの解消証明とはしない。screen modeは別のcapture backendとして両者の校正と30遷移を実機で完走した。数値と条件差は `NATIVE_OBSERVATIONS_2026-10-22.md` に記録し、製品全体の速度受入とは区別する。

primeが架空データの**作品3の第3頁（page index 2）**を表示してから校正を開始し、同じclientサイズ・DPI・表示倍率・中央ROIを準備する。クリック座標はinput HWNDのclient原点、ROIはtop HWND capture原点。両client寸法を別途確認し、GetClientRect(top)とImageGrab寸法が完全一致しない場合は失敗する。root PIDと作成時刻、両HWNDの所有PID、正確なtitleを検査。ハンドルは10進数で指定する。

校正（それぞれのバックエンドに対応する `--backend flet` または `pywebview` を指定）：

```powershell
python tools/benchmark_native_pixels.py --calibrate --capture-mode screen --backend pywebview --root-pid 1234 --top-hwnd 123456 --input-hwnd 123456 --title 'PixivVault' --previous 200 400 --next 900 400 --roi 350 180 500 450 --metadata artifacts/native-pixels/session.json --output artifacts/native-pixels/pywebview
```

上記PID/HWND/title/座標は仮値。`--metadata` JSONに `session_label`・`seed`・`fixture`・`source`・`dpi` の非空値が必須。`--previous`を2回送り0頁を捕捉、`--next`で1頁、2頁を捕捉する。2連続安定条件を満たし、3頁が十分異なる画像でなければ失敗。`template-0.png`～`template-2.png` と **全画面 `frame-0.png`～`frame-2.png`**、`calibration.json` を保存。同一captureから切り出したROIとtemplateのハッシュ一致を検証。**primeがfull frameの作品とpage counterを0/1/2と目視確認するまでpending_human_reviewであり承認しない。**

測定は `--measure --calibration-approved --samples 30` を使用する。**校正直後はpage2、再測定時もprimeがpage2に戻してから起動する。** 最初のnative入力より前に、現在のROIが校正済みpage2と一致することを画素で確認し、不一致なら拒否する。PID/作成時刻、HWNDごとの所有PID、title、top/input client寸法、ROI、座標、backend、必須JSON、テンプレート/full frame SHA-256を照合。未知PIDや作成時刻の照会失敗は拒否する。page2から0へ戻って `0→1→2→1→0` を巡回。`perf_counter_ns` はinput windowのverify後、WM_MOUSEMOVE直前に取得するため、**事前verifyは計測値に含まずcapture overheadは含む**。各遷移2.5秒期限。`result.json` に全サンプル、p50/p95、capture overheadを保存する。

`--metadata <json>` にclone/source/viewport/DPIや試験条件のJSONオブジェクトを記録できる。比較時は条件を揃え、双方の結果とPNGをprimeが別途精査する。今回のscreen方式はWindows11・DPI175%の専用プロセスで確認したが、別PC/全DPIの確認ではない。黒画像、画像同一、ウィンドウが別PID、タイムアウトを測定成功として扱わない。実GUI計測はprime担当。
