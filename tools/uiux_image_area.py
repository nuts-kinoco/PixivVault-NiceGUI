"""A17 measurement targets for the current product and its normal-state matrix."""

import json
from pathlib import Path

SCRIPT = Path(__file__).with_suffix('.js').read_text(encoding='utf-8')
SIZES = ((1024, 768, 100), (1280, 800, 100), (1920, 1080, 100), (1950, 1030, 100))
THRESHOLDS = {(1280, 800): .55, (1920, 1080): .60}

LABELS = {
    'download': 'ダウンロード（作者一覧）', 'queue': 'キュー', 'failed': '失敗リスト',
    'dedup': '重複整理', 'learning': '学習', 'ranking': 'ランキング',
    'personal': 'カスタムランキング', 'library': 'ライブラリ（作者一覧）',
    'library-works': 'ライブラリ（作品一覧）', 'bookmarks': 'マイブックマーク',
    'recommend': 'おすすめ', 'view-history': '閲覧履歴', 'metadata': 'メタデータ補完',
    'scheduler': '定期チェック',
}
KINDS = {'learning': 'learning', 'download': 'authors', 'queue': 'text',
         'failed': 'text', 'metadata': 'status', 'scheduler': 'status'}
CHROME = {
    'banner': ('[data-testid=db-status-banner]', '[data-testid=cookie-banner]'),
    'header': ('.pv-header .pv-title', '[data-testid=auth-status]', '.pv-header-actions'),
    'api': ('.pv-conn-row', '.pv-header [data-testid=request-retry-status]'),
    'tabs': ('.pv-nav',),
    'heading': ('.pv-content .pv-library-heading', '.pv-content .pv-serif-heading',
                '.pv-dialog-title', '.pv-library-detail-header', '.pv-ud-title'),
    'toolbar': ('.pv-content .pv-manual-toolbar', '.pv-content .pv-toolbar:not(:has([data-testid=gallery-search]))',
                '.pv-library-summary', '.pv-library-subviews', '.pv-failure-toolbar',
                '.pv-ud-moderow', '.pv-ud-tools', '.pv-ud-note', '.pv-ud-thead',
                '.pv-learning-swipe .pv-toolbar', '.pv-recommend-shelf-header', '.pv-recommend-trends'),
    'candidates': ('[data-testid=personal-candidates-row]', '[data-testid=personal-chips]'),
    'status': ('.pv-content [role=status]', '.pv-content .pv-counter', '.pv-personal-status',
               '[data-testid=view-history-status]'),
    'search': ('.pv-toolbar:has([data-testid=gallery-search])', '[data-testid=library-search]',
               '[data-testid=bookmark-search]', '[data-testid=failed-search]', '[data-testid=unified-search]'),
}
TARGETS = {
    'download': ('[data-testid=unified-author-list]', '[data-testid=unified-author-row]', None),
    'queue': ('[data-testid=queue-list]', '.pv-job-row', None),
    'failed': ('[data-testid=failed-rows]', '[data-testid=failed-record]', None),
    'dedup': ('.pv-dedup-list', '[data-testid=duplicate-near-group]', '[data-testid=duplicate-thumbnail] .q-img__image'),
    'learning': ('.pv-learning-art-frame', 'img[data-pv-displayed=true]', 'img[data-pv-displayed=true]'),
    'ranking': ('[data-testid=ranking-view] [data-testid=gallery-scroll]', '.pv-card', '.pv-card img'),
    'personal': ('[data-testid=personal-ranking-view] [data-testid=gallery-scroll]', '.pv-card', '.pv-card img'),
    'library': ('[data-testid=library-authors-scroll]', '.pv-card', '.pv-card img'),
    'library-works': ('[data-testid=library-pages-scroll]', '.pv-card', '.pv-card img'),
    'bookmarks': ('[data-testid=bookmark-scroll]', '.pv-card', '.pv-card img'),
    'recommend': ('.pv-recommend-shelves', '[data-testid^=recommend-work-]',
                  '[data-testid^=recommend-thumb-] .q-img__image'),
    'view-history': ('.pv-tone-b-history-list', '[data-testid=view-history-row]', '.pv-tone-b-thumb .q-img__image'),
    'metadata': (None, None, None),
    'scheduler': (None, None, None),
}


def measure_image_area(page, target):
    return page.evaluate(SCRIPT, target)


