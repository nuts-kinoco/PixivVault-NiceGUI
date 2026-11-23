# r7 状態

- ui-integ の土台 c66ea3a7。対象は Dots R6-M01・R6-L01。M03・M04 は fix-notice 取り込み後。書き手は親。
- 全体試験候補 ba78f45b2b6b4f6a7c7dd7328fbb283226b07950。src b732d135667b1af7e5b21124bb1f336b9af2c786、tests e3ce2385c62b12d0c3feb9eeeb744676e0e5b1a8、tools e08680ce7e6e787c063dcb1cf3aa4e6063457bde。
- 前版の固定 Git blob で7件失敗、対照5件合格。ランキング実経路も失敗再現。対象16件、関連204件（0fb2a733）、最終対象19件（ba78f45b）が合格。
- 指定の全体1回は最終候補で4,766合格、0失敗、3スキップ、DPAPI 3選択外。P6 306箇所・指摘0。担当一時領域、起動プロセスの残存0。
- 次は候補と OUTCOME.md を独立再判定へ渡す。仕様 C、Native、支援技術による読み上げは残る。
