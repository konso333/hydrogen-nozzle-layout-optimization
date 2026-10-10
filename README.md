# 氢燃烧器喷嘴阵列几何布局优化

本项目在圆形燃烧器端面内生成和比较喷嘴中心坐标。当前阶段只研究几何可行性与几何分布特征，为后续 Fluent CFD 建模提供候选方案。

本程序不会根据几何指标推断氢转化率、燃烧完全度、温度场、压损或壁面热流，也不会自动生成任何 CFD 数值。因此，输出中的“Pareto 候选”仅表示几何层候选，不表示燃烧性能最优。

## 14 mm 实体喷嘴：工程配置与几何校验

最新 CAD 喷嘴外径已确认为 **14 mm**。独立配置
[examples/engineering_nozzle_14mm.json](examples/engineering_nozzle_14mm.json)
记录这一尺寸；安装区域半径、喷嘴外缘之间的最小间隙、喷嘴外缘到区域边界的最小余量尚未确定，均显式保存为 `null`。
历史 `GeometryConfig()` 和旧实验的 `d=4 / R=55 / s_min=8` 保持原值，不代表最新喷嘴的工程安装要求。

只读检查：

```bash
python scripts/inspect_engineering_config.py --config examples/engineering_nozzle_14mm.json
```

当前输出 `parameters_complete=false`，并列出三个待定参数；不生成坐标、写结果或调用 Fluent。
需要检查参数是否齐全时追加 `--require-complete`，待定参数存在时退出码为 `2`。
输入格式或数值非法也会报错，不会回退到旧默认值。

所有尺寸字段单位均为 mm：

| 字段 | 含义 | 当前值 |
| --- | --- | --- |
| `nozzle_outer_diameter_mm` | 实体喷嘴外径，不是内部流道直径 | 14 |
| `installation_radius_mm` | 圆形有效安装区域半径 | null |
| `nozzle_edge_gap_mm` | 两个喷嘴外缘之间的最小间隙，不是中心距 | null |
| `wall_clearance_mm` | 喷嘴外缘到安装区域边界的最小余量 | null |

明确提供参数后，检查器计算：`最小中心距 = 外径 + 外缘间隙`，
`最大中心半径 = 安装区域半径 - 外径/2 - 壁面余量`。
显式的 `0` 表示零间隙，不能代替尚未确定的 `null`。
`parameters_complete=true` 仅表示输入齐全，不证明已能容纳目标 N、已完成制造校核或具有更优燃烧性能。

第一步建立配置和检查入口；第二步接入现有几何约束校验；第三步接入单个工程排布生成与独立导出。
第四步接入同一工程安装条件下的可变 N 搜索；工程结果尚未接入 M2/M3 归档。
这份工程配置不能作为 `run_search_experiment.py --config` 的 M3 搜索设计使用；
工程生成与搜索入口均保留实际安装半径与余量记录。

第二步可只读检查已有坐标。先在工程配置副本中明确填写三个待定参数，再运行：

```bash
python scripts/inspect_engineering_config.py --config path/to/engineering.json --coordinates path/to/coordinates.csv --N 24
```

当前仓库模板仍包含 `null`，用它校验坐标会直接报错，不默认采用 55 mm 安装半径或零间隙。
坐标 CSV 支持原 `x_mm,y_mm` 和 M2 的 `nozzle_id,x_mm,y_mm,z_mm` 格式，单位 mm；
M2 格式要求 `z_mm=0`，不自动投影非平面坐标。

适配器 [engineering_geometry.py](engineering_geometry.py) 复用原 `GeometryConfig` 和几何校验函数：

- `R = installation_radius_mm - wall_clearance_mm`，此 R 仅为校验半径。
- `d = nozzle_outer_diameter_mm`，保持实体外径，不通过放大直径代替间隙。
- `s_min = nozzle_outer_diameter_mm + nozzle_edge_gap_mm`，单位 mm。

报告同时保留原工程配置 `configuration`、校验参数 `validation_geometry`、
约束结果 `validation` 以及实测外缘间隙、壁面余量和两者的剩余裕量 `measured_clearances_mm`。
`boundary_ok` 包含壁面余量要求；`overlap_ok` 检查实体是否重叠；`spacing_ok` 另外检查装配间隙。
少于两个喷嘴时，喷嘴之间的间隙为 `null`；没有喷嘴时，壁面余量也为 `null`。
校验要求明确的正整数 N，空坐标不能通过。数值容差沿用 `1e-9 mm`，报告中的裕量不作截零处理。

