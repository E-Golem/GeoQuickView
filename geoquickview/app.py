"""Native Qt desktop UI. Expensive I/O stays in a bounded worker pool."""
from __future__ import annotations
from copy import deepcopy
from pathlib import Path
import json
import tempfile
import threading
import traceback

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets as W
import pyqtgraph as pg

from . import __version__
from .data import (inspect, render, query, layer_space, change_extent, crs_name,
                   reference, band_info, exact_statistics, reproject, Cancelled, coordinate_unit)
from .display import limits, rgba, geometry_parts

pg.setConfigOptions(imageAxisOrder="row-major", background="#edf2f5", foreground="#607183", antialias=True)


def text_json(value):
    return json.dumps(value, ensure_ascii=False, indent=2, default=lambda v: v.item() if isinstance(v, np.generic) else str(v))


class Signals(QtCore.QObject):
    result = QtCore.Signal(object)
    error = QtCore.Signal(str)
    progress = QtCore.Signal(float)
    finished = QtCore.Signal()


class Worker(QtCore.QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn, self.signals, self.cancel = fn, Signals(), threading.Event()

    @QtCore.Slot()
    def run(self):
        try:
            self.signals.result.emit(self.fn(self.cancel, self.signals.progress.emit))
        except Cancelled as exc:
            self.signals.error.emit(str(exc))
        except Exception:
            self.signals.error.emit(traceback.format_exc())
        finally:
            self.signals.finished.emit()


class MainWindow(W.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"GeoQuickView  {__version__}")
        self.resize(1440, 900)
        self.setMinimumSize(1080, 720)
        self.setAcceptDrops(True)
        self.pool = QtCore.QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.jobs = set()
        self.entries = {}
        self.target = ""
        self.generation = 0
        self.rendering = False
        self.render_pending = False
        self.operation = None
        self.demo_directory = None
        self.render_timer = QtCore.QTimer(self)
        self.render_timer.setSingleShot(True)
        self.render_timer.setInterval(240)
        self.render_timer.timeout.connect(self.render_scene)
        self._build()
        self._style()
        self._menus()

    def _build(self):
        root = W.QWidget()
        self.setCentralWidget(root)
        outer = W.QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        header = W.QWidget()
        header.setObjectName("header")
        bar = W.QHBoxLayout(header)
        bar.setContentsMargins(24, 16, 24, 16)
        brand = W.QLabel("GeoQuickView")
        brand.setObjectName("brand")
        bar.addWidget(brand)
        sub = W.QLabel("地理数据 · 轻松查看")
        sub.setObjectName("subtitle")
        bar.addWidget(sub)
        bar.addStretch()
        badge = W.QLabel("●  离线桌面版")
        badge.setObjectName("badge")
        bar.addWidget(badge)
        outer.addWidget(header)
        toolbar = W.QWidget()
        tools = W.QHBoxLayout(toolbar)
        tools.setContentsMargins(20, 12, 20, 12)
        self.open_button = W.QPushButton("＋  打开数据")
        self.open_button.setObjectName("primary")
        self.open_button.clicked.connect(self.choose_files)
        tools.addWidget(self.open_button)
        for title, callback in (("适配图层  F", self.fit_current), ("精确统计", self.statistics),
                                ("重投影", self.reproject_dialog), ("导出地图", self.export_dialog)):
            button = W.QPushButton(title)
            button.clicked.connect(callback)
            tools.addWidget(button)
        tools.addStretch()
        hint = W.QLabel("拖入文件即可查看")
        hint.setObjectName("muted")
        tools.addWidget(hint)
        outer.addWidget(toolbar)
        splitter = W.QSplitter()
        outer.addWidget(splitter, 1)
        left = W.QWidget()
        left.setObjectName("sidebar")
        left_layout = W.QVBoxLayout(left)
        left_layout.setContentsMargins(16, 14, 16, 14)
        left_title = W.QLabel("数据图层")
        left_title.setObjectName("section")
        left_layout.addWidget(left_title)
        self.layers = W.QListWidget()
        self.layers.setSpacing(5)
        self.layers.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.layers.setTextElideMode(QtCore.Qt.TextElideMode.ElideMiddle)
        self.layers.currentItemChanged.connect(lambda *_: self.populate_selection())
        self.layers.itemChanged.connect(lambda *_: self.request_render())
        left_layout.addWidget(self.layers, 1)
        layer_tools = W.QHBoxLayout()
        for name, callback in (("上移", lambda: self.move_layer(-1)), ("下移", lambda: self.move_layer(1)), ("移除", self.remove_layer)):
            button = W.QPushButton(name)
            button.clicked.connect(callback)
            layer_tools.addWidget(button)
        left_layout.addLayout(layer_tools)
        demo = W.QPushButton("加载合成演示数据")
        demo.clicked.connect(self.open_demo)
        left_layout.addWidget(demo)
        description = W.QLabel("支持 GeoTIFF、NetCDF\nShapefile、GeoJSON、GeoPackage\n\n勾选显示 · 单击选择 · 地图单击查询")
        description.setObjectName("muted")
        description.setWordWrap(True)
        left_layout.addWidget(description)
        splitter.addWidget(left)
        center = W.QWidget()
        center_layout = W.QVBoxLayout(center)
        center_layout.setContentsMargins(12, 12, 12, 12)
        map_bar = W.QHBoxLayout()
        self.crs_label = W.QLabel("地图视图")
        self.crs_label.setObjectName("section")
        map_bar.addWidget(self.crs_label)
        map_bar.addStretch()
        crs_button = W.QPushButton("地图坐标系…")
        crs_button.clicked.connect(self.choose_crs)
        map_bar.addWidget(crs_button)
        center_layout.addLayout(map_bar)
        self.stack = W.QStackedWidget()
        welcome = W.QWidget()
        welcome_layout = W.QVBoxLayout(welcome)
        welcome_layout.addStretch()
        glyph = W.QLabel("GQ")
        glyph.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        glyph.setStyleSheet("font-size: 70px; color: #0d9488;")
        welcome_layout.addWidget(glyph)
        title = W.QLabel("从一个数据文件开始")
        title.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 25px; font-weight: 600;")
        welcome_layout.addWidget(title)
        message = W.QLabel("拖入栅格、气象 NC 或矢量文件\n快速了解属性、数值与空间分布")
        message.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        message.setStyleSheet("font-size: 15px; color: #718096; line-height: 150%;")
        welcome_layout.addWidget(message)
        welcome_layout.addStretch()
        self.stack.addWidget(welcome)
        self.canvas = pg.PlotWidget()
        self.canvas.setAspectLocked(True)
        self.canvas.showGrid(x=True, y=True, alpha=.1)
        self.canvas.setMenuEnabled(False)
        self.canvas.getPlotItem().hideButtons()
        self.canvas.setLabel("bottom", "X")
        self.canvas.setLabel("left", "Y")
        self.canvas.getViewBox().sigRangeChanged.connect(lambda *_: self.request_render())
        self.canvas.scene().sigMouseClicked.connect(self.map_clicked)
        self.canvas.scene().sigMouseMoved.connect(self.mouse_moved)
        self.stack.addWidget(self.canvas)
        center_layout.addWidget(self.stack, 1)
        self.legend = W.QLabel("")
        self.legend.setMinimumHeight(25)
        self.legend.setTextFormat(QtCore.Qt.TextFormat.RichText)
        center_layout.addWidget(self.legend)
        self.map_note = W.QLabel("数据保留在本机，查看不会改写源文件。")
        self.map_note.setObjectName("muted")
        self.map_note.setWordWrap(True)
        center_layout.addWidget(self.map_note)
        splitter.addWidget(center)
        right = W.QWidget()
        right.setObjectName("sidebar")
        right_layout = W.QVBoxLayout(right)
        right_layout.setContentsMargins(16, 14, 16, 14)
        self.active_label = W.QLabel("数据详情")
        self.active_label.setObjectName("section")
        self.active_label.setWordWrap(True)
        right_layout.addWidget(self.active_label)
        self.selector = W.QComboBox()
        self.selector.currentIndexChanged.connect(self.selection_changed)
        right_layout.addWidget(self.selector)
        self.rgb_widget = W.QWidget()
        rgb_layout = W.QHBoxLayout(self.rgb_widget)
        rgb_layout.setContentsMargins(0, 0, 0, 0)
        self.rgb_enabled = W.QCheckBox("RGB")
        self.rgb_enabled.toggled.connect(self.selection_changed)
        rgb_layout.addWidget(self.rgb_enabled)
        self.rgb_bands = []
        for _ in range(3):
            combo = W.QSpinBox()
            combo.setMinimum(1)
            combo.valueChanged.connect(self.selection_changed)
            rgb_layout.addWidget(combo)
            self.rgb_bands.append(combo)
        right_layout.addWidget(self.rgb_widget)
        self.dimensions = W.QWidget()
        self.dimension_form = W.QFormLayout(self.dimensions)
        self.dimension_form.setContentsMargins(0, 0, 0, 0)
        self.dimension_spins = {}
        self.dimension_labels = {}
        right_layout.addWidget(self.dimensions)
        self.tabs = W.QTabWidget()
        self.properties = W.QTreeWidget()
        self.properties.setHeaderLabels(["属性", "值"])
        self.properties.setColumnWidth(0, 125)
        self.properties.setAlternatingRowColors(True)
        self.properties.setWordWrap(True)
        self.tabs.addTab(self.properties, "属性")
        appearance = W.QWidget()
        style_layout = W.QVBoxLayout(appearance)
        form = W.QFormLayout()
        self.cmap = W.QComboBox()
        self.cmap.addItems(["viridis", "plasma", "inferno", "magma", "terrain", "RdBu_r", "gray"])
        self.cmap.currentTextChanged.connect(self.style_changed)
        form.addRow("色带", self.cmap)
        self.lower, self.upper = W.QDoubleSpinBox(), W.QDoubleSpinBox()
        for control in (self.lower, self.upper):
            control.setDecimals(6)
            control.setRange(-1e30, 1e30)
            control.editingFinished.connect(self.style_changed)
        form.addRow("下限", self.lower)
        form.addRow("上限", self.upper)
        self.opacity = W.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.opacity.setRange(0, 100)
        self.opacity.setValue(100)
        self.opacity.valueChanged.connect(self.style_changed)
        form.addRow("透明度", self.opacity)
        style_layout.addLayout(form)
        auto = W.QPushButton("按预览的 2–98% 重设拉伸")
        auto.clicked.connect(self.auto_stretch)
        style_layout.addWidget(auto)
        colour = W.QPushButton("矢量线条颜色…")
        colour.clicked.connect(self.vector_colour)
        style_layout.addWidget(colour)
        self.histogram = pg.PlotWidget()
        self.histogram.setMouseEnabled(x=False, y=False)
        self.histogram.setMaximumHeight(160)
        self.histogram.setLabel("bottom", "物理值（预览抽样）")
        style_layout.addWidget(self.histogram)
        style_layout.addWidget(W.QLabel("此处是预览抽样。\n全量值范围请使用“精确统计”。"))
        style_layout.addStretch()
        self.tabs.addTab(appearance, "显示")
        self.query_text = W.QPlainTextEdit()
        self.query_text.setReadOnly(True)
        self.query_text.setPlaceholderText("单击地图，读取原始分辨率的像元值或附近要素。")
        self.tabs.addTab(self.query_text, "查询")
        right_layout.addWidget(self.tabs, 1)
        copy_button = W.QPushButton("复制完整属性")
        copy_button.clicked.connect(self.copy_properties)
        right_layout.addWidget(copy_button)
        splitter.addWidget(right)
        splitter.setSizes([240, 850, 350])
        splitter.setStretchFactor(1, 1)
        self.status = W.QStatusBar()
        self.setStatusBar(self.status)
        self.coordinate = W.QLabel("")
        self.status.addPermanentWidget(self.coordinate)
        self.progress = W.QProgressBar()
        self.progress.setFixedWidth(150)
        self.progress.hide()
        self.status.addPermanentWidget(self.progress)
        self.cancel_button = W.QPushButton("取消任务")
        self.cancel_button.clicked.connect(self.cancel_operation)
        self.cancel_button.hide()
        self.status.addPermanentWidget(self.cancel_button)
        self.status.showMessage("就绪")

    def _style(self):
        self.setStyleSheet("""
        QWidget {font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 12px; color: #243748;}
        QMainWindow, QWidget#sidebar {background: #ffffff;}
        QWidget#header {background: #122c3b;}
        QLabel#brand {color: #f2fbfb; font-size: 23px; font-weight: 700;}
        QLabel#subtitle {color: #b4c7cf; padding-left: 15px;}
        QLabel#badge {color: #77dec4; background: #204151; border-radius: 10px; padding: 6px 12px;}
        QLabel#section {font-size: 14px; font-weight: 600; padding: 4px 0 9px;}
        QLabel#muted {color: #718397; font-size: 11px;}
        QPushButton {background: #f4f7fa; border: 1px solid #dce5ec; border-radius: 6px; padding: 8px 12px;}
        QPushButton:hover {background: #e6f4f1; border-color: #6cbab1;}
        QPushButton:pressed {background: #d2e9e4;}
        QPushButton#primary {background: #0d9488; border-color: #0d9488; color: white; font-weight: 600;}
        QPushButton#primary:hover {background: #087c73;}
        QComboBox, QSpinBox, QDoubleSpinBox {padding: 6px; border: 1px solid #d8e2ea; border-radius: 5px; background: white;}
        QListWidget {border: none; background: white;}
        QListWidget::item {padding: 10px 5px; border-radius: 6px; background: #f4f7fa;}
        QListWidget::item:selected {background: #dff3ed; color: #075f57;}
        QTreeWidget {border: none; alternate-background-color: #f7f9fb;}
        QTreeWidget::item {padding: 4px 1px;}
        QHeaderView::section {background: #f3f6f9; padding: 7px; border: none; font-weight: 600;}
        QTabWidget::pane {border: 1px solid #e2e8ef; border-radius: 4px;}
        QTabBar::tab {padding: 9px 15px; background: #f5f8fa; border: none;}
        QTabBar::tab:selected {color: #0d8176; background: #e4f4ef;}
        QPlainTextEdit {border: none; background: #f8fafc; padding: 6px; selection-background-color: #a7ddd2;}
        QStatusBar {background: #f5f8fa; border-top: 1px solid #e2e8ef;}
        QSplitter::handle {background: #edf2f6; width: 1px;}
        """)

    def _menus(self):
        menu = self.menuBar().addMenu("文件")
        for name, shortcut, fn in (("打开数据", "Ctrl+O", self.choose_files), ("导出地图", "Ctrl+E", self.export_dialog),
                                    ("加载合成演示数据", "", self.open_demo), ("退出", "Ctrl+Q", self.close)):
            action = menu.addAction(name)
            if shortcut:
                action.setShortcut(shortcut)
            action.triggered.connect(fn)
        fit = QtGui.QShortcut(QtGui.QKeySequence("F"), self)
        fit.activated.connect(self.fit_current)
        about = self.menuBar().addMenu("帮助").addAction("关于 GeoQuickView")
        about.triggered.connect(lambda: W.QMessageBox.about(self, "关于",
            f"GeoQuickView {__version__}\n离线地理数据快看工具\n\n源码：github.com/E-Golem/GeoQuickView\n"
            "本项目 MIT；依赖库保留各自许可，详见发行包 THIRD_PARTY_NOTICES。\n演示数据为合成数据，不是观测数据。"))

    def selected(self):
        item = self.layers.currentItem()
        return self.entries.get(item.data(QtCore.Qt.ItemDataRole.UserRole)) if item else None

    def ordered(self):
        return [self.entries[self.layers.item(i).data(QtCore.Qt.ItemDataRole.UserRole)] for i in range(self.layers.count())]

    def visible(self):
        return [self.entries[self.layers.item(i).data(QtCore.Qt.ItemDataRole.UserRole)]
                for i in reversed(range(self.layers.count()))
                if self.layers.item(i).checkState() == QtCore.Qt.CheckState.Checked]

    def run_job(self, fn, callback, label="", heavy=False, error=None):
        worker = Worker(fn)
        self.jobs.add(worker)
        connection = QtCore.Qt.ConnectionType.QueuedConnection
        worker.signals.result.connect(callback, connection)
        worker.signals.error.connect(error or self.show_error, connection)
        worker.signals.finished.connect(lambda: self.finish_job(worker), connection)
        if heavy:
            if self.operation:
                self.jobs.remove(worker)
                self.status.showMessage("请先完成或取消当前任务。", 4000)
                return
            self.operation = worker
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
            self.progress.show()
            self.cancel_button.show()
            worker.signals.progress.connect(lambda p: self.progress.setValue(round(p*100)), connection)
        if label:
            self.status.showMessage(label)
        self.pool.start(worker)
        return worker

    def finish_job(self, worker):
        self.jobs.discard(worker)
        if self.operation is worker:
            self.operation = None
            self.progress.hide()
            self.cancel_button.hide()

    def show_error(self, message):
        if "已取消" in message:
            self.status.showMessage("任务已取消", 5000)
        else:
            box = W.QMessageBox(W.QMessageBox.Icon.Warning, "未能完成", message.splitlines()[-1], parent=self)
            box.setDetailedText(message)
            box.exec()

    def choose_files(self):
        paths, _ = W.QFileDialog.getOpenFileNames(self, "打开地理数据", "",
            "地理数据 (*.tif *.tiff *.nc *.nc4 *.cdf *.shp *.geojson *.json *.gpkg *.vrt *.img *.jp2 *.hdf *.h5);;所有文件 (*)")
        for path in paths:
            self.open_path(path)

    def open_path(self, path):
        self.run_job(lambda *_: inspect(path), self.add_layer, f"正在读取属性：{Path(path).name}")

    def open_demo(self):
        if self.demo_directory is None:
            self.demo_directory = tempfile.TemporaryDirectory(prefix="geoquickview-demo-")
        from .samples import create_samples
        def ready(files):
            for kind in ("raster", "vector"):
                self.open_path(files[kind])
        self.run_job(lambda *_: create_samples(self.demo_directory.name), ready, "正在创建合成演示数据…")

    def add_layer(self, layer):
        key = id(layer)
        entry = {"key": key, "layer": layer, "selection": deepcopy(layer.selection),
                 "style": {"cmap": "viridis", "lower": None, "upper": None, "opacity": 1., "line": "#d88720"},
                 "frame": None, "items": []}
        self.entries[key] = entry
        item = W.QListWidgetItem(f"{layer.kind.upper()}  {layer.name}")
        item.setToolTip(layer.path)
        item.setData(QtCore.Qt.ItemDataRole.UserRole, key)
        item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(QtCore.Qt.CheckState.Checked)
        self.layers.insertItem(0, item)
        self.layers.setCurrentItem(item)
        self.stack.setCurrentIndex(1)
        if len(self.entries) == 1:
            self.target = layer.wkt
            self.fit_current()
        self.crs_label.setText("地图视图 · "+crs_name(self.target))
        self.status.showMessage(f"已打开 {layer.name}", 5000)
        self.request_render()

    def populate_selection(self):
        entry = self.selected()
        controls = [self.selector, self.rgb_enabled, *self.rgb_bands, self.cmap, self.lower, self.upper, self.opacity]
        blockers = [QtCore.QSignalBlocker(control) for control in controls]
        self.selector.clear()
        while self.dimension_form.rowCount():
            self.dimension_form.removeRow(0)
        self.dimension_spins = {}
        self.dimension_labels = {}
        self.rgb_widget.hide()
        if not entry:
            self.active_label.setText("数据详情")
            self.properties.clear()
            return
        layer, selection, style = entry["layer"], entry["selection"], entry["style"]
        self.active_label.setText(layer.name)
        self.selector.addItems(layer.choices)
        if layer.kind == "raster":
            self.selector.setCurrentIndex(selection["bands"][0]-1)
            self.rgb_widget.show()
            self.rgb_enabled.setChecked(len(selection["bands"]) == 3)
            for i, spin in enumerate(self.rgb_bands):
                spin.setMaximum(len(layer.choices))
                spin.setValue(selection["bands"][i] if len(selection["bands"]) == 3 else min(i+1, len(layer.choices)))
        elif layer.kind == "vector":
            self.selector.setCurrentText(selection["layer"])
        else:
            self.selector.setCurrentText(selection["variable"])
            try:
                from .nc import grid_info
                grid = grid_info(layer.path, selection["variable"])
                for dim, size in grid["extra"].items():
                    spin = W.QSpinBox()
                    spin.setRange(0, size-1)
                    spin.setValue(selection.get("indices", {}).get(dim, 0))
                    spin.valueChanged.connect(lambda value, dim=dim: self.dimension_changed(dim, value))
                    from .nc import dimension_label
                    box = W.QWidget()
                    row = W.QHBoxLayout(box)
                    row.setContentsMargins(0, 0, 0, 0)
                    spin.setKeyboardTracking(False)
                    coordinate = W.QLabel(dimension_label(layer.path, selection["variable"], dim, spin.value()))
                    coordinate.setWordWrap(True)
                    coordinate.setObjectName("muted")
                    row.addWidget(spin, 1)
                    row.addWidget(coordinate, 2)
                    self.dimension_form.addRow(f"{dim} [0…{size-1}]", box)
                    self.dimension_spins[dim] = spin
                    self.dimension_labels[dim] = coordinate
            except ValueError:
                pass
        self.cmap.setCurrentText(style["cmap"])
        self.lower.setValue(style["lower"] or 0)
        self.upper.setValue(style["upper"] if style["upper"] is not None else 1)
        self.opacity.setValue(round(style["opacity"]*100))
        del blockers
        self.populate_properties()
        self.update_histogram()
        self.update_legend()

    def populate_properties(self):
        self.properties.clear()
        self.preview_property = None
        entry = self.selected()
        if not entry:
            return
        md = deepcopy(entry["layer"].metadata)
        if entry["layer"].kind == "raster":
            md["当前波段属性"] = band_info(entry["layer"].path, entry["selection"]["bands"][0])
        if entry["layer"].kind == "nc":
            md["当前变量属性"] = md["变量"][entry["selection"]["variable"]]
            md["当前切片索引"] = entry["selection"].get("indices", {})
            from .nc import grid_info
            try:
                spatial = grid_info(entry["layer"].path, entry["selection"]["variable"])
                md["当前变量空间属性"] = {
                    "坐标系": crs_name(spatial["wkt"]), "坐标系 WKT": spatial["wkt"],
                    "范围": spatial["extent"], "空间坐标单位": coordinate_unit(spatial["wkt"]),
                    "X/Y 分辨率": [spatial["dx"], spatial["dy"]],
                    "宽 × 高": [len(spatial["x"]), len(spatial["y"])]}
            except ValueError as exc:
                md["当前变量空间属性"] = {"定位状态": str(exc)}
            from .nc import dimension_label
            md["当前切片坐标"] = {d: dimension_label(entry["layer"].path, entry["selection"]["variable"], d, i)
                                  for d, i in entry["selection"].get("indices", {}).items()}
        frame = entry["frame"]
        summary = self.preview_summary(entry)
        if entry["layer"].kind != "vector":
            md["当前预览抽样值范围（物理值）"] = summary
        entry["shown_metadata"] = md
        def add(parent, key, value):
            if isinstance(value, dict):
                item = W.QTreeWidgetItem(parent, [str(key), f"{len(value)} 项"])
                for k, v in value.items():
                    add(item, k, v)
            else:
                text = json.dumps(value, ensure_ascii=False, default=str) if isinstance(value, (list, tuple)) else str(value if value is not None else "未提供")
                item = W.QTreeWidgetItem(parent, [str(key), text])
                item.setToolTip(1, text)
        for key, value in md.items():
            add(self.properties, key, value)
        self.properties.expandToDepth(0)
        for index in range(self.properties.topLevelItemCount()):
            item = self.properties.topLevelItem(index)
            if item.text(0) == "当前预览抽样值范围（物理值）":
                self.preview_property = self.properties.takeTopLevelItem(index)
                self.properties.insertTopLevelItem(0, self.preview_property)
                self.preview_property.setExpanded(True)

    def selection_changed(self, *_):
        entry = self.selected()
        if not entry or self.selector.currentIndex() < 0:
            return
        kind = entry["layer"].kind
        if kind == "raster":
            entry["selection"] = {"bands": [s.value() for s in self.rgb_bands] if self.rgb_enabled.isChecked()
                                  else [self.selector.currentIndex()+1]}
        elif kind == "vector":
            entry["selection"] = {"layer": self.selector.currentText()}
        else:
            old = entry["selection"]
            entry["selection"] = {"variable": self.selector.currentText(),
                                  "indices": old.get("indices", {}) if old["variable"] == self.selector.currentText() else {}}
        entry["style"]["lower"] = entry["style"]["upper"] = None
        entry["frame"] = None
        self.populate_selection()
        self.request_render()

    def dimension_changed(self, dim, value):
        entry = self.selected()
        if entry:
            entry["selection"].setdefault("indices", {})[dim] = value
            from .nc import dimension_label
            self.dimension_labels[dim].setText(dimension_label(entry["layer"].path, entry["selection"]["variable"], dim, value))
            entry["style"]["lower"] = entry["style"]["upper"] = None
            self.populate_properties()
            self.request_render()

    def current_extent(self):
        ranges = self.canvas.getViewBox().viewRange()
        return (ranges[0][0], ranges[1][0], ranges[0][1], ranges[1][1])

    def fit_current(self):
        entry = self.selected()
        if not entry:
            return
        try:
            wkt, bounds = layer_space(entry["layer"], entry["selection"])
            if bounds is None:
                raise ValueError("该图层只有属性可用，无法确定可绘制的空间范围。")
            if bool(wkt) != bool(self.target):
                raise ValueError("未知坐标系图层不能与已定位的地图叠加。请单独打开或核实源坐标系。")
            bounds = change_extent(bounds, wkt, self.target)
            if bounds[0] == bounds[2] or bounds[1] == bounds[3]:
                pad = max(abs(bounds[0]), abs(bounds[1]), 1)*.01
                bounds = (bounds[0]-pad, bounds[1]-pad, bounds[2]+pad, bounds[3]+pad)
            self.canvas.disableAutoRange()
            self.canvas.setRange(xRange=(bounds[0], bounds[2]), yRange=(bounds[1], bounds[3]), padding=.025)
            self.request_render()
        except Exception as exc:
            self.map_note.setText(str(exc))

    def choose_crs(self):
        value, accepted = W.QInputDialog.getText(self, "地图坐标系", "输入 EPSG 代码、PROJ 定义或 WKT：",
                                                 text="EPSG:4326")
        if accepted and value:
            try:
                candidate = reference(value).ExportToWkt()
                entry = self.selected()
                if entry:
                    source, bounds = layer_space(entry["layer"], entry["selection"])
                    if not source or bounds is None:
                        raise ValueError("当前图层的空间参照未知，无法转换地图坐标系。")
                    change_extent(bounds, source, candidate)
                self.target = candidate
                self.crs_label.setText("地图视图 · "+crs_name(self.target))
                self.fit_current()
            except Exception as exc:
                self.show_error(str(exc))

    def request_render(self):
        self.generation += 1
        self.render_pending = True
        if self.entries:
            self.map_note.setText("正在更新预览…")
        self.render_timer.start()

    def render_scene(self):
        if self.rendering or not self.entries:
            return
        self.rendering = True
        self.render_pending = False
        generation = self.generation
        extent, target = self.current_extent(), self.target
        snapshot = [(entry["key"], entry["layer"], deepcopy(entry["selection"])) for entry in self.visible()]
        def calculate(cancel, progress):
            result = []
            for key, layer, selection in snapshot:
                if cancel.is_set():
                    break
                try:
                    result.append((key, render(layer, selection, target, extent, 1536), ""))
                except Exception as exc:
                    result.append((key, None, str(exc)))
            return generation, result
        worker = self.run_job(calculate, self.render_ready, error=lambda error: self.map_note.setText(error.splitlines()[-1]))
        worker.signals.finished.connect(self.render_finished, QtCore.Qt.ConnectionType.QueuedConnection)

    def render_finished(self):
        self.rendering = False
        if self.render_pending:
            self.render_timer.start()

    def render_ready(self, result):
        generation, frames = result
        if generation != self.generation:
            return
        visible = {entry["key"] for entry in self.visible()}
        for entry in self.entries.values():
            if entry["key"] not in visible:
                for item in entry["items"]:
                    item.hide()
        notes = []
        for key, frame, error in frames:
            if key not in self.entries:
                continue
            entry = self.entries[key]
            entry["frame"] = frame
            self.paint_entry(entry)
            if error:
                notes.append(f'{entry["layer"].name}：{error}')
            elif frame:
                notes.append(frame.note)
        self.map_note.setText("  ·  ".join(dict.fromkeys(notes)) or "没有勾选可见图层")
        self.update_histogram()
        self.update_legend()
        entry = self.selected()
        if entry:
            summary = self.preview_summary(entry)
            entry["shown_metadata"]["当前预览抽样值范围（物理值）"] = summary
            item = getattr(self, "preview_property", None)
            if item is not None:
                item.takeChildren()
                for key, value in summary.items():
                    W.QTreeWidgetItem(item, [key, str(value)])

    def paint_entry(self, entry):
        for item in entry["items"]:
            self.canvas.removeItem(item)
        entry["items"] = []
        frame = entry["frame"]
        if frame is None:
            return
        style = entry["style"]
        order = list(reversed(self.ordered())).index(entry)
        if frame.data is not None:
            style["lower"], style["upper"] = limits(frame.data, style["lower"], style["upper"])
            image = pg.ImageItem(rgba(frame.data, style["cmap"], style["lower"], style["upper"]))
            nx, ny = frame.data.shape[1], frame.data.shape[0]
            x0, y0, x1, y1 = frame.extent
            transform = QtGui.QTransform()
            transform.translate(x0, y1)
            transform.scale((x1-x0)/nx, -(y1-y0)/ny)
            image.setTransform(transform)
            image.setZValue(order)
            image.setOpacity(style["opacity"])
            self.canvas.addItem(image)
            entry["items"].append(image)
            image.setVisible(entry in self.visible())
        else:
            path = QtGui.QPainterPath()
            point_x, point_y = [], []
            for geometry in frame.geometries:
                for kind, values in geometry_parts(geometry):
                    if not len(values):
                        continue
                    if kind == "point":
                        point_x.extend(values[:, 0])
                        point_y.extend(values[:, 1])
                    else:
                        path.moveTo(float(values[0, 0]), float(values[0, 1]))
                        for x, y in values[1:]:
                            path.lineTo(float(x), float(y))
            item = W.QGraphicsPathItem(path)
            item.setPen(pg.mkPen(style["line"], width=1.6))
            item.setZValue(order)
            item.setOpacity(style["opacity"])
            self.canvas.addItem(item)
            entry["items"].append(item)
            item.setVisible(entry in self.visible())
            if point_x:
                points = pg.ScatterPlotItem(point_x, point_y, size=6, brush=style["line"], pen=None)
                points.setZValue(order)
                points.setOpacity(style["opacity"])
                self.canvas.addItem(points)
                entry["items"].append(points)
                points.setVisible(entry in self.visible())
        if self.selected() is entry and frame.data is not None:
            blockers = [QtCore.QSignalBlocker(self.lower), QtCore.QSignalBlocker(self.upper)]
            self.lower.setValue(style["lower"])
            self.upper.setValue(style["upper"])

    def style_changed(self, *_):
        entry = self.selected()
        if not entry:
            return
        entry["style"].update(cmap=self.cmap.currentText(), lower=self.lower.value(),
                              upper=self.upper.value(), opacity=self.opacity.value()/100)
        self.paint_entry(entry)
        self.update_legend()

    def auto_stretch(self):
        entry = self.selected()
        if entry and entry["frame"] and entry["frame"].data is not None:
            entry["style"]["lower"] = entry["style"]["upper"] = None
            self.paint_entry(entry)
            self.update_legend()

    def vector_colour(self):
        entry = self.selected()
        if entry:
            colour = W.QColorDialog.getColor(QtGui.QColor(entry["style"]["line"]), self, "矢量线条颜色")
            if colour.isValid():
                entry["style"]["line"] = colour.name()
                self.paint_entry(entry)

    def update_histogram(self):
        self.histogram.clear()
        entry = self.selected()
        if not entry or entry["frame"] is None or entry["frame"].data is None:
            return
        data = entry["frame"].data
        finite = data[np.isfinite(data)]
        if finite.size:
            values, edges = np.histogram(finite[::max(1, finite.size//200000)], bins=64)
            self.histogram.plot(edges, values, stepMode="center", fillLevel=0,
                                pen="#0d9488", brush=pg.mkBrush(13, 148, 136, 80))

    def move_layer(self, direction):
        row = self.layers.currentRow()
        new = row+direction
        if 0 <= new < self.layers.count():
            item = self.layers.takeItem(row)
            self.layers.insertItem(new, item)
            self.layers.setCurrentItem(item)
            self.request_render()

    def remove_layer(self):
        item = self.layers.currentItem()
        if item is None:
            return
        blocker = QtCore.QSignalBlocker(self.layers)
        key = item.data(QtCore.Qt.ItemDataRole.UserRole)
        entry = self.entries[key]
        self.layers.takeItem(self.layers.row(item))
        self.entries.pop(key)
        for graphics in entry["items"]:
            self.canvas.removeItem(graphics)
        del blocker
        self.populate_selection()
        self.request_render()
        if not self.entries:
            self.target = ""
            self.stack.setCurrentIndex(0)
            self.crs_label.setText("地图视图")

    def mouse_moved(self, position):
        if self.canvas.sceneBoundingRect().contains(position):
            point = self.canvas.getViewBox().mapSceneToView(position)
            self.coordinate.setText(f"X  {point.x():.5f}    Y  {point.y():.5f}")

    def map_clicked(self, event):
        entry = self.selected()
        if not entry or event.button() != QtCore.Qt.MouseButton.LeftButton:
            return
        point = self.canvas.getViewBox().mapSceneToView(event.scenePos())
        layer, selection, target = entry["layer"], deepcopy(entry["selection"]), self.target
        key = entry["key"]
        def ready(value):
            if self.selected() and self.selected()["key"] == key:
                self.query_text.setPlainText(text_json(value))
                self.tabs.setCurrentWidget(self.query_text)
        self.run_job(lambda *_: query(layer, selection, point.x(), point.y(), target), ready,
                     "正在读取原始分辨率数据…")

    def copy_properties(self):
        entry = self.selected()
        if entry:
            W.QApplication.clipboard().setText(text_json(entry.get("shown_metadata", entry["layer"].metadata)))
            self.status.showMessage("属性已复制", 3000)

    def statistics(self):
        entry = self.selected()
        if entry:
            layer, selection = entry["layer"], deepcopy(entry["selection"])
            def ready(value):
                self.query_text.setPlainText(text_json(value))
                self.tabs.setCurrentWidget(self.query_text)
                self.status.showMessage("精确统计完成", 5000)
            self.run_job(lambda cancel, progress: exact_statistics(layer, selection, cancel, progress),
                         ready, "正在全量计算当前选择的统计…", heavy=True)

    def cancel_operation(self):
        if self.operation:
            self.operation.cancel.set()
            self.status.showMessage("正在取消，等待当前数据块结束…")

    def reproject_dialog(self):
        entry = self.selected()
        if not entry:
            return
        dialog = W.QDialog(self)
        dialog.setWindowTitle("重投影 · 保存为新文件")
        dialog.resize(500, 300)
        layout = W.QFormLayout(dialog)
        source = W.QLabel(crs_name(layer_space(entry["layer"], entry["selection"])[0]))
        source.setWordWrap(True)
        layout.addRow("源坐标系", source)
        target = W.QComboBox()
        target.setEditable(True)
        target.addItems(["EPSG:4326", "EPSG:3857", "EPSG:4490"])
        layout.addRow("目标坐标系", target)
        method = W.QComboBox()
        for label, value in (("最近邻（保留类别/原始值）", "near"), ("双线性（连续量）", "bilinear"), ("三次卷积（连续量）", "cubic")):
            method.addItem(label, value)
        layout.addRow("重采样", method)
        all_bands = W.QCheckBox("处理全部波段（大文件可能耗时较长）")
        all_bands.setVisible(entry["layer"].kind == "raster")
        layout.addRow(all_bands)
        suffix = ".gpkg" if entry["layer"].kind == "vector" else ".tif"
        info = W.QLabel(f"输出 {suffix}；NC 输出当前变量和切片。\n源文件不会改写。")
        info.setWordWrap(True)
        layout.addRow(info)
        buttons = W.QDialogButtonBox(W.QDialogButtonBox.StandardButton.Ok | W.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if dialog.exec() != W.QDialog.DialogCode.Accepted:
            return
        destination, _ = W.QFileDialog.getSaveFileName(self, "选择新输出文件", str(Path(entry["layer"].path).with_name(
            Path(entry["layer"].path).stem+"_reprojected"+suffix)), f"输出文件 (*{suffix})")
        if not destination:
            return
        layer, selection = entry["layer"], deepcopy(entry["selection"])
        if layer.kind == "raster" and all_bands.isChecked():
            selection["bands"] = list(range(1, len(layer.choices)+1))
        target_value, resampling = target.currentText(), method.currentData()
        def ready(path):
            self.open_path(path)
            self.status.showMessage("重投影完成："+path, 8000)
        self.run_job(lambda cancel, progress: reproject(layer, selection, destination, target_value, resampling, cancel, progress),
                     ready, "正在重投影…", heavy=True)

    def export_dialog(self):
        if not self.visible():
            return
        dialog = W.QDialog(self)
        dialog.setWindowTitle("通用研究区地图")
        dialog.resize(520, 580)
        form = W.QFormLayout(dialog)
        title = W.QLineEdit(Path(self.selected()["layer"].name).stem if self.selected() else "研究区地图")
        form.addRow("标题", title)
        width, height = W.QDoubleSpinBox(), W.QDoubleSpinBox()
        for control, value in ((width, 20), (height, 15)):
            control.setRange(5, 100)
            control.setValue(value)
        form.addRow("宽度（cm）", width)
        form.addRow("高度（cm）", height)
        dpi, font = W.QSpinBox(), W.QSpinBox()
        dpi.setRange(72, 1200)
        dpi.setValue(300)
        font.setRange(6, 36)
        font.setValue(10)
        form.addRow("DPI", dpi)
        form.addRow("字体大小（pt）", font)
        bounds = self.current_extent()
        extent_fields = []
        for label, value in zip(("最小 X", "最小 Y", "最大 X", "最大 Y"), bounds):
            control = W.QDoubleSpinBox()
            control.setRange(-1e30, 1e30)
            control.setDecimals(6)
            control.setValue(value)
            form.addRow(label, control)
            extent_fields.append(control)
        grid, scale, boundaries = W.QCheckBox("经纬网"), W.QCheckBox("比例尺"), W.QCheckBox("包含可见矢量边界")
        for control in (grid, scale, boundaries):
            control.setChecked(True)
            form.addRow(control)
        note = W.QLabel("采用当前可见图层、变量/切片、色带与拉伸。\n未知坐标系不绘制地理比例尺。")
        note.setWordWrap(True)
        form.addRow(note)
        buttons = W.QDialogButtonBox(W.QDialogButtonBox.StandardButton.Ok | W.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != W.QDialog.DialogCode.Accepted:
            return
        destination, _ = W.QFileDialog.getSaveFileName(self, "导出地图", "map.png", "PNG (*.png);;PDF (*.pdf)")
        if not destination:
            return
        entries = [(entry["layer"], deepcopy(entry["selection"]), deepcopy(entry["style"]))
                   for entry in self.visible() if boundaries.isChecked() or entry["layer"].kind != "vector"]
        if not entries:
            self.show_error("没有可导出的图层。")
            return
        from .export import export_map
        arguments = dict(extent=tuple(control.value() for control in extent_fields), wkt=self.target,
                         destination=destination, title=title.text(), width_cm=width.value(), height_cm=height.value(),
                         dpi=dpi.value(), font_size=font.value(), grid=grid.isChecked(), scalebar=scale.isChecked())
        def ready(path):
            self.status.showMessage("地图已保存："+path, 10000)
            W.QMessageBox.information(self, "地图已导出", path)
        self.run_job(lambda cancel, progress: export_map(entries, **arguments, cancel=cancel, progress=progress),
                     ready, "正在生成地图…", heavy=True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            if url.isLocalFile():
                self.open_path(url.toLocalFile())
        event.acceptProposedAction()

    def closeEvent(self, event):
        for job in self.jobs:
            job.cancel.set()
        if not self.pool.waitForDone(3000):
            self.status.showMessage("正在结束后台任务，请稍后关闭。")
            event.ignore()
            return
        if self.demo_directory:
            self.demo_directory.cleanup()
        event.accept()

    def update_legend(self):
        entry = self.selected()
        candidates = ([entry] if entry and entry in self.visible() else []) + list(reversed(self.visible()))
        entry = next((e for e in candidates if e["frame"] is not None and e["frame"].data is not None
                      and e["frame"].data.ndim == 2), None)
        if not entry:
            self.legend.clear()
            return
        from matplotlib import colormaps
        import html
        style, frame = entry["style"], entry["frame"]
        lo, hi = limits(frame.data, style["lower"], style["upper"])
        colours = [QtGui.QColor.fromRgbF(*colormaps[style["cmap"]](x)[:3]).name() for x in np.linspace(0, 1, 16)]
        blocks = "".join(f'<span style="background:{colour};">&nbsp;&nbsp;</span>' for colour in colours)
        self.legend.setText(f'{html.escape(entry["layer"].name)} &nbsp; {lo:.5g} &nbsp; {blocks} &nbsp; {hi:.5g} '
                            f'&nbsp; {html.escape(frame.unit)}')


    def preview_summary(self, entry):
        frame = entry["frame"]
        if frame is None or frame.data is None:
            return {"状态": "未生成栅格预览"}
        values = frame.data[np.isfinite(frame.data)]
        return {"模式": "当前视窗抽样，未扫描全量", "有限样本": int(values.size),
                "最小值": float(values.min()) if values.size else None,
                "最大值": float(values.max()) if values.size else None}
