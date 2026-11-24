"""Rebuild the per-scenario evidence index with Git source comparisons."""
import argparse
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

from check_inventory_coverage import load_rows

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / 'docs/cos'
OUTPUT = DOCS / 'COS_EVIDENCE_REUSE_20261124.md'
RESULTS = DOCS / 'COS_PREP_TEST_RESULTS_20261124.json'
PROMPT = DOCS / 'COS_FULL_USECASE_PROMPT_20261124.md'
OLD45 = ROOT / 'docs/OLD45_STATUS_2026-11-19.md'
SOURCE = 'src/pixivvault_nicegui/'
SCENARIO = re.compile(r'F-[A-Z]{2}-\d{2}')
REFERENCE = re.compile(r'\btest_[a-zA-Z0-9_]+\.py\b')
APPLIES = '適合（再利用）'
RETEST = '要再試験'
UNKNOWN = '根拠未確定'

# Entrypoints and shared boundaries for the scoped CoS routes. Ledger source references
# are added per row. Modules that did not exist at a candidate still participate in diff.
SURFACE = {
    'ST': 'bootstrap startup_tab native_instance native_startup auth_status ui/shell ui/theme',
    'DL': 'services ui/unified_download_view ui/managed_download_view ui/author_media_view author_media managed_downloads job_service batch_engine',
    'QU': 'services job_service ui/job_views media_jobs',
    'FA': 'services failed_records_state ui/failed_records_view job_service',
    'DE': 'services ui/duplicate_view local_media_adapter managed_quarantine legacy_quarantine duplicate_inspection',
    'LE': 'services ui/learning_view ui/learning_reset discovery_ranking visual_preference_worker visual_preference_store',
    'RK': 'services ui/ranking_view ranking_source request_policy pixiv_read_client owned_https cookie_expiry_refresh',
    'PR': 'services ui/personal_ranking_view personal_ranking external_vault external_vault_transport premium_request_budget',
    'LI': 'services ui/library_view local_media_adapter managed_generations',
    'BM': 'services ui/bookmark_view bookmark_cache_store bookmark_remote_adapter',
    'RE': 'services ui/recommend_view recommendation_acquisition recommendation_state recommendation_media',
    'VW': 'services ui/viewer_controller ui/recommendation_viewer local_media_adapter viewer_exports',
    'AU': 'services metadata_auto ui/metadata_view ui/scheduler_view',
    'SE': 'services preferences ui/preferences_view native_profile',
    'BO': 'services external_vault external_vault_transport ui/external_vault_view ui/premium_gate_view',
    'SP': 'services request_policy managed_network_stop_gate managed_network_stop_shared_scope managed_network_stop_transport ui/request_status_view',
    'NO': 'services ui/shared_log_view notifications',
    'HI': 'services ui/viewer_backup_view ui/view_history_view legacy_copy legacy_backup_history local_media_adapter',
    'OS': 'bootstrap desktop_integration native_startup uri_launch extension_bridge extension_application',
    'ER': 'services ui/shell request_policy job_service',
    'EX': 'services legacy_exports viewer_exports ui/preferences_view',
    'SC': 'services ui/shell bootstrap ui/learning_view ui/theme',
}
PROBES = {
    'F-ST-02': 'q_shell_20261117', 'F-ST-03': 'q_shell_20261117',
    'F-ST-04': 'h02_startup_tab_20261117', 'F-FA-02': 'q17_aggregate_notice_20261117',
    'F-DL-06': 'u11_failmsg', 'F-DE-02': 'p4_x09_original_location_20261119',
    'F-RK-01': 'ranking_source ranking_ui premium_a1_ai_filter',
    'F-RK-02': 'ranking_source ranking_ui premium_a1_ai_filter',
    'F-PR-01': 'personal_ranking external_vault_ui',
    'F-PR-02': 'y_ranking_personal_20261118 personal_ranking',
    'F-LI-01': 'l14_bookmark_count local_media_adapter', 'F-BM-01': 'l14_bookmark_count',
    'F-RE-01': 'task4_cd_r01_r07', 'F-RE-03': 'task4_cd_r01_r07 aa_recommend_superlike_20261118',
    'F-AU-02': 'i_metadata_auto_20261117', 'F-HI-02': 'd5_legacy_discovery_reopen local_media_adapter',
    'F-SP-01': 'managed_network_stop_integration managed_network_stop_gate',
    'F-SP-02': 'b3_recovery_guidance_20261116 managed_network_stop_integration p6_network p4_offline_acceptance_20261119',
    'F-SP-03': 'stop_recovery_status managed_network_stop_integration',
}
SMOKE = {
    'startup': 'F-ST-01 F-ST-02 F-ST-03', 'download': 'F-DL-01 F-DL-03',
    'queue': 'F-QU-01', 'failed': 'F-FA-01', 'history': 'F-HI-01',
    'dedup': 'F-DE-01', 'learning': 'F-LE-01', 'ranking': 'F-RK-01',
    'library': 'F-LI-01', 'bookmarks': 'F-BM-01', 'recommend': 'F-RE-01',
    'personal': 'F-PR-02', 'scheduler': 'F-AU-01', 'metadata': 'F-AU-02',
    'settings': 'F-SE-01',
}
PROOF = (
    ('F-DE-02', '164745bc', 'ごみ箱の件数確認と消去・X09 復元'),
    ('F-LE-04', '4544f921', '視覚学習領域だけの削除・通常履歴保持'),
    ('F-EX-01', '00671c18', 'E01 ZIP の読戻し'),
    ('F-HI-02', '00671c18', '旧 DB 再読込と静止画 61 作品'),
)
FIX = 'verify/uiux-final-20261124/FIX_OUTCOME.md'


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, encoding='utf-8').strip()


