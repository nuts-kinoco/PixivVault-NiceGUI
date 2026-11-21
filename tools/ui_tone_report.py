"""Validate and summarize a completed P5 tone capture."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from shutil import copytree

from PIL import Image

from tools.ui_tone_evidence import comparison, toolbar_rows


LABELS = dict(download='ダウンロード', queue='キュー', failed='失敗リスト', dedup='重複整理',
              learning='学習', ranking='ランキング', personal='自分のランキング', library='ライブラリ',
              bookmarks='マイブックマーク', recommend='おすすめ', history='直近のバックアップ',
              scheduler='定期チェック', metadata='メタデータ補完', settings='設定')
LABELS.update({'view-history':'閲覧履歴','metadata-ledger':'メタデータ台帳（展開）',
               'shared-log':'共有ログ（展開）','shared-log-collapsed':'共有ログ（折りたたみ）',
               'stop-recovery':'永続429停止の回復ダイアログ','stop-uncertain':'中断GETの回復ダイアログ',
               'startup-reference':'起動設定・参照先不可','startup-cookie-rejected':'起動設定・Cookie読込不可',
               'startup-lock-busy':'起動設定・停止記録使用中','startup-legacy-uncertain':'起動設定・旧形式判別不可',
               'startup-legacy-copy-error':'起動設定・旧DB複製不可','startup-stop-migration':'起動設定・停止履歴移行要',
               'stop-history-guide':'停止履歴の確認と移行案内','stop-migration-dialog':'正式移行のダイアログ'})
LABELS.update({'personal-results':'自分のランキング（検索結果）','request-wait':'今回の429・再試行待機',
               'request-limit':'今回の429・試行上限と共有停止'})
SETTINGS_GROUPS = ('アカウント','保存ポリシー・通知','作品メタデータ','絵柄・構図の推論',
                   'エクスポート','詳細設定','学習状況')
GROUP_REFS = {'Pixiv Cookieファイル':'アカウント','フォロー中リストの同期':'アカウント',
              '保存':'保存ポリシー・通知','通信':'詳細設定','キャッシュ':'詳細設定','Windows':'詳細設定',
              **{key:key for key in ('作品メタデータ','絵柄・構図の推論','エクスポート')}}
DEFAULT_STATE = {'download':'作者一覧0件','queue':'ジョブ・ログ0件','failed':'失敗記録0件',
                 'dedup':'画像0件','learning':'候補0件','ranking':'順位データ0件',
                 'personal':'未取得','library':'保存済み作品0件','bookmarks':'キャッシュ0件',
                 'recommend':'候補0件','history':'バックアップ対象0件','view-history':'閲覧記録0件',
                 'scheduler':'実行履歴0件','metadata':'補完対象0件','metadata-ledger':'補完対象0件',
                 'shared-log':'合成ログ0件','shared-log-collapsed':'折りたたみ状態',
                 'settings':'設定フォーム','stop-recovery':'永続停止条件','stop-uncertain':'中断GET条件'}


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def dump(path, value):
    path.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n',encoding='utf-8',newline='\n')


def supplement(root, source, selected):
    """Bring in complete cells with the same product revisions and viewports."""
    summary = read(root/'summary.json')
    incoming = read(source/'summary.json')
    lifecycle = read(root/'lifecycle.json')
    receipt = read(source/'lifecycle.json')
    if (any(summary[k]!=incoming[k] for k in ('port_revision','original_revision'))
            or not set(incoming['matrix']).issubset(summary['matrix'])
            or receipt['remaining'] or not all(receipt['owned_ports_closed'].values())
            or any(net['denied'] for net in incoming['network'].values())):
        raise ValueError('supplement revisions, conditions, guard, or lifecycle do not match')
    records, _, _ = collect(source,incoming)
    for condition in incoming['matrix']:
        for key in selected:
            record = records.get((condition,key),{})
            cell = incoming['matrix'][condition][key]
            if (cell['errors'] or 'port' not in record or
                    'original' in record and record['original']['tone'].get('flet_declared') is None):
                raise ValueError(f'incomplete supplement: {condition}/{key}')
    for condition in incoming['matrix']:
        for key in selected:
            copytree(source/condition/key,root/condition/key,dirs_exist_ok=True)
            summary['matrix'][condition][key] = incoming['matrix'][condition][key]
    summary['screens'] = list(dict.fromkeys([*summary['screens'],*selected]))
    summary.setdefault('supplemental_runs',[]).append(dict(screens=list(selected),conditions=list(incoming['matrix']),
        profiles=incoming['profiles'],
        network=incoming['network'],foreign_blocked=incoming['foreign_blocked'],temporary_run=receipt['run_temp']))
    lifecycle.setdefault('supplemental_runs',[]).append(receipt)
    lifecycle['owned_ports_closed'].update(receipt['owned_ports_closed'])
    for image,count in receipt['observed_by_image'].items():
        lifecycle['observed_by_image'][image] = lifecycle['observed_by_image'].get(image,0) + count
    dump(root/'summary.json',summary)
    dump(root/'lifecycle.json',lifecycle)


def md(value):
    return str(value).replace('|','\\|').replace('\n',' ')


def heading(data):
    rows = data['headings']
    if not rows:
        return '検出0'
    found = []
    for row in rows:
        font = (row['font'] or '継承').split(',')[0].strip('"')
        value = f'{row["size"] or "既定"} / {font} / {row["weight"] or "既定"}'
        if value not in found:
            found.append(value)
    return f'{len(rows)}件 ' + '・'.join(found)


def toolbar(data):
    controls = [control for group in data['toolbars'] for row in group['rows'] for control in row['controls']]
    counts = [len(row) for row in toolbar_rows(controls)]
    return len(counts), max(counts, default=0), counts


def buttons(data):
    return ' / '.join(f'{name} {count}' for name,count in sorted(data['buttons'].items())) or '0件'


def dimensions(path, expected):
    with Image.open(path) as picture:
        if picture.size != expected:
            raise ValueError(f'{path}: expected {expected}, found {picture.size}')
        picture.verify()


def item_metrics(audit):
    tone = audit['tone']
    rows, max_row, _ = toolbar(tone)
    return {'height':tone['in_viewport_height'],'rows':rows,'max_row':max_row,
            'extent':tone.get('content_extent',tone.get('semantic_extent_height'))}


def flet_settings_groups(audit):
    anchors = [(row['text'],row['rect'][1]) for row in audit['semantics']
               if row['text'] in SETTINGS_GROUPS or row['text'].startswith('ver ')]
    return {key:stop-start for (key,start),(_,stop) in zip(anchors,anchors[1:]) if key in SETTINGS_GROUPS}


def related_reference(records, condition, key):
    if key in {'scheduler','metadata','metadata-ledger'}:
        settings = records.get((condition,'settings'),{}).get('original')
        name = '保存ポリシー・通知' if key == 'scheduler' else '作品メタデータ'
        if settings and (extent := flet_settings_groups(settings).get(name)):
            return f'設定・{name}（参考）',dict(height=None,rows=None,max_row=None,extent=extent)
    if key in {'shared-log','shared-log-collapsed'}:
        queue = records.get((condition,'queue'),{}).get('original')
        if queue:
            start = next((row['rect'][1] for row in queue['semantics'] if row['text']=='ダウンロードキューログ'),None)
            end = max((row['rect'][1]+row['rect'][3] for row in queue['semantics']
                       if start is not None and row['rect'][1]>=start),default=None)
            if end is not None:
                return 'キュー内ログ（参考）',dict(height=None,rows=None,max_row=None,extent=end-start)
    return None,None


def collect(root, summary):
    records, errors = {}, []
    counts = Counter()
    for condition, cells in summary['matrix'].items():
        size = tuple(map(int, condition.rsplit('/',1)[1].split('_')[0].split('x')))
        for key, cell in cells.items():
            record = records[condition,key] = {}
            for side in ('original','port'):
                if side in cell['errors']:
                    errors.append(f'{condition} / {key} / {side} — {cell["errors"][side]}')
                if not cell.get(side):
                    continue
                dimensions(root / condition / cell[side]['image'],size)
                record[side] = read(root / condition / cell[side]['audit'])
                if side == 'original' and record[side]['tone'].get('flet_declared') is None:
                    errors.append(f'{condition} / {key} / Flet 宣言値未取得')
                counts[side] += 1
            if cell.get('paired'):
                dimensions(root / condition / cell['paired'], (2*size[0],size[1]))
                counts['paired'] += 1
            folder = root / condition / key
            if key == 'settings' and 'port' in record:
                groups = record['port']['settings_groups']
                for group in groups:
                    dimensions(folder / group['image'], (group['w'],group['h']))
                    read(folder / group['metrics'])
                with Image.open(folder / 'port-all-groups.png') as stitched:
                    stitched.verify()
                counts.update(settings_groups=len(groups),settings_all=1)
    return records, counts, errors


def signed(number, unit=''):
    return '—' if number is None else f'{number:+g}{unit}'


def generate(root):
    summary = read(root / 'summary.json')
    lifecycle = read(root / 'lifecycle.json')
    records, counts, errors = collect(root, summary)
    expected_conditions = {f'{profile}/{size}_dark' for profile in ('normal','empty')
                           for size in ('1280x800','1920x1080')}
    if missing := expected_conditions.difference(summary['matrix']):
        errors.append(f'条件未収録 {", ".join(sorted(missing))}')
    for condition in expected_conditions.intersection(summary['matrix']):
        for key in summary['screens']:
            if (condition,key) not in records or 'port' not in records[condition,key]:
                errors.append(f'{condition} / {key} / NiceGUI 証拠未取得')
    sheets = []
    deltas = []
    for (condition,key), record in records.items():
        if 'port' not in record:
            continue
        left = record.get('original')
        reference, flet = (LABELS[key],item_metrics(left)) if left else related_reference(records,condition,key)
        port = item_metrics(record['port'])
        data = {k:comparison(port[k],flet[k]) for k in port} if flet else dict.fromkeys(port)
        sheet = dict(condition=condition,screen=key,reference=reference,port=port,reference_values=flet,
                     delta=data, paired='original' in record)
        sheets.append(sheet)
        if data['extent'] is not None:
            ratio = 100 * data['extent'] / record['port']['tone']['viewport_height']
            deltas.append(dict(**sheet,extent_delta_pp=round(ratio,2)))
    # One result per screen. Select the largest absolute viewport-normalized change.
    largest = {}
    for item in sorted(deltas,key=lambda row:-abs(row['extent_delta_pp'])):
        largest.setdefault(item['screen'],item)
    top = list(largest.values())[:10]
    dump(root/'tone_comparisons.json',{'counts':dict(counts),'errors':errors,
                                     'screens':sheets,'largest_extent_changes':top})
    lines = ['# UI トンマナ一覧 第1回', '',
             f'NiceGUI `{summary["port_revision"]}` / Flet `{summary["original_revision"]}`', '',
             'Chromium（Edge）、ダーク、1280×800 と 1920×1080、通常と空。すべて隔離データ。写真の左は Flet、右は NiceGUI。', '',
             f'主画面の NiceGUI {counts["port"]} 枚、Flet {counts["original"]} 枚、並置 {counts["paired"]} 枚。'
             f' 設定の全体 {counts["settings_all"]} 枚、グループ別 {counts["settings_groups"]} 枚。', '',
             '各行の高さは「初期表示の本体 / 内側スクロール込みの本体（後者のビューポート高比）」。ポートの本体はタブ領域、ダイアログまたは指定領域。'
             ' Flet の比較値は Chromium のセマンティクス座標の範囲。CanvasKit の実スクロール高・描画済みフォント・枠の CSS 値は取得できないため、宣言値と DOM 座標を別々に記録した。'
             ' 差分の符号は NiceGUI − Flet。専用 Flet 画面がない場合の参照は設計上の比較先。', '',
             'ツールバーの行はコントロール座標の縦方向の重なりから算出。NiceGUI は DOM コンテナごと、Flet はセマンティクスの操作要素を同じ高さの行にまとめた。'
             ' Flet 側でロールなしの入力やチップ型ボタンがあり、行・要素数は観測できた範囲の目安。', '',
             '設定の全体画像は同じ画面をスクロールし、撮影した実ピクセルをつないだもの。グループ画像も同じ撮影から切り出した。', '',
             '設定グループの Flet 参考値は直後のグループの見出しまでの間隔。設定タブ全体の比較とともに、実際の画素境界を見ながら確認できる。', '',
             '## 縦方向の差が大きい10画面', '',
             '| 画面 | 条件 | Flet の参照 | 縦方向の差 | ビューポート比の差 |',
             '|---|---|---|---:|---:|']
    for item in top:
        key, condition, ref = (item[k] for k in ('screen','condition','reference'))
        lines.append(f'| {LABELS[key]} | {condition} | {ref} | '
                     f'{item["reference_values"]["extent"]} → {item["port"]["extent"]} px '
                     f'({signed(item["delta"]["extent"]," px")}) | {signed(item["extent_delta_pp"]," pp")} |')
    sheet_by_key = {(row['condition'],row['screen']):row for row in sheets}
    for condition in summary['matrix']:
        lines.extend(['',f'## {condition}','',
                      '| 画面 | 見出し様式 | ツールバーの行（各行の要素数） | ボタン様式 | 空・表示状態 | 縦の長さ | Flet 基準との差 |',
                      '|---|---|---|---|---|---|---|'])
        for key in summary['screens']:
            record = records.get((condition,key),{})
            label = LABELS[key]
            if 'port' not in record:
                lines.append(f'| {label} | — | — | — | — | — | 撮影情報なし |')
                continue
            a = record['port']['tone']
            sheet = sheet_by_key[condition,key]
            ref = sheet['reference']
            diff = sheet['delta']
            evidence = f'[{label}]({condition}/{key}/port.png)'
            if sheet['paired']:
                evidence += f' [並置]({condition}/{key}/paired.png)'
            _, _, rows = toolbar(a)
            fallback = DEFAULT_STATE.get(key,'設定確認状態') if condition.startswith('empty/') else '通常の合成データ'
            state = ' / '.join(e['label'] for e in a['states'][:2]) or fallback
            extent = f'{a["in_viewport_height"]:g} / {a["content_extent"]} px ({a["content_ratio"]:g})'
            delta = (f'{ref} Δ初期 {signed(diff["height"]," px")}・Δ縦 {signed(diff["extent"]," px")}・'
                     f'Δ行 {signed(diff["rows"])}・Δ最大行 {signed(diff["max_row"])}' if ref else '対応なし')
            lines.append('| '+' | '.join(map(md,(evidence,heading(a),' / '.join(map(str,rows)) or '0行',
                                                 buttons(a),state,extent,delta)))+' |')
        settings = records.get((condition,'settings'),{}).get('port')
        if settings:
            prefix = f'{condition}/settings'
            flet_groups = flet_settings_groups(records[condition,'settings']['original']) if 'original' in records[condition,'settings'] else {}
            lines.extend(['',f'[設定全体]({prefix}/port-all-groups.png)','',
                          '| 設定グループ | 見出し様式 | 行ごとの要素数 | ボタン様式 | 空・表示状態 | 縦の長さ | Flet 基準との差 |',
                          '|---|---|---|---|---|---:|---|'])
            for group in settings['settings_groups']:
                a = read(root / prefix / group['metrics'])
                name = f'[{group["title"]}]({prefix}/{group["image"]})'
                _, _, rows = toolbar(a)
                ref = GROUP_REFS.get(group['title'])
                delta = (f'{ref}の見出し間隔 {flet_groups[ref]} px と比較 {signed(group["h"]-flet_groups[ref]," px")}'
                         if ref in flet_groups else '対応なし')
                lines.append('| '+' | '.join(map(md,(name,heading(a),' / '.join(map(str,rows)) or '0行',
                                                     buttons(a),'設定フォーム',f'{group["h"]} px',delta)))+' |')
    lines.extend(['','## 未取得と実行記録',''])
    lines.extend(f'- {error}' for error in errors)
    lines.extend(['- Flet の実スクロール高・描画済み CSS 値は CanvasKit のため未取得。'
                  ' 実行中の Flet コントロールの宣言値とアクセシビリティ座標は各 original.json に収録。',
                  '- NiceGUI の赤い「本番DB」帯に写るパスは隔離した合成保存先。通常・空は作品等の合成データの区分。'
                  ' 定期チェックは認証待ちの初期表示、メタデータ補完の通常状態は合成の補完結果を入力した。',
                  '- 専用 Flet 画面のない項目は NiceGUI 画像。共有ログの参照は Flet のキュー内ログ。'
                  ' Native のファイル選択画面、実認証・外部送信後の画面、正式な移行実行後の画面は採取対象外。',
                  f'- 起動した子孫プロセスの終了確認。残存 {len(lifecycle["remaining"])} 件。'
                  f' 確認ポート {len(lifecycle["owned_ports_closed"])} 件。 [lifecycle.json](lifecycle.json)',
                  '- 条件、ペア、ブラウザが遮断した外部宛先とサーバー通信ガードの実測。 [summary.json](summary.json)',
                  '- 画面・条件ごとの数値差。 [tone_comparisons.json](tone_comparisons.json)', ''])
    (root/'TONE_MATRIX.md').write_text('\n'.join(lines),encoding='utf-8',newline='\n')
    return {'counts':dict(counts),'errors':errors,'top':[(row['screen'],row['condition'],row['delta']['extent'],
                                                         row['extent_delta_pp']) for row in top]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path)
    parser.add_argument('--supplement',nargs='+',metavar='RUN_OR_SCREEN')
    args = parser.parse_args()
    if args.supplement:
        source, *selected = args.supplement
        if not selected:
            parser.error('--supplement requires at least one screen')
        supplement(args.root,Path(source),selected)
    result = generate(args.root)
    print(json.dumps(result,ensure_ascii=False))
    raise SystemExit(bool(result['errors']))
