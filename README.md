# GeoQuickView

Windows 离线地理数据快看工具。拖入一个文件，查看属性、空间分布和原始像元值；气象 NC 可以直接选择变量、时间和层次。

![合成演示数据界面](docs/images/viewer.png)

## 运行

优先在 [GitHub Releases](https://github.com/E-Golem/GeoQuickView/releases/latest) 下载最新便携包与 SHA-256 校验文件。

也可在 [Windows 构建页面](https://github.com/E-Golem/GeoQuickView/actions/workflows/windows.yml) 的成功任务中下载 GeoQuickView-windows-x64 构建产物。

使用 Windows 64 位便携包时，解压整个目录，双击其中的 **GeoQuickView.exe**。无需安装 Python、Conda、QGIS 或 ArcGIS。也可以把数据拖到窗口，或通过 Windows“打开方式”选择该程序。

发行包保留 _internal 文件夹、share 文件夹和第三方许可文件，不要只复制 exe。当前发行包未进行代码签名。

### 首次运行与 Windows SmartScreen

双击 **GeoQuickView.exe** 时，Windows 可能弹出“Windows 已保护你的电脑 / Microsoft Defender SmartScreen 阻止了无法识别的应用启动”。这是 Windows 对**未签名、尚未建立信誉**的程序的标准提示，不代表程序有病毒：

1. 点击提示中的 **“更多信息”**，再点击 **“仍要运行”**。
2. 如果文件夹是从网上下载的 zip 解压出来的，先右键 zip → **属性** → 勾选 **“解除锁定”** → 确定，再解压；已经解压的话，双击发行包里的 **“解除阻止.cmd”** 清除下载标记即可。
3. 校验下载完整性：用 `SHA256SUMS.txt` 对照 zip 的 SHA-256。
4. 发行包内附 **“首次运行说明.txt”**，包含以上步骤与目录结构说明。

该提示无法在程序内部消除：只有使用受信任的代码签名证书对 exe 签名，才能让所有电脑不再提示。本项目目前未购买代码签名证书；如需在企业内部分发，可用单位证书签名后再打包。

从源码运行（仓库自带便携环境 `.conda`，无需 conda 激活）：

~~~powershell
. scripts\env.ps1
.\.conda\python.exe -m geoquickview
~~~

若需要从零重建环境：

~~~powershell
conda env create -f environment.yml --prefix .conda
. scripts\env.ps1
~~~

也可以传入文件：

~~~powershell
.\.conda\python.exe -m geoquickview "D:\data\image.tif" "D:\data\weather.nc"
~~~

> **关于中文路径**：项目可以放在中文目录下运行。PROJ 无法从非 ASCII 路径读取 `proj.db`，因此 `scripts\env.ps1` 与程序启动代码都会清除 `PROJ_DATA`/`PROJ_LIB`，让 PROJ 使用自身搜索路径。请不要手动把这两个变量指向安装目录内的 `proj` 文件夹。

## 0.3 功能

| 模块 | 已实现 |
| --- | --- |
| 打开 | 按文件内容识别 NetCDF/HDF5，无后缀的 WRF 输出可直接打开；未知坐标系图层按经纬度或省界坐标系假定后叠加显示 |
| 界面 | 启动窗口为鼠标所在屏幕可用区域的三分之二并居中；显示页可滚动以适应较小窗口；地图坐标系下拉选择（常用投影、手动输入、从文件读取投影）；属性列可调整、横向滚动、双击查看可复制全文；坐标轴默认关闭并可切换；地图缩放设上下限；GitHub 仓库按钮 |
| 底图 | 默认显示内置中国省界，可开关；随地图坐标系转换，未知坐标系时自动隐藏；出图可包含省界 |
| 属性 | 坐标系及完整 WKT、范围、X/Y 分辨率、宽高、数据类型、波段/变量、单位、缩放、NoData、已有统计、矢量字段 |
| 栅格 | 单波段伪彩色、RGB、波段切换、色带/拉伸、透明度、直方图、缩放平移、原始分辨率像元查询 |
| NC | 变量切换、按实际维度生成切片控件、CF 时间/日历标签、0–360 经度转换、纬度方向、缩放与缺失值；重复经度端点自动合并，非均匀一维坐标按等间距近似预览；WRF 网格由全局属性推导，二维曲线经纬度按中心行列近似定位 |
| 矢量 | Shapefile、GeoJSON、GeoPackage 多图层查看、边界/点线显示、字段属性与附近要素查询 |
| 多图层 | 开关、顺序、透明度；每个图层独立后台渲染，过期任务取消，未完成的图层显示加载状态；视窗不变时切换可见性立即生效 |
| 精确统计 | 对选定波段或 NC 当前切片分块全量计算，可取消；结果写入“属性”页并自动定位高亮，不占用“查询”页；不会把结果写回输入文件 |
| 重投影 | 选定/全部栅格波段、NC 当前二维切片输出 GeoTIFF，矢量输出 GeoPackage；进度、取消与独立输出 |
| 简易出图 | 通用研究区模板：标题、可选矢量边界、色标、经纬网、比例尺；范围、配色、字体、尺寸、DPI；PNG/PDF |

![合成地图示例](docs/images/map.png)

界面中的色标、拉伸和直方图来自当前预览抽样；“精确统计”才会扫描所选波段/切片的全部像元。原始存储值、缩放后的物理值、已有缓存统计分别展示。预览重投影不会修改输入数据。

### 0.4.1 修复

- 导出地图与重投影的保存对话框默认指向“文档\GeoQuickView 导出”（不可用时回退到用户目录、临时目录），不再依赖程序所在目录或当前工作目录；便携版放在只读位置也能导出。
- 选择没有写权限的文件夹时给出提示并自动回到可写文件夹重新选择；后台遇到权限错误时显示“无法写入文件”和可用的默认文件夹，而不是原始的 `Permission denied`。
- 输出文件重名时默认名自动避开已有文件（`名称.png`、`名称_1.png`…）；手动选择已存在的文件会提示改名，避免“文件已存在”导致导出失败（导出与重投影均适用）。
- 没有可见图层时点击“导出地图”，会说明需要先打开数据，而不是没有反应。

### 0.4.0 新增

- **无后缀 WRF/NetCDF**：按文件内容（HDF5 / CDF 签名）识别，不再依赖 `.nc` 扩展名。WRF 输出用 `MAP_PROJ`、`DX/DY`、`CEN_LAT/CEN_LON`、`TRUELAT/STAND_LON` 推导 Lambert/极射/墨卡托/经纬度网格与坐标系，可直接预览、查询、统计并叠加省界底图；`Times` 字符数组作为时间标签。
- **二维曲线经纬度**：没有 WRF 全局属性但带二维 `XLAT/XLONG` 的文件，用中心行/列近似定位预览（界面中标注为近似），查询与统计仍基于该近似网格。
- **未知坐标系默认显示**：加入未声明坐标系的图层时，范围看起来是经纬度则按 EPSG:4326 处理，否则按省界坐标系（北京 1954 Lambert）假定，并在属性中注明“假定”，尽量叠加到底图上；只有索引坐标的 NC 不做假定。
- **地图坐标系改为下拉**：常用投影（省界默认、WGS 84、Web Mercator、CGCS2000、CGCS2000 3° 高斯-克吕格、UTM 50N/51N）+ 可编辑输入 + “从文件读取坐标系…”（读取栅格、矢量 `.prj` 或 NetCDF 的投影）。

### 0.3.1 修复

- “精确统计”不再写入“查询”页：结果进入“属性”页的「精确统计（全量，物理值）」节点，完成后自动切换、定位并短时高亮；“查询”页只保留地图单击查询。切换波段/变量/切片时旧统计失效。
- 多图层预览按图层独立渲染、按代次取消并缓存：慢图层不再阻塞其它图层，视窗不变时切换可见性立即生效；未完成的图层显示“加载中”，不显示上一代画面。
- NetCDF 的重复经度端点（例如 0–360 中含 360°）自动合并；一维但不等间距的坐标按等间距近似预览，查询与统计仍用原始坐标。非均匀坐标不提供重投影并说明原因。

详见 [已知问题](docs/issues.md)。

### 0.3.2 修复

- 打包版（便携 exe）把 PROJ 数据放在 `share\proj`（与 `_internal` 同级）而不是 `_internal` 内部，PROJ 按自身 DLL 的相对位置即可找到 `proj.db`。此前若系统临时目录不允许写入或复制 PROJ 镜像失败，“加载合成演示数据”等首次坐标系查询会报“PROJ: proj_create_from_database: Cannot find proj.db”。
- 打包后自动检查该布局；源码运行与便携包共用同一套查找逻辑，ASCII 路径仍优先使用，非 ASCII 路径继续复制到临时镜像作为后备。

详见 [已知问题](docs/issues.md)。

## 适用范围

- 首批验证格式：GeoTIFF、NetCDF（含无后缀的 WRF 输出）、Shapefile、GeoJSON、GeoPackage。其它 GDAL 栅格驱动是否可用取决于发行包；HDF 多子数据集选择、NC 分组浏览尚未提供。
- 支持 NetCDF-3 classic/64-bit offset 与 NetCDF-4；CDF-5 暂不支持。
- 无二维数值变量的 NC 仍可查看和复制全部属性；不提供一维曲线图。
- NC 地图支持可识别空间参照的一维坐标：等间距网格按仿射栅格绘制；重复经度端点自动合并；一维但不等间距的坐标按等间距近似预览。WRF 输出按全局属性推导投影；带二维 `XLAT/XLONG` 的曲线网格按中心行/列近似定位。像元查询与精确统计使用原始（或该近似）网格。非均匀与曲线网格不提供重投影（会改变像元定位）。
- 缺少坐标系的数据按范围假定为经纬度或省界坐标系后叠加显示，属性中会注明“假定”，可用“地图坐标系…”修正；只有索引坐标的 NC 不做假定。
- 大 TIF 按当前波段/视窗读取；NC 先切片与抽样再读取。缺少概览的压缩文件仍可能需要较多底层 I/O；没有“任意 1 GB 文件瞬间打开”的保证。
- 矢量预览会简化线条，最多 50,000 个要素或 1,000,000 个顶点；触及上限会明确提示显示不完整。导出地图遇到截断时拒绝生成不完整图，需缩小范围。
- 查询原始像元而非预览插值值。当前矢量查询返回点击附近最多 10 个要素；不提供拓扑编辑。
- 输出使用新文件名，避免意外覆盖。独立裁剪、格式转换、重采样和缺失值处理工具按需求安排到后续版本。
- 基础流程不调用在线底图或自动下载边界。内置省界来自用户提供的“中国标准地图-审图号GS(2020)4619号-shp格式/省界.shp”，保留原始几何与北京 1954 Lambert 坐标系；也可加载自己的边界。演示数据是合成数据。

## 验证与打包

~~~powershell
. scripts\env.ps1
$env:QT_QPA_PLATFORM = 'offscreen'
.\.conda\python.exe scripts\run_tests.py --gui
.\.conda\python.exe scripts\build_windows.py
.\.conda\python.exe scripts\verify_bundle.py
~~~

`scripts\run_tests.py` 会依次执行 28 项核心与健壮性测试、23 组 Qt 界面检查，并把临时文件放到 `artifacts\test-tmp\`；在受限沙箱下直接运行 `python -m unittest` 会因系统临时目录不可写而失败。

`tests\test_robustness.py` 覆盖安全/健壮性关注点：畸形输入在子进程中运行（原生解析器崩溃与死循环会被捕获，并返回可读错误）、输入文件不被改写、重投影不残留临时文件、核心流程不访问网络；界面检查同时验证打开畸形文件后窗口仍可用。

打包产生 `dist\GeoQuickView\GeoQuickView.exe` 和 `dist\GeoQuickView-0.4.1-windows-x64.zip`（附 `SHA256SUMS.txt`）。

详见 [验证说明](docs/VALIDATION.md)、[需求及开源调研](docs/planning.md)、[开发说明](AGENTS.md)、[已知问题](docs/issues.md)。测试样例由代码生成，不随仓库上传真实科研数据、虚拟环境或缓存。Windows GitHub Actions 工作流负责测试、打包与便携包自检。

## 技术与许可

Python 3.12、PySide6、GDAL/OGR、xarray/h5netcdf/SciPy、NumPy/PyQtGraph、Matplotlib/Cartopy。查看与出图使用同一组图层、切片、范围与颜色参数。

本项目源码采用 MIT；第三方组件和用户提供的省界数据保留各自权利，省界数据不因随包提供而改用 MIT。来源记录见 geoquickview/assets/china_provinces.source.json。发行目录提供 THIRD_PARTY_NOTICES.txt、完整版本/来源记录及包内提供的许可文本。TuiView、viewtif、ncvue、FlashViewer、EOmaps、QGIS 为前期设计调研参考，本项目未复制它们的应用实现。
