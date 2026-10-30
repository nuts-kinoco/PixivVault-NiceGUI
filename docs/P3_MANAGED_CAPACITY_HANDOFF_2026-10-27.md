# P3 L12 managed active容量再計測（CoS確認待ち）

固定233 / `ac13cf6879f228927db393357547526a783efa8715f2bbd68da150c7e7f9a0d1` から分離。LibraryState.visible_authors の先頭50作者を対象とし、管理下のactive receiptとページの実bytesを再読取。receipt digest、ページ長・digest・画像内容とactive generationを確認し、成功値だけ作者容量に反映。管理外の旧フォルダー/ZIPは加算しない。active receiptがない作者や失敗は0とせず、以前の値を保持する。全receiptのカタログ検証が失敗した場合は全体を失敗として以前の表示を保持する。

UIは「管理保存容量を再計測」を非同期実行し、実行中重複を抑止。検索・画面切替・reload/閉鎖、state generation、active indexの前後不一致で結果を破棄。表示は管理保存と明示、結果はLibrarySnapshotとその画面セッションに限定し、新しいDB・sidecar・永続cacheを作らない。成功0と未計測Noneを混同しない。

CoS: S隔離の合成fixtureで `python -B -m pytest tests/test_managed_capacity.py tests/test_library_view.py tests/test_library_filters.py tests/test_managed_downloads.py -q` を実行（既存環境のテストファイル名を事前照合）。合成の複数作者・50超・複数ページ・receiptと画像の実byteを比較し、未知/失敗と0、active世代切替、リンク・不正名・破損、検索/タブ切替・close・重複クリック中の取消、容量順の反映を確認。実GUIでは管理保存と旧データが混在する作者も表示対象が管理保存に限定されることを確認。結果・画像・旧候補/元データ前後hash、process通常終了を記録。実Cookie/API・ユーザーデータは使わない。

実装者のpytest/import/構文/build/GUIは未実行。旧フォルダー/ZIPと永続capacity cacheは未実装。原L12全体の受入や152操作台帳の加点は行わない。独立レビュー・正式受入も未完。
