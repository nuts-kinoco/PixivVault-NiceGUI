"""検証ラウンドの収束チェック（PV、2026-11-19）。

使い方（検証結果を受け取るたびに必ず実行する。CLAUDE.md の H）:
  python ops\\convergence_check.py add <workstream> <round> --high N --medium N --classes CLASS1,CLASS2 [--note TEXT]
  python ops\\convergence_check.py check [<workstream>]
  python ops\\convergence_check.py list

判定規則（根拠は CLAUDE.md の運用 F／本人方針: 同じ問題が 2 回続いたら前提を見直す、3 回で独立した分析）:
  R1 同じ欠陥の型が 2 ラウンド連続 -> SWITCH: 次の修正の前に「その型の全箇所の網羅」か「構造で防ぐ設計」へ切り替える。
  R2 high の件数が 3 ラウンド連続で減っていない（>=3 ラウンドの記録で単調減少でない）-> CONSULT: 独立した設計・受入条件の相談を必須にする。
  R3 同じ作業が 5 ラウンドを超えた -> REPLAN: 受入条件・脅威モデル・範囲の妥当性を見直し、本人に報告する。
  R4 新しい型が最新ラウンドで増えている（前ラウンドに無い型が 2 つ以上）-> SWEEP: 自由探索をやめ、網羅先行の検証依頼にする。
いずれの SWITCH/CONSULT/REPLAN/SWEEP も、人の判断に任せず、出たら次の依頼の前に実行する。
"""
import json
import sys
from pathlib import Path

LOG = Path(__file__).with_name('rounds.json')


def load():
    if LOG.exists():
        return json.loads(LOG.read_text(encoding='utf-8'))
    return []


def save(rows):
    LOG.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding='utf-8')


def check(rows, workstream):
    mine = sorted((r for r in rows if r['workstream'] == workstream), key=lambda r: r['round'])
    actions = []
    if not mine:
        return ['(記録なし)']
    last = mine[-1]
    if len(mine) >= 2:
        prev = mine[-2]
        repeated = sorted(set(last['classes']) & set(prev['classes']))
        if repeated:
            actions.append('SWITCH: 型 %s が連続（R%d, R%d）。次の修正の前に全箇所の網羅か構造で防ぐ設計へ' % (','.join(repeated), prev['round'], last['round']))
        newer = sorted(set(last['classes']) - set(prev['classes']))
        if len(newer) >= 2:
            actions.append('SWEEP: 前ラウンドに無い型が %d 件（%s）。自由探索をやめ網羅先行の検証依頼へ' % (len(newer), ','.join(newer)))
    if len(mine) >= 3:
        highs = [r['high'] for r in mine[-3:]]
        if not (highs[0] > highs[1] > highs[2]) and highs[-1] > 0:
            actions.append('CONSULT: high が 3 ラウンドで単調に減っていない（%s）。独立した設計・受入条件の相談を必須に' % highs)
    if last['round'] > 5 and last['high'] > 0:
        actions.append('REPLAN: %d ラウンド目で high が残る。受入条件・脅威モデル・範囲を見直し本人へ報告' % last['round'])
    return actions or ['OK: 収束中（R%d、high=%d）' % (last['round'], last['high'])]


def main(argv):
    rows = load()
    if not argv or argv[0] == 'list':
        for r in sorted(rows, key=lambda r: (r['workstream'], r['round'])):
            print(r['workstream'], 'R%d' % r['round'], 'high=%d medium=%d' % (r['high'], r['medium']), ','.join(r['classes']), r.get('note', ''))
        return 0
    if argv[0] == 'add':
        ws, rnd = argv[1], int(argv[2])
        opts = {}
        i = 3
        while i < len(argv):
            opts[argv[i].lstrip('-')] = argv[i + 1]
            i += 2
        rows = [r for r in rows if not (r['workstream'] == ws and r['round'] == rnd)]
        rows.append(dict(workstream=ws, round=rnd, high=int(opts.get('high', 0)), medium=int(opts.get('medium', 0)),
                         classes=[c for c in opts.get('classes', '').split(',') if c], note=opts.get('note', '')))
        save(rows)
        for a in check(rows, ws):
            print(ws, a)
        return 0
    if argv[0] == 'check':
        names = [argv[1]] if len(argv) > 1 else sorted({r['workstream'] for r in rows})
        for ws in names:
            for a in check(rows, ws):
                print(ws, a)
        return 0
    print(__doc__)
    return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
