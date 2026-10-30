# P3 UI state fix / CoS handoff

Base p3-gui-fix157/157 verified before copy. HEAD2cf4a7a7286dd728e8df8125094498a3622babba. SHA0c8f2151c9f5655c68a9a9be2077c4a3641299f1daa6f8edd80ba23126222363. Only manifest product files copied, no CoS artifacts. Base processes/ports untouched.

## Evidence and limits

CoS result18BEFC909E7517DC72881A4D24A2C331E9431F35CFAAD343338E7A1FD9EC6DBB was verified. Both failure PNGs were viewed. 1280 failed trend not added, recommendation revision1 and added[]; screenshot showed LANDSCAPE still unregistered. 1920 passed that operation and page2, later failed preferences not saved: preferences revision1, novel=both, animation=folder but shared cache=1024; screenshot likewise showed1024. Thus there is no evidence of a total preferences write failure or common SQLite outage. Neither Unicode label mismatch nor filesystem exception was observed here.

Recommendation product defect found by static control-flow inspection: update() publishes persistent revision/groups before _change finishes rendering. During that interval the UI exposes enabled trend controls while _change rejects calls when busy. Existing checker waited only for groups and clicked before waiting for data-busy false. The previous fix placed idle waiting after the failing trend addition, too late. This is a supported race mechanism; browser event ordering itself was not recorded. Product now disables current shelf buttons and both refresh/update controls throughout refresh/mutation, restores original capability restrictions, and applies the same lock to newly rendered controls. Busy covers initial refresh too. Closed views do not emit updates. It does not queue new actions or change persistence/CAS semantics.

Preferences is distinct: the global role=option nth(index) selection and immediate next action did not establish which visible menu choice was selected or whether the new value reached the server. Two choices persisted, third remained unchanged, so the entire-save hypothesis is contradicted. Checker now scopes the visible listbox, selects exact labels, and waits for each server-side data-value acknowledgement before saving. Product acknowledgement describes the existing draft, not saved state; save still performs the existing atomic PreferencesStore CAS update. The fixture snapshot now obtains revision and values from one PreferencesSnapshot rather than two reads. Exact cause of the old third selection not changing remains unproven without its event trace.

## Minimal changes

- RecommendView busy/capability lifecycle: disable during operation, restore after render or failure without enabling unsupported actions.
- PreferencesView data-value acknowledgement on each existing select value event. No save behavior, option order, defaults or original label changed.
- Checker waits idle before trend registration, visible listbox/exact values, acknowledged drafts recorded in preference-draft.json, then original persisted values/cache apply/receipt preservation checks.
- Page2 acquisition still requires local expansion32 cards, exactly one new search call ['search','landscape',2,'all'] and ordered accumulated IDs1020..1059. No timeout increase, fixed sleep, alternate click, swallowed exception or relaxed expected result.
- test_p3_ui_state.py covers blocked operation, success/failure capability restoration, newly rendered controls, and draft acknowledgement versus atomic save/reopen. Existing regression files retained.

## CoS verification (not executed by Codex)

Verify final manifest and source preservation first. Use existing S venv, PYTHONPATH=<candidate>/src, PYTHONDONTWRITEBYTECODE=1, fresh S-only TEMP/TMP/artifact output. Do not use/stop8089 or any other owner. Run pytest tests/test_p3_ui_state.py tests/test_p3_gui_fix.py tests/test_preferences.py tests/test_recommendation_acquisition.py tests/test_p3_residuals_fixtures.py with -p no:cacheprovider, fresh --basetemp, --junitxml. Record skips explicitly; former34PASS/4SKIP and fixture passes belong to the prior snapshot only.

Run tools/check_p3_residuals_browser.py --channel msedge --output <candidate>/artifacts/<new-owned-run> under CoS authority. Keep both1280x720 and1920x1080 initial AND restart. Require original twenty UI judgments, review save, trend add, local expansion, exact page2 GET/ID order, author boost, all three selected settings persisted together, cache512MiB and clear without receipt loss, author3media receipts, BOTH novel reader, original frames playback, stored-state restart and zero external requests. Keep preference-draft.json alongside failure snapshots to distinguish a selection/draft failure from save/CAS/apply failure. If failure recurs, capture event/busy/selected value transitions before another fix; no blind rerun.

This candidate has NOT been tested, syntax-checked, linted or launched by Codex. No GUI, API, Cookie, model, GPU, OS/auth change, install, publish or main integration. No old refusal replay. Long-path implementation was inherited unchanged; prior fixture success alone is not proof of its original root cause. Current work introduces no storage-contract decision. Independent audit/native/live/CI/total UX and P4 decision remain pending dot/CoS gates.