@lru_cache(maxsize=None)
def diff(candidate, head, root):
    return set(git('diff', '--name-only', '--no-renames', candidate, head, '--', root).splitlines())


@lru_cache(maxsize=None)
def tree(candidate, root):
    return set(git('ls-tree', '-r', '--name-only', candidate, '--', root).splitlines())


def verdict(passed, candidate, paths, delta):
    if not passed or not candidate or not paths:
        return UNKNOWN
    return RETEST if delta & paths else APPLIES


def coverage():
    prompt = PROMPT.read_text(encoding='utf-8')
    scenarios = dict.fromkeys(re.findall(r'^\| (F-[A-Z]{2}-\d{2}) \|', prompt, re.M))
    links = defaultdict(list)
    for line in prompt.splitlines():
        if match := re.match(r'^\| (FL-[A-Z]+\d+) \| ([^|]+) \|', line):
            for fid in SCENARIO.findall(match[2]):
                links[match[1]].append(fid)
    old_routes = {}
    block = prompt.split('### OLD45', 1)[1].split('### 元 inventory', 1)[0]
    for line in block.splitlines():
        if match := re.match(r'^\| ([A-Z]\d[^|]+) \| ([^|]+) \|', line):
            for key in match[1].split('・'):
                old_routes[key.strip()] = SCENARIO.findall(match[2])
    if (len(scenarios), len(links), len(old_routes)) != (57, 212, 45):
        raise ValueError('Unexpected CoS / ledger / OLD45 denominator')
    return scenarios, links, old_routes


def refs(cell, files):
    recognized = set()
    for name in re.findall(r'(?<![\w/])(?:src/pixivvault_nicegui/)?(?:[a-z_]+/)*[a-z_][\w]*\.py\b', cell):
        matches = {p for p in files if p == name or p.endswith('/' + name)}
        if len(matches) == 1:
            recognized.update(matches)
    return recognized


def note_candidate(note):
    match = re.search(r'\b(?:work|候補|コミット)\s+`?([0-9a-f]{7,40})\b', note)
    return match[1] if match else ''