坐标校验通过退出码为 `0`，几何约束失败为 `1`，配置不完整或输入错误为 `2`。
检查过程不修改坐标文件、不保存新排布、不运行 Fluent；旧几何接口和历史结果保持原样。

### 第三步：生成单个工程排布

先在工程配置副本中填写已确认的安装半径、装配间隙和壁面余量，再准备布局参数 JSON。
例如矩形布局的参数文件格式如下；这里的 **16 mm 节距和 4×6 数量组合仅演示格式，不是已确定的工程方案**：

```json
{"spacing": 16, "rows": 4, "columns": 6}
```

明确提供 N、布局和参数文件后运行（文件路径需替换为自己的副本）：

```bash
python scripts/generate_engineering_layout.py --config path/to/engineering.json --layout rectangular --N 24 --parameters path/to/parameters.json --output-dir outputs/engineering_layouts/new_run
```

支持现有内置矩形、三角晶格、环形、扇形等生成器；参数沿用各生成器接口，长度单位 mm、角度单位 rad。
固定 N=24 的旧 baseline 仍使用其原有布局参数；需要改变节距、环半径或 N 时选通用布局类型。
入口复用 [engineering_layout.py](engineering_layout.py)、现有 `CaseSpec` 和几何校验，不修改旧生成算法。
安装区域或间隙仍为 `null` 时直接拒绝生成；也不会自动放大安装区域、降低间隙、改变 N 或重用旧坐标。

生成成功后，新目录包含：

- `coordinates.csv`：沿用 `nozzle_id,x_mm,y_mm,z_mm` 格式，单位 mm，`z_mm=0`。
- `layout.png`：实体喷嘴按真实外径绘制，分别标注安装边界、壁面余量后的外缘极限和允许中心边界。
- `generation.json`：原工程配置、完整解析后的生成参数、几何编号、校验结果、实测间隙与余量，以及 Git/运行环境记录。

`geometry_case_id` 沿用 M2 的几何规格编号，标识的是扣除壁面余量后的几何输入；
不同实际安装半径和壁面余量可能得到同一编号，必须同时保留 `configuration`，不能把该编号当作完整工程或 CFD 工况身份。
`geometry_case_spec` 可通过现有 `CaseSpec.from_normalized(...).generate()` 重现坐标。
这些文件属于独立工程几何预览，不是 M2/M3 归档，也不证明制造或 CFD 就绪。

输出目录必须尚不存在，重复运行应换新目录。几何不可行退出码为 `1`，配置不完整、参数或输出错误为 `2`，成功为 `0`。
配置和几何失败不会创建输出目录；`generation.json` 最后写入，若导出中途失败，应将该目录视为不完整结果并选择新目录重试。
本入口不运行 Fluent。当前项目模板的三个待定参数保持 `null`，因此还不能据此生成实际工程阵列。

### 第四步：工程条件下的可变 N 搜索

[engineering_search.py](engineering_search.py) 将同一份完整工程配置转换为现有 M3 搜索设计，
复用原有参数预检、生成器、几何约束、去重、评价指标和 Pareto 计算。
搜索组文件只声明 `schema_version`、`name`、`groups`，可选 `description` 与 `symmetry_tolerance`；
不能通过 `geometry_space`、`blocks` 或布局参数覆盖工程尺寸。
工程模板仍有三个 `null`，入口会拒绝搜索，也不会默认运行旧的 426 组设计。

[examples/engineering_search_groups.json](examples/engineering_search_groups.json) 演示矩形、三角晶格的 N 范围与节距组合，
以及固定数量的扇形参数组合。**该文件中的数量、节距、半径和角度仅演示格式，使用前需另行确认。**
完整工程配置与明确的搜索组副本准备好后运行：

```bash
python scripts/search_engineering_layouts.py --config path/to/engineering.json --search path/to/search_groups.json --output-dir outputs/engineering_search/new_run
```

每个组的 `N` 可为正整数、离散列表或 `{"start": 12, "stop": 40, "step": 4}` 范围，
从 start 按 step 递增，取不超过 stop 的值。
组内将每个 N 与每份 `parameter_sets` 交叉组合；`rows*columns`、环喷嘴数和扇区数量等必须与每个 N 匹配。
例如环形的 `points_per_ring` 随 N 改变时，应分别建组，不能用不匹配的数量组合继续搜索。
所有组在生成坐标前完成预检；非法参数使整次搜索报错，有限的几何不可行方案则记录失败原因并继续。
参数是显式搜索选择：改变安装半径不会自动缩放环半径、扇区半径或节距。

