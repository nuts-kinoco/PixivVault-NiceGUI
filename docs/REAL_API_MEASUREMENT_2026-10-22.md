# 実API読取計測の到達点

2026-10-22、ユーザーの追加承認に基づき、既存Cookieを読取専用で扱う隔離probeを実行した。**homeのHTTP200応答までは取得したが、認証済みと確認できず停止した。ランキング・作品情報・画像取得の実測は未完である。** 保存された認証Cookieは記録上の期限を過ぎており、診断用に事前構築したrequestには含まれなかった。

認証値、Cookieの期限日時、user/work ID、応答本文、実画像は表示・保存・Git公開していない。元Cookieは各試行で元bytesの前後比較が一致した。元アプリ、DB、設定を起動・変更する経路は使っていない。

## 実行結果

| 試行 | 到達点 | TTFB | 全本文取得まで | 判定 |
|---|---|---:|---:|---|
| 初回 / 静的例外地点を付けた診断 | home用header構築でValueError、HTTP応答なし | — | — | 計測用parseの改行互換を修正 |
| 修正後 | home HTTP200 | 1231.299 ms | 1271.653 ms | 認証済み判定がfalseで停止 |
| 認証状態の形だけを調べる診断 | home HTTP200、最大1 GETで終了 | 181.504 ms | 206.430 ms | 認証済み判定がfalseで停止 |

TTFBは送信開始から最初のbody 1 byteまで、全本文取得は同じ開始点からbody読取完了までで、両者を加算しない。これらは各1試行で、同条件反復・p95・cold/warm分類を行った値ではない。合成viewerのFlet/NiceGUI比較や速度改善率へ混ぜない。

診断は固定のboolと上限付きcountだけを記録した。認証Cookieがsnapshot内に存在すること、期限内のものがないこと、事前prepared requestに認証Cookieがないことを確認した。home内の`meta-global-data`は0件で、現行predicateによる認証成立を確認できなかった。

事前prepared requestは`fetch()`で使うrequestとは別のオブジェクトであり、送信headerそのものを採取・公開したものではない。Cookieファイルの期限情報はサーバー側sessionの有効性を示す値でもない。今回の次の条件は、ローカルで更新したCookieを用意してから再度限定probeを実行することであり、期限を無視して送信したり、認証判定を緩める変更は加えていない。

## 計測用コードで修正した不具合

元bytesをメモリ上の`StringIO`へ渡すとき、既定の改行処理ではCRLFのCRがCookie値へ残った。通常のtext file読取を使う`MozillaCookieJar.load()`と同じ動作にするため、`StringIO(..., newline=None)`へ変更した。Cookie値の一律`strip()`や、元ファイルの書換えはしていない。

合成CookieでLF/CRLF/CRを比較する回帰を追加し、修正前にCRLFの値末尾CRとCR-onlyのparse拒否を再現した。修正後は12 offline検査が成功し、独立担当もRED/GREENとコードを照合した。認証診断の追加後は14件成功し、認証成功・未認証のどちらもhome 1件で終了すること、固定schemaが文字列・未知キー・不正countを拒否することを確認した。これらの14件はignored probe専用のunittestであり、公開アプリのpytest件数には足さない。

## 読取・出力の境界

専用子プロセスは原アプリをimportせず、Cookieの限定snapshotだけを使用する。送信先は厳密なHTTPS host/path検査を通し、GETに限定する。APIと画像のsessionを分け、画像用sessionへCookie/Authorizationを渡さない。受信Set-Cookieを原本へ保存せず、redirectと自動retryは行わない。

通常probeはhome→ranking→detail→pages→画像最大3件の固定上限で、各段階の検査失敗で停止する。認証診断はhome最大1件に限定する。今回ランキング以降は実行されていない。接続5秒/読取10秒、応答30秒/子全体95秒/親120秒、本文・画素数の上限を設けた。

親は子のstderrを公開せず、stdoutは固定schemaのbool/数値/例外型だけを受け入れる。静的例外地点は許可されたファイル名と行番号だけで、例外message・locals・フルパスを出さない。子環境から`SSLKEYLOGFILE`を除き、親環境は変更しない。これらの送信・出力制限と改行修正は実装担当とは別の担当が読取レビューし、blocking指摘なし。

コード・offline結果・sanitized reportはignoredの`artifacts/real-api-probe-20261022/`へ保存した。初回・診断・修正後・認証診断の結果を別ファイルとして残し、上書きしていない。今回の公開差分にはprobe本体、認証情報、実データを含めない。
