"""Exercise real Qt controls and worker completion, without dialogs or network."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import json
import sys
import tempfile
import time

import geoquickview
from PySide6 import QtCore, QtGui, QtWidgets as W
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

def wait(owner=None):
    owner = owner or window
    deadline = time.monotonic()+25
    while time.monotonic() < deadline:
        application.processEvents()
        time.sleep(.025)  # Release the GIL so Python worker imports can make progress.
        if not owner.jobs and not owner.rendering and not owner.render_timer.isActive():
            return
    import faulthandler
    faulthandler.dump_traceback()
    print("STATE", len(owner.jobs), owner.rendering, owner.render_pending, owner.status.currentMessage(), errors, flush=True)
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
    from unittest.mock import patch
    previous_crs = window.target
    with patch("geoquickview.app.W.QInputDialog.getText", return_value=("EPSG:3857", True)), \
         patch("geoquickview.app.change_extent", side_effect=ValueError("test invalid domain")):
        window.choose_crs()
    assert window.target == previous_crs
    assert errors.pop() == "test invalid domain"
    checks.append("failed map projection leaves previous CRS intact")
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
    projected = str(Path(directory)/"gui-reprojected.gpkg")
    with patch("geoquickview.app.W.QDialog.exec", return_value=W.QDialog.DialogCode.Accepted), \
         patch("geoquickview.app.W.QFileDialog.getSaveFileName", return_value=(projected, "GeoPackage")):
        window.reproject_dialog()
        wait()
    assert Path(projected).is_file()
    opened_path = window.selected()["layer"].path
    assert Path(opened_path).samefile(projected), (opened_path, projected)
    checks.append("reprojection dialog saves and opens result")
    map_file = str(Path(directory)/"gui-map.png")
    with patch("geoquickview.app.W.QDialog.exec", return_value=W.QDialog.DialogCode.Accepted), \
         patch("geoquickview.app.W.QFileDialog.getSaveFileName", return_value=(map_file, "PNG")), \
         patch("geoquickview.app.W.QMessageBox.information", return_value=W.QMessageBox.StandardButton.Ok):
        window.export_dialog()
        wait()
    assert Path(map_file).stat().st_size > 5000
    checks.append("map export dialog writes PNG")
    from tests.nc_cases import nc_cases
    cases = nc_cases(directory)
    standalone = MainWindow()
    standalone.show_error = errors.append
    standalone.show()
    standalone.add_layer(inspect(cases["auxiliary"]))
    wait(standalone)
    assert standalone.selector.currentText() == "temperature"
    standalone.selector.setCurrentText("auxiliary")
    wait(standalone)
    assert not standalone.target and standalone.selected()["frame"] is not None
    standalone.selector.setCurrentText("temperature")
    wait(standalone)
    assert standalone.target and standalone.selected()["frame"] is not None
    standalone.close()
    checks.append("NC variable switching keeps geographic and index coordinates separate")
    window.add_layer(inspect(cases["profile"]))
    wait()
    assert not window.selector.isEnabled()
    assert "temperature" in window.selected()["shown_metadata"]["变量"]
    checks.append("metadata-only NetCDF opens without a renderable variable")
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
