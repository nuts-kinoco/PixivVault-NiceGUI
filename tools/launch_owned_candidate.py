"""Source-checkout URI entry; imports this candidate independent of browser cwd."""
from pathlib import Path
import sys

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
    from pixivvault_nicegui.bootstrap import main
    raise SystemExit(main())
