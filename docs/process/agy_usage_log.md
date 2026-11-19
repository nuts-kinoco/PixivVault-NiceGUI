# AGY 利用記録（PV）

形式: 日時 / プロバイダ・モデル・effort（確認できなければ未報告）/ 担当 / 成果と検証 / 採用・不採用 / Codex・Claude との重複

- 2026-11-18: 利用許可を受領（AGY の利用枠 100%）。まだ利用していない。最初の用途: wt-w（保存領域）の Terra 修正後に、Sol と別系統の独立レビュー（Argon high、読み取り専用）。
- 2026-11-19: Argon（gemini-4-argon-high、effort 未報告）/ wt-w 最終差分（d1c0636、Sol 指摘の修正確認）の読み取り専用レビュー / 結果は tmp\agy_wtw_out.md / タイムアウト（900 秒、出力なし）で結果なし。不採用。再送せず Sol へ回した / 重複作業なし（Sol の指定 2 周の対象外の追加差分のため）
- 2026-11-19: Argon（gemini-4-argon-high、effort 未報告）/ wt-ai RequestAdmission（FL-Z10）の競合・優先逆転の読み取り専用監査（Sol 5 回不合格後の別系統確認）/ 結果は tmp\agy_z10_out.md（1500 秒上限、送信は 1 回、再送しない）/ 採否は回収後に記載 / 重複作業なし
- 採否（Z10）: Argon の指摘（待機中の印が先頭判定を歪める 4 件）は 4 件とも妥当と判断し採用。構造を簡素化（仮確定・事後チェック廃止、parked はチェックポイント中だけ、譲り待ちの起点は先頭になった時点）。Argon は Sol と同じ構造の欠陥を別経路で指摘した（有効）。タイムアウトなし（約 5 分）。
- 2026-11-19: Argon（gemini-4-argon-high、effort 未報告）/ wt-w アカウント分離（9 ラウンド不合格後）の設計・受入条件の妥当性の相談（読み取り専用、1 回送信、再送しない、上限 1500 秒）/ 結果は tmp\agy_wtw_acct_out.md / 採否は回収後 / Astra（run advisor）と同じ資料・同じ問いを独立に並行
- Argon 回答（tmp/agy_wtw_acct_out.md、約 3 分で完了、タイムアウトなし）: 原因＝設計 7 割（省略可能な account_id の暗黙フォールバック）、出口（保留削除・完了刻印）が無防備。推奨＝入口で束縛・出口が必須引数で検証。残る穴＝手動バッチ開始（unified_download_view.py:912）が account_id 未指定、require_current_account が実クライアントで恒真の疑い。受入の線＝画面を開いたままのアカウント切替後の操作と、ディスパッチ開始時・各作者一覧取得ごとの照合までを必須、内部属性を裏から書き換える TOCTOU 注入は脅威モデル外。採否: 内容は有効と判断（Astra の回答と Sol 第 10 回の結果を見て確定）。
- 2026-11-19: Argon（gemini-4-argon-high、effort 未報告）/ wt-w アカウント分離層の再設計書の作成（設計書のみ、コード変更なし、本人指示『設計そのものを再設計させても良い』）/ 結果は tmp\argon_redesign_acct_out.md（1 回送信、再送しない、上限 1700 秒）/ 採否は回収後 / Sol 第 10 回は並行で継続（止めない）
- 2026-11-19: Argon（gemini-4-argon-high、effort 未報告）/ wt-ak FL-I15 統合の再設計書（収束チェックの SWITCH・CONSULT を受けた設計の相談、本人指示『設計そのものを再設計させても良い』）/ 結果は tmp\argon_redesign_ak_out.md（1 回送信、再送しない、上限 1700 秒）/ 採否は回収後
- 2026-11-19: AGY 1.5.12（最新の更新履歴も 1.5.12）。`agy models` の選択肢: gemini-4-neon（low〜xhigh）、gemini-4-argon（low〜high）、gemini-4-hydrogen（low〜xhigh）、orchestrator、gemini-3.8-flash、claude-opus-5-5、claude-sonnet-5-5、gpt-oss-120b。使い分けの仮説（未検証）: Argon high=設計・監査（実績あり）、Neon=別系統の独立した設計意見、Hydrogen=軽い網羅・定型調査。`agy update` は実行中の agy ジョブを壊さないよう、ジョブ完了後に確認する。
- 2026-11-19: Neon（gemini-4-neon-high、effort 未報告）/ wt-ak FL-I15 統合の再設計書（Argon と同じ資料・同じ問いを独立に並行、重要な設計分岐のため 2 名）/ 結果は tmp\neon_redesign_ak_out.md（1 回送信、再送しない、上限 1700 秒）/ 採否は回収後
- Neon 回答（tmp/neon_redesign_ak_out.md、約 5 分）: Argon と同じ結論（A＋D、LocalTagView）。独立した 2 モデルが一致したため採用。再裁定: 補完タグは検索語・永続領域に使わず、読み取り時の順位付け・傾向・ランキング印にだけ使う。
| 2026-11-20 00:09-00:16 | AGY | gemini-4-neon-high | 未報告 | wt-p4 差分の独立レビュー（E01/X09/V08、問い5つ） | 高1 中1 低2、ツリー不変を確認 | Sol に真偽確定を依頼（採否は Sol 結果後） | 重複なし |
| 2026-11-20 | 採否追記 | gemini-4-neon-high（wt-p4 レビュー） | - | Sol が真偽確定: 高(E01 settings)不採用＝元と同じ全DBバックアップで復元に必要、中・低2 採用し修正（b4d0784） | 3/4 採用 | 有効 | - |
| 2026-11-20 | AGY | gemini-4-neon-high | 未報告 | wt-p6 再検証（3 問に絞る） | 問1・3 問題なし、問2 低 | 採用（低は既知の限界） | 重複なし |
