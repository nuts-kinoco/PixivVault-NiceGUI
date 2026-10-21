# 移植進捗

更新: 2026-10-22 01:41 JST。今回の作業単位: `bootstrap/phase0-shell`。

## 状態

段階0の基盤準備と段階1のデモ初稿を実装し、ローカルの自動試験を通過。段階0/1の製品受入は未完。
作成済みrepoをcloneし、専用ブランチ・専用venvを準備した。
元の非公開ソース・DB・画像・認証情報は転送していない。元アプリは変更していない。

## 担当

- worker-1 / medium: 純粋な画面状態、架空fixture、状態の単体試験。
- worker-2 / medium: デモシェル、テーマ、テーマ試験、起動境界の独立読取レビュー。
- prime: 契約、起動入口、環境、CI、統合確認、記録、Git。

## 検証

環境: Windows / Python 3.13.11 / NiceGUI 3.18.0 / pytest 8.4.2。新repoの専用venvで実行。

| コマンド | 実測結果 |
|---|---|
| `python -m pytest -q --junitxml=artifacts/pytest.xml` | 27 passed、2.49秒、exit 0 |
| `python -m pip check` | No broken requirements found、exit 0 |
| `python -m compileall -q src tests` | exit 0 |
| `python -m pixivvault_nicegui --check` | demo / synthetic / 127.0.0.1 / native false、exit 0 |
| `git diff --check` | exit 0 |

27件の内訳は状態/fixture/起動境界/テーマの24件と、サーバー側ページ構造2件・HTTPスモーク1件。
HTTPスモークはブラウザを起動せず、専用子プロセスのloopbackサーバーへGETし、試験後はその子だけを停止した。
50カード・単一dialog・既存8タブの構成、別クライアントの状態分離と削除時の解除を確認した。
試験所要時間2.49秒はアプリの応答速度ではない。

初回レビューで修正した箇所:
- 同じ作品への連続要求A/Bで古いAが先着した際の誤採用。要求ごとに世代を更新し、発行済みtokenを一度だけ受理する。
- multiprocessingの子読込時に起動入口が再実行される経路。`__main__`だけを入口にし、回帰試験を追加した。
- NiceGUIの`window_size`指定がnativeを暗黙に有効化する経路。通常モードではNoneにし、窓を開かない設定を検証した。
- タブ構成、検索200ms待機、前後端の無効表示、装飾を現行方針へ合わせた。

Windowsの目視・クリック・DPI・終了・性能比較は未実施。
pywebviewは未導入。GitHub Actionsは定義のみで、リモート未pushのため未実行。
ローカルの試験結果をGitHub CIの成功とは扱わない。

## 確認待ち

1. 新repoが公開、元repoが非公開のため、新repo非公開化の承認を確認中。pushは保留。
2. 実画面操作の権限は未承認。単体試験/HTTP検査とは区別する。

## 次の受入

初回実装をローカルコミットとして保存する。対象はこのrepoの新規実装・試験・文書のみ。
公開範囲の確認後にpushしてCIを確認し、実画面操作の許可後にV01〜V08を検証する。
その後、同一実画像を使う比較fixtureと現行の測定条件を固定する。デモを作っただけで段階3の性能受入へ進まない。
