"""Build a reproducible Windows portable directory and zip from a conda environment."""
from pathlib import Path
import json
import os
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import geoquickview
from geoquickview import __version__

ARTIFACTS = ROOT/"artifacts"
ARTIFACTS.mkdir(exist_ok=True)
DIST = ROOT/"dist"
BUILD = ROOT/"build"
for path in (DIST, BUILD):
    if not path.resolve().is_relative_to(ROOT):
        raise RuntimeError("Build paths must remain inside the project.")
os.environ["PYINSTALLER_CONFIG_DIR"] = str(ARTIFACTS/"pyinstaller-cache")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def icon():
    from PySide6 import QtCore, QtGui, QtSvg
    app = QtGui.QGuiApplication.instance() or QtGui.QGuiApplication([])
    image = QtGui.QImage(256, 256, QtGui.QImage.Format.Format_ARGB32)
    image.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(image)
    QtSvg.QSvgRenderer(str(ROOT/"geoquickview"/"assets"/"icon.svg")).render(painter)
    painter.end()
    image.save(str(ARTIFACTS/"icon.png"))
    from PIL import Image
    with Image.open(ARTIFACTS/"icon.png") as bitmap:
        bitmap.save(ARTIFACTS/"icon.ico", sizes=[(16,16), (32,32), (48,48), (64,64), (128,128), (256,256)])


def notices(output):
    licenses = output/"THIRD_PARTY_LICENSES"
    licenses.mkdir(exist_ok=True)
    records = []
    for record_path in sorted((Path(sys.prefix)/"conda-meta").glob("*.json")):
        record = json.loads(record_path.read_text(encoding="utf-8"))
        name, version = record["name"], record["version"]
        source = Path(record.get("link", {}).get("source", ""))/"info"/"licenses"
        copied = []
        if source.is_dir():
            for file in source.rglob("*"):
                if file.is_file():
                    relative = file.relative_to(source)
                    destination = licenses/name/relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(file, destination)
                    copied.append(str(relative))
        records.append({"name": name, "version": version, "license": record.get("license", "see upstream"),
                        "source_package": record.get("url") or f'{record.get("channel", "")}/{record.get("fn", "")}',
                        "license_files": copied})
    python_license = Path(sys.prefix)/"LICENSE_PYTHON.txt"
    if python_license.exists():
        shutil.copy2(python_license, licenses/"LICENSE_PYTHON.txt")
    (output/"THIRD_PARTY_NOTICES.json").write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = ["GeoQuickView third-party components", "",
             "GeoQuickView's own source is MIT. Bundled components retain their individual licenses.",
             "This inventory covers the complete build environment; some listed build-only components are not bundled.",
             "License texts supplied by conda packages are in THIRD_PARTY_LICENSES.",
             "Exact source-package URLs and versions are recorded in THIRD_PARTY_NOTICES.json.", ""]
    lines.extend(f'{r["name"]} {r["version"]}: {r["license"]}' for r in records)
    (output/"THIRD_PARTY_NOTICES.txt").write_text("\n".join(lines)+"\n", encoding="utf-8")
    return records


def main():
    if os.name != "nt":
        raise SystemExit("Build the Windows package on Windows.")
    icon()
    import PyInstaller.__main__
    PyInstaller.__main__.run([str(ROOT/"GeoQuickView.spec"), "--noconfirm", "--clean",
                             "--distpath", str(DIST), "--workpath", str(BUILD)])
    output = DIST/"GeoQuickView"
    for filename in ("README.md", "LICENSE"):
        shutil.copy2(ROOT/filename, output/filename)
    shutil.copytree(ROOT/"docs", output/"docs", dirs_exist_ok=True)
    records = notices(output)
    files = sorted(p for p in output.rglob("*") if p.is_file())
    archive = DIST/f"GeoQuickView-{__version__}-windows-x64.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in files:
            bundle.write(path, path.relative_to(DIST))
    report = {"version": __version__, "python": sys.version, "files": len(files),
              "unpacked_bytes": sum(p.stat().st_size for p in files),
              "zip_bytes": archive.stat().st_size, "archive": str(archive),
              "license_records": len(records), "license_records_with_files": sum(bool(r["license_files"]) for r in records)}
    (ARTIFACTS/"build-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