def target_for(screen):
    region, item, image = TARGETS[screen]
    root = {'view-history': '.pv-tone-b-history-card', 'metadata': '[data-testid=metadata-view]',
            'scheduler': '[data-testid=scheduler-view]'}.get(screen, '.pv-content')
    return dict(region=region, item=item, image=image, chrome=CHROME, root=root,
                start=item if screen == 'recommend' else None, single=screen == 'learning',
                chromeLimit=region is not None)


def result_for(cell, metrics):
    kind = KINDS.get(cell.screen, 'gallery')
    if kind in {'text', 'status'}:
        return '対象外'
    if metrics is None or metrics['area_ratio'] is None or not metrics['items']:
        return '未確認'
    if kind == 'learning':
        return '学習基準' if metrics['visible_images'] else '未確認'
    if kind == 'gallery' and not metrics['rendered_images']:
        return '未確認'
    threshold = THRESHOLDS.get((cell.width, cell.height))
    if threshold is None:
        return '参考'
    return '達成' if metrics['area_ratio'] >= threshold else '未達'


def proven(cell, record):
    metrics = record.get('image_area')
    return bool(record.get('product') and record.get('state_proven') and metrics and
                result_for(cell, metrics) != '未確認' and
                (not cell.original or (record.get('paired') and record.get('original_state_proven'))))


def write_report(out, matrix, records, metadata):
    def num(value):
        return '―' if value is None else f'{value:.1f}'

    def percent(value):
        return '―' if value is None else f'{value:.1%}'

    def link(record, key, text):
        return f'[{text}]({record[key]})' if key in record else '―'

    rows = []
    lines = [
        '# A17 画像・カード領域の現状計測', '',
        f"移植版 src `{metadata['product_tree']}`。元 Flet `{metadata['original_revision']}`。",
        '隔離した合成データ、100% ズーム、CSS px。全 14 画面・表示種別 × 4 サイズ × 2 テーマ = 112 条件。', '',
        '領域は一覧スクロール枠のクライアント矩形を上位のスクロール枠・ビューポートで切り取った面積。'
        'おすすめは共用スクロール枠の先頭カード以降。学習は contain 後の画像実寸。閲覧履歴はモーダル内の一覧。'
        '画像先頭 y はウィンドウ上端からの座標。未読込なら ―。', '',
        'ダウンロードは作者一覧枠を代理指標として判定。キュー・失敗リストの割合は文字一覧の参考値。'
        'メタデータ補完・定期チェックは操作と状態のみ。画像付き一覧の面積判定は対象外。'
        '1024×768 と 1950×1030 には暫定しきい値の指定なし。', '',
        'クロームの内訳は領域開始までの各要素の高さ。同じ縦位置に並ぶ要素は区分をまたいで重複する。'
        '未割当には余白、未分類の行、モーダルの上方の空間を含む。要素単位の矩形は [IMAGE_AREA.json](IMAGE_AREA.json) に記録。', '',
        '| 条件 | 画面 | 領域 % | 画像 y | 領域 y | バナー | ヘッダー | API | タブ | 見出し | 操作 | 候補 | 状態 | 検索 | 未割当 | 判定 | 画像 | 元 Flet と並置 |',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|',
    ]
    for cell in matrix:
        record = records.get(cell.key, {})
        metrics = record.get('image_area') or {}
        status = result_for(cell, metrics or None)
        if not record.get('state_proven'):
            status = '未確認'
        rect = metrics.get('visible_rect')
        chrome = [num(metrics.get('chrome', {}).get(name, {}).get('height')) for name in CHROME]
        fields = [f'{cell.width}×{cell.height} {cell.theme}', LABELS[cell.screen],
                  percent(metrics.get('area_ratio')), num(metrics.get('first_image_y')),
                  num(rect[1] if rect else None), *chrome,
                  num(metrics.get('chrome_unassigned_height')), status, link(record, 'product', '移植版'),
                  link(record, 'paired', '並置') if cell.original else '専用タブなし']
        lines.append('| ' + ' | '.join(fields) + ' |')
        rows.append(dict(cell=cell.to_dict(), status=status, **record))
    lines += ['', '## 状態を確認できなかった条件', '']
    for cell, row in zip(matrix, rows):
        if not proven(cell, row):
            lines.append(f"- {cell.key} · {row.get('issue', '画像・一覧または並置の成立を確認できない。')}")
    (out / 'IMAGE_AREA.md').write_text('\n'.join(lines) + '\n', encoding='utf-8', newline='\n')
    (out / 'IMAGE_AREA.json').write_text(
        json.dumps(dict(metadata=metadata, rows=rows), ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8', newline='\n')
