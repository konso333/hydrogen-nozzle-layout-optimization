# 氢燃烧器喷嘴阵列几何布局优化

本项目在圆形燃烧器端面内生成和比较喷嘴中心坐标。当前阶段只研究几何可行性与几何分布特征，为后续 Fluent CFD 建模提供候选方案。

本程序不会根据几何指标推断氢转化率、燃烧完全度、温度场、压损或壁面热流，也不会自动生成任何 CFD 数值。因此，输出中的“Pareto 候选”仅表示几何层候选，不表示燃烧性能最优。

## 安装

建议使用 Python 3.10 或更高版本：

```bash
python -m pip install -r requirements.txt
```

所有命令均在 `nozzle_layout_optimization/` 目录执行。

## 支持的布局

- `A_Rectangular`：固定 N=24 的 6×4 矩形 baseline，节距 18 mm。
- `B_Hexagonal`：固定 N=24 的 4-5-6-5-4 三角晶格 baseline，最近邻节距 16 mm。
- `C_Double_Ring`：固定 N=24 的 8+16 双环 baseline。
- `D_Triple_Ring`：固定 N=24 的中心点+7+16 三层 baseline。
- `rectangular`：可参数化矩形网格。
- `hexagonal`：可参数化二维三角晶格。
- `ring`：显式设置每个环的半径、喷嘴数和相位角。
- `sector`：单扇形或多扇区布局，支持多个径向层和整体旋转。
- `radial_spoke`：等角度径向辐射布局。
- `staggered_ring`：相邻圆环按 `delta_theta` 依次错位。
- `nonuniform_ring`：显式或幂律设置非均匀环半径和各环数量。
- `deterministic_irregular`：由确定性径向律和角度增量生成的非网格对比布局。
- `cross_5`：opt-in 的固定五喷嘴十字拓扑；`pitch` 为项目自由参数，不推广为任意 `cross_N`。

所有生成器使用统一接口：

```python
from layouts import generate_layout

points = generate_layout(
    layout_type="sector",
    N=24,
    R=55,
    d=4,
    s_min=8,
    num_sectors=4,
    points_per_sector=6,
    inner_radius=18,
    outer_radius=46,
    sector_angle=3.141592653589793 / 6,
    angular_offset=3.141592653589793 / 16,
    radial_levels=3,
)
```

角度参数均使用弧度。相同参数始终产生完全相同的坐标。生成器返回：

```text
[(x1, y1), (x2, y2), ...]
```

生成阶段会检查点数、圆形边界、喷嘴重叠和最小中心距。`s_min` 表示最小中心距；实际硬约束为：

```text
任意两个喷嘴中心距 >= max(d, s_min)
任意喷嘴中心半径 <= R - d/2
```

M1 输入契约：数量参数要求严格的 Python/NumPy 整数，拒绝 bool、浮点数（包括 `4.0`）和字符串。环排列的连续参数（半径、相位、径向指数）接受可转换为有限 float 的值，例如 `ring_radii=["20"]`；通用几何配置仍要求有限实数及合法范围。`include_center` 接受 Python/NumPy 布尔标量，不接受任意真值对象。

优化目标先转换为 float，有限数字字符串和 Decimal 可以参与比较，无法转换的值及 NaN/Inf 不进入 Pareto 前沿。公共生成入口统一验证扩展生成器的最终坐标；独立几何诊断对有限但非法的尺寸/间距返回失败报告，生成入口则严格拒绝。搜索只过滤几何不可行，输入错误与程序异常继续抛出。完整契约、兼容影响及验收记录见 [M1_VALIDATION.md](M1_VALIDATION.md)。

## M2：可追溯实验归档

旧生成、比较和搜索入口仍保持默认行为。新归档通过独立入口启用：

```bash
python scripts/archive_experiment.py
```

该示例归档矩形 baseline、带旧 candidate ID 的双环搜索候选和扇区排列，并逐一从保存参数重新生成核验。每次运行创建独立的 `outputs/runs/<run_id>/run.json`，其 `cases` 索引关联各 case 的 JSON、坐标 CSV、PNG、硬约束验证和几何指标。

