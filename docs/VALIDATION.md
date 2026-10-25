# GeoQuickView 0.1 验证记录

日期：2026-10-25。测试平台：Windows 11 x64、Python 3.12、GDAL 3.13。

## 已验证范围

| 检查 | 结果与覆盖 |
| --- | --- |
| 核心集成测试 | 14 项通过：栅格属性、NoData/缩放、RGB/视窗上限、NC 变量/切片/日历/经纬度方向、中文路径的 NC3/NC4、valid_range、三种矢量格式、重投影、取消清理、未知坐标系、旋转像元、源文件变化 |
| Qt 交互测试 | 10 组通过：波段、RGB、手工拉伸、隐藏图层、NC 切片及时间标签、不同维度变量、图层顺序、后台统计、移除全部图层 |
| 离线地图 | 禁止 Python 网络连接时导出 PNG/PDF，并检查输出文件；实际图像已进行视觉检查 |
| 便携包迁移 | 在临时目录解压 zip，移除外部 Python/Conda/GDAL/PROJ/Qt 搜索路径，从不同工作目录运行 exe |
| 便携包业务流程 | 中文目录下创建并打开合成数据，验证五种格式、像元缩放、精确统计、栅格/NC/矢量重投影、PNG/PDF；实际 Qt 界面加载栅格+矢量及 NC |
| 真实 TIF | 本机一份约 224 MB、5843 × 4310、12 波段且带概览的文件，通过属性、预览和原始像元查询验证 |
| 不改写输入 | 读取与统计前后，检查输入 TIFF 及其附属文件大小和修改时间保持不变；输出重名时拒绝覆盖 |
| 大文件分配 | 逻辑大小 12 GiB、每波段 1 GiB 的稀疏合成 TIFF，只选择一波段，预览数组上限 1536 × 1536 |

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

测试样例由 geoquickview/samples.py 生成。仓库不包含真实科研数据、研究文件截图或虚拟环境；README 的界面与地图图片均来自合成数据。
