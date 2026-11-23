# Dots 返信の要約（FIXNOTICE-R8、統括経由、合言葉 暁の天秤-FN8-2941 一致、対象 6d6352e1）
総評: 統合不可。429 停止後に待機済みの別取得が送信に進めるため、送信許可境界を修正してから再判定。
- H01 停止確定前に間隔待ちへ入った別取得が、停止確定後も送信へ進める（request_policy.py:536–561、pixiv_read_client.py:873–886。別リーダー B が停止フラグを確認し送信間隔の待機に入り、その間に A の 429＋終了異常で停止が確定しても、B は待機後に停止フラグを再確認しない。提案: 共有の送信許可境界で停止確定と送信開始を直列化）。※第 9 回（Sol 再検証 M1 と同じ）で対応中。
- M01 最終再読で初めて得た STOP／結果不明が直後の照合失敗で消える（request_notice_gates.py:249–263, 280–306）。
- M02 アイドルタイマーの終了異常は製品入口で固定通知に届かない（owned_https.py:381–431、pixiv_read_client.py:837–871）。※第 9 回で対応中（Sol M2 と同じ）。
- M03 R/W SQL だけの継続異常を観測が open と扱う（request_notice_gates.py:154–184、managed_network_stop_gate.py:453–480）。
- M04 旧リーダーの選択証明の合算で通知が解除不能になる（request_policy.py:113–122, 458–468、request_notice_gates.py:197–213）。
- L01 native_profile.py:408–414、managed_network_stop_profile_scope.py:59–69。
前回 4 件の照合: (1) 終了異常の帰属＝修正を確認 (2) R/W SQLite の close＝指定ケースは修正（SQL 実行時だけの異常は別=M03） (3) 標準 HTTPS の 429＋cleanup＝直列は修正、並行は未解消（H01） (4) 最終確認で見つけた STOP＝一部解消（M01）。
未確認: full.log・release-green.log・candidate-state.txt は公開枝で 404（軽量ブランチに含めていない）。実行候補と提出枝のソース同一性、Windows 実機・実ブラウザー・並行時の実測。