`run_id` 表示一次实验批次；`case_id` 是完整规范化规格的确定性 SHA-256 身份。同一规格跨 run 保持同一 case ID；原 `candidate_id` 保留为兼容字段。参数单位为 mm / rad，Git、环境和运行时间单独保存为 provenance。CFD 初始状态为 `not_started`，所有未提供的 CFD 指标为 `null`。

用于论文、答辩、正式 CFD 交接的 run，应尽量在包含生成代码的 **clean Git working tree** 下生成，并记录依赖环境。`dirty=True` 只表示存在未提交修改；当前不保存未提交源码快照，因此 commit hash 本身不足以完全复现 dirty run。

检查某个 case 的局部文件和几何再生成结果：

```bash
python scripts/archive_experiment.py --verify outputs/runs/<run_id>/cases/<case_id>/case.json
```

完整归档核验另用 `verify_run()`，检查 run/case/provenance/CFD 的关联，并核验所有 case 的文件和再生成结果：

```bash
python scripts/archive_experiment.py --verify-run outputs/runs/<run_id>/run.json
```

API、归档结构、身份规则、CFD 交接契约和 M2 验收结果见 [M2_EXPERIMENTS.md](M2_EXPERIMENTS.md)。

## 生成 N=24 baseline

```bash
python scripts/generate_baselines.py
```

可覆盖几何参数：

```bash
python scripts/generate_baselines.py --R 55 --d 4 --s-min 8
```

四个 baseline 使用原项目已经验证过的数学构造，不使用随机采样。

## 比较 N=24 扩展布局

```bash
python scripts/compare_layouts.py
```

该脚本比较四个 baseline，以及 sector、radial、交错环、非均匀环和确定性非网格示例。

## 可变 N 搜索

默认搜索 N=12～40：

```bash
python scripts/search_variable_n.py
```

也可指定范围和几何约束：

```bash
python scripts/search_variable_n.py --N-min 12 --N-max 40 --R 55 --d 4 --s-min 8
```

程序对每个 N 构造多个确定性候选，过滤越界、重叠、间距不足或数量不正确的方案，然后计算几何指标。搜索使用三个相互独立的最大化目标：

1. N；
2. `uniformity_score`；
3. `min_center_distance`。

程序采用非支配解/Pareto frontier，不把三个量随意相加，也不输出唯一“最佳 N”。

## 几何评价指标

M4 将硬约束、几何描述和优化目标分开：

- `validate_layout_constraints()` 只判定数量、边界、重叠和最小间距；
- `evaluate_geometry_metrics()` 只计算带单位、定义域和方向元数据的几何指标；
- `evaluate_geometry()` 是保留给旧调用方的兼容组合报告；
- `ObjectiveProfile` 显式引用指标，不另算一套数值，也不使用加权总分。

完整数学定义、单位、N=0/1/2 行为、不变性和科研解释边界见 [M4_GEOMETRY_EVALUATION.md](M4_GEOMETRY_EVALUATION.md)。

几何评价包括：

- 最小中心距；
- 最近邻距离均值和标准差；
- spacing/boundary 约束裕量；
- 最大、平均中心半径、径向标准差及其尺度归一化形式；
- 质心偏移及归一化形式；
- 基于质心中心化二阶矩的各向异性；
- x 轴、y 轴和原点中心对称性；
- 最近邻距离变异系数与几何均匀性分数。

对每个喷嘴 i，先计算最近邻距离 `q_i`。定义：

```text
CV_nn = std(q_i) / mean(q_i)
uniformity_score = 1 / (1 + CV_nn)
```

分数范围为 0～1，越高表示各喷嘴的局部最近邻距离越一致。它是空间规则性的几何代理指标，不是燃烧效率。单一圆环也可能具有很高的最近邻规则性，因此分析时应同时查看中心半径、径向标准差和布局类型。

数学上未定义的指标返回 `None`（JSON 中为 `null`），不伪造 0，也不让 NaN/Inf 进入 Pareto。默认 objective profile 仍严格保留原 `N + uniformity_score + min_center_distance`，所以 M0-M3 默认前沿不变；M4 新指标不会自动加入优化。

## 输出目录

程序只向 `outputs/` 写结果：

