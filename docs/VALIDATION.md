# GeoQuickView 0.3 / 0.3.1 / 0.3.2 验证记录

日期：2026-11-04；0.3.1、0.3.2 修复复核 2026-11-06。测试平台：Windows x64、Python 3.12.15、GDAL 3.13.3、PySide6 6.11.2。

## 本次迁移（项目移入中文路径）

项目从 `Desktop\GLASS\GeoQuickView` 迁至 `Desktop\开发\GeoQuickView`，暴露出一个此前被 ASCII 路径掩盖的真实缺陷：

- **缺陷**：`geoquickview/__init__.py` 在导入时把 `PROJ_DATA`/`PROJ_LIB` 指向安装目录内的 `proj` 文件夹。PROJ 无法通过非 ASCII 路径打开 `proj.db`，导致所有坐标系解析失败（`Cannot find proj.db`），属性/预览/重投影整体不可用；打包版（冻结布局）同样受影响。
- **修复**：导入时先丢弃继承的 `PROJ_DATA`/`PROJ_LIB`（避免其他软件或已失效盘符的残留值接管），再处理自身数据目录——ASCII 路径直接使用；非 ASCII 路径则复制到 `%TEMP%\GeoQuickView\proj-data` 这一 ASCII 镜像目录并指向它。源码运行与便携包均适用。
- **回归测试**：`test_non_ascii_runtime_path_does_not_break_proj_lookup` 断言 PROJ 变量只指向含 `proj.db` 的 ASCII 路径，并在父进程故意设置无效 `PROJ_DATA` 时，子进程仍能解析 `EPSG:4326` 与 `EPSG:3857`。
- **其他迁移项**：`.conda` 经复制 + 显式前缀改写迁移（`conda rename` 在 38k 文件规模下卡住，未采用）；改写后清除旧字节码缓存，并核验 7762 个 `.py` 源文件语法无误、新旧环境互不污染。

## 0.3.1 修复验证（2026-11-06）

三项用户反馈问题的修复与验证，需求与原因记录见 [issues.md](issues.md)。

| 问题 | 修复后的验证 |
| --- | --- |
| 精确统计写入“查询”页 | 界面检查断言统计完成后当前标签页为“属性”，属性树顶部为「精确统计（全量，物理值）」节点且带高亮；「查询」页原有内容不被统计覆盖；切换选择后旧统计清除 |
| 多图层预览串行阻塞、可见性滞后 | 界面检查延迟注入（栅格层 1 秒、NC 层 0.05 秒）证明 NC 先出图时栅格仍处于“加载中”状态；视窗不变时勾选/取消勾选可见性不触发任何重新渲染 |
| NC 重复经度端点/非均匀坐标无法预览 | 集成测试：0–360 含 360° 的网格合并端点后仍判定为规则网格，预览值范围与查询原始列正确；非均匀纬度网格可预览整体场范围，查询仍命中原始像元，重投影明确拒绝并说明原因 |

同时确认：过期渲染任务按代次丢弃，不会覆盖新视窗画面；未完成的图层显示加载状态而不是上一代画面。

## 0.3.2 打包版 PROJ 定位修复验证（2026-11-06）

用户报告便携版“加载合成演示数据”时报 `PROJ: proj_create_from_database: Cannot find proj.db`，原因与修复见 [issues.md 第 4 条](issues.md)。

| 检查 | 结果 |
| --- | --- |
| 打包布局 | 便携目录中 `share\proj\proj.db` 与 `_internal` 同级，`_internal\Library\share\proj` 不再存在；`build_windows.py` 打包后断言，`verify_bundle.py` 解压后再次断言 |
| 无可写临时目录 | 把 TEMP/TMP/LOCALAPPDATA/USERPROFILE 指向不可用位置（PROJ 镜像无法生成）运行便携包 `--self-check`：通过。修复前同样条件在 `reference("EPSG:4326")` 处报 Cannot find proj.db |
| 常规运行 | 便携包 `--self-check` 与源码测试全部通过；PROJ 临时镜像作为后备路径不受影响 |

## 已验证范围

