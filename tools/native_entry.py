"""Windows onedir 配布専用入口。"""

from __future__ import annotations

import multiprocessing
import sys
from pathlib import Path


def main() -> int:
    # PyInstaller の子プロセスは bootstrap の import より先に分岐させる。
    multiprocessing.freeze_support()
    argv = list(sys.argv[1:])
    if '--internal-ugoira-codec' in argv:
        # A codec child must not open another GUI or load the user profile.
        if len(argv) != 2 or argv[0] != '--internal-ugoira-codec':
            return 2
        try:
            from pixivvault_nicegui.ugoira_mp4_worker import main as codec_main
            codec_main(argv[1])
        except Exception:
            return 1
        return 0
    # Keep codec subprocesses independent of desktop profile/owner imports.
    from pixivvault_nicegui.native_instance import NativeInstanceError, NativeAppRuntimeError
    from pixivvault_nicegui.native_profile import NativeProfileError
    try:
        return _regular_entry(argv)
    except SystemExit as error:
        # argparse.error() raises SystemExit(2), not ValueError. A packaged
        # --noconsole URI launch must show the same fixed, privacy-safe
        # refusal as other startup failures instead of silently disappearing.
        # In source diagnostics keep argparse's original precise exception,
        # including the existing --check-output validation contract.
        if not getattr(sys, 'frozen', False):
            raise
        # Preserve argparse's successful --help/exit(0) behavior in builds.
        if error.code is None or error.code == 0:
            return 0
        return _report_startup_rejected()
    except NativeAppRuntimeError:
        return _report_runtime_failed()
    except (NativeInstanceError, NativeProfileError, ValueError, OSError):
        # An expected admission refusal must never reach PyInstaller's
        # windowed unhandled-exception dialog with an internal traceback.
        # Keep the user message fixed; never echo paths, credentials or errors.
        return _report_startup_rejected()


def _report_startup_rejected() -> int:
    if sys.platform == 'win32' and getattr(sys, 'frozen', False):
        import ctypes
        try:
            window = ctypes.WinDLL('user32', use_last_error=True).MessageBoxW
            window.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                               ctypes.c_wchar_p, ctypes.c_uint]
            window.restype = ctypes.c_int
            window(None, '起動設定、または既に開いているPixivVaultを確認してください。'
                   ' 必要なら設定を確認して再起動してください。',
                   'PixivVault — 起動を確認できません', 0x30)
        except Exception:
            pass  # A safe nonzero exit is preferable to a second traceback.
    return 2


def _report_runtime_failed() -> int:
    if sys.platform == 'win32' and getattr(sys, 'frozen', False):
        import ctypes
        try:
            window = ctypes.WinDLL('user32', use_last_error=True).MessageBoxW
            window.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                               ctypes.c_wchar_p, ctypes.c_uint]
            window.restype = ctypes.c_int
            window(None, 'アプリの実行または終了処理を確認できません。'
                   ' 再起動の前に既存ウィンドウを確認してください。',
                   'PixivVault — 実行中に問題が発生しました', 0x30)
        except Exception:
            pass
    return 3


def _regular_entry(argv) -> int:
    from pixivvault_nicegui.native_startup import native_entry_arguments
    argv = native_entry_arguments(argv)
    from pixivvault_nicegui.bootstrap import main as bootstrap_main

    output_path = None
    if "--check-output" in argv:
        index = argv.index("--check-output")
        if index + 1 >= len(argv):
            raise SystemExit("--check-output には絶対パスを指定してください")
        output_path = Path(argv[index + 1])
        if not output_path.is_absolute():
            raise SystemExit("--check-output には絶対パスを指定してください")
        del argv[index:index + 2]
        if "--check" not in argv:
            raise SystemExit("--check-output は --check と組み合わせてください")
    if "--native" not in argv:
        argv.append("--native")
    if output_path is not None:
        # 指定先の親ディレクトリは呼び出し側が明示的に作成する。
        with output_path.open("w", encoding="utf-8") as output:
            saved_stdout = sys.stdout
            try:
                sys.stdout = output
                return bootstrap_main(argv)
            finally:
                sys.stdout = saved_stdout
    if "--check" in argv and sys.stdout is None:
        # Windows GUI subsystem: 出力先を指定しなければ診断の終了コードだけ返す。
        import io

        with io.StringIO() as sink:
            saved_stdout = sys.stdout
            try:
                sys.stdout = sink
                return bootstrap_main(argv)
            finally:
                sys.stdout = saved_stdout
    return bootstrap_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
