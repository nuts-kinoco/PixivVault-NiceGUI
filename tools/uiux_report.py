"""Build the three audit ledgers from saved capture records."""
from collections import Counter, defaultdict
import json
from pathlib import Path

from tools.p5_design_evidence import write_json
from tools.uiux_rules import ITEMS, MANUAL, finding


def proven(cell, record):
    return bool(record.get('product') and record.get('measured') and record.get('state_proven') and
                (cell.screen != 'settings' or (record.get('group_images') and not record.get('missing_settings_groups'))) and
                (not cell.original or (record.get('paired') and record.get('original_state_proven'))))


def coverage(matrix, records):
    applicable = [cell for cell in matrix if not cell.unsupported]
    captured = sum(proven(cell, records.get(cell.key, {})) for cell in applicable)
    return dict(planned=len(applicable), missing=len(applicable) - captured, captured=captured,
                inapplicable=len(matrix) - len(applicable))


def table(headers, rows):
    def line(values):
        return '| ' + ' | '.join(str(value).replace('|', '\\|').replace('\n', ' ') for value in values) + ' |'

    return '\n'.join([line(headers), line(['---'] * len(headers)), *(line(row) for row in rows)])


def image_link(path, label=None):
    return f'[{label or Path(path).name}]({path})' if path else '—'


def adjudicate(out, record):
    """Keep raw readings in metrics.json and qualify observations with known limits."""
    rows = record.get('findings', ())
    zero_panel = not record.get('state_proven') and any(
        row['item'] == 'A11' and row['selector'] == '.pv-content' for row in rows)
    low_tooltip_candidates = {row['selector'] for row in rows if row['item'] == 'A1'
                              and isinstance(row['measurement'], (int, float))
                              and row['measurement'] <= 1.02}
    tooltip_nodes = set()
    if record.get('dom') and low_tooltip_candidates:
        data = json.loads((out / record['dom']).read_text(encoding='utf-8'))
        tooltip_nodes = {node['selector'] for node in data['nodes']
                         if node['role'] == 'tooltip' and node['selector'] in low_tooltip_candidates}
    toast_selectors = [toast['selector'] for toast in record.get('motion', {}).get('toasts', ())]
    for row in rows:
        reason = ''
        if row['status'] == 'violation':
            if zero_panel and row['selector'] not in ('.pv-content', 'document'):
                reason = '対象タブは高さ 0px。画面固有の計測としての帰属を確認できない。'
            elif row['item'] == 'A1' and row['selector'] in tooltip_nodes and row['state'] == 'normal':
                reason = 'ツールチップの遷移中に前景と背景がほぼ同色。静止後の色を要確認。'
            elif row['item'] == 'A1' and row['selector'] in low_tooltip_candidates and any(
                    row['selector'] == toast or row['selector'].startswith(toast + ' > ')
                    for toast in toast_selectors):
                reason = 'トースト配下で前景と背景がほぼ同色。遷移完了後の色を要確認。'
        yield dict(row, status='review', review_reason=reason) if reason else row


