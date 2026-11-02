import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_API = _ROOT / "api"
for _path in (_API, _ROOT):
    if _path.is_dir() and str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
