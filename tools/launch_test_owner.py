"""Explicit source test entry. All app paths must be below this workspace.

Create a new empty workspace with --init-workspace. Launch with
--test-workspace ABSOLUTE_PATH -- followed by ordinary application arguments.
The application never creates a shared owner automatically: use its migration UI.
"""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--init-workspace', type=Path)
    parser.add_argument('--test-workspace', type=Path)
    parser.add_argument('app_args', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.init_workspace is not None:
        root = args.init_workspace
        if args.test_workspace or args.app_args or not root.is_absolute():
            parser.error('initialize a new absolute test workspace only')
        from pixivvault_nicegui.managed_network_stop_gate import _reject_links
        _reject_links(root)
        root.mkdir(exist_ok=False)
        (root / '.pixivvault-isolated-test-workspace').write_bytes(
            b'PixivVault isolated source test workspace v1\n')
        return 0
    if args.test_workspace is None:
        parser.error('--test-workspace required')
    from pixivvault_nicegui.test_owner_runtime import isolated_test_workspace
    from pixivvault_nicegui.bootstrap import main as app_main
    app_args = args.app_args
    if app_args[:1] == ['--']:
        app_args = app_args[1:]
    with isolated_test_workspace(args.test_workspace):
        return app_main(app_args)

if __name__ == '__main__':
    raise SystemExit(main())
