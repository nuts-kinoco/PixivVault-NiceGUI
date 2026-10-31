"""Launch *real* original Flet gui.py with a truly empty generated failed queue.

Unlike a no-match filter over seventeen records, this uses the existing
inert FakeDB.get_failed_jobs() == [] of the Flet source-copy harness.
Original source files are OS-pinned against writes for the entire GUI run.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import check_stage5_original_failed17_native as original


def run(out: Path) -> int:
    out = out.resolve()
    if out.exists() or not out.is_relative_to((ROOT / 'artifacts').resolve()):
        raise ValueError('fresh synthetic-only original Flet empty GUI root required')
    before = original.read_pinned_source()
    sys.path.insert(0, str(original.HERE))
    import builder_probe
    import import_guard
    import launch_true_gui

    # Never replace this with the 17-row adapter: true-empty and no-match
    # are mutually exclusive source conditions in gui.py.
    if (builder_probe.FakeDB().get_failed_jobs() != []
            or builder_probe.FakeDB().db_path.startswith('S:/Projects/PixivVault/')):
        raise RuntimeError('original Flet empty FakeDB invariant unavailable')

    previous_guard = import_guard.apply_critical_guards
    previous_bytecode = sys.dont_write_bytecode
    failure = None

    def add_source_write_guard(*args, **kwargs):
        previous_guard(*args, **kwargs)
        sys.addaudithook(original.source_copy_write_denial)

    with original.pinned_win32_sources(
        [original.SOURCE / name for name in sorted(before)],
        require_single_link=True,
    ):
        if original.read_pinned_source() != before:
            raise RuntimeError('original source changed between SHA and OS pin')
        sys.dont_write_bytecode = True
        import_guard.apply_critical_guards = add_source_write_guard
        try:
            # The actual gui.py.main_window, all 8 source tabs, native Flet.
            launch_true_gui.run(out)
        except Exception as exc:
            failure = {'class': type(exc).__name__, 'excerpt': str(exc)[:240]}
        finally:
            sys.dont_write_bytecode = previous_bytecode
            import_guard.apply_critical_guards = previous_guard
        after = original.read_pinned_source()
    if not out.is_dir():
        raise RuntimeError('original native GUI did not create owned result root')

    guard = {
        'schema': 1,
        'scope': 'real original Flet gui.py true empty failed_queue synthetic FakeDB',
        'source_gui_sha256': original.EXACT_GUI_SHA,
        'source_count': len(before), 'source_unchanged': before == after,
        'generated_failure_count': 0,
        'original_data_cookie_used': False,
        'real_original_gui_returned': failure is None,
        'failure': failure,
        'formal_pass_added': 0,
        'full_original_feature_parity': False,
    }
    with (out / 'generated-failed-empty-source-guard.json').open('x', encoding='utf8') as stream:
        json.dump(guard, stream, ensure_ascii=False, indent=2)
    print(json.dumps({
        'real_original_gui_returned': failure is None,
        'source_unchanged': before == after,
        'generated_failure_count': 0,
        'formal_pass_added': 0,
    }))
    if failure or before != after:
        raise RuntimeError('real original true-empty GUI ended in failure or source change')
    return 0


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('usage: check_stage5_original_failed_empty_native.py NEW_ROOT')
    raise SystemExit(run(Path(sys.argv[1])))
