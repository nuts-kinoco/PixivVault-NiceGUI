# 機密検出の候補の確認（2026-11-20、GitHub push 後）
P6 の機密検出（verify/p6-20261119/secrets-final.json）: Git 履歴の候補 176 件（token 162・development-password 8・cookie 6）。値は出力していない。
監督（Claude）が各行を原本で確認（tmp/inspect_secrets.py・inspect2.py・inspect3.py）:
- development-password・cookie の 14 件: すべて試験用の合成値（generated-*, synthetic, *_dummy, SYNTHETIC_NOT_A_CREDENTIAL, example-only 等）。
- token 162 件: 試験の合成値（fixture-access, generated-99, secret-value 等）と、src/pixivvault_nicegui/external_public_profiles.py の pixiv 公式アプリの公開 client_id/client_secret（広く公開済みの定数。個人の秘密ではない）。
結論: 本人の Cookie・トークン・開発パスワードは含まれない。GitHub の pv-dev-baseline への push は維持。