```text
outputs/
├─ figures/       PNG 布局图和 Pareto 权衡图
├─ coordinates/   喷嘴坐标 CSV
└─ summaries/     指标、可变 N 结果和 Pareto 候选 CSV
```

主要汇总文件：

- `outputs/summaries/baseline_layout_summary.csv`
- `outputs/summaries/layout_comparison.csv`
- `outputs/summaries/variable_n_results.csv`
- `outputs/summaries/pareto_candidates.csv`
- `outputs/figures/variable_n/pareto_tradeoffs.png`

### GitHub 中保留的代表性结果

为避免仓库包含数百份可重复生成的坐标和图片，`.gitignore` 默认排除批量输出。仓库只保留以下代表性结果：

- `outputs/figures/A_Rectangular.png`
- `outputs/figures/B_Hexagonal.png`
- `outputs/figures/comparison_E_Sector_4x6.png`
- `outputs/figures/variable_n/pareto_tradeoffs.png`
- 上述 A、B、sector 示例的坐标 CSV
- `outputs/summaries/` 中的五个汇总 CSV

`outputs/coordinates/variable_n/` 中的逐候选坐标、其余布局图和 `outputs/test_tmp/` 不进入 Git。它们均可通过前述三个脚本重新生成；忽略规则不会删除本地结果。

## Fluent CFD 接口

`optimization/cfd_metrics.py` 只读取未来 Fluent 后处理得到的 CSV，不估算或填充缺失值。CSV 需要包含：

```text
candidate_id
hydrogen_conversion
outlet_temperature_mean
outlet_temperature_std
pressure_loss
max_wall_heat_flux
```

读取方式：

```python
from optimization.cfd_metrics import load_cfd_results

results = load_cfd_results("fluent_results.csv")
```

未知 CFD 指标应在 CSV 中留空，加载后保持为 `None`。旧 `candidate_id` 只能在明确的 run 内经唯一映射关联几何，不能区分多工况或多次执行。

M6 的独立 `cfd` 包提供带单位的工况规格、确定性 `cfd_case_id`、显式 case 选择、交接包和按 `attempt_id` 验证的结果回填；M2 几何身份及旧 CSV 接口保持不变。使用 `python scripts/cfd_handoff_example.py` 可运行只包含 null 结果的 synthetic/test-only 示例。完整 API、指标公式、状态与重跑边界见 [M6_CFD_HANDOFF.md](M6_CFD_HANDOFF.md)。本阶段不运行 Fluent。

## 测试

M3 提供独立、可选的 JSON 搜索配置入口；默认只报告计划数量：

```bash
python scripts/run_search_experiment.py
python scripts/run_search_experiment.py --config examples/m3_small_search.json --execute --archive-root outputs/runs
```

搜索配置是显式实验设计快照；修改 geometry 不会自动重新推导 layout-specific 参数，需要显式修改参数组。默认 Pareto 在所有合法 unique cases 上全局计算，多个 block 也共同参与比较。

配置组合、去重、异常处理及归档说明见 [M3_SEARCH_SPACE.md](M3_SEARCH_SPACE.md)。原三个生成/比较/搜索脚本保持原行为。

```bash
pytest
```

测试覆盖 baseline 数量与边界、最小距离、对称性、ring/sector 数量、确定性、非法可变 N 候选过滤，以及 CFD 空值读取。

项目通过 `pytest.ini` 将 pytest 临时文件固定在 `outputs/test_tmp/`，避免依赖 Windows 用户临时目录的访问权限。该目录仅包含测试期间生成的临时文件。

## M5：文献与布局可追溯关系

M5 用独立 JSON registry 区分 `literature_backed`、`literature_inspired` 和 `engineering_derived`，并记录 direct primary 与 secondary review 证据。新增的 `cross_5` 和 H1 七管正六角 8/10/12 mm spacing 实验均为 opt-in；citation 不参与 case ID，默认 426-case 搜索不变。

文献 DOI、每类布局的保守 supported claim、真实硬件与二维点阵的差别、运行示例和 CFD 解释边界见 [M5_LITERATURE_LAYOUTS.md](M5_LITERATURE_LAYOUTS.md)。
