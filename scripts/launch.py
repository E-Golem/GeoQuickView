"""A windowed launcher with useful diagnostics instead of a hidden traceback."""
import sys
import tempfile
from pathlib import Path
import traceback

if __name__ == "__main__":
    try:
        from geoquickview.__main__ import main
        raise SystemExit(main())
    except Exception:
        details = traceback.format_exc()
        directory = Path(tempfile.gettempdir())/"GeoQuickView"
        directory.mkdir(exist_ok=True)
        (directory/"crash.log").write_text(details, encoding="utf-8")
        if "--self-check" not in sys.argv and "--smoke-test" not in sys.argv:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, f"程序未能启动。\n诊断信息：{directory / 'crash.log'}", "GeoQuickView", 16)
        raise SystemExit(1)
