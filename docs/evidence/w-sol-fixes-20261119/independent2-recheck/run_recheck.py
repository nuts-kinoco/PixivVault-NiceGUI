import os
import sys
import pytest
print(f"pytest pid={os.getpid()} parent={os.getppid()}", flush=True)
raise SystemExit(pytest.main(sys.argv[1:]))