# 実Cookie読取の認証・一覧互換と実画面受入

基準main: `e8835c4e4209efe3eb76c296889c672b0e6a3898`。専用branchは `phase4/pixiv-auth-compat`。全移植を最終目標とし、この単位は段階4のライブ読取マイルストーンである。原版、Cookie原本、本番DB、実画像は変更しない。

## 変更と原版契約

- ログイン済み本人の旧 `meta-global-data.userData.id` に加え、`__NEXT_DATA__.props.pageProps` の厳密な `isLoggedIn is True` と `gaUserData` の一意な正IDを認証根拠にする。両形式があるときは有効性とID一致を必須とする。
- 重複する認証HTML属性・JSONキー、欠損content、不正型、複数認証ブロック、未閉scriptなどは固定エラーで拒否する。正常な自己閉じmetaは旧互換として許可する。CSRFトークンは読取・保持しない。
- pixivが一覧に返す表示不可のmasked項目は、IDと取得件数を検査した後に除外する。maskedだけのページを終端にせず、後続offsetを取得する。公開/非公開×イラスト/小説の4経路すべての完走、空終端、重複のみページの拒否、件数/時間上限を維持する。
- HTTPS宛先の限定、GET-only、期限切れCookie拒否、画像CDNへのCookie不送信、レスポンス/認証値の固定エラー化は変更しない。代替画像のhostを画像許可先へ追加して回避したわけではない。

## 再現・修正・独立レビュー

認証拡張の初稿に対する追加回帰では、正常meta拒否、重複属性/JSONキーの最後勝ち、値なしcontentのTypeErrorなどを **7失敗/1成功** として再現した。修正後の認証・4経路アダプタは100件成功。独立担当も同100件を実行しAPPROVEと判定した。

実Cookieでは認証成功後、公開の2経路だけが `InvalidPayload` になった。本文・作品IDを保存せず構造の件数だけでmasked項目との一致を確認し、通常作品と分離した。対象の回帰と実reader注入transportの計116件が成功。別担当の静的レビューも重大指摘なし（当該担当のpytest再実行は未成立なので、独立実走とは数えない）。

## 合成環境の受入

- 全pytestは製品コードに対して **677 passed, 1 warning**、検証器ゲート回帰追加後680件、終了要求の回帰追加後 **681 passed, 1 warning**。既存Starlette非推奨警告1件のみ。依存関係、syntax、差分検査も確認。XMLは `artifacts/auth-compat-20261022/full-resume.xml`、`full-gated.xml`、`full-final.xml`。
- `check_live_bookmark_browser.py --identity-format next|meta` は使い捨ての架空Cookieと合成wire transportを実 `LivePixivReadClient` へ注入する。両方式で実Edgeの1280×720/1920×1080、71作品、4経路、画像画素、I/Tab、選択/検索/条件、取得失敗/復旧、2clientと旧画像snapshot保持を受入。外部ブラウザ通信/ページエラーなし、通常終了/port解放、ソース不変。
- CIのChromium操作試験も、従来のread-interface代替から実reader＋合成Next.js transportへ変更する。実CookieはCIへ渡さない。独立レビューで検査の偽陽性を指摘し、指定形式との一致、実要求数/経路完走数、GET/host/Cookie安全性を必須ゲートにした。旧fakeへの無言fallbackを回帰試験で拒否する。レポートの `actual_parser_used` は選択値なので、それ単独では実行証明にしない。合成wireにはmasked行も含め、UI一覧の正常71件を維持する。
- 凍結43入力からのonedir/noconsoleを別フォルダーへコピーし1050ファイル一致。exe SHA-256 `c4eb0659b7eb74ca5d1ae22f4b201035cea7fcde327eb52657dbf27418e35988`。`native-accepted/native.json` では合成旧SQLite/保存PNG/永続ブックマーク/タグ、実GDI復帰、通常8プロセス終了、port解放が成功。DPI168、安定2/2標本、平均画素差0.0000106705（上限1）、errors/forced_cleanup空。これは合成DB経路であり実Cookie配布exeの受入ではない。

## 実Cookie・実API・実Edge

認証2GET成功後、修正版で4経路完走と実画像1枚のメモリ上decodeを確認した。続く専用実Edge probeの `real-ui-03/result.json` は、認証・全経路cache・検索・選択・サムネイル・**2ページの画像decode**・I/Tab・元カードfocus・タブ往復状態まで成功した。

この実UI試験はGET計102（うち画像52）、実アカウント書込み0、画像Cookie不送信、Cookie原本前後一致、通常終了、port解放、強制cleanupなし、source不変。実作品のID/本文/画像/スクリーンショット/traceは証跡へ保存していない。前2試行は検索/選択段階とviewer段階でAssertionErrorが発生し、初回は強制cleanupした。診断を細分化した再実行が成功したが、**今回の再開時にviewer製品コードを追加修正したわけではなく、前試行の全原因を特定済みとは主張しない**。失敗記録は保持する。

## 残る受入

合成masked E2Eの追加走では全操作の後に15秒の終了待ちが失敗し、強制cleanupした記録もある。診断付き再走は正常終了したが、Windows Proactorの接続resetとの因果は未確定。検証器を全ページ・ブラウザ・Playwright終了後に短命なloopback POSTを送る順序へ変え、宛先完全一致・redirect禁止・ACK確認・15秒期限を維持した。`edge-next-final/result.json` と `edge-meta-final/result.json` は診断機能なしで正常終了、強制cleanupなし、ソース不変。過去失敗を削除せず、間欠症状が一般に根治したとは主張しない。

実Cookieを使うコピーexeのUI操作、任意作品/小説本文/うごイラ、全DPI/別PC、長時間、大規模データ、残る絞込/類似/おすすめ、DL/queue/failed等は別の未完マイルストーン。PR headとマージ後mainのCIはこの記録とは別に確定する。単体数や限定受入を段階4全体・移植完了へ換算しない。
