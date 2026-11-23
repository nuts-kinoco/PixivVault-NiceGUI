"""Read back the R2 images and append disjoint repair ownership to the audit."""
from collections import Counter
import json
from pathlib import Path
import re

from PIL import Image

from tools.p5_design_evidence import write_json
from tools.uiux_report import image_link, table


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'verify/uiux-audit-20261122/r2'
OWNERS = (
    ('共通部品・テーマ', ('theme.py', 'shell.py', 'shared_log_view.py', 'request_status_view.py')),
    ('設定・確認ダイアログ', ('preferences_view.py', 'native_profile_view.py', 'premium_gate_view.py',
                          'premium_recovery_view.py', 'shared_get_recovery_view.py',
                          'stop_recovery_view.py', 'stop_migration_view.py')),
    ('ダウンロード・管理タブ', ('unified_download_view.py', 'managed_download_view.py',
                           'download_status_details.py', 'job_views.py', 'failed_records_view.py',
                           'duplicate_view.py', 'scheduler_view.py', 'metadata_view.py')),
    ('ライブラリ・ビューア', ('library_view.py', 'viewer_backup_view.py', 'artwork_viewer.py',
                         'library_filter_panel.py', 'view_history_view.py')),
    ('ランキング・学習・おすすめ', ('ranking_view.py', 'personal_ranking_view.py', 'learning_view.py',
                            'learning_reset.py', 'learning_toast.py', 'ranking_card_menus.py',
                            'recommend_view.py')),
)
REPAIRS = (
    '200% 時のシェルとスクロールを再現し、共通ナビ・通知・フォーカスの状態を再計測。',
    '設定全グループと確認画面で、開閉・取消・主操作・読む順序を再点検。',
    '検索・フィルタ・行操作の当たり判定、長文表示、キーボード到達性を再測定。',
    'カードと詳細ビューの全文手段、補助文、フォーカス復帰を確認。',
    '絞り込み、作品タイトル・説明、読み込み・エラー表示を再測定。',
)


def load(name):
    return json.loads((OUT / name).read_text(encoding='utf-8'))


def owner(row):
    screen, selector = row['screen'], row['selector']
    if (selector == '.pv-content'
            or re.search(r'\[data-testid="(?:tab-|theme-toggle|cookie-banner|auth-status|db-status-banner|pixiv-home)', selector)
            or screen.startswith(('shared-log', 'request-'))):
        return 0
    if screen in {'settings', 'extension', 'extension-confirm', 'communication-result', 'borrow-on',
                  'premium-recovery', 'toast-success', 'toast-error'} or screen.startswith(('startup-', 'stop-')):
        return 1
    if screen.startswith(('download', 'queue', 'failed', 'dedup', 'scheduler', 'metadata')):
        return 2
    if screen in {'library', 'bookmarks', 'history', 'viewer', 'view-history', 'view-history-delete'}:
        return 3
    return 4


def highlights(rows):
    """Keep top findings diverse across criteria and repair owners."""
    selected, seen, per_item = [], set(), Counter()
    ordered = sorted(rows, key=lambda row: row['selector'] != '.pv-content')
    for row in ordered:
        selector = re.sub(r':nth-of-type\(\d+\)', ':nth-of-type(*)', row['selector'])
        # Shared shell issues often repeat on every route and at every size.
        group = owner(row)
        key = (row['item'], group, selector, row['state'])
        if key in seen or per_item[row['item']] >= 2:
            continue
        selected.append(row)
        seen.add(key)
        per_item[row['item']] += 1
        if len(selected) == 15:
            break
    return selected


