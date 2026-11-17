"""docs/ledger_map.json の rules を(再)生成する。手で決めた分類規則の一覧がこのファイルの本体。

規則は「元の file:行範囲 (+種別)」→ 台帳 FL-ID または "n/a:理由"。先に書いた規則が優先。
根拠: 元コードの該当範囲を読んだ(2026-11-17)。FL-Z.. は docs/FEATURE_LEDGER_ADDENDA_2026-11-17.md の行。
build_ledger_map.py は rules / manual を保持したまま auto だけ作り直す。
使い方: python -B tools/seed_ledger_rules.py   (ledger_map.json の rules を置き換える。manual は保持)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

MAP = Path(__file__).resolve().parent.parent / "docs" / "ledger_map.json"

NA_UNUSED = "n/a:呼び出し元なし（元リポ全 .py で名前が定義以外に現れない公開関数。静的参照数 0）"
NA_TABLE_SETTINGS = "n/a:設定キーの保存先テーブル（キーは setting_key 項目で個別に対応）"
NA_ACCESSOR = "n/a:設定値の読み書き口（キーごとの機能は setting_key 項目で対応）"
NA_CLASS = "n/a:クラス定義そのもの（個別メソッドの項目で対応）"
NA_MOCK = "n/a:開発用のモック候補データ（mock-* の固定データ。利用者の機能ではない）"
NA_TIMING = "n/a:性能計測ログの書式（診断。利用者の操作ではない）"
NA_HOVER = "n/a:見た目のホバー効果（機能ではない）"
NA_UIDISPATCH = "n/a:UI スレッドへの投入機構（内部実装。NiceGUI では別方式）"

# (file, 開始行, 終了行, 対応先, 種別の絞り込み(None=全種別))
R = []


def r(file, a, b, fl, kinds=None):
    R.append(dict(file=file, lines=[a, b], fl=fl, kinds=kinds))


# ---- core.py (ダウンロード本体) ----
r("core.py", 29, 31, "FL-Z01")
r("core.py", 33, 79, "FL-Z14")
r("core.py", 80, 88, "FL-Z11")
r("core.py", 89, 98, "FL-C10")
r("core.py", 99, 122, "FL-D16")
r("core.py", 123, 434, "FL-Z02")
r("core.py", 435, 466, "FL-Z03")
r("core.py", 467, 524, "FL-Z01")
r("core.py", 525, 720, "FL-C20")
r("core.py", 721, 856, "FL-Z09")
r("core.py", 857, 936, "FL-D16")
r("core.py", 1075, 1085, "FL-Z12")
r("core.py", 937, 1108, "FL-D15")
r("core.py", 1150, 1170, "FL-D09")
r("core.py", 1185, 1195, "FL-Z08")
r("core.py", 1255, 1300, "FL-Z06")
r("core.py", 1329, 1340, "FL-Z09")
r("core.py", 1109, 1813, "FL-D00")
r("core.py", 1845, 1855, "FL-D09")
r("core.py", 1814, 2181, "FL-D07")
r("core.py", 2182, 2279, "FL-D00")
r("core.py", 2280, 2384, "FL-C22")
r("core.py", 2385, 2546, "FL-Z02")
r("core.py", 2547, 2719, "FL-Z09")
r("core.py", 2720, 3131, "FL-D13")
r("core.py", 3132, 3176, "FL-D14")

# ---- pixiv_client.py ----
r("pixiv_client.py", 24, 119, "FL-C15")
r("pixiv_client.py", 159, 226, "FL-Z05")
r("pixiv_client.py", 227, 343, "FL-Z04")
r("pixiv_client.py", 344, 456, "FL-A11")
r("pixiv_client.py", 457, 685, "FL-A08")
r("pixiv_client.py", 686, 760, "FL-Z05")
r("pixiv_client.py", 761, 943, "FL-D00")
r("pixiv_client.py", 944, 1029, "FL-D15")
r("pixiv_client.py", 1030, 1090, "FL-M02")
r("pixiv_client.py", 1091, 1191, "FL-A11")
r("pixiv_client.py", 1192, 1233, "FL-C02")
r("pixiv_client.py", 1234, 1254, "FL-J01")
r("pixiv_client.py", 1255, 1309, "FL-I19")
r("pixiv_client.py", 1310, 1360, "FL-J06")
r("pixiv_client.py", 1361, 1480, "FL-K02")
r("pixiv_client.py", 1481, 1608, "FL-K06")
r("pixiv_client.py", 1609, 1626, "FL-D00")
r("pixiv_client.py", 1627, 1651, "FL-I13")
r("pixiv_client.py", 1652, 1678, "FL-D15")
r("pixiv_client.py", 1679, 1744, "FL-D00")
r("pixiv_client.py", 1745, 1838, "FL-I09")
r("pixiv_client.py", 1839, 1885, "FL-D14")
r("pixiv_client.py", 1886, 1924, "FL-M03")
r("pixiv_client.py", 1925, 1968, "FL-I09")
r("pixiv_client.py", 1969, 2010, "FL-N02")
r("pixiv_client.py", 2011, 2086, "FL-I09")
r("pixiv_client.py", 2087, 2120, "FL-C15")
r("pixiv_client.py", 2121, 2329, "FL-C15")
r("pixiv_client.py", 1, 23, "FL-Z05")
r("pixiv_client.py", 110, 158, "FL-M02")

# ---- database.py ----
r("database.py", 26, 151, "FL-Z07")
r("database.py", 192, 218, "FL-C20")
r("database.py", 219, 239, "FL-I19")
r("database.py", 240, 240, NA_CLASS)
r("database.py", 241, 262, "FL-A05")
r("database.py", 264, 790, "FL-Z13", ["db_table"])           # テーブル別の規則は下の TABLE_RULES が先に当たる
r("database.py", 794, 899, "FL-C21")
r("database.py", 903, 919, "FL-K01")
r("database.py", 921, 945, "FL-K06")
r("database.py", 947, 995, "FL-K06")
r("database.py", 997, 1034, "FL-L08")
r("database.py", 1036, 1084, "FL-L03")
r("database.py", 1086, 1099, "FL-K03")
r("database.py", 1101, 1128, "FL-K05")
r("database.py", 1130, 1270, "FL-H03")
r("database.py", 1272, 1330, "FL-H06")
r("database.py", 1356, 1493, "FL-C21")
r("database.py", 1495, 1516, "FL-D00")
r("database.py", 1518, 1555, "FL-Z06")
r("database.py", 1556, 1604, "FL-D00")
r("database.py", 1606, 1710, "FL-C20")
r("database.py", 1711, 1752, "FL-G01")
r("database.py", 1753, 1772, "FL-C20")
r("database.py", 1773, 1832, "FL-C02")
r("database.py", 1833, 1852, NA_ACCESSOR)
r("database.py", 1853, 1860, "FL-B10")
r("database.py", 1861, 1876, "FL-D05")
r("database.py", 1877, 1939, "FL-D14")
r("database.py", 1940, 1974, "FL-F01")
r("database.py", 1975, 1998, "FL-Z08")
r("database.py", 1999, 2032, "FL-I19")
r("database.py", 2033, 2470, "FL-I19")
r("database.py", 2471, 2571, "FL-L07")
r("database.py", 2572, 2607, "FL-L08")
r("database.py", 2608, 2673, "FL-B06")
r("database.py", 2674, 2825, "FL-I19")
r("database.py", 2826, 2832, "FL-A05")

# ---- 周辺エンジン ----
r("epub_builder.py", 1, 200, "FL-D15")
r("rate_coordinator.py", 1, 100, "FL-Z10")
r("registry_helper.py", 1, 100, "FL-A14")
r("scheduler.py", 1, 260, "FL-O01")
r("tray.py", 1, 60, "FL-A06")
r("ui_dispatch.py", 1, 400, NA_UIDISPATCH)
r("style_infer.py", 1, 600, "FL-C21")
r("nana7mi_vault.py", 1, 3000, "FL-B10")
r("main.py", 355, 365, "FL-B11")
r("main.py", 375, 390, "FL-N01")
r("main.py", 540, 570, "FL-A13")

# ---- server.py(拡張機能連携。iOS・Web 連携は本人が除外) ----
r("server.py", 790, 800, "FL-X02")
r("server.py", 835, 845, "FL-N02")
r("server.py", 900, 975, "FL-X01")
r("server.py", 1005, 1012, "FL-X02")
r("server.py", 1115, 1125, "FL-N03")
r("server.py", 1150, 1155, "FL-X01")
r("server.py", 1170, 1180, "FL-X01")
r("server.py", 1300, 1335, "FL-N05")
r("server.py", 1350, 1530, "FL-X01")
r("server.py", 1530, 1545, "FL-X01")

# ---- UI: 画集ビューア ----
r("artbook_viewer.py", 440, 465, "FL-M05")
r("artbook_viewer.py", 505, 560, "FL-M03")
r("artbook_viewer.py", 760, 790, "FL-M03")
r("artbook_viewer.py", 810, 820, "FL-M04")
r("artbook_viewer.py", 1160, 1170, "FL-M01")
r("artbook_viewer.py", 1260, 1275, "FL-M01")
r("artbook_viewer.py", 1290, 1320, "FL-M02")
r("artbook_viewer.py", 1470, 1490, "FL-M01")

# ---- UI: 学習(discovery) ----
r("discovery_engine.py", 20, 70, NA_MOCK)
r("discovery_engine.py", 630, 660, "FL-I14")
r("discovery_ranking.py", 200, 230, "FL-I19")
r("discovery_display_state.py", 60, 100, "FL-I14")
r("discovery_outcome.py", 60, 150, "FL-I14")
r("discovery_population.py", 190, 215, "FL-I14")
r("discovery_tab.py", 125, 145, "FL-I11")
r("discovery_tab.py", 480, 495, "FL-I14")
r("discovery_tab.py", 510, 525, "FL-C14")
r("discovery_tab.py", 885, 905, "FL-I14")
r("discovery_tab.py", 1900, 2110, "FL-I02")
r("discovery_tab.py", 2280, 2360, "FL-I14")
r("discovery_tab.py", 2780, 2900, "FL-I11")
r("discovery_tab.py", 3120, 3140, "FL-I12")
r("discovery_tab.py", 3340, 3420, "FL-I12")
r("discovery_tab.py", 4240, 4260, "FL-I11")
r("discovery_tab.py", 4620, 4670, "FL-I14")
r("discovery_tab.py", 4760, 4840, "FL-I14")
r("discovery_tab.py", 4980, 5030, "FL-I13")
r("discovery_tab.py", 5160, 5265, "FL-I16")
r("discovery_tab.py", 5600, 5700, "FL-I14")

# ---- UI: 共通部品 ----
r("filter_panel.py", 1, 500, "FL-K04")
r("grid_card_size_ui.py", 1, 120, "FL-C25")

# ---- UI: gui.py ----
r("gui.py", 140, 150, "FL-A02")
r("gui.py", 275, 285, NA_HOVER)
r("gui.py", 470, 490, "FL-B11")
r("gui.py", 1110, 1175, "FL-A08")
r("gui.py", 1685, 1695, "FL-C04")
r("gui.py", 2905, 2930, "FL-E03")
r("gui.py", 3200, 3212, "FL-E01")
r("gui.py", 3610, 3625, "FL-E01")
r("gui.py", 3700, 3705, "FL-E06")
r("gui.py", 4340, 4372, "FL-F03")
r("gui.py", 4820, 4870, "FL-C25")
r("gui.py", 4876, 4985, "FL-D13")
r("gui.py", 5005, 5020, "FL-H01")
r("gui.py", 5380, 5395, "FL-H05")
r("gui.py", 5490, 5498, "FL-H07")
r("gui.py", 5575, 5598, "FL-H05")
r("gui.py", 5820, 5840, "FL-H04")
r("gui.py", 6125, 6135, "FL-B01")

# ---- UI: ライブラリ ----
r("library_tab.py", 135, 150, "FL-K03")
r("library_tab.py", 1170, 1180, "FL-K10")
r("library_tab.py", 1215, 1230, "FL-K05")
r("library_tab.py", 1840, 1880, "FL-K06")
r("library_tab.py", 2160, 2240, "FL-K01")
r("library_tab.py", 2360, 2370, "FL-K02")
r("library_tab.py", 3110, 3190, "FL-K11")

# ---- UI: ランキング ----
r("ranking_tab.py", 130, 165, "FL-J12")
r("ranking_tab.py", 450, 460, "FL-J07")
r("ranking_tab.py", 515, 520, "FL-C25")
r("ranking_tab.py", 585, 635, "FL-J09")
r("ranking_tab.py", 745, 755, "FL-J10")
r("ranking_tab.py", 800, 860, "FL-J08")
r("ranking_tab.py", 945, 1070, "FL-J07")
r("ranking_tab.py", 1145, 1160, "FL-J06")
r("ranking_tab.py", 1170, 1180, "FL-J07")
r("ranking_tab.py", 1260, 1270, "FL-J03")
r("ranking_tab.py", 1280, 1360, "FL-J01")
r("ranking_tab.py", 2480, 2495, "FL-J11")

# ---- UI: おすすめ ----
r("recommend_tab.py", 138, 145, "FL-L02")
r("recommend_tab.py", 148, 158, "FL-L01")
r("recommend_tab.py", 255, 265, "FL-L01")
r("recommend_tab.py", 318, 335, NA_TIMING)
r("recommend_tab.py", 450, 460, "FL-L01")
r("recommend_tab.py", 490, 520, "FL-L05")
r("recommend_tab.py", 600, 815, "FL-L05")
r("recommend_tab.py", 2525, 2535, "FL-L07")
r("recommend_tab.py", 2700, 2780, "FL-L07")
r("recommend_tab.py", 3125, 3265, "FL-L03")
r("recommend_tab.py", 3370, 3380, "FL-L03")
r("recommend_tab.py", 3420, 3430, "FL-L03")
r("recommend_tab.py", 3460, 3600, "FL-L10")
r("recommend_tab.py", 3940, 3945, "FL-C25")

# ---- UI/エンジン: ダウンロード(統合) ----
r("unified_download.py", 20, 40, "FL-D02")
r("unified_download.py", 41, 92, "FL-D03")
r("unified_download.py", 93, 104, "FL-D10")
r("unified_download.py", 105, 175, "FL-D04")
r("unified_download.py", 176, 215, "FL-D10")
r("unified_download.py", 216, 290, "FL-D10")
r("unified_download_tab.py", 150, 165, "FL-D10")
r("unified_download_tab.py", 345, 385, "FL-D10")
r("unified_download_tab.py", 425, 435, "FL-D05")
r("unified_download_tab.py", 465, 475, "FL-D03")
r("unified_download_tab.py", 505, 540, "FL-D09")
r("unified_download_tab.py", 585, 640, "FL-D04")
r("unified_download_tab.py", 735, 785, "FL-D02")
r("unified_download_tab.py", 1235, 1270, "FL-D09")
r("unified_download_tab.py", 1370, 1480, "FL-D01")
r("unified_download_tab.py", 1550, 1565, "FL-D10")

TABLE_RULES = {
    # テーブル名(または テーブル.列の先頭テーブル) → 対応先
    "works": "FL-D00", "following_users": "FL-C02", "settings": NA_TABLE_SETTINGS,
    "author_settings": "FL-Z08", "failed_queue": "FL-F01",
    "discovery_preference": "FL-I19", "discovery_author_preference": "FL-I19",
    "discovery_swipe_history": "FL-I19", "discovery_view_history": "FL-B06",
    "discovery_population": "FL-I19", "discovery_population_meta": "FL-I19",
    "discovery_population_seen": "FL-I19", "discovery_hidden_works": "FL-L07",
    "discovery_hidden_authors": "FL-L07", "discovery_boosted_authors": "FL-L07",
    "bookmark_remote_cache": "FL-K06", "superlike_info_cache": "FL-L08",
    "recommend_section_carryover": "FL-L03", "work_embeddings": "FL-C21",
    "work_embed_failures": "FL-C21", "work_page_scan": "FL-C21", "file_ledger": "FL-H03",
    "library_capacity_cache": "FL-K05", "quarantine_exclusions": "FL-H06",
}
COLUMN_RULES = {
    "works.missing_streak": "FL-Z06", "works.work_meta": "FL-C20", "works.meta_fetched_at": "FL-C20",
    "works.meta_source": "FL-C20", "works.meta_attempts": "FL-C20", "works.is_bookmarked": "FL-K06",
    "following_users.is_zipped": "FL-Z08", "following_users.is_favorite": "FL-D05",
    "following_users.follow_order": "FL-D03", "following_users.is_following": "FL-C02",
}


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    data = json.loads(MAP.read_text(encoding="utf-8")) if MAP.exists() else {}
    data["rules"] = R
    data["table_rules"] = TABLE_RULES
    data["column_rules"] = COLUMN_RULES
    data["na_unused"] = NA_UNUSED
    data.setdefault("manual", {})
    MAP.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print("rules:", len(R), "table_rules:", len(TABLE_RULES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
