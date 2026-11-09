# 氢燃烧器多喷嘴阵列设计与 Fluent 仿真平台

一个在本机运行的网页平台，用于从单喷嘴 STEP 模型生成多喷嘴阵列，并对符合现有供气模板的方案运行 Fluent 冷流试算。用户可以在网页上选择喷嘴数量、圆形或扇形安装区及排列方式，查看三维装配，下载几何与计算结果。

**平台代码、启动脚本和当前文档位于 `codex/shape-power-search` 分支。** 本仓库的默认首页介绍平台，早期的工程几何和命令行研究见文末。 [进入平台开发分支](https://github.com/konso333/hydrogen-nozzle-layout-optimization/tree/codex/shape-power-search)

## 可以做什么

| 使用场景 | 功能和输出 |
| --- | --- |
| 快速排布 | 使用示例或导入单喷嘴 STEP／STP，只填喷嘴数量即可试排。支持圆形和扇形安装区，以及方阵、蜂巢、向日葵、环形。查看俯视图和三维装配，下载排布图和坐标。 |
| 独立几何设计 | 按安装边界和间隙检查可行性，比较同一条件下的四种排列。空间不足时，给出经过核验的数量或尺寸调整方案，由用户选择采用。支持保存、恢复几何方案和导出实体 STEP。 |
| Fluent 冷流试算 | 在支持的几何与供气模板内，从阵列和流体域生成体网格，调用本机 Fluent，并整理计算状态、速度和压力场图、流量、压差及可重新打开的计算文件。空气模式支持单方案或相同安装面积对比。 |

## 本机启动

```powershell
git clone --branch codex/shape-power-search https://github.com/konso333/hydrogen-nozzle-layout-optimization.git
cd hydrogen-nozzle-layout-optimization
python -m pip install -r requirements-platform.txt
python scripts/run_platform.py
```

启动后在本机浏览器打开 `http://127.0.0.1:8765/`。首次体验可直接用内置示例，输入 **11**，点击「生成排布」。

- `/` 进入快速排布。
- `/?mode=geometry` 进入几何工作台，比较排列、管理方案和导出 STEP。
- `/?mode=advanced` 进入专业工作台，配置流体域、网格和 Fluent 工况。运行求解需要本机 Fluent、对应 Python SDK 和可用许可证。 [冷流配置与运行说明](https://github.com/konso333/hydrogen-nozzle-layout-optimization/blob/codex/shape-power-search/M28_AIR_COLD_FLOW_PLATFORM.md)

## 当前验证范围

- **几何**已核对真实 B0 喷嘴的圆形和扇形排布、可行性建议、导入和下载。11 喷嘴 STEP 装配已重新读取，核对实体数量、位置和总体积。 [几何工作台验收记录](https://github.com/konso333/hydrogen-nozzle-layout-optimization/blob/codex/shape-power-search/M30_GEOMETRY_WORKSPACE.md) · [快速排布版本](https://github.com/konso333/hydrogen-nozzle-layout-optimization/commit/c5c1afd81c345d17c1f2fa3d613ca93e9f515ea6)
- **仿真**已用真实 B0 单、双喷嘴跑通 GPU 空气定温冷流流程，并生成实际场图和报告。两组均未达到收敛标准，网格独立性和燃烧性能仍待验证。氢气／空气非反应混合与相同燃料热输入对比属于实验工作流。 [冷流验收与适用范围](https://github.com/konso333/hydrogen-nozzle-layout-optimization/blob/codex/shape-power-search/M28_AIR_COLD_FLOW_PLATFORM.md) · [方案比较说明](https://github.com/konso333/hydrogen-nozzle-layout-optimization/blob/codex/shape-power-search/M19_ONE_CLICK_WORKFLOW.md)

## 项目历史

早期研究覆盖喷嘴几何约束、排布搜索、实验归档和 CFD 前处理。 [查看原几何研究 README](https://github.com/konso333/hydrogen-nozzle-layout-optimization/blob/3792d41e866f955c8d51df9e24e79aac4362d4ad/README.md) · [查看平台开发记录](https://github.com/konso333/hydrogen-nozzle-layout-optimization/commits/codex/shape-power-search/)