def write_report(out, matrix, records, metadata):
    counts = coverage(matrix, records)
    index = {key: {k: v for k, v in record.items() if k not in ('findings', 'tab_order')}
             for key, record in records.items()}
    header = json.dumps(dict(metadata=metadata, counts=counts, captures=index), ensure_ascii=False, indent=2)
    plan_rows = ',\n'.join('    ' + json.dumps(cell.to_dict(), ensure_ascii=False, separators=(',', ':'))
                          for cell in matrix)
    (out / 'CAPTURES.json').write_text(header[:-2] + ',\n  "plan": [\n' + plan_rows + '\n  ]\n}\n',
                                      encoding='utf-8', newline='\n')
    findings = []
    for cell in matrix:
        if cell.unsupported:
            continue
        record = records.get(cell.key, {})
        local = list(adjudicate(out, record)) if record.get('measured') else []
        if record and not proven(cell, record):
            reason = record.get('issue', '未実行または状態根拠・元 Flet 並置が不足')
            local.append(finding('A16', 'capture', reason, '撮影・状態根拠・対応する元 Flet 並置',
                                 status='missing', severity='High'))
        findings.extend(dict(row, screen=cell.screen, condition=cell.condition,
                             evidence=record.get('product')) for row in local)
    order = {'High': 0, 'Medium': 1, 'Low': 2}
    visible_first = {item: rank for rank, item in enumerate(
        ('A11', 'A15', 'A1', 'A6', 'A12', 'A13', 'A8', 'A3', 'A5', 'A4',
         'A7', 'A10', 'A2', 'A14', 'A9', 'A16'))}
    findings.sort(key=lambda f: (order[f['severity']], visible_first[f['item']],
                                 f['measurement'] if f['item'] == 'A1' and isinstance(f['measurement'], float) else 0,
                                 f['screen'], f['selector']))
    violations = [row for row in findings if row['status'] == 'violation']
    reviews = [row for row in findings if row['status'] == 'review']
    measured = [cell for cell in matrix if records.get(cell.key, {}).get('measured')]
    measured_screens = {cell.screen for cell in measured}
    measured_items = {item for cell in measured for item in records[cell.key].get('items_observed', ())}
    by_item = Counter(f['item'] for f in violations)
    by_screen = Counter(f['screen'] for f in violations)
    highlights = []
    seen = set()
    for row in violations:
        key = row['screen'], row['item'], row['selector'], row['state']
        if key not in seen:
            seen.add(key)
            highlights.append(row)
        if len(highlights) == 15:
            break
    write_json(out / 'FINDINGS.json', dict(coverage=counts, violation_count=len(violations) if measured else None,
                                         by_item={k: by_item[k] if k in measured_items else None for k in ITEMS},
                                         by_screen={k: by_screen[k] if k in measured_screens else None
                                                    for k in dict.fromkeys(cell.screen for cell in matrix)},
                                         highlights=highlights,
                                         review_count=len(reviews) if measured else None, findings=findings))
    grouped = defaultdict(list)
    for cell in matrix:
        grouped[cell.screen].append(cell)
    observed = Counter((row['screen'], row['item'], row['status']) for row in findings)
    matrix_rows = []
    for screen, cells in grouped.items():
        scoped = coverage(cells, records)
        states = []
        for item in ITEMS:
            failures = observed[screen, item, 'violation']
            review = observed[screen, item, 'review']
            observed_items = [r for c in cells if not c.unsupported and
                              (r := records.get(c.key, {})).get('measured')]
            if item == 'A16':
                label = f"{scoped['captured']}/{scoped['planned']}"
            elif failures:
                label = f'違反 {failures}'
            elif not any(item in r.get('items_observed', ()) for r in observed_items):
                label = '未測定'
            else:
                label = '要確認' if review or MANUAL.get(item) else '機械基準内'
            states.append(label)
        matrix_rows.append([screen, *states])
    index_rows = []
    images = Counter()
    for cell in matrix:
        record = records.get(cell.key, {})
        for kind in ('product', 'original', 'paired'):
            if record.get(kind):
                images[kind] += 1
        images['settings group'] += len(record.get('group_images', ()))
        if any(record.get(k) for k in ('product', 'original', 'paired', 'group_images')):
            index_rows.append((cell.screen, cell.condition, *(image_link(record.get(k))
                              for k in ('product', 'original', 'paired')),
                              ', '.join(image_link(p, record.get('group_titles', {}).get(Path(p).name))
                                        for p in record.get('group_images', ())),
                              record.get('issue', '確認済み' if proven(cell, record) else '未確認')))

    def write(name, *parts):
        (out / name).write_text('\n\n'.join(parts) + '\n', encoding='utf-8', newline='\n')

    write('AUDIT_MATRIX.md', '# 画面 × A1〜A16', f"条件成立 {counts['captured']} / {counts['planned']}。"
          f" 未成立 {counts['missing']}、状態入力未定義または対象なし {counts['inapplicable']}。"
          ' 「要確認」は機械で違反を確定できなかった項目。A16 は成立数／適用条件数。',
          table(['画面', *ITEMS], matrix_rows),
          '仕様 C の独立した目視、監督の並置確認、実機確認は別途判定。')
    headers = ['重大度', '項目', '画面', '条件', '状態', '要素', '測定値', 'しきい値', '出典']

    def rows(values):
        return [[row[k] for k in ('severity', 'item', 'screen', 'condition', 'state', 'selector',
                                  'measurement', 'threshold', 'source')] for row in values]

    write('VIOLATIONS.md', '# 違反と要確認',
          f'機械違反 {len(violations)} 件、要確認候補 {len(reviews)} 件。' if measured else '機械測定未実行。件数は未確定。',
          '## 項目別', table(['項目', '違反'], ((item, by_item[item] if item in measured_items else '未測定') for item in ITEMS)),
          '## 画面別', table(['画面', '違反'], ((screen, by_screen[screen] if screen in measured_screens else '未測定') for screen in grouped)),
          '## 優先確認 15 件', table(headers, rows(highlights)),
          '## 機械違反（重大度順）', table(headers, rows(violations)),
          '## 誤検出を含み得る候補',
          table([*headers, '保留理由'], [base + [review.get('review_reason', '')]
                                           for base, review in zip(rows(reviews), reviews)]),
          '## 人による確認', table(['項目', '確認内容'], MANUAL.items()),
          '## 出典', '[Accessibility](https://developer.apple.com/design/human-interface-guidelines/accessibility) · '
          '[Buttons](https://developer.apple.com/design/human-interface-guidelines/buttons) · '
          '[Typography](https://developer.apple.com/design/human-interface-guidelines/typography) · '
          '[監査仕様](../../../docs/UIUX_AUDIT_SPEC_20261122.md)')
    write('IMAGES_INDEX.md', '# 画像索引',
          f"移植版 {images['product']}、Flet {images['original']}、並置 {images['paired']}、"
          f"設定グループ等 {images['settings group']}。並置の左が Flet、右が移植版。",
          '未撮影の条件を含む全件は [CAPTURES.json](CAPTURES.json)。',
          table(['画面', '条件', '移植版', 'Flet', '並置', '補足画像', '状態'], index_rows))
    return counts, images
