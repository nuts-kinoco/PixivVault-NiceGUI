"""Windows onedir 配布専用入口。"""

from __future__ import annotations

import multiprocessing
import sys
from pathlib import Path


def main() -> int:
    # PyInstaller の子プロセスは bootstrap の import より先に分岐させる。
    multiprocessing.freeze_support()
    from pixivvault_nicegui.bootstrap import main as bootstrap_main

    argv = list(sys.argv[1:])
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
