"""Compatibility entry point; all browser checks use the single tested runner."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.run_browser_checks import main

if __name__ == "__main__":
    raise SystemExit(main())
