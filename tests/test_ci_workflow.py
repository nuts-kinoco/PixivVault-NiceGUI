"""同名ジョブの重複でGitHub Actions全体が無効になる回帰を検査する。"""

from pathlib import Path
import re


def test_unique_jobs_and_explicit_browser_execution():
    workflow = Path(__file__).resolve().parents[1] / '.github/workflows/ci.yml'
    source = workflow.read_text(encoding='utf-8')
    jobs = source.split('\njobs:\n', 1)[1]
    names = re.findall(r'^  ([a-zA-Z_][\w-]*):\s*$', jobs, re.MULTILINE)
    assert names and len(names) == len(set(names)), names
    assert {'tests', 'browser'} <= set(names)
    assert 'python tools/run_browser_checks.py --channel chromium' in jobs
    assert 'python tools/check_learning_persistence_browser.py --channel chromium' in jobs
    assert 'artifacts/browser-ci/**/*.png' in jobs
    assert 'artifacts/browser-ci/**/*.json' in jobs
    assert 'path: artifacts/browser-ci/' not in jobs
    assert 'python -m pytest -q' in jobs
