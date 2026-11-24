"""Build labeled contact sheets from isolated UI audit and P5 captures."""
import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from tools.p5_design_evidence import ORIGINAL_JAPANESE_FONT, write_json
from tools.uiux_image_area import LABELS


NAMES = {
    **LABELS, 'download': 'ダウンロード', 'library': 'ライブラリ',
    'history': '直近のバックアップ', 'settings': '設定', 'startup-and-storage': '起動と保存先',
    'startup-reference': '起動：保存先の参照', 'startup-cookie-rejected': '起動：ログイン確認',
    'startup-lock-busy': '起動：保存先の使用中', 'startup-legacy-uncertain': '起動：旧データの確認',
    'startup-legacy-copy-error': '起動：コピー失敗', 'startup-stop-migration': '起動：停止履歴の移行',
    'stop-history-guide': '停止履歴の確認', 'stop-migration-dialog': '停止履歴の移行',
    'stop-recovery': '取得停止の確認・回復', 'stop-uncertain': '取得停止状態の確認',
    'borrow-on': '借用プレミアムを有効にする', 'premium-recovery': '借用プレミアムの回復',
    'communication-result': '通信結果の確認', 'request-wait': '通信の再試行待ち',
    'request-limit': '通信制限', 'shared-log': '共有ログ', 'shared-log-collapsed': '共有ログ（折りたたみ）',
    'metadata-ledger': 'メタデータ台帳の詳細', 'extension': 'ブラウザー拡張機能',
    'extension-confirm': 'ブラウザー拡張機能の確認', 'toast-success': '成功通知', 'toast-error': 'エラー通知',
    'ranking-date': 'ランキングの日付選択', 'personal-filters': 'カスタムランキングの絞り込み',
    'dedup-trash': '重複整理のごみ箱', 'learning-settings': '学習の設定',
    'learning-review': '学習の保留一覧', 'learning-reset': '学習判定の消去確認',
    'download-details': 'ダウンロードの詳細', 'viewer': '作品ビューア',
    'queue-delete-confirm': 'キューの削除確認', 'failed-delete-confirm': '失敗リストの削除確認',
    'view-history-delete': '閲覧履歴の削除確認',
}
STATES = {'normal': '通常', 'error': 'エラー', 'loading': '待機', 'empty': '空', 'long': '長文'}
THEMES = {'light': 'ライト', 'dark': 'ダーク'}
PAIR_SCREENS = ('download', 'queue', 'failed', 'dedup', 'learning', 'ranking', 'personal',
                'library', 'bookmarks', 'recommend', 'history', 'settings')
CONDITIONS = [(w, h, theme) for w, h in ((1280, 800), (1920, 1080)) for theme in THEMES]


