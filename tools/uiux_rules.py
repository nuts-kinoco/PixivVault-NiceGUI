"""Classification for the fixed 2026-11-22 UI audit contract."""
from collections import defaultdict
from itertools import combinations
from math import hypot
import re


SOURCES = {
    **dict.fromkeys(('A1', 'A2', 'A3', 'A4', 'A11', 'A12', 'A13', 'A14'), 'HIG Accessibility'),
    **dict.fromkeys(('A5', 'A6', 'A7'), 'HIG Buttons'),
    **dict.fromkeys(('A8', 'A9', 'A10'), 'HIG Typography'),
    'A15': 'UIUX_AUDIT_SPEC_20261122.md §A15',
    'A16': 'UIUX_AUDIT_SPEC_20261122.md §A16/D',
}
ITEMS = tuple(f'A{i}' for i in range(1, 17))
VERBS = re.compile(r'^(保存|再試行|削除|移行|取得|更新|開く|閉じる|取消|取り消|キャンセル|戻る|進む|実行|停止|再開|確認|選択|追加|編集|変更|解除|同期|復元|回復|続け|適用|読み込|表示|接続|検索|開始|中断|選ぶ|前へ|次へ|リセット|コピー|ダウンロード|インポート|エクスポート|スキャン|再読込)')
TERMS = re.compile(r'(エラー|失敗|警告|成功|完了|選択|無効|停止|中断|取消|復旧|^済$|^未$|^ON$|^OFF$)')
SEVERITY = dict.fromkeys(('A1', 'A6', 'A11', 'A12', 'A13', 'A15'), 'High')
MANUAL = {
    'A1': '画像・グラデーション・疑似要素・ネストした透明グループの背景は画素で確認。',
    'A2': '状態と文章・アイコン・形の意味が対応するか、状態の切替前後を確認。',
    'A3': '枠外まで広げた疑似要素の当たり判定とタッチ対象の例外を確認。',
    'A4': '同一操作群か、コンパクトな複合コントロールの一部か確認。',
    'A5': '塗りボタンの主従と、独立した操作群の切り分けを確認。',
    'A6': '全破壊操作の二段階、主操作、取消と実際に変更が起きる時点を確認。',
    'A7': '動詞の語順、タブや選択肢との区別、実際の遷移先を確認。',
    'A8': '補助文扱いの妥当性と、小さな細字の読みやすさを確認。',
    'A9': '読み込まれた実フォント、トークンを経由する指定、見出しの階層を確認。',
    'A10': '日本語長文の折返しとレンダリング上の行間を確認。',
    'A11': '重要操作の順序、縦スクロールでの到達性、200% での内容保持を確認。',
    'A12': '見た目と Tab 順の対応、複合操作の矢印キー、各主操作の Enter を確認。',
    'A13': 'OS 読み上げ、アクセシブル名の意味、状態通知の実際の読み上げを確認。',
    'A14': '通知の重要性、時間経過で消える条件、操作に必要な動きかを確認。',
    'A15': 'ツールチップ・展開がポインターとキーボードで実際に使えるか確認。',
    'A16': '実 UI の状態を写しているかと、作れない状態の理由を確認。',
}


def contrast(foreground, background):
    def luminance(rgb):
        channels = [channel / 255 for channel in rgb[:3]]
        linear = [channel / 12.92 if channel <= .04045 else ((channel + .055) / 1.055) ** 2.4
                  for channel in channels]
        return sum(channel * weight for channel, weight in zip(linear, (.2126, .7152, .0722)))

    dark, light = sorted((luminance(foreground), luminance(background)))
    return (light + .05) / (dark + .05)


def finding(item, selector, measurement, threshold, *, status='violation', severity=None, state='normal'):
    return dict(item=item, selector=selector, measurement=measurement, threshold=threshold,
                source=SOURCES[item], status=status, state=state,
                severity=severity or SEVERITY.get(item, 'Medium'))


def spacing(left, right):
    x, y, w, h = left['rect']
    a, b, c, d = right['rect']
    gap_x, gap_y = max(a - x - w, x - a - c), max(b - y - h, y - b - d)
    return hypot(max(0, gap_x), max(0, gap_y)), gap_x, gap_y


