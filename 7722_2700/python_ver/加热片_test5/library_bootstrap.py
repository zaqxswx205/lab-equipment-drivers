"""Resolve the shared driver directory without copying driver sources."""
import os
import sys
from pathlib import Path

MODULES_DIR = Path(os.environ.get("LAB_EQUIPMENT_MODULES", Path(__file__).resolve().parents[3] / "modules")).resolve()
if not all((MODULES_DIR / name).is_file() for name in ("itech7722.py", "keithley2700.py")):
    raise ImportError(f"找不到仪器公共库: {MODULES_DIR}；请设置 LAB_EQUIPMENT_MODULES")
sys.path.insert(0, str(MODULES_DIR))