| 检查 | 结果与覆盖 |
| --- | --- |
| 核心集成测试 | 23 项通过：栅格属性、NoData/缩放、RGB/视窗上限、NC 变量/切片/日历/经纬度方向、中文路径的 NC3/NC4、valid_range、三种矢量格式、重投影、取消清理、未知坐标系、旋转像元、源文件变化、内置 34 省界及离线投影、非 ASCII 运行路径下的 PROJ 解析、NC 重复经度端点、NC 非均匀坐标、PROJ 数据目录的源码/打包布局查找 |
| Qt 交互测试 | 22 组通过：波段、RGB、手工拉伸、隐藏图层、NC 切片及时间标签、不同维度变量、失败投影保持原坐标系、图层顺序、后台统计写入属性页并高亮、可见性切换复用缓存、多图层独立渲染与加载状态、重投影与出图菜单、仅属性 NC、空间/索引模式切换、移除全部图层、属性全文、缩放极限、坐标轴、省界开关与 GitHub 按钮 |
| 离线地图 | 禁止 Python 网络连接时导出 PNG/PDF，并检查输出文件；实际图像已进行视觉检查 |
| 便携包迁移 | 在临时目录解压 zip，移除外部 Python/Conda/GDAL/PROJ/Qt 搜索路径，从不同工作目录运行 exe |
| 便携包业务流程 | 中文目录下创建并打开合成数据，验证五种格式、像元缩放、精确统计、栅格/NC/矢量重投影、PNG/PDF；实际 Qt 界面加载栅格+矢量及 NC |
| 真实 TIF | 本机一份约 224 MB、5843 × 4310、12 波段且带概览的文件，通过属性、预览和原始像元查询验证 |
| 不改写输入 | 读取与统计前后，检查输入 TIFF 及其附属文件大小和修改时间保持不变；真实文件及附属文件 SHA-256 在便携版打开前后保持不变；输出重名时拒绝覆盖 |
| 大文件分配 | 逻辑大小 12 GiB、每波段 1 GiB 的稀疏合成 TIFF，只选择一波段，预览数组上限 1536 × 1536 |

复现命令（覆盖沙箱下系统临时目录不可写的限制）：

~~~powershell
. scripts\env.ps1
$env:QT_QPA_PLATFORM = 'offscreen'
.\.conda\python.exe scripts\run_tests.py --gui
~~~

运行脚本输出是权威记录。最终发行包通过 scripts/verify_bundle.py 后，artifacts/portable-verification.json 的 passed 字段为 true；GitHub Actions 另外在 Windows runner 上执行同一组脚本，不以“已配置工作流”当作“云端测试已通过”。

## 性能的解释

性能脚本分别记录属性耗时、预览读取耗时、导入耗时、工作集/内存峰值，以及源文件及附属文件是否变化。已有概览、系统缓存、文件压缩和磁盘都会影响结果。

开发过程中的一轮真实文件结果为属性约 0.03 秒、预览约 0.09 秒，不包含约 1.8 秒的后端导入时间，也不是冷启动软件的耗时承诺。请以本机 scripts/benchmark.py 的当前输出为准。

稀疏 TIFF 只验证内存没有随全部波段的逻辑大小分配；其物理文件很小，不能用来推断真实 1 GiB 压缩文件的读取速度。

## 尚未覆盖

- 尚未在独立、全新 Windows 虚拟机以及所有 Windows 配置上测试；路径隔离验证不能替代这类兼容性验证。
- NC 测试使用合成气象数据。用户真实 NC 的投影习惯、变量命名和维度组织仍需代表文件验证。
- 曲线/非结构网格、不规则坐标、重复经度端点、CDF-5、NC 分组和 HDF 多子数据集选择不属于首版已验证能力。
- 独立裁剪、格式转换、重采样和缺失值处理工具按用户决定放到后续版本；首版处理功能为重投影。

## 可复现命令

~~~powershell
python -m unittest discover -s tests -v
python -m tests.gui_smoke
python scripts\benchmark.py "D:\data\image.tif" --sparse-case
python scripts\build_windows.py
python scripts\verify_bundle.py
~~~

测试样例由 geoquickview/samples.py 生成。仓库包含用户明确指定的省界底图，不包含其他真实科研数据、研究文件截图或虚拟环境；README 的界面与地图图片使用合成栅格与提供的省界底图。