def inspect(snapshot, *, zoom):
    results = []
    controls = defaultdict(list)
    previous_heading = None
    width = snapshot['viewport'][0]
    for node in snapshot['nodes']:
        selector = node['selector']
        state = node.get('state', 'normal')
        text = node.get('text', '')
        size, weight = node.get('fontSize', 0), node.get('weight', 400)

        def add(item, actual, expected, **options):
            results.append(finding(item, selector, actual, expected, state=state, **options))

        if text and size:
            large = size >= 24 or (weight >= 700 and size >= 14 * 96 / 72)
            expected = 3 if large else 4.5
            if node.get('uncertainColor'):
                add('A1', node['uncertainColor'], expected, status='review')
            elif node.get('foreground') and node.get('background'):
                ratio = contrast(node['foreground'], node['background'])
                if ratio < expected:
                    add('A1', round(ratio, 3), expected)
        if node.get('pseudo'):
            continue
        if node.get('indicator') and not (TERMS.search(text) or node.get('icon') or node.get('shape')):
            add('A2', node['indicator'], '文字・形・太さ・アイコンの状態差', status='review')
        if node.get('control'):
            if not node.get('nestedControl'):
                controls[node.get('cluster', 'body')].append(node)
            hit = node.get('hitRect') or node['rect']
            if not node.get('hiddenHit') and min(hit[2:]) < 28:
                add('A3', hit[2:], '28×28 CSS px、最小 20×20',
                    status='violation' if min(hit[2:]) < 20 or node.get('hitRect') else 'review',
                    severity='High' if min(hit[2:]) < 20 else 'Medium')
            if width <= 1024 and min(hit[2:]) < 44 and not node.get('hiddenHit'):
                add('A3', hit[2:], '44×44 CSS px のタッチ目安', status='review', severity='Low')
            if not node.get('name'):
                add('A13', '空の名前', 'アクセシブル名あり',
                    status='review' if node.get('nameFromDomOnly') else 'violation')
            if node.get('tabIndex', 0) < 0 and state != 'disabled' and not node.get('roving'):
                add('A12', 'tabIndex < 0', 'Tab で到達可能',
                    status='review' if node.get('nestedControl') else 'violation')
        if node.get('button'):
            label = node.get('name') or text
            if re.fullmatch(r'(?i)\s*ok\s*', label):
                add('A7', 'OK 単独', '操作内容を示すラベル')
            elif label and node.get('actionLabel') and not VERBS.match(label):
                add('A7', '動詞辞書に一致しない', '動詞で始まる', status='review')
            if node.get('opens') and label and not label.endswith('…'):
                add('A7', '末尾に三点リーダーなし', '遷移・ダイアログを開く操作は …',
                    status='review' if node['opens'] == 'inferred' else 'violation')
        if node.get('danger'):
            if node.get('defaultAction'):
                add('A6', '破壊操作が既定', '破壊操作を既定にしない')
            if not node.get('dangerStyle'):
                add('A6', '破壊的スタイルを識別できない', '破壊的操作を様式で識別', status='review')
            if node.get('inDialog') and not node.get('dialogCancel'):
                add('A6', '取消ボタン候補なし', '確認ダイアログに取消', status='review')
        if text and size:
            minimum = 13 if node.get('bodyText') else 11
            if size < minimum:
                add('A8', size, f'{minimum} CSS px 以上')
            if size <= 13 and weight <= 300:
                add('A8', dict(size=size, weight=weight), '小文字に weight 300 以下を使わない')
            if node.get('fontIssue'):
                add('A9', node['fontIssue'], '見出し Yu Mincho、本文ゴシック', status='review')
            if node.get('sizeIssue'):
                add('A9', size, '既存サイズ段階を使用', status='review')
            if node.get('lines', 0) >= 3 and node.get('lineHeight'):
                ratio = node['lineHeight'] / size
                if ratio < 1.5:
                    add('A10', round(ratio, 3), 1.5)
        if node.get('heading'):
            level = node['heading']
            if previous_heading is not None and level > previous_heading + 1:
                add('A13', [previous_heading, level], '見出しレベルを飛ばさない')
            previous_heading = level
        if node.get('liveMissing'):
            add('A13', '状態領域に live role なし', 'role=status/alert 等', status='review')
        if node.get('dialogMissing'):
            add('A13', 'dialog role なし', 'role=dialog または alertdialog')
        horizontal = node.get('clientWidth', 0) > 0 and node.get('scrollWidth', 0) > node['clientWidth'] + 1
        vertical = node.get('clientHeight', 0) > 0 and node.get('scrollHeight', 0) > node['clientHeight'] + 1
        if node.get('clip') and (horizontal or vertical or node.get('ancestorClip')):
            if (text or node.get('control')) and not node.get('fullText'):
                add('A15', [node.get('clientWidth'), node.get('scrollWidth'),
                            node.get('clientHeight'), node.get('scrollHeight')],
                    'はみ出しなし、または全文手段あり')
        if node.get('ownScroll') and horizontal and not node.get('intentionalScroll'):
            add('A15', [node['clientWidth'], node['scrollWidth']], '意図しない横スクロールなし',
                status='review')
        if zoom == 200 and node.get('control') and node.get('offHorizontal'):
            add('A11', node['rect'], '操作が横方向に切れない',
                status='review' if node.get('intentionalScroll') else 'violation')
    for group in controls.values():
        filled = [node for node in group if node.get('filled') and node.get('state') != 'disabled']
        if len(filled) > 2:
            results.append(finding('A5', filled[0]['cluster'], len(filled), '1–2 個',
                                   status='review' if filled[0].get('clusterInferred') else 'violation'))
        for left, right in combinations(group, 2):
            if left.get('controlContainer') == right['selector'] or right.get('controlContainer') == left['selector']:
                continue
            gap, horizontal, vertical = spacing(left, right)
            expected = 12 if left.get('bordered') and right.get('bordered') else 24
            if min(horizontal, vertical) < 0 and gap < expected:
                results.append(finding('A4', left['selector'], dict(px=round(gap, 2), neighbor=right['selector']),
                                       f'約 {expected} CSS px、接触なし',
                                       status='review' if gap > 0 or left.get('clusterInferred') else 'violation'))
    if snapshot.get('documentWidth', width) > width + 1:
        for item in ('A11', 'A15') if zoom == 200 else ('A15',):
            results.append(finding(item, 'document', [width, snapshot['documentWidth']], '横スクロールなし'))
    return results