新输出目录包含 `search_report.json`、`feasible_candidates.csv`、`pareto_candidates.csv`；
存在有限几何目标的可行候选时还输出 `pareto_tradeoffs.png`。
JSON 保存原工程配置、实际校验参数、完整 M3 搜索设计与逐案规格、去重来源、不可行原因、
各 N 的可行数量和 Pareto 数量、几何目标定义及 Git/运行环境记录。
CSV 保留实际安装半径、实体外径和两项要求间隙，并给出实测间隙和壁面余量；
`minimum_spacing_margin`、`minimum_boundary_margin` 分别为两者相对要求的剩余裕量。
两种 CSV 即使没有记录也保留表头；单喷嘴的两喷嘴间距为空值，不纳入当前 Pareto。

筛选目标沿用现有定义：同时最大化 N、最近邻均匀性与最小中心距，比较范围限于本次全部唯一可行候选。
`largest_feasible_N` 只是**给定 N 和参数组合中**找到的最大可行数量；
零可行时为 `null`，不能当作安装空间的理论容量上限，更不能当作功率或燃烧效率最优数量。
此处还没有反应 CFD 性能目标。
报告中的 `case_id` 沿用有效几何规格编号，需与原 `configuration` 一同保留，含义与第三步的 `geometry_case_id` 相同。
报告中的完整 `specifications` 可通过 `CaseSpec.from_normalized(...).generate()` 重现坐标。
选定方案后，也可用对应的 N、布局类型和布局参数，通过第三步入口独立生成坐标和安装边界图；
第三步命令行使用其默认容差，因此搜索采用自定义容差时应直接重放完整规格。
输出属于工程搜索报告，不是 M2/M3 归档。

成功且至少有一个可行方案退出码为 `0`；零可行时仍保存诊断报告，退出码为 `1`；
配置不完整、搜索参数非法或导出错误为 `2`。输出目录必须尚不存在，`search_report.json` 最后写入。
配置或预检失败不创建输出目录；导出失败的目录视为不完整结果，重试应另选新目录。

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

M7 的独立 `fluent` 包将已核验 M6 包转换成五层参数映射、几何中间文件、注释式 journal 模板和 dry-run 报告。运行 `python scripts/fluent_prepare_example.py` 可生成 synthetic/test-only 示例；不会启动 Fluent，未确认项保持 unresolved，模板始终不可直接执行。API、来源追踪、静态核验与首次实际运行的缺项见 [M7_FLUENT_AUTOMATION.md](M7_FLUENT_AUTOMATION.md)。

M8A 的独立 `fluent_pilot` 包为 H1 hex7 S10 提供本机环境预检、科研输入门控和结构化启动计划。使用 `python scripts/fluent_pilot_preflight.py --help` 查看入口；本机路径仅从 CLI/环境变量注入，缺项保持 unresolved，执行权限固定为 false，不启动 Fluent 或创建 attempt。详见 [M8A_FLUENT_PILOT_PREFLIGHT.md](M8A_FLUENT_PILOT_PREFLIGHT.md)。

M8B 的独立 `fluent_execution` 包提供 3D 单喷嘴工程试运行框架。`python scripts/fluent_execute_pilot.py --prepare-example` 只生成准备包；默认 inspect/dry-run 不创建 attempt 或启动 Fluent。真实执行须另行提供三维 mesh/case、完整审阅后的 journal、本机启动契约及显式 `--execute`。正式指标映射仍未解决，工程结果不进入科研优化数据；H1 与 M8A 原门控不变。详见 [M8B_FLUENT_EXECUTION.md](M8B_FLUENT_EXECUTION.md)。

已有冷态 case/data 的层流试算可使用 `python scripts/fluent_coldflow.py --config .local/coldflow.json --inspect`。
该独立工程入口默认只检查；显式 `--execute` 后分批求解、保存、监测并退出。
通用配置模板不包含本机路径，原 M8B/M8A 门控与基准保持不变。配置与使用见 [FLUENT_COLDFLOW.md](FLUENT_COLDFLOW.md)。

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