def capture(path, candidate):
    cases = defaultdict(list)
    for node in ET.parse(path).iter('testcase'):
        module = node.attrib['classname'].split('.')[-1]
        case = node.attrib['name']
        state = next((tag for tag in ('failure', 'error', 'skipped') if node.find(tag) is not None), 'passed')
        if not re.fullmatch(r'test_[A-Za-z0-9_]+', module):
            raise ValueError(f'Unexpected pytest module: {module!r}')
        # Keep the node name without parameter values or failure output.
        cases[module + '.py'].append([case.split('[', 1)[0], state])
    duration = sum(float(s.attrib['time']) for s in ET.parse(path).iter('testsuite'))
    data = dict(candidate=git('rev-parse', candidate), duration_seconds=round(duration, 2),
                cases=dict(sorted(cases.items())))
    RESULTS.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')


def generate(head):
    scenarios, links, old_routes = coverage()
    ledger, _ = load_rows()
    files = tree(head, 'src')
    test_files = {Path(p).name for p in tree(head, 'tests')}
    data = json.loads(RESULTS.read_text(encoding='utf-8'))
    case_counts = {name: Counter(state for _, state in cases) for name, cases in data['cases'].items()}
    by_f = defaultdict(list)
    details = []
    unique = Counter()
    paths_by_f = {}
    for fid in scenarios:
        base = {f'{SOURCE}{name}.py' for name in SURFACE[fid[2:4]].split()}
        paths_by_f[fid] = base & files

    def add(ids, label, scope, candidate, result, paths=(), extra=''):
        paths = set(paths)
        delta = diff(candidate, head, 'src') if candidate else set()
        paths &= files | (tree(candidate, 'src') if candidate else set())
        changed = paths & delta
        state = verdict(result, candidate, paths, delta)
        unique[state] += 1
        index = f'E{len(details)+1:03}'
        for fid in ids:
            if fid not in scenarios:
                raise ValueError(f'Unmapped CoS scenario: {fid}')
            by_f[fid].append((index, state, scope))
        names = '、'.join(sorted(p.removeprefix(SOURCE) for p in paths))
        changes = '、'.join(sorted(p.removeprefix(SOURCE) for p in changed)) or '—'
        details.append((index, '・'.join(ids) or '—', label, scope, f'`{candidate[:12]}`' if candidate else '—',
                        state + (f'（{extra}）' if extra else ''), names or '—', changes))

    def tests(name):
        counters = case_counts.get(name, Counter())
        path = f'tests/{name}'
        retained = bool(counters['passed']) and path in tree(data['candidate'], 'tests') and path in tree(head, 'tests')
        return counters, retained and not counters['failure'] and not counters['error']

    for fid, modules in PROBES.items():
        for module in modules.split():
            name = f'test_{module}.py'
            counts, passed = tests(name)
            add([fid], f'[{name}](../../tests/{name}) / [実行結果]({RESULTS.name})',
                '自動・試験ファイル内の該当アサーション', data['candidate'], passed, paths_by_f[fid],
                '、'.join(f'{state} {count}' for state, count in sorted(counts.items())))

    # Preserve every ledger test citation and its FL-to-CoS route. A textual citation
    # without a result is intentionally unresolved. A focused PASS is a new observation.
    for lid, row in sorted(ledger.items()):
        ids = links[lid]
        paths = refs(row['port'] + ' ' + row['note'], files)
        citation = f'{lid} / [台帳](../FEATURE_LEDGER{"_ADDENDA" if row.get("addenda") else ""}_2026-11-17.md)'
        mentioned = sorted(set(REFERENCE.findall(row['port'] + ' ' + row['note'])))
        for name in mentioned:
            counts, passed = tests(name)
            add(ids, f'{citation} / `{name}`', '台帳記載の自動試験',
                data['candidate'] if passed else note_candidate(row['note']), passed, paths,
                f'該当モジュール {counts["passed"]} PASS' if passed else '合格を示す候補別原ログが未照合')
        if not mentioned:
            add(ids, citation, '台帳の実装・状態欄', note_candidate(row['note']), False, paths,
                '実行合格が結び付いていない')

    for line in OLD45.read_text(encoding='utf-8').splitlines():
        if not line.startswith('| '):
            continue
        cells = [cell.strip() for cell in line.split('|')[1:-1]]
        if len(cells) < 5 or cells[0] not in old_routes:
            continue
        key, _, status, basis, _ = cells[:5]
        candidate = '6ffb60d8' if key == 'U16' else 'a2352403' if status == 'PASS' else ''
        # K01/K02/K09 were promoted by a later real run. Keep its lineage pending
        # unless the older evidence scope can be established separately.
        if key in {'K01', 'K02', 'K09'}:
            candidate = ''
        ids = old_routes[key]
        joined = set().union(*(paths_by_f[fid] for fid in ids))
        added = {p for lid in re.findall(r'FL-[A-Z]+\d+', basis)
                 if lid in ledger for p in refs(ledger[lid]['port'], files)}
        add(ids, f'{key} / [OLD45](../{OLD45.name})', '旧残件の記録された局所判定',
            candidate, status == 'PASS', joined | added, status)

    smoke_path = 'verify/a17-smoke-20261124/summary.json'
    smoke = json.loads((ROOT / smoke_path).read_text(encoding='utf-8'))
    for screen, ids in SMOKE.items():
        ids = ids.split()
        for cell, metrics in smoke['cells'].items():
            if cell.split('|')[0] != screen:
                continue
            paths = set().union(*(paths_by_f[fid] for fid in ids))
            add(ids, f'[`{cell.replace("|", "/")}`](../../{smoke_path})', '隔離 UI セルの表示・到達',
                smoke['evidence']['product_head'], not metrics['failures'], paths,
                '原セル FAIL' if metrics['failures'] else '')
    for fid, candidate, action in PROOF:
        add([fid], '[D5/D9 複製プロファイル](../../verify/d5-proof-20261124/OUTCOME.md)',
            '隔離操作・' + action, candidate, True, paths_by_f[fid])
    for fid, action in (('F-LE-03', '学習セッション中の探索設定'), ('F-SE-02', '学習リセット件数確認')):
        add([fid], f'[最終 UI 再計測](../../{FIX})', '隔離 UI・' + action, 'b4c7f335', True,
            paths_by_f[fid] | {SOURCE + 'ui/learning_reset.py', SOURCE + 'ui/learning_view.py'})
    add(['F-SP-02', 'F-ST-05'], '[競合通知の生成・回復](../../verify/gatebusy-20261124/OUTCOME.md)',
        '隔離操作・監視のみで固定化しないこと', '3d4763c1', True,
        paths_by_f['F-SP-02'] | paths_by_f['F-ST-05'])

    d6 = 'verify/d6-offline-20261124/OUTCOME.md'
    for fid, action, passed in (
        ('F-LE-02', '判定から次画像', True), ('F-LI-01', '作者検索', False),
        ('F-VW-02', '画像送り', True), ('F-RE-01', '先頭画像', False),
        ('F-BM-01', '先頭画像', True),
    ):
        add([fid], f'[D6 隔離オフライン]({("../../" + d6)})',
            '速度比較・' + action, 'fedf4ad5', passed, paths_by_f[fid],
            '中央値条件のみ' if passed else '原比較の中央値未達')
    add(['F-RK-01', 'F-PR-01', 'F-PR-02'],
        '[R8b の反映記録](../OLD45_STATUS_2026-11-19.md#実機確認による更新2026-11-24cos-r8bverifycos_20261124_r8br8b_screen_reviewmd)',
        '実機の期間・個人検索の観測', '', True,
        paths_by_f['F-RK-01'] | paths_by_f['F-PR-01'], '起動時の候補 SHA を原証拠で照合する')

    total = Counter(item[1] for items in by_f.values() for item in items)
    out = [
        '# CoS 試験の既存証拠再利用表（2026-11-24）', '',
        f'比較対象の src tree `{git("rev-parse", head + ":src")}`。'
        '表の「適合」は証拠の観測範囲に限る。実機で見た表示は表示の証拠として使い、'
        '機能は操作結果に対応する自動試験または隔離実証も照合する。', '',
        '候補の src と比較対象の src を Git でパス単位に比較する。'
        '監視するパスは試験経路の入口・共有境界と、台帳の移植版欄から一意に解決できたファイル。'
        'パス不明、候補別結果なし、未実施・失敗は「根拠未確定」。改版は「要再試験」。'
        '表示のみのセルや、一つのアサーションの PASS はユースケース全体を昇格させない。', '',
        'D7 の既存全体結果は `bd98f04f` の 5,678 PASS / 3 SKIP / 3 deselected。'
        '集計には個別 node 一覧がなく、旧全体ログには別実行の FAIL が残るため、'
        '台帳の固有試験名へ全体集計を転記しない。'
        ' [最終集計](../../verify/final-20261124/d7_c_drive_summary.json)'
        ' [初回原ログ](../../verify/final-20261124/pytest_full.log)', '',
        f'証拠行 {len(details)} 件。適合 {unique[APPLIES]}／要再試験 {unique[RETEST]}／根拠未確定 {unique[UNKNOWN]}。'
        f'複数 F-ID に対応する証拠を各 F-ID に計上すると {sum(total.values())} 件。'
        '原台帳 212 項目・OLD45 45 項目の記載を参照。以下は F-ID 57 件の明細。', '',
        '| ユースケース | 適合 | 要再試験 | 根拠未確定 | 証拠番号 |',
        '| --- | ---: | ---: | ---: | --- |',
    ]
    for fid in scenarios:
        items = by_f[fid]
        counts = Counter(item[1] for item in items)
        labels = '、'.join(f'[{idx}](#{idx.lower()})' for idx, _, _ in items)
        out.append(f'| {fid} | {counts[APPLIES]} | {counts[RETEST]} | {counts[UNKNOWN]} | {labels or "—"} |')
    out += ['', '## 証拠ごとの比較', '',
            '| 証拠 | F-ID | 原証拠 | 観測範囲 | 候補 | 判定 | 監視した src 相対パス | 変更あり |',
            '| --- | --- | --- | --- | --- | --- | --- | --- |']
    for idx, *cells in details:
        out.append(f'| <a id="{idx.lower()}"></a>{idx} | ' + ' | '.join(cells) + ' |')
    out += ['', '## 運用', '',
            'Git 上で確認できる証拠と、台帳・OLD45 から参照される試験を対象にした。'
            '原ログ未回収の行は条件を満たす証拠が得られた時点で追記する。'
            '隔離 UI の失敗は保存し、実機の認証・ネイティブ・OS・データ前後照合は監督または本人の結果を追加する。'
            ' 承認済み操作は [開始条件](COS_FULL_START_CONDITIONS_20261124.md) を参照。', '']
    return '\n'.join(out)


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--head', default='HEAD')
    parser.add_argument('--junit', type=Path)
    parser.add_argument('--tested-commit')
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    if bool(args.junit) != bool(args.tested_commit):
        parser.error('--junit and --tested-commit must be supplied together')
    if args.junit:
        capture(args.junit, args.tested_commit)
    head = git('rev-parse', args.head + '^{commit}')
    document = generate(head)
    if args.write:
        OUTPUT.write_text(document, encoding='utf-8', newline='\n')
    elif document != OUTPUT.read_text(encoding='utf-8'):
        raise SystemExit('Evidence table is stale. Run with --write.')
    print(re.search(r'^証拠行 .*$', document, re.M)[0])


if __name__ == '__main__':
    main()
