"""inventory.json の全項目が ledger_map.json で台帳(FL-ID)または n/a:<理由> に対応づけられているかを検査する。

終了コード: 0=未対応ゼロかつ整合 / 1=未対応あり / 2=整合エラー(存在しない ID・空の理由など)
解決順(resolve): manual > 自動(確かな方法: section-T/name/text/range/qualname) > テーブル規則 > 未使用(参照数0) > rules
                 > 自動(弱い方法: range-wide=広い範囲 / nearest=近傍)
「弱い方法」だけで対応づけた件数は別に出す(--strict-weak を付けると、それも未対応として扱い非 0 で終了)。
使い方: python -B tools/check_inventory_coverage.py [--list N] [--json OUT] [--strict-weak]
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

STRONG = ("section-T", "name", "text", "range", "qualname")
WEAK = ("range-wide", "nearest")


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
        if best is None or w < best[0]:
            best = (w, rl)
    return best[1] if best else None


def resolve(it, mp):
    """戻り値=(対応先, 出所) 。対応なしは (None, 'unmapped')"""
    iid = it["id"]
    man = mp.get("manual", {})
    if iid in man:
        return man[iid], "manual"
    au = mp.get("auto", {}).get(iid)
    if au and au["method"] in STRONG:
        return au["fl"], "auto-" + au["method"]
    if it["kind"] == "db_table":
        name = it["name"]
        cr = mp.get("column_rules", {})
        if name in cr:
            return cr[name], "table-rule"
        tr = mp.get("table_rules", {})
        if name.split(".")[0] in tr:
            return tr[name.split(".")[0]], "table-rule"
    if it["kind"] in ("api_public", "db_method") and it.get("ref_count", 1) == 0:
        return mp.get("na_unused", "n/a:呼び出し元なし"), "unused"
    rl = rule_hit(mp.get("rules", []), it)
    if rl:
        return rl["fl"], "rule"
    if au and au["method"] in WEAK:
        return au["fl"], "auto-" + au["method"]
    return None, "unmapped"


def load():
    inv = json.loads((HERE.parent / "docs" / "inventory.json").read_text(encoding="utf-8"))
    mp = json.loads((HERE.parent / "docs" / "ledger_map.json").read_text(encoding="utf-8"))
    rows, _ = blm.parse_ledger(blm.LEDGER)
    fl_ids = set(rows) | set(blm.parse_addenda_ids(blm.ADDENDA))
    return inv, mp, fl_ids


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", type=int, default=40, help="未対応の一覧の最大表示件数")
    ap.add_argument("--json", default="")
    ap.add_argument("--strict-weak", action="store_true")
    a = ap.parse_args(argv)
    inv, mp, fl_ids = load()
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
    for k, v in mp.get("auto", {}).items():
        if k not in ids:
            errors.append("auto に inventory に無い項目(inventory 再生成後に build_ledger_map.py を実行): " + k)
            break
        if v["fl"] not in fl_ids and not all(p in fl_ids for p in v["fl"].split(",")):
            errors.append("auto が台帳に無い ID を指す: %s" % v["fl"])
            break
    by_src: dict[str, int] = {}
    unmapped, weak = [], []
    na = 0
    used_fl: dict[str, int] = {}
    for it in items:
        tgt, src = resolve(it, mp)
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
            used_fl[tgt.split(",")[0]] = used_fl.get(tgt.split(",")[0], 0) + 1
        if src in ("auto-range-wide", "auto-nearest"):
            weak.append(it)
    total = len(items)
    print("inventory 件数:", total)
    print("対応づけの出所:", json.dumps(by_src, ensure_ascii=False, sort_keys=True))
    print("  うち n/a:", na, " / 台帳 FL へ:", total - len(unmapped) - na)
    print("  弱い方法(広い範囲・近傍)のみ:", len(weak), "(--strict-weak で未対応扱い)")
    print("対応先に使われた FL の数:", len(used_fl))
    n_un = len(unmapped) + (len(weak) if a.strict_weak else 0)
    print("未対応:", len(unmapped) + (len(weak) if a.strict_weak else 0))
    for it in (unmapped + (weak if a.strict_weak else []))[: a.list]:
        print("  %s %s:%d [%s] %s" % (it["id"], it["file"], it["line"], it["kind"], it["name"][:60].replace("\n", " ")))
    if errors:
        print("整合エラー:", len(errors))
        for e in errors[:30]:
            print("  ", e)
    if a.json:
        Path(a.json).write_text(json.dumps({"total": total, "by_source": by_src, "unmapped": [i["id"] for i in unmapped],
                                            "errors": errors}, ensure_ascii=False, indent=1), encoding="utf-8")
    if errors:
        return 2
    return 1 if n_un else 0


if __name__ == "__main__":
    sys.exit(main())