def contact_sheets(entries, output, stem, *, paired=False):
    font = ImageFont.truetype(str(ORIGINAL_JAPANESE_FONT), size=23)
    heading = ImageFont.truetype(str(ORIGINAL_JAPANESE_FONT), size=26)
    width, gap = 2400, 20
    columns = 1 if paired else 2
    per_sheet = columns * 4
    tile_width = (width - (columns + 1) * gap) // columns
    max_height = 738
    row_height = max_height + 43
    manifest = []
    for offset in range(0, len(entries), per_sheet):
        batch = entries[offset:offset + per_sheet]
        height = 64 + ((len(batch) + columns - 1) // columns) * (row_height + gap)
        canvas = Image.new('RGB', (width, height), '#1c222b')
        draw = ImageDraw.Draw(canvas)
        title = '既存並置　左 Flet ／ 右 NiceGUI' if paired else 'PixivVault NiceGUI　隔離 UI 監査'
        draw.text((gap, 12), title, fill='#ffffff', font=heading)
        for index, entry in enumerate(batch):
            x = gap + index % columns * (tile_width + gap)
            y = 64 + index // columns * (row_height + gap)
            draw.text((x, y), entry['label'], fill='#ffffff', font=font)
            if entry['captured']:
                with Image.open(entry['path']) as source:
                    if source.size != entry['size']:
                        raise ValueError(f"unexpected source dimensions: {entry['source']}")
                    image = ImageOps.contain(source.convert('RGB'), (tile_width, max_height),
                                             Image.Resampling.LANCZOS)
                    canvas.paste(image, (x, y + 36))
            else:
                draw.text((x, y + 55), '撮影未取得', fill='#fff2bb', font=font)
        path = output / f'{stem}-{offset // per_sheet + 1:02d}.jpg'
        canvas.save(path, quality=91, subsampling=0, optimize=True)
        with Image.open(path) as saved:
            saved.verify()
        manifest.append(dict(sheet=path.name, width=width, height=height,
                             cells=[{k: v for k, v in entry.items() if k != 'path'} for entry in batch]))
    return manifest


def entries_for(index, roots):
    for w, h, theme in CONDITIONS:
        for cell in index['plan']:
            if (cell['width'], cell['height'], cell['theme'], cell['zoom']) != (w, h, theme, 100):
                continue
            key = cell['key']
            record, base = {}, roots[-1][0]
            for folder, captures in roots:
                if key in captures:
                    record, base = captures[key], folder
                    break
            source = record.get('product') or f'{key}/port.png'
            label = f"{NAMES[cell['screen']]}　{w}×{h}　{THEMES[theme]}　{STATES[cell['state']]}"
            if record.get('product') and not record.get('state_proven'):
                label += '　状態未確認'
            yield dict(label=label, source=f'{base.name}/{source}', path=base / source,
                       size=(w, h), captured=bool(record.get('product')),
                       state_proven=bool(record.get('state_proven')))


def pair_entries(p5):
    index = json.loads((p5 / 'capture.json').read_text(encoding='utf-8'))
    for w, h, theme in CONDITIONS:
        condition = f'matrix-r1/normal/{w}x{h}_{theme}'
        for key in PAIR_SCREENS:
            source = f"{condition}/{index['matrix'][condition][key]['paired']}"
            yield dict(label=f'{NAMES[key]}　{w}×{h}　{THEMES[theme]}　通常',
                       source=source, path=p5 / source, size=(2 * w, h), captured=True)


def settings_entries(roots):
    for w, h, theme in CONDITIONS:
        key = f'normal/{w}x{h}-{theme}-100/settings'
        for base, captures in roots:
            record = captures.get(key, {})
            if record.get('group_titles'):
                break
        for source in record.get('group_images', ()):
            title = record['group_titles'].get(Path(source).name)
            if title:
                path = base / source
                with Image.open(path) as image:
                    size = image.size
                yield dict(label=f'設定・{title}　{w}×{h}　{THEMES[theme]}',
                           source=f'{base.name}/{source}', path=path, size=size, captured=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--remeasured', type=Path, action='append', default=[])
    parser.add_argument('--p5', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    baseline = json.loads((args.baseline / 'CAPTURES.json').read_text(encoding='utf-8'))
    roots = [(folder, json.loads((folder / 'CAPTURES.json').read_text(encoding='utf-8'))['captures'])
             for folder in [*args.remeasured, args.baseline]]
    product = contact_sheets(list(entries_for(baseline, roots)), args.out, 'nicegui')
    pairs = contact_sheets(list(pair_entries(args.p5)), args.out, 'flet-nicegui', paired=True)
    settings = contact_sheets(list(settings_entries(roots)), args.out, 'settings')
    p5 = json.loads((args.p5 / 'capture.json').read_text(encoding='utf-8'))
    write_json(args.out / 'index.json', {
        'baseline_revision': baseline['metadata']['product_revision'],
        'remeasured_revisions': {folder.name: json.loads((folder / 'CAPTURES.json').read_text(encoding='utf-8'))
                                 ['metadata']['product_revision'] for folder in args.remeasured},
        'pair_revisions': {key: p5[key] for key in ('original', 'port')},
        'product': product, 'pairs': pairs, 'settings': settings,
    })


if __name__ == '__main__':
    main()