def verify_images(captures):
    counts, reasons, invalid = Counter(), Counter(), []
    indexed = set()
    plans = {cell['key']: cell for cell in captures['plan']}
    for key, record in captures['captures'].items():
        cell = plans[key]
        if record.get('issue'):
            reasons[record['issue']] += 1
        expected = cell['width'], cell['height']
        images = [(kind, record[kind], expected if kind != 'paired' else (2 * expected[0], expected[1]))
                  for kind in ('product', 'original', 'paired') if record.get(kind)]
        images.extend(('settings-group', image, None) for image in record.get('group_images', ()))
        for kind, name, size in images:
            path = (OUT / name).resolve()
            if not path.is_relative_to(OUT.resolve()):
                raise ValueError('image outside r2')
            indexed.add(path)
            try:
                with Image.open(path) as image:
                    valid = image.format == 'PNG' and (size is None or image.size == size)
                    image.verify()
                if valid:
                    counts[kind] += 1
                else:
                    invalid.append(name)
            except (OSError, SyntaxError):
                invalid.append(name)
    extras = sorted(path.relative_to(OUT).as_posix() for path in OUT.rglob('*.png')
                    if path.resolve() not in indexed)
    for name in extras:
        try:
            with Image.open(OUT / name) as image:
                image.verify()
        except (OSError, SyntaxError):
            invalid.append(name)
    return counts, reasons, invalid, extras


def main():
    data = load('FINDINGS.json')
    captures = load('CAPTURES.json')
    rows = [row for row in data['findings'] if row['status'] == 'violation']
    top = highlights(rows)
    counts, reasons, invalid, extras = verify_images(captures)
    summary = dict(images=counts, capture_issues=reasons, invalid_images=invalid, supplemental_images=extras,
                   by_item=data['by_item'], by_screen=data['by_screen'], coverage=captures['counts'],
                   violations=len(rows), reviews=data['review_count'], highlights=top,
                   photographed_screens=len({key.rsplit('/', 1)[-1] for key, record in captures['captures'].items()
                                               if record.get('product')}),
                   matched_pairs=sum(bool(record.get('paired') and record.get('original_state_proven')
                                          and record.get('state_proven'))
                                     for record in captures['captures'].values()),
                   measured_cells=sum(bool(record.get('measured')) for record in captures['captures'].values()),
                   batches=len(captures['metadata'].get('batches', ())))
    write_json(OUT / 'R2_SUMMARY.json', summary)

    owner_rows = []
    for index, (label, files) in enumerate(OWNERS):
        candidates = [row for row in rows if owner(row) == index]
        actual_files = [file for file in files if (ROOT / 'src/pixivvault_nicegui/ui' / file).is_file()]
        items = Counter(row['item'] for row in candidates)
        owner_rows.append((label, '、'.join(f'`{file}`' for file in actual_files),
                           len(candidates), '、'.join(f'{item} {n}' for item, n in items.most_common()),
                           REPAIRS[index]))
    if len({file for _, files in OWNERS for file in files}) != sum(len(files) for _, files in OWNERS):
        raise ValueError('repair owners overlap')
    path = OUT / 'VIOLATIONS.md'
    body = path.read_text(encoding='utf-8').split('\n## 優先確認 15 件（第 2 回）', 1)[0].rstrip()
    priority = table(['#', '項目', '画面', '条件', '要素', '実測', 'しきい値', '画像'],
                     ((i, row['item'], row['screen'], row['condition'], row['selector'],
                       row['measurement'], row['threshold'], image_link(row['evidence']))
                      for i, row in enumerate(top, 1)))
    assignment = table(['修正単位', '所有ファイル（src/pixivvault_nicegui/ui）', '一次件数', '計測項目', '再検証'],
                       owner_rows)
    path.write_text(f'{body}\n\n## 優先確認 15 件（第 2 回）\n\n{priority}\n\n'
                    '## 修正の割当案\n\n共通由来の所見は共通部品の担当が修正し、各画面の担当が再現条件を返す。'
                    '件数は条件ごとの実測の一次割当。\n\n' + assignment + '\n',
                    encoding='utf-8', newline='\n')
    index_path = OUT / 'IMAGES_INDEX.md'
    index_body = index_path.read_text(encoding='utf-8').split('\n## 再試行前の参照画像', 1)[0].rstrip()
    if extras:
        index_body += ('\n\n## 再試行前の参照画像\n\n現行計測への対応が未確定のため、実測画像数と A16 の成立数から除く。\n\n' +
                       table(['画像'], ((image_link(name, name),) for name in extras)))
    index_path.write_text(index_body + '\n', encoding='utf-8', newline='\n')
    return int(bool(invalid))


if __name__ == '__main__':
    raise SystemExit(main())
