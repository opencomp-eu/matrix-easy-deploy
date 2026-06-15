# Test package. Redirect bytecode so `tests/__pycache__/` is not created here
# (that directory breaks `python -m unittest tests/*` shell glob expansion).
from __future__ import annotations

import os
from pathlib import Path

_root = Path(__file__).resolve().parents[1]
os.environ.setdefault("PYTHONPYCACHEPREFIX", str(_root / ".pycache"))
