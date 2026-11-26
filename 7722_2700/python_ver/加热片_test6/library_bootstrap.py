"""Load instrument drivers bundled with this project."""
import sys
from pathlib import Path

MODULES_DIR = Path(__file__).resolve().parent / "modules"
if not all((MODULES_DIR / name).is_file() for name in ("itech7722.py", "keithley2700.py")):
    raise ImportError(f"找不到项目内仪器库: {MODULES_DIR}；请确认 itech7722.py 和 keithley2700.py 完整")
sys.path.insert(0, str(MODULES_DIR))
