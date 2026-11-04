# GeoQuickView 开发说明

Windows 离线地理数据快看工具（PySide6 + GDAL + xarray/h5netcdf）。面向用户的功能说明见 [README.md](README.md)，验证记录见 [docs/VALIDATION.md](docs/VALIDATION.md)，需求基线见 [docs/planning.md](docs/planning.md)，已知待办见 [docs/issues.md](docs/issues.md)。

## 环境与中文路径（重要）

**开始任何开发或测试前，先加载环境（点号前缀使其影响当前会话）：**

```powershell
. scripts\env.ps1
```

该脚本把 `.conda\Library\bin` 加入 `PATH`、设置 `GDAL_DATA`、并**清除继承来的 `PROJ_DATA`/`PROJ_LIB`**。

**PROJ 的硬约束**：PROJ 无法通过非 ASCII 路径打开 `proj.db`。项目位于中文路径 `Desktop\开发\...`，因此：

- `geoquickview/__init__.py` 在导入时先丢弃继承的 `PROJ_DATA`/`PROJ_LIB`（可能指向其他软件或已失效的盘符），再处理自身数据目录：
  - 数据目录路径为 ASCII → 直接指过去；
  - 含非 ASCII 字符（本机即如此）→ 用逐文件复制（不用 `shutil.copytree`，某些托管环境只允许单层建目录）镜像到第一个可写的 ASCII 候选位置：`%TEMP%\GeoQuickView\proj-data` → `%LOCALAPPDATA%\GeoQuickView\proj-data` → `~\.geoquickview\proj-data`，并指向该镜像。
- 镜像只建一次，之后用 `proj.db` 大小做校验命中。
- **不要手动把 `PROJ_DATA`/`PROJ_LIB` 指向项目内的 `proj` 目录**，那样会立刻触发 `Cannot find proj.db`。
- 该行为由 `test_non_ascii_runtime_path_does_not_break_proj_lookup` 覆盖：断言 PROJ 变量只指向含 `proj.db` 的 ASCII 路径，并在父进程持有无效 `PROJ_DATA` 时仍能解析 `EPSG:4326`/`EPSG:3857`。

> 打包版同样适用：便携包在中文目录下会镜像 PROJ 数据后正常运行。

## 运行

```powershell
. scripts\env.ps1
.\.conda\python.exe -m geoquickview                                  # 启动界面
.\.conda\python.exe -m geoquickview D:\data\image.tif D:\data\x.nc   # 直接打开文件
```

## 测试

```powershell
. scripts\env.ps1
$env:QT_QPA_PLATFORM = 'offscreen'
.\.conda\python.exe scripts\run_tests.py --gui
```

`scripts\run_tests.py` 会先跑 20 项核心集成测试，再跑 20 组 Qt 界面检查。**不要直接使用 `python -m unittest`**：本工具在受限沙箱下运行，默认系统临时目录可被创建但不可写，而测试需要在其中生成合成栅格/NC。该脚本把 `tempfile` 重定向到 `artifacts\test-tmp\`，两种环境都能跑。

当前基线：**20 项核心测试 + 20 组界面检查全部通过**。

## 打包与发布

```powershell
. scripts\env.ps1
.\.conda\python.exe scripts\build_windows.py     # 生成 dist\GeoQuickView\ 和 zip + SHA256SUMS.txt
.\.conda\python.exe scripts\verify_bundle.py     # 解压到新目录、剥离环境变量后自检
```

`verify_bundle.py` 使用 `tempfile.TemporaryDirectory`，在同一沙箱限制下需要先把 temp 指向可写目录，或在本机普通终端中执行。

GitHub Actions（`.github/workflows/windows.yml`）在云端 Windows runner 上执行同一组脚本；推送 `v*` 标签时才发布 Release。

## 代码结构

| 路径 | 职责 |
| --- | --- |
| `geoquickview/data.py` | GDAL 栅格/矢量读取、预览渲染、像元查询、精确统计、重投影 |
| `geoquickview/nc.py` | NetCDF 变量/维度/切片、网格定位、切片导出 |
| `geoquickview/app.py` | Qt 主窗口：图层、属性、显示、查询、出图与底图 |
| `geoquickview/export.py` | Matplotlib/Cartopy 模板出图（PNG/PDF） |
| `geoquickview/display.py` | 共享配色与几何拆分 |
| `geoquickview/samples.py` | 可重复生成的合成演示数据（不含真实科研数据） |
| `geoquickview/assets/` | 图标与用户提供的中国省界底图（含来源与校验记录） |

## 约定

- **仓库边界**：仓库不提交真实科研数据、`.conda`、`dist/`、`build/`、`artifacts/`（见 `.gitignore`）。
- 输入文件只读取，绝不原地改写；输出使用新文件名，重名即拒绝覆盖。
- 界面中的色带拉伸/直方图来自预览抽样；“精确统计”才扫描全量。二者不得混淆展示。
- 预览与出图共用同一套图层、切片、范围与配色参数，避免显示不一致。
- 修改 `nc.py`、`data.py` 的坐标系/缺失值逻辑后，务必运行完整测试（含中文路径用例）。
- `.conda` 是通过复制并显式改写前缀迁移过来的。**重新创建环境时**用 `conda env create -f environment.yml --prefix .conda`，不要依赖 `conda rename`（在本机 38k 文件规模下会卡住）。