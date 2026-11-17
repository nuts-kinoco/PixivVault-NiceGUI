"""FEATURE_LEDGER_ADDENDA の付録A(元の UI 文言のうち、移植版 src に同じ文言が見つからないもの)を再生成する。

機械的な手がかりであり、同値の判定ではない(言い回しが違うだけの可能性がある)。
inventory.json と ledger_map.json から、台帳 FL ごとに件数・例を出す。マーカー間だけを書き換える。
使い方: python -B tools/build_addenda_appendix.py
"""
from __future__ import annotations

import collections
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import check_inventory_coverage as c  # noqa: E402
import build_ledger_map as blm  # noqa: E402

BEGIN = "<!-- APPENDIX-A:BEGIN -->"
END = "<!-- APPENDIX-A:END -->"
KINDS = ("ui_text", "menu_item", "label_const", "snack")


def build_text():
    inv, mp, fl = c.load()
    miss = set(mp["port_text_missing"])
    groups = collections.defaultdict(list)
    uniq = set()
    for it in inv["items"]:
        if it["id"] in miss and it["kind"] in KINDS:
            tgt, src = c.resolve(it, mp)
            uniq.add(it["id"])
            # 複数の FL に結ばれた項目は、先頭だけでなく全ての FL の行に数える
            for fl in (c.split_targets(tgt) if tgt and not tgt.startswith("n/a") else [tgt or "?"]):
                groups[fl[:60]].append((it, src))
    rows, _ = c.load_rows()
    out = [BEGIN, "",
           "元の UI 文言(ui_text・menu_item・label_const・snack)のうち、移植版 `src/pixivvault_nicegui` 全体にその文言(f 文字列は最長の固定部分)が見つからないもの。",
           "言い回しが違うだけの可能性があるため、**同値の判定ではなく確認の手がかり**。件数は台帳の FL ごと。",
           "", "| 台帳 FL | 台帳の状態 | 未検出の文言 件数 | 例(元の場所) |", "|---|---|---|---|"]
    total = len(uniq)
    for k in sorted(groups):
        v = groups[k]
        st = rows[k]["state"][:8] if k in rows else ("n/a" if k.startswith("n/a") else "")
        ex = "; ".join("「%s」%s:%d" % (i["name"][:22].replace("|", "/").replace("\n", " "), i["file"], i["line"]) for i, _ in v[:3])
        out.append("| %s | %s | %d | %s |" % (k[:60], st, len(v), ex))
    out += ["", "合計 %d 件(複数の FL に結ばれた項目は、各 FL の行に 1 件ずつ数えるため、表の件数の和は合計より多い)。" % total, "", END]
    return "\n".join(out), total


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    text, total = build_text()
    p = blm.ADDENDA
    body = p.read_text(encoding="utf-8")
    if BEGIN in body and END in body:
        a = body.index(BEGIN)
        b = body.index(END) + len(END)
        body = body[:a] + text + body[b:]
    else:
        body = body.rstrip("\n") + "\n\n" + text + "\n"
    p.write_text(body, encoding="utf-8", newline="\n")
    print("appendix A rows total:", total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
