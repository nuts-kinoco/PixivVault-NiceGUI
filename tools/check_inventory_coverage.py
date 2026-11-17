"""inventory.json の全項目が ledger_map.json で台帳(FL-ID)または n/a:<理由> に対応づけられているかを検査する。

終了コード: 0=未対応ゼロかつ整合 / 1=未対応あり(または --strict-gaps で未解決の差あり) / 2=整合エラー(存在しない ID・空の理由・--strict-reverse の逆方向エラーなど)
解決順(resolve): manual > 自動(確かな方法: section-T/name/text/range) > テーブル規則 > 未使用(参照数0。動的ディスパッチは除く) > rules
                 > 自動(qualname) > 単位規則(scope) > 広い規則 > 自動(弱い方法: range-wide=広い範囲 / nearest=近傍)
「弱い方法」だけで対応づけた件数は別に出す(--strict-weak を付けると、それも未対応として扱い非 0 で終了)。

規則(rules)の根拠の強さは、規則の行範囲の幅で決める(名前指定・n/a も同じ。RULE_NARROW_MAX と UNIT_MAX):
  幅 RULE_NARROW_MAX 行以内 → tier A / scope が function・file で幅 UNIT_MAX 行以内 → tier B / それより広い規則 → tier C(rule-wide)。
対応先の台帳の判定が「無」「弱」の項目と、元の UI 文言が移植版に見つからない項目(port_text_missing)は、
「未解決の差」として別集計する(対応づけ済みでも差は残っているため。--strict-gaps で非 0 終了)。
引用範囲に inventory 項目が 0 件の台帳行は逆方向の警告として出す(--strict-reverse でエラー)。
使い方: python -B tools/check_inventory_coverage.py [--list N] [--json OUT] [--strict-weak] [--strict-all] [--strict-gaps] [--strict-reverse]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_ledger_map as blm  # noqa: E402

STRONG_A = ("section-T", "name", "text", "range")
WEAK = ("range-wide", "nearest")
WEAK_SRC = ("auto-range-wide", "auto-nearest", "rule-wide")
TIER_B = ("auto-qualname", "rule-function", "rule-file")

RULE_NARROW_MAX = blm.WIDE   # 手書き規則の行範囲がこの行数以内なら tier A(名前指定・n/a も同じ)。超えたら tier C
UNIT_MAX = 400               # scope=function/file の規則を tier B(単位での対応)と認める最大の幅。超えたら tier C
GAP_STATES = ("無", "弱")     # 台帳の状態がこれで始まる行に結ばれた項目は「未解決の差」

# フレームワークが getattr などで動的に呼ぶメソッド。静的な参照数が 0 でも「呼び出し元なし」にしない
DYNAMIC_DISPATCH = re.compile(r"(?:^|\.)(?:do_[A-Z]+|log_message|log_request|log_error|handle|handle_one_request|setup|finish|run|emit)$")


def split_targets(t: str):
    """'FL-A01, FL-B02' -> ['FL-A01','FL-B02']。n/a は含めない。"""
    return [p.strip() for p in t.split(",") if p.strip() and not p.strip().startswith("n/a")]


def auto_target(au):
    """自動対応の対応先。also(同じ関数名・設定キーが出る他の台帳行)も全て含める。"""
    out = [au["fl"]]
    for x in au.get("also", []) or []:
        if x not in out:
            out.append(x)
    return ", ".join(out)


def valid_target(t: str, fl_ids: set):
    """対応先の形式検査。戻り値=(ok, 理由)"""
    if t.startswith("n/a:"):
        return (bool(t[4:].strip()), "n/a の理由が空")
    if t.startswith("n/a"):
        return (False, "n/a は 'n/a:<理由>' の形式")
    for part in [p.strip() for p in t.split(",")]:
        if part not in fl_ids:
            return (False, "台帳・追記に無い ID: " + part)
    return (True, "")


def rule_hit(rules, it):
    best = None
    for rl in rules:
        if rl["file"] != it["file"]:
            continue
        a, b = rl["lines"]
        if not (a <= it["line"] <= b):
            continue
        if rl.get("kinds") and it["kind"] not in rl["kinds"]:
            continue
        w = b - a
        if rl.get("name_re"):
            if not re.search(rl["name_re"], it["name"]):
                continue
            w = -1           # 名前で決まる規則は範囲だけの規則より優先(どの規則が採られるか。根拠の強さは幅で決める)
        if best is None or w < best[0]:
            best = (w, rl)
    return best[1] if best else None


def resolve(it, mp):
    """戻り値=(対応先, 出所)。対応なしは (None, 'unmapped')。出所の強さ(tier):
      A(狭い根拠): manual / auto-section-T,name,text,range / table-rule / unused / rule(行範囲が RULE_NARROW_MAX 行以内)
      B(単位での対応): auto-qualname(同じ関数内の近い項目) / rule-function・rule-file(幅 UNIT_MAX 行以内)
      C(弱い): rule-wide(RULE_NARROW_MAX 行を超える規則。名前指定・n/a・scope でも UNIT_MAX を超えれば同じ) / auto-range-wide / auto-nearest
    順序: manual > 自動(A) > テーブル規則 > 未使用 > 狭い規則 > qualname > 単位規則 > 広い規則 > 自動(C)"""
    iid = it["id"]
    man = mp.get("manual", {})
    if iid in man:
        return man[iid], "manual"
    au = mp.get("auto", {}).get(iid)
    if au and au["method"] in STRONG_A:
        return auto_target(au), "auto-" + au["method"]
    if it["kind"] == "db_table":
        name = it["name"]
        cr = mp.get("column_rules", {})
        if name in cr:
            return cr[name], "table-rule"
        tr = mp.get("table_rules", {})
        if name.split(".")[0] in tr:
            return tr[name.split(".")[0]], "table-rule"
    if (it["kind"] in ("api_public", "db_method") and it.get("ref_count", 1) == 0
            and not DYNAMIC_DISPATCH.search(it["name"])):
        return mp.get("na_unused", "n/a:呼び出し元なし"), "unused"
    rl = rule_hit(mp.get("rules", []), it)
    kind_of_rule = None
    if rl:
        width = rl["lines"][1] - rl["lines"][0] + 1
        if width <= RULE_NARROW_MAX:
            return rl["fl"], "rule"
        kind_of_rule = "wide"
        if rl.get("scope") in ("file", "function") and width <= UNIT_MAX:
            kind_of_rule = rl["scope"]
    if au and au["method"] == "qualname":
        return auto_target(au), "auto-qualname"
    if kind_of_rule in ("file", "function"):
        return rl["fl"], "rule-" + kind_of_rule
    if kind_of_rule == "wide":
        return rl["fl"], "rule-wide"
    if au and au["method"] in WEAK:
        return auto_target(au), "auto-" + au["method"]
    return None, "unmapped"


def load():
    inv = json.loads((HERE.parent / "docs" / "inventory.json").read_text(encoding="utf-8"))
    mp = json.loads((HERE.parent / "docs" / "ledger_map.json").read_text(encoding="utf-8"))
    rows, _ = blm.parse_ledger(blm.LEDGER)
    fl_ids = set(rows) | set(blm.parse_addenda_ids(blm.ADDENDA))
    return inv, mp, fl_ids


def load_rows():
    """本体(A〜P)と追記(FL-Z・FL-Y など)の台帳行を合わせて返す。戻り値=(rows, 設定キー索引 sec_t)。"""
    rows, sec_t = blm.parse_ledger(blm.LEDGER)
    for fid, r in blm.parse_addenda(blm.ADDENDA).items():
        rows.setdefault(fid, r)
    return rows, sec_t


def is_gap_row(rows, fid):
    r = rows.get(fid)
    return bool(r) and r["state"].strip().startswith(GAP_STATES)


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", type=int, default=40, help="未対応の一覧の最大表示件数")
    ap.add_argument("--json", default="")
    ap.add_argument("--strict-weak", action="store_true", help="弱い対応(tier C)も未対応として扱う")
    ap.add_argument("--strict-all", action="store_true", help="単位での対応(tier B)と弱い対応(tier C)も未対応として扱う")
    ap.add_argument("--strict-gaps", action="store_true",
                    help="未解決の差(対応先の台帳判定が無・弱の項目、元の UI 文言が移植版に無い項目)があれば非 0 で終了")
    ap.add_argument("--strict-reverse", action="store_true", help="引用範囲に inventory 項目が 0 件の台帳行をエラーにする")
    a = ap.parse_args(argv)
    inv, mp, fl_ids = load()
    rows, sec_t = load_rows()
    items = inv["items"]
    ids = {i["id"] for i in items}
    errors = []
    for k, v in mp.get("manual", {}).items():
        if k not in ids:
            errors.append("manual に inventory に無い項目: " + k)
        ok, why = valid_target(v, fl_ids)
        if not ok:
            errors.append("manual %s: %s" % (k, why))
    for rl in mp.get("rules", []):
        ok, why = valid_target(rl["fl"], fl_ids)
        if not ok:
            errors.append("rule %s%s: %s" % (rl["file"], rl["lines"], why))
    for k, v in list(mp.get("table_rules", {}).items()) + list(mp.get("column_rules", {}).items()):
        ok, why = valid_target(v, fl_ids)
        if not ok:
            errors.append("table/column rule %s: %s" % (k, why))
    bad_auto = [k for k in mp.get("auto", {}) if k not in ids]
    if bad_auto:
        errors.append("auto に inventory に無い項目 %d 件(inventory 再生成後に build_ledger_map.py を実行): %s" % (len(bad_auto), bad_auto[:3]))
    for k, v in mp.get("auto", {}).items():
        if k in ids:
            for p_ in [v["fl"]] + list(v.get("also", []) or []):
                if p_ not in fl_ids and not all(q_.strip() in fl_ids for q_ in p_.split(",")):
                    errors.append("auto が台帳に無い ID を指す: %s (%s)" % (p_, k))
    by_src: dict[str, int] = {}
    unmapped, weak, unit = [], [], []
    na = 0
    used_fl: dict[str, int] = {}
    resolved: dict[str, tuple] = {}
    for it in items:
        tgt, src = resolve(it, mp)
        resolved[it["id"]] = (tgt, src)
        by_src[src] = by_src.get(src, 0) + 1
        if tgt is None:
            unmapped.append(it)
            continue
        ok, why = valid_target(tgt, fl_ids)
        if not ok:
            errors.append("%s (%s:%d) → %s: %s" % (it["id"], it["file"], it["line"], tgt, why))
        if tgt.startswith("n/a"):
            na += 1
        else:
            for fl in split_targets(tgt):          # 複数の FL に結ばれた項目は、全ての FL に数える
                used_fl[fl] = used_fl.get(fl, 0) + 1
        if src in WEAK_SRC:
            weak.append(it)
        elif src in TIER_B:
            unit.append(it)
    total = len(items)
    print("inventory 件数:", total)
    print("対応づけの出所:", json.dumps(by_src, ensure_ascii=False, sort_keys=True))
    print("  うち n/a:", na, " / 台帳 FL へ:", total - len(unmapped) - na)
    a_n = total - len(unmapped) - len(weak) - len(unit)
    print("  tier A 狭い根拠:", a_n, " / tier B 単位での対応(同じ関数内の近い項目・関数全体・ファイル全体):", len(unit),
          " / tier C 弱い(広い範囲・近傍・広い手規則):", len(weak))
    print("  規則の幅の上限: tier A は %d 行以内 / tier B(scope=function・file)は %d 行以内 / それより広い規則は tier C" % (RULE_NARROW_MAX, UNIT_MAX))
    print("厳格な未対応: 未対応+C =", len(unmapped) + len(weak), " / 未対応+B+C =", len(unmapped) + len(weak) + len(unit))
    print("対応先に使われた FL の数:", len(used_fl))
    wk: dict[str, int] = {}
    for it in weak:
        t_, _ = resolved[it["id"]]
        for fl in split_targets(t_):
            wk[fl] = wk.get(fl, 0) + 1
    print("弱い対応の多い FL(上位 10):", ", ".join("%s=%d" % kv for kv in sorted(wk.items(), key=lambda x: -x[1])[:10]))
    extra = (weak if (a.strict_weak or a.strict_all) else []) + (unit if a.strict_all else [])
    n_un = len(unmapped) + len(extra)
    print("未対応:", n_un)
    for it in (unmapped + extra)[: a.list]:
        print("  %s %s:%d [%s] %s" % (it["id"], it["file"], it["line"], it["kind"], it["name"][:60].replace("\n", " ")))

    # 未解決の差: 対応づけ済みでも、対応先の台帳の判定が 無・弱 の項目と、元の UI 文言が移植版に見つからない項目
    gap_items: dict[str, list] = {}
    gap_fl_by_state = {"無": set(), "弱": set()}
    for it in items:
        t_, _ = resolved[it["id"]]
        if t_ is None:
            continue
        for fl in split_targets(t_):
            if is_gap_row(rows, fl):
                gap_items.setdefault(it["id"], []).append(fl)
                gap_fl_by_state[rows[fl]["state"].strip()[0]].add(fl)
    missing_ids = set(mp.get("port_text_missing", [])) & ids
    miss_na = miss_gap = miss_ok = 0
    miss_ok_by_fl: dict[str, int] = {}
    for it in items:
        if it["id"] not in missing_ids:
            continue
        t_, _ = resolved[it["id"]]
        if t_ is None or t_.startswith("n/a"):
            miss_na += 1
        elif it["id"] in gap_items:
            miss_gap += 1
        else:
            miss_ok += 1
            for fl in split_targets(t_):
                miss_ok_by_fl[fl] = miss_ok_by_fl.get(fl, 0) + 1
    gap_total = len(gap_items) + miss_ok
    gap_fl_n = len(gap_fl_by_state["無"]) + len(gap_fl_by_state["弱"])
    print("未解決の差: %d 件(= 対応先が無・弱の項目 %d 件 + 対応先は有でも元の UI 文言が移植版に無い項目 %d 件)" % (gap_total, len(gap_items), miss_ok))
    print("  無・弱の台帳行: %d 行(無 %d 行・弱 %d 行)" % (gap_fl_n, len(gap_fl_by_state["無"]), len(gap_fl_by_state["弱"])))
    print("  port_text_missing: %d 件(うち n/a %d 件・無/弱の行 %d 件・有の行 %d 件)" % (len(missing_ids), miss_na, miss_gap, miss_ok))
    print("  有の行で文言が無い項目の多い FL(上位 10):", ", ".join("%s=%d" % kv for kv in sorted(miss_ok_by_fl.items(), key=lambda x: -x[1])[:10]) or "なし")

    # 逆方向の検査(引用範囲に inventory 項目が 0 件の台帳行。--strict-reverse でエラー)
    inv_keys = {i["name"] for i in items if i["kind"] == "setting_key"}
    # exclude_hidden_* は ranking_tab.py:159-182 の絞り込み辞書のキーで、get_setting のキーではない(台帳 T 節の分類誤り)
    t_missing = sorted(k for k in sec_t if not k.startswith("_") and k not in inv_keys
                       and k not in ("exclude_hidden_authors", "exclude_hidden_works"))
    by_file: dict[str, list] = {}
    for it in items:
        by_file.setdefault(it["file"], []).append(it["line"])
    empty_rows = []
    for fid, r in rows.items():
        if r["refs"] and not any(a_ <= ln <= b_ for f_, a_, b_ in r["refs"] for ln in by_file.get(f_, [])):
            empty_rows.append(fid)
    print("参考: 台帳の設定キー索引(T)にあるが inventory に無いキー:", t_missing or "なし")
    print("警告: 引用範囲に inventory 項目が 0 件の台帳行(行番号のずれ・引用形式の不備の疑い):", len(empty_rows), empty_rows)
    if a.strict_reverse:
        for fid in empty_rows:
            errors.append("逆方向: %s の引用範囲に inventory 項目が 0 件" % fid)
    if errors:
        print("整合エラー:", len(errors))
        for e in errors[:30]:
            print("  ", e)
    if a.json:
        Path(a.json).write_text(json.dumps({
            "total": total, "by_source": by_src, "unmapped": [i["id"] for i in unmapped],
            "tier_a": a_n, "tier_b": len(unit), "tier_c": len(weak),
            "gap_items": len(gap_items), "gap_rows_none": sorted(gap_fl_by_state["無"]), "gap_rows_weak": sorted(gap_fl_by_state["弱"]),
            "port_text_missing": len(missing_ids), "port_text_missing_on_ok_rows": miss_ok,
            "reverse_empty_rows": empty_rows, "errors": errors}, ensure_ascii=False, indent=1), encoding="utf-8")
    if errors:
        return 2
    if n_un or (a.strict_gaps and gap_total):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
