"""Check and refresh the two full-test prompt appendices without importing PV.

Use python -B tools/check_full_prompt_coverage.py [--refresh].
Use --inventory to print each original inventory entry's resolution.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
from pathlib import Path
import re
import sys

from check_inventory_coverage import load, load_rows, resolve, split_targets


ROOT = Path(__file__).resolve().parents[1]
PORT = ROOT / "src/pixivvault_nicegui"
PROMPTS = (
    ROOT / "docs/cos/COS_FULL_USECASE_PROMPT_20261124.md",
    ROOT / "docs/cos/UIUX_FULL_TEST_PROMPT_20261124.md",
)
KINDS = {"handler", "setting_key", "shortcut", "menu_item", "background", "snack", "route"}
EXCLUDED = {
    "FL-X01": "本人裁定で iOS 連携を除外。専用拡張の受付とキューは別項目で照合。",
    "FL-X02": "本人裁定で LAN Web 連携を除外。ヘッダーからの外部 Pixiv リンクは別項目で照合。",
    "FL-X03": "本人裁定で Shaft 専用タブを除外。借用プレミアムの現行入口は別項目で照合。",
    "FL-X04": "本人裁定で旧保管庫取込を除外。既存データの読取は D9 の証拠を照合。",
}

# FL row selectors | functional items | UI items | evidence required beyond safe viewing
COVERAGE = """
A01 A02 A03 A13 | F-ST-01 F-ST-04 | U-S00 U-S42 U-R18 | 隔離起動と本人の窓・異常記録
A04-A07 C18 V02-V04 | F-ST-02 F-OS-01 F-HI-01 | U-S00 U-S11 U-S23 U-N01 U-N02 U-R18 U-R22 | 本人のトレイ・ネイティブ窓・再起動
A08-A11 B03 C01 C02 Z03 Z04 Z15 Z16 | F-ST-05 F-SE-03 F-OS-02 | U-S00 U-S15 U-S22 U-R12 U-R13 | 認証・同期は本人または合成、権限境界を照合
A12 C21 Z14 | F-LE-04 F-SE-01 | U-S15 U-S33 U-S39 | 偽ワーカー・本人の GPU
A14 C04 C26 N01-N04 N06 N07 Y17 Y18 | F-OS-02 F-SE-01 | U-S16 U-S23 U-R19 U-R20 U-R21 | 隔離 Windows・専用拡張・偽受信の証拠
B01 B02 B11 B12 V01 | F-ST-01 F-ST-02 F-ST-03 F-SC-01 | U-S00 U-S14 U-N01 U-N03 U-R22 | 初回と両テーマ、統合ヘッダー
B04 B05 B06 G01 I17 | F-HI-01 F-HI-02 | U-S11 U-S12 U-S13 U-S40 | 履歴変更と OS の保存先は隔離・本人
B07 C24 | F-SE-01 F-SE-02 | U-S15 U-S16 U-S17 | 隔離の保存・競合・再起動
B08 B09 N05 C10 Y10 | F-NO-01 F-OS-03 F-LE-02 | U-S18 U-S24 U-S38 U-N04 | 固定通知・合成の失敗・本人の通知センター
B10 C07 O04 Y16 | F-BO-01 F-BO-02 F-AU-02 | U-S17 U-S22 | 解錠・枠は本人、偽時刻
C03 C22 Z07 Z13 V06 | F-SE-03 F-HI-02 F-SC-01 F-SC-02 | U-S13 U-S16 U-S21 U-S42 U-R14 U-R22 | 隔離の旧 DB 読取とバイト照合、本人の選択・エクスポート
C05 C06 O01-O03 O06 | F-AU-01 F-AU-02 | U-S18 U-R16 | 偽時刻・隔離の受付履歴
C08 C09 C17 D15 Z02 Z12 | F-DL-06 F-VW-02 F-SE-02 | U-S15 U-S25 U-S36 U-S37 U-R09 U-R17 | 隔離の出力ファイル、ZIP／MP4／GIF
C11 C19 D11-D13 | F-DL-05 F-SE-02 | U-S01 U-S09 U-S15 U-R08 | 隔離の公開範囲・リンク・配置
C12 C13 Z05 Z10 | F-SE-02 F-SP-02 F-SC-03 | U-S16 U-S20 U-N04 | 偽 429・優先レーン・待機の証拠
C14-C16 | F-SE-01 F-SE-02 F-SE-03 | U-S17 U-R15 | 隔離の消去と再生成
C20 O05 V05 | F-AU-01 F-AU-03 F-SE-01 | U-S15 U-S19 U-R22 | 偽取得・進行・停止
C23 I18 | F-LE-03 F-SE-03 | U-S17 U-S29 U-S39 | 隔離 CSV・件数確認とリセット
C25 K12 | F-SE-01 F-LI-02 F-HI-01 | U-S06 U-S08 U-S09 U-S11 U-S41 | サイズ保存と保持は隔離
D00 D06 D07 D09 D10 Z01 Z11 | F-DL-03 F-DL-04 F-AU-02 | U-S01 U-S02 U-S25 | 隔離の受付・差分・命名・ロックと停止
D01-D05 | F-DL-01 F-DL-02 F-AU-02 | U-S01 U-S25 U-R16 | 書込みは隔離
D08 D14 Z08 | F-DL-03 F-DL-06 | U-S25 U-R09 | 作者別設定・フォローと媒体は隔離／本人
D16 D17 Z06 Z09 | F-DL-06 F-DL-05 F-LI-01 | U-S01 U-S03 U-S08 U-S38 | 隔離の品質・欠落判定・保存実物
E01-E07 | F-QU-01 F-QU-02 | U-S02 U-S24 U-S26 | 一括操作・削除は隔離
F01-F05 | F-FA-01 F-FA-02 | U-S03 U-S27 U-R10 | 例外、CSV、削除は隔離
H01-H07 | F-DE-01 F-DE-02 | U-S04 U-S28 U-R11 | 隔離のスキャン・復元・空にする、本人の保存先
I01-I07 I19 Y07 Y08 | F-LE-01 F-LE-02 | U-S05 U-S29 U-S38 U-R01 | キーと判定の書込・取消は隔離
I08-I10 | F-LE-01 F-LE-03 F-RE-03 F-VW-01 | U-S05 U-S29 U-S35 U-R04 | ビューア・送信・保存は隔離／本人
I11-I16 Y09 Y11 Y12 | F-LE-01 F-LE-03 | U-S05 U-S29 U-R01 | 補充・再試行の分岐は偽取得
J01 J13 | F-RK-01 | U-S06 U-R06 | 許可枠 N1。欠測は偽取得と画面で補う
J02-J07 Y01 | F-RK-02 | U-S06 U-S30 U-S41 U-R06 | 条件変更・自動ページ追加は偽取得
J08-J10 J14 | F-RK-03 F-VW-01 | U-S30 U-S35 U-R04 U-R05 | リンク・保存・非表示は隔離／本人
J11 J12 Y02-Y04 | F-PR-01 F-PR-02 | U-S07 U-S31 U-N02 | 許可枠 N2。タグ編集・追加取得・書込は隔離
K01-K05 K08 K09 K11 K13 K14 Y05 | F-LI-01 F-LI-02 F-VW-02 | U-S08 U-S32 U-S36 U-S37 U-N01 | 外部検索・容量と閲覧記録は隔離。OS は本人
K06 K07 Z17 | F-BM-01 F-BM-02 F-LI-02 | U-S09 U-S32 U-R07 U-R08 | 自動更新・再読込・実書込は偽取得
K10 Y06 | F-LI-02 F-LE-04 | U-S33 U-R21 | 偽推論と本人の GPU／実在庫
L01-L04 L09 Y13-Y15 | F-RE-01 F-RE-02 | U-S10 U-S41 | 許可枠 N3。棚設定・追加取得は隔離
L05-L08 L10 | F-RE-02 F-RE-03 | U-S34 U-R02 U-R03 U-R04 U-R05 | 隔離の非表示・復帰・送信／受付
M01-M04 | F-VW-01 F-RK-03 F-RE-03 | U-S35 U-R04 U-R05 | 外部遷移・記録・送信は本人／隔離
M05-M07 | F-VW-02 | U-S35 U-S37 | 本人の物理マウス・再生と隔離ファイル
X01-X04 | F-EX-01 | U-R23 | 本人裁定の対象外
""".strip()

# Modules producing display surfaces. Several pages share the same builders.
UI_MODULES = """
U-S00 shell bootstrap native_startup startup_tab
U-S01 unified_download_view managed_download_view
U-S02 U-S26 job_views
U-S03 U-S27 failed_records_view
U-S04 U-S28 duplicate_view
U-S05 U-S29 learning_view learning_toast
U-S06 U-S30 ranking_view ranking_card_menus
U-S07 U-S31 personal_ranking_view
U-S08 U-S32 library_view media_views library_filter_panel
U-S09 U-S32 bookmark_view
U-S10 U-S34 recommend_view author_browse_view
U-S11 viewer_backup_view
U-S12 U-S40 view_history_view
U-S13 U-S42 saved_folder_button native_profile_view
U-S15 U-S16 U-S17 U-S23 U-S39 preferences_view
U-S15 following_sync_view
U-S18 automation_dialog scheduler_view
U-S19 metadata_view
U-S20 stop_recovery_view shared_get_recovery_view request_status_view
U-S21 stop_migration_view
U-S22 external_vault_view premium_gate_view premium_recovery_view
U-S23 desktop_integration extension_application extension_bridge extension_cookie_store
U-S24 shared_log_view
U-S25 download_status_details unfollowed_authors_view author_media_view
U-S29 U-S39 learning_reset
U-S33 similar_view
U-S35 U-S41 artwork_viewer artwork_page viewer_controller recommendation_viewer
U-S36 novel_save_view
U-S37 ugoira_view
U-S38 action_controls image_failure management_dialog
U-S41 gallery card_size_control card_context_menu
""".strip()

AUDIT_SCREEN_GROUPS = """
U-S01 download
U-S02 queue
U-S03 failed
U-S04 dedup
U-S05 learning
U-S06 ranking
U-S07 personal
U-S08 library
U-S09 bookmarks
U-S10 recommend
U-S11 history
U-S12 view-history
U-S13 startup-and-storage
U-S15 settings
U-S18 scheduler
U-S19 metadata metadata-ledger
U-S20 stop-recovery stop-uncertain communication-result request-wait request-limit
U-S21 startup-stop-migration stop-history-guide stop-migration-dialog
U-S22 borrow-on premium-recovery
U-S23 extension extension-confirm
U-S24 shared-log shared-log-collapsed
U-S25 download-details
U-S26 queue-delete-confirm
U-S27 failed-delete-confirm
U-S28 dedup-trash
U-S29 learning-settings learning-review learning-reset
U-S30 ranking-date
U-S31 personal-filters
U-S35 viewer
U-S38 toast-success toast-error
U-S40 view-history-delete
U-S42 startup-reference startup-cookie-rejected startup-lock-busy startup-legacy-uncertain startup-legacy-copy-error
""".strip()

OLD45_GROUPS = """
D01 | F-LE-01 F-LE-02 | U-R01
R01a R07 | F-RE-03 | U-S34
R01b | F-RE-01 | U-S10
R08 R10 | F-RE-03 | U-R02 U-R03
R16 R17 | F-RE-03 F-VW-01 | U-R04 U-R05
K01 K02 K04 | F-RK-01 F-RK-02 F-PR-01 | U-R06
K09 | F-PR-02 | U-S31
L14 L16 | F-BM-01 F-BM-02 | U-R07 U-R08
L17 | F-HI-02 F-LI-01 | U-S08
U10 U11 | F-DL-03 F-DL-06 | U-R09
U16 | F-DL-03 F-DL-04 F-RK-03 F-RE-03 | U-S25 U-S30 U-S34
Q13 Q17 | F-FA-01 F-FA-02 | U-R10 U-S27
X01 X09 | F-DE-01 F-DE-02 | U-R11 U-S28
S01 S03 S04 | F-SE-03 | U-R12 U-R13 U-R14
S09 S15 | F-OS-01 F-OS-03 | U-S23 U-S38
S13 | F-SP-02 | U-S20
S20 | F-SE-03 | U-R15
S22 | F-OS-02 | U-S23
E01 | F-SE-03 F-HI-02 | U-S16
V06 V08 | F-VW-01 F-VW-02 | U-S35
W01 | F-LI-02 F-LE-04 | U-R21
T02 | F-AU-02 | U-R16
M01 M10 | F-DL-06 F-VW-02 | U-R17 U-S37
N01 N03 N04 | F-OS-01 | U-R18 U-S23
N06 N07 N09 N10 | F-OS-02 | U-R19 U-R20 U-R21
Z04 | F-BO-01 F-PR-01 F-SP-02 | U-S07 U-S20 U-S22
""".strip()

ORIGINAL_KEY_GROUPS = """
F-SE-03/U-S13|save_path
F-ST-05/U-S22|my_user_id last_sync_date
F-DL-03/F-DL-05/U-S25|auto_archive_new_users enable_bookmark_hardlink
F-BO-02/U-S22|nana7mi_pw_hash nana7mi_pw_salt nana7mi_shaft_last_refresh_at
F-LE-03/U-S29|discovery_search_mode discovery_ratio_style discovery_ratio_subject
F-LE-01/F-LE-02/U-S05|discovery_following_cursor discovery_related_cursor discovery_search_cursor discovery_population_cursor discovery_generic_tags_extra
F-EX-01/U-R22|web_bridge_enabled web_api_token
F-AU-02/U-S18|auto_check_interval_hours check_on_startup
F-OS-02/U-S23|ext_autolaunch_confirm_shown
F-BM-01/F-BM-02/U-S09|bookmark_cache_fetched_at
F-PR-02/U-S31|ranking_personal_tags ranking_personal_filters ranking_personal_candidates_open exclude_hidden_authors exclude_hidden_works
F-RE-02/U-S10|recommend_section_order recommend_section_collapsed recommend_certain_group_collapsed recommend_trend_added_tags
F-SE-01/U-S15|{const:key}
""".strip()

BEGIN = "<!-- coverage:start -->"
END = "<!-- coverage:end -->"
ITEM_ID = re.compile(r"\b(?:F-[A-Z]{2}-\d{2}|U-[A-Z]\d{2})\b")
SURFACE_CALLS = {
    "managed_dialog", "ui.dialog", "ui.menu", "ui.tooltip", "ui.notify", "ui.notification",
    "notify_action", "ui.spinner", "ui.select", "outlined_select", "settings_group", "ui.expansion",
}
EXTRA = ("bootstrap", "native_startup", "startup_tab", "desktop_integration")


def expand(selectors):
    for selector in selectors.split():
        first, _, last = selector.partition("-")
        if last:
            if first[0] != last[0]:
                raise ValueError(f"Invalid FL range: {selector}")
            for index in range(int(first[1:]), int(last[1:]) + 1):
                yield f"FL-{first[0]}{index:02d}"
        else:
            yield f"FL-{first}"


def coverage_rows(valid_ids):
    rows = {}
    for line in COVERAGE.splitlines():
        selectors, functional, visual, evidence = map(str.strip, line.split("|"))
        for identifier in expand(selectors):
            if identifier in rows:
                raise ValueError(f"Duplicate FL row: {identifier}")
            rows[identifier] = (functional, visual, evidence)
    missing, extra = valid_ids - rows.keys(), rows.keys() - valid_ids
    if missing or extra:
        raise ValueError(f"FL coverage mismatch: missing={sorted(missing)} extra={sorted(extra)}")
    return rows


def parse_source(path):
    return ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))


def assignment(tree, name):
    return next(node.value for node in tree.body if isinstance(node, ast.Assign)
                and any(isinstance(lhs, ast.Name) and lhs.id == name for lhs in node.targets))


def node_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{node_name(node.value)}.{node.attr}"
    return ""


def surface_call(node):
    if not isinstance(node, ast.Call):
        return ""
    name = node_name(node.func)
    if name in SURFACE_CALLS:
        return name
    if isinstance(node.func, ast.Attribute) and node.func.attr == "props":
        if any(isinstance(arg, ast.Constant) and isinstance(arg.value, str)
               and ("popover=" in arg.value or "role=dialog" in arg.value) for arg in node.args):
            return "props"
    return ""


def source_tables(original_keys, section_keys):
    preferences = parse_source(PORT / "preferences.py")
    view = parse_source(PORT / "ui/preferences_view.py")
    titles = ast.literal_eval(assignment(view, "PREFERENCE_GROUP_TITLES"))
    specs = {}
    settings = ["", "### 移植版の設定キー", "",
                "| グループ／入口 | キー | ラベル | 既定・選択肢 |",
                "| --- | --- | --- | --- |"]
    for call in assignment(preferences, "SPECS").elts:
        key, label, default, choices, group = [ast.literal_eval(arg) for arg in call.args]
        location = titles.get(key, titles.get(group, group))
        if group == "ui_internal":
            location, label = "初回案内（内部）", "案内の表示済み状態"
        elif group == "local_tagger_internal":
            location = "ローカルタグ補完モデル"
            label = {"local_tagger_path": "モデルフォルダ", "local_tagger_enabled": "タグ補完を自動で使う",
                     "local_tagger_receipt": "検証済みの内部記録"}[key]
        elif key == "grid_card_size_mode":
            location = "カードのサイズ操作"
        specs[key] = location
        default_display = "環境依存・伏せる" if key == "local_tagger_path" else (default or "空")
        options = "／".join(choices)
        settings.append(f"| {location} | `{key}` | {label} | {default_display}"
                        f"{' · ' + options if options else ''} |")

    aliases = {"_size_mb": "discovery_cache_size_mb", "_size_count": "discovery_cache_size_count",
               "_collapsed": "recommend_section_collapsed"}
    ledger_keys = {aliases.get(key, key) for key in section_keys}
    extra_keys = ledger_keys - original_keys
    original = ["", f"### 元の設定キー（inventory {len(original_keys)} 項目、台帳 T 節で追加 {len(extra_keys)} 項目）", "",
                "| キー | 所在・扱い |", "| --- | --- |"]
    locations = {}
    for line in ORIGINAL_KEY_GROUPS.splitlines():
        target, keys = line.split("|")
        for key in keys.split():
            locations[key] = target
    for key in sorted(original_keys | ledger_keys):
        if key in specs:
            location = specs[key] + "（上表と照合）"
        elif key == "{const:key}":
            location = locations[key] + "。定数参照の抽出行。出典の設定グループを照合する。"
        elif key.startswith("web_"):
            location = locations[key] + "。本人裁定の対象外、値の確認なし。"
        elif key == "auto_check_interval_hours":
            location = locations[key] + "。自動処理の定期チェックで 0／6／12／24／48 時間。"
        elif key == "check_on_startup":
            location = locations[key] + "。自動処理の定期チェックで起動時にもチェック。"
        else:
            location = locations.get(key)
            if not location:
                raise ValueError(f"Original settings key without a route: {key}")
            location += "。専用画面または内部／旧データの照合。"
        original.append(f"| `{key}` | {location} |")
    return settings + original


def ui_source_index(visual_ids):
    module_targets = {}
    for row in UI_MODULES.splitlines():
        fields = row.split()
        item_ids = [field for field in fields if ITEM_ID.fullmatch(field)]
        if not item_ids or set(item_ids) - visual_ids:
            raise ValueError(f"Invalid UI source route: {row}")
        for module in fields[len(item_ids):]:
            module_targets[module] = "・".join(item_ids)

    shell = parse_source(PORT / "ui/shell.py")
    nav = [ast.literal_eval(node.elts[0]) for node in assignment(shell, "NAVIGATION").elts]
    expected = ["download", "queue", "failed", "dedup", "learning", "ranking", "personal", "library",
                "bookmarks", "recommend"]
    if nav != expected or {f"U-S{i:02}" for i in range(1, len(nav) + 1)} - visual_ids:
        raise ValueError(f"Navigation changed: {nav}")

    paths = [*sorted((PORT / "ui").glob("*.py")), *(PORT / f"{name}.py" for name in EXTRA),
             *sorted(PORT.glob("extension_*.py"))]
    surface_rows = []
    for path in paths:
        calls = [(node.lineno, name) for node in ast.walk(parse_source(path)) if (name := surface_call(node))]
        target = module_targets.get(path.stem)
        if not calls and not target:
            continue
        if not target:
            raise ValueError(f"UI surface without a route: {path.relative_to(PORT)}")
        locations = "、".join(f"{number} {call}" for number, call in sorted(calls)) or "画面の入口・専用部品"
        surface_rows.append(f"| `{path.relative_to(PORT).as_posix()}` | {target} | {locations} |")
    lead = ["", "### 移植版の画面実装索引", "",
            "主ナビ（shell.py の NAVIGATION）: " + " → ".join(f"`{key}`" for key in nav) + "。",
            "各呼出しのホスト・確認欄を上の行列で個別に採る。ヘルパーの通知は発生元の画面でも採る。",
            "JS の開閉・ページ送り・ポップオーバー、専用部品内の空・エラーは U-S29〜41 と U-N02 を参照。",
            "", "| 実装（src/pixivvault_nicegui 内） | 画面項目 | ダイアログ／メニュー／通知／選択等の行 |",
            "| --- | --- | --- |"]
    existing = parse_source(ROOT / "tools/uiux_audit.py")
    p5 = parse_source(ROOT / "tools/p5_design_evidence.py")
    audit_screens = {ast.literal_eval(key) for key in assignment(existing, "SCREENS").keys if key is not None}
    audit_screens.update(ast.literal_eval(assignment(p5, "SCREENS")))
    screen_routes = [line.split() for line in AUDIT_SCREEN_GROUPS.splitlines()]
    defined_screens = [screen for _, *screens in screen_routes for screen in screens]
    if Counter(defined_screens) != Counter(audit_screens) or {route[0] for route in screen_routes} - visual_ids:
        raise ValueError(f"UI audit screen routes differ: {audit_screens.symmetric_difference(defined_screens)}")
    audit_table = ["", f"### 既存の監査・撮影入口（{len(audit_screens)} 画面）", "",
                   "既存の監査定義と撮影定義からキーを取得。今回の状態行列には上の実装索引の下位画面も含める。",
                   "", "| 試験項目 | 既存の入口 |", "| --- | --- |"]
    audit_table.extend(f"| {identifier} | " + "、".join(f"`{screen}`" for screen in screens) + " |"
                       for identifier, *screens in screen_routes)
    return lead + surface_rows + audit_table


def original_index(inventory, ledger_map, rows, details):
    counts = defaultdict(Counter)
    targets = defaultdict(set)
    originals = set()
    not_applicable = []
    for entry in inventory["items"]:
        kind, screen = entry["kind"], entry.get("screen") or "(画面分類なし)"
        if kind not in KINDS:
            continue
        group = (kind, screen)
        raw, method = resolve(entry, ledger_map)
        links = split_targets(raw or "")
        if set(links) - rows.keys():
            raise ValueError(f"Inventory links outside FL coverage: {entry['id']} {links}")
        if not raw:
            bucket = "unmapped"
        elif raw.startswith("n/a:"):
            bucket = "na"
            not_applicable.append(f"| `{entry['file']}:{entry['line']}` | {raw.removeprefix('n/a:')} |")
        else:
            bucket = "ledger"
            targets[group].update(identifier[:4] for identifier in links)
        counts[group][bucket] += 1
        if kind == "setting_key":
            originals.add(entry["name"])
        if details:
            print(f"{kind} {entry['file']}:{entry['line']} {entry['name']} → {raw or '未対応'} ({method})")

    if any(count["unmapped"] for count in counts.values()):
        raise ValueError("Original inventory has unmapped entries. Inspect --inventory.")
    totals = sum((counter for counter in counts.values()), Counter())
    table = ["", "### 元 inventory の照合", "",
             f"対象 {totals.total()} 件。台帳へ対応 {totals['ledger']} 件、元の一覧で理由付き非該当 "
             f"{totals['na']} 件、未対応 {totals['unmapped']} 件。",
             "元 inventory の行ごとに既存の ledger_map の割当を読む。個別根拠は検査スクリプトの --inventory で確認できる。",
             "この集計には既存の単位規則と弱い対応も含む。対応の存在は、到達や製品合格の証拠とは区別する。",
             "", "| 種別 | 元の画面／処理 | 件数 | FL グループ | 元の非該当 |",
             "| --- | --- | ---: | --- | ---: |"]
    for (kind, screen), counter in sorted(counts.items()):
        prefixes = "・".join(sorted(targets[(kind, screen)])) or "—"
        table.append(f"| {kind} | {screen} | {counter.total()} | {prefixes} | {counter['na']} |")
    table += ["", "| 元の非該当・出典 | 理由 |", "| --- | --- |", *not_applicable]
    print(f"Inventory: {totals.total()} entries; FL={totals['ledger']}, n/a={totals['na']}, missing=0")
    return table, originals


def old45_table(defined):
    source = (ROOT / "docs/OLD45_STATUS_2026-11-19.md").read_text(encoding="utf-8")
    status, _, _ = source.partition("## P5")
    ids = re.findall(r"^\| ([A-Z]\d{2}[a-z]?) \|", status, flags=re.M)
    table = ["", "### OLD45 の再判定表にある 45 項目", "",
             "判定の分母は 45。元の CSV が必要な場合は監督が回収し、手順・期待結果を照合する。",
             "", "| OLD45 項目 | 機能 | UI/UX |", "| --- | --- | --- |"]
    covered = []
    for line in OLD45_GROUPS.splitlines():
        original, functional, visual = (part.split() for part in line.split("|"))
        if set(functional) - defined[0] or set(visual) - defined[1]:
            raise ValueError(f"OLD45 route has undefined prompt items: {line}")
        covered.extend(original)
        table.append(f"| {'・'.join(original)} | {'・'.join(functional)} | {'・'.join(visual)} |")
    if len(ids) != 45 or Counter(ids) != Counter(covered):
        raise ValueError("OLD45 routes must match all 45 items of the status table")
    return table


def test_ids(prompt):
    source = prompt.split(BEGIN, 1)[0]
    rows = re.findall(r"^\| (F-[A-Z]{2}-\d{2}|U-[A-Z]\d{2})[ （|]", source, flags=re.M)
    headings = re.findall(r"^(?:- \*\*|\*\*)(U-[A-Z]\d{2})(?:\*\*| [^*]+\*\*)[。．]", source, flags=re.M)
    ids = rows + headings
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate test ID definitions")
    return set(ids)


def appendices(inventory, ledger_map, rows, section_keys, prompt_sources, details):
    original_table, original_keys = original_index(inventory, ledger_map, rows, details)
    settings = source_tables(original_keys, section_keys)
    print(f"Settings: {len(original_keys)} original keys; "
          f"{len(assignment(parse_source(PORT / 'preferences.py'), 'SPECS').elts)} port specs")
    exclusions = ["", "### 本人裁定の4件", "",
                  "| FL-ID | 理由と照合先 |", "| --- | --- |"]
    exclusions.extend(f"| {identifier} | {reason} |" for identifier, reason in EXCLUDED.items())
    defined = [test_ids(prompt) for prompt in prompt_sources]
    audit_checks = {f"U-{kind}{number:02}" for kind, last in (("A", 17), ("B", 9))
                    for number in range(1, last + 1)}
    if audit_checks - defined[1]:
        raise ValueError(f"Missing UI/UX audit items: {sorted(audit_checks - defined[1])}")
    old45 = old45_table(defined)
    appendices = []
    for index, ids in enumerate(defined):
        ledger = [
            "台帳本体・追補の一意 FL-ID を行ごとに照合する。",
            f"**{len(rows)}/{len(rows)} 対応、未対応 0、本人裁定による除外 {len(EXCLUDED)} 件。**",
            "隔離・本人と記した行は CoS の実機操作または UI の目視だけで確定できない。下表の代替証拠と未確認を報告に残す。",
            "", "| FL-ID | 試験項目 | 観察範囲・補う証拠 |", "| --- | --- | --- |",
        ]
        for identifier, targets in sorted(rows.items()):
            references = targets[index].split()
            if set(references) - ids:
                raise ValueError(f"{PROMPTS[index].name}: {identifier} refers to undefined items {references}")
            ledger.append(f"| {identifier} | {'・'.join(references)} | {targets[2]} |")
        table = ledger + exclusions + old45 + original_table + settings
        if index == 1:
            table += ui_source_index(ids)
        print(f"{PROMPTS[index].name}: {len(rows)} FL IDs, {len(EXCLUDED)} ruled out, "
              f"{len(ids)} defined test IDs")
        appendices.append("\n".join(table))
    return appendices


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="rewrite only the generated Markdown appendices")
    parser.add_argument("--inventory", action="store_true", help="list individual original inventory resolutions")
    args = parser.parse_args()
    inventory, ledger_map, fl_ids = load()
    source_rows, section_keys = load_rows()
    if set(source_rows) != fl_ids:
        raise ValueError("Original FL-ID indexes disagree")
    rows = coverage_rows(fl_ids)
    original = [path.read_text(encoding="utf-8") for path in PROMPTS]
    tables = appendices(inventory, ledger_map, rows, section_keys, original, args.inventory)
    stale = []
    for path, prompt, table in zip(PROMPTS, original, tables, strict=True):
        if prompt.count(BEGIN) != 1 or prompt.count(END) != 1:
            raise ValueError(f"Invalid appendix markers: {path}")
        before, old = prompt.split(BEGIN, 1)
        _, after = old.split(END, 1)
        updated = f"{before}{BEGIN}\n{table}\n{END}{after}"
        if updated != prompt:
            if args.refresh:
                path.write_text(updated, encoding="utf-8", newline="\n")
            else:
                stale.append(path.name)
    if stale:
        print("Stale appendix: " + ", ".join(stale), file=sys.stderr)
    return int(bool(stale))


if __name__ == "__main__":
    raise SystemExit(main())
