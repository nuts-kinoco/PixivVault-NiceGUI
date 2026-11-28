"""Bind the original condition IDs to the isolated capture inventory."""
import csv
import hashlib
import json
from pathlib import Path
import re
import sys
from PIL import Image

SCREENS = {
    **{f'U-S{i:02}': name for i, name in enumerate(
        ('download', 'queue', 'failed', 'dedup', 'learning', 'ranking', 'personal',
         'library', 'bookmarks', 'recommend'), 1)},
    'U-S11-backup': 'history', 'U-S12-history': 'view-history',
    'U-S15-settings': 'settings', 'U-S16-settings': 'extension',
    'U-S17-settings': 'premium-recovery', 'U-S18': 'automation',
    'U-S18-automation': 'automation', 'U-S18-scheduler': 'scheduler',
    'U-S19-metadata': 'metadata', 'U-S20': 'request-limit',
    'U-S20-stop-details': 'stop-details', 'U-S26-bulk-confirm': 'queue-delete-confirm',
    'U-S27-row-confirm': 'failed-delete-confirm', 'U-S40-delete-guard': 'view-history-delete',
}


def capture_key(condition):
    match = re.fullmatch(r'(.+)_(1280x800|1920x1080)_(dark|light)_(normal|empty|error|loading)', condition)
    if not match or match[1] not in SCREENS:
        return None
    name, size, theme, state = match.groups()
    return f'{state}/{size}-{theme}-100/{SCREENS[name]}'


def records(conditions, root):
    rows = []
    for condition in conditions:
        key = capture_key(condition)
        path = root / key / 'metrics.json' if key else None
        data = json.loads(path.read_text(encoding='utf-8'))['capture'] if path and path.is_file() else {}
        relative = data.get('product')
        image = (root / relative).resolve() if relative else None
        info = data.get('image', {})
        valid_image = False
        if image and image.is_relative_to(root.resolve()) and image.is_file():
            try:
                with Image.open(image) as picture:
                    valid_image = (list(picture.size) == [info.get('width'), info.get('height')] and
                                   hashlib.sha256(image.read_bytes()).hexdigest() == info.get('sha256'))
            except OSError:
                pass
        bound = bool(data.get('loaded_modules_sha256')) and data.get('loaded_modules_stable') is True
        rows.append(dict(condition=condition, capture_key=key,
                         image=relative if valid_image else None,
                         state_proven=bool(data.get('state_proven') and valid_image and bound),
                         issue=(data.get('issue', '') or ('画像・実行時来歴未確認' if not valid_image or not bound else ''))
                               if data else '状態境界または撮影未実施'))
    return rows


def main():
    source, root, target = map(Path, sys.argv[1:4])
    with source.open(encoding='utf-8-sig', newline='') as f:
        conditions = [row['condition'] for row in csv.DictReader(f)]
    if len(conditions) != 233 or len(set(conditions)) != 233:
        raise ValueError('expected 233 distinct original conditions')
    result = records(conditions, root)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f"conditions={len(result)} with_image={sum(bool(r['image']) for r in result)} "
          f"state_proven={sum(r['state_proven'] for r in result)}")


if __name__ == '__main__':
    main()
