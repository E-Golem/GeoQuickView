"""Exercise real Qt controls and worker completion, without dialogs or network."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import json
import sys
import tempfile
import time

import geoquickview
from PySide6 import QtCore, QtGui, QtWidgets as W, QtTest
from geoquickview.app import MainWindow
from geoquickview.data import inspect
from geoquickview.samples import create_samples

application = W.QApplication([])
for filename in ("msyh.ttc", "segoeui.ttf"):
    path = Path(os.environ.get("WINDIR", "C:/Windows"))/"Fonts"/filename
    if path.is_file():
        QtGui.QFontDatabase.addApplicationFont(str(path))
errors = []
def capture_exception(kind, value, tb):
    errors.append(f"{kind.__name__}: {value}")
    sys.__excepthook__(kind, value, tb)
sys.excepthook = capture_exception
window = MainWindow()
window.show_error = errors.append
window.show()
checks = []

def wait():
    deadline = time.monotonic()+25
    while time.monotonic() < deadline:
        application.processEvents()
        QtTest.QTest.qWait(25)
        if not window.jobs and not window.rendering and not window.render_timer.isActive():
            return
    raise AssertionError("Qt worker timeout")

with tempfile.TemporaryDirectory() as directory:
    files = create_samples(directory)
    window.add_layer(inspect(files["raster"]))
    wait()
    assert window.selected()["frame"].data.ndim == 2
    checks.append("raster open and preview")
    window.selector.setCurrentIndex(1)
    wait()
    assert window.selected()["selection"]["bands"] == [2]
    checks.append("band selector")
    window.rgb_enabled.setChecked(True)
    wait()
    assert window.selected()["frame"].data.shape[-1] == 3
    checks.append("RGB")
    window.rgb_enabled.setChecked(False)
    wait()
    window.lower.setValue(280)
    window.upper.setValue(320)
    window.style_changed()
    assert window.selected()["style"]["lower"] == 280
    checks.append("manual colour limits")
    window.layers.currentItem().setCheckState(QtCore.Qt.CheckState.Unchecked)
    wait()
    window.style_changed()
    assert not any(item.isVisible() for item in window.selected()["items"])
    window.layers.currentItem().setCheckState(QtCore.Qt.CheckState.Checked)
    wait()
    checks.append("visibility survives style changes")
    window.add_layer(inspect(files["nc"]))
    wait()
    assert set(window.dimension_spins) == {"time", "level"}
    assert window.selected()["shown_metadata"]["当前变量空间属性"]["X/Y 分辨率"] == [5., 5.]
    window.dimension_spins["time"].setValue(1)
    window.dimension_spins["level"].setValue(1)
    wait()
    assert window.selected()["selection"]["indices"] == {"time": 1, "level": 1}
    assert "2026-01-02" in window.dimension_labels["time"].text()
    checks.append("NC slicing and CF time label")
    window.selector.setCurrentText("precipitation")
    wait()
    assert set(window.dimension_spins) == {"time"}
    checks.append("NC variables with different dimensions")
    window.add_layer(inspect(files["vector"]))
    wait()
    window.move_layer(1)
    wait()
    assert window.selected()["layer"].kind == "vector"
    checks.append("layer order")
    window.statistics()
    wait()
    assert "要素数" in window.query_text.toPlainText()
    checks.append("background exact statistics")
    output = Path("artifacts/gui-controls.png")
    output.parent.mkdir(exist_ok=True)
    window.grab().save(str(output))
    while window.entries:
        window.remove_layer()
        wait()
    assert window.stack.currentIndex() == 0
    checks.append("remove all layers")
    window.close()
assert not errors, errors
Path("artifacts/gui-controls.json").write_text(json.dumps({"checks": checks, "errors": errors}, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"GUI: {len(checks)} checks passed")
