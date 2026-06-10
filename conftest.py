"""Root conftest.py — add relocated subdirectory paths to sys.path.

After the refactoring in commit 3108776 (루트 파일 6개폴더로 분류 정리),
modules were moved to bot/, reports/, and backtest/ subdirectories.
Test files that import them directly (without the package prefix) need
these directories on sys.path so pytest can discover the modules.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).parent

for _subdir in ("bot", "reports", "backtest"):
    _p = str(_ROOT / _subdir)
    if _p not in sys.path:
        sys.path.insert(0, _p)
