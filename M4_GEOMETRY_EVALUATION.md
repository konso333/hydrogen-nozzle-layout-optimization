# M4：结构化几何评价体系

M4 将“能否满足几何要求”“几何上是什么样”“研究时选择什么优化方向”分为三个层次。它没有修改八类排列的数学构造、坐标、点顺序、case identity 或 CFD 接口，也没有建立几何指标与燃烧性能之间的经验关系。

## 1. 三层职责

### 1.1 Hard constraints

硬约束只回答当前点集是否满足规定的几何要求。唯一公共判定入口仍是 `validate_layout_constraints()`：

| 结果字段 | 判定 |
| --- | --- |
| `count_ok` | 实际点数等于期望 N；N 为 `None` 时不施加数量约束 |
| `boundary_ok` | 对每个中心半径 r_i，均有 r_i <= R - d/2 + tolerance |
| `overlap_ok` | N < 2，或最小中心距 >= d - tolerance |
| `spacing_ok` | N < 2，或最小中心距 >= max(d, s_min) - tolerance |
| `feasible` | 上述四项全部通过 |

`nozzle_count` 是约束证据，`failure_reasons` 是诊断信息。`overlap_ok` 与 `spacing_ok` 有意保留：前者表达独立物理不重叠条件，后者表达包含不重叠的设计最小中心距，所以当 `s_min <= d` 时二者判定会重复，但语义不同。

裕量不是新的 pass/fail 规则。`minimum_spacing_margin` 和 `minimum_boundary_margin` 可以在容差带内略小于零而对应约束仍通过；硬约束判定继续只由上述函数负责。

### 1.2 Geometry metrics

`evaluate_geometry_metrics()` 只返回几何描述值，不返回合法性判定。`evaluate_geometry()` 保留为兼容组合入口，返回同一组 metrics 加原硬约束报告。

指标定义集中在 `geometry.definitions.METRIC_DEFINITIONS`。每项包含 key、显示名、描述、单位、方向、类别、定义域、未定义值、优化资格、缩放行为、旋转和排列不变性。`metric_definitions()` 提供 JSON-ready 副本。

数学上未定义的数值一律为 Python `None`，进入 JSON 后为 `null`。不使用 0、NaN 或 Infinity 代替缺失结果。

### 1.3 Optimization objectives

objective 是对已有 metric（或遗留 case 描述量）的显式引用，不重新计算几何量。`optimization.objectives.ObjectiveProfile` 记录 key、`minimize`/`maximize` 方向和来源，不支持权重或单一综合评分。

当前只有一个正式 profile：

| 查询名 | 目标 | 方向 | 说明 |
| --- | --- | --- | --- |
| `legacy` / `default` | `N` | maximize | 为 M0-M3 兼容保留的 case descriptor |
|  | `uniformity_score` | maximize | 引用正式 metric |
|  | `min_center_distance` | maximize | 引用正式 metric |

`get_objective_profile()` 和 `objective_profiles()` 可查询该契约。没有新增 `geometry_uniformity_v1` 等研究者尚未确认的 profile；新指标不会自动加入 Pareto。旧 profile 仍是全局非支配比较，不是加权求和。

## 2. 修改前指标审计

M4 前 `evaluate_geometry()` 共返回 18 个字段：

- 硬约束/诊断：`nozzle_count`、`count_ok`、`boundary_ok`、`overlap_ok`、`spacing_ok`、`feasible`、`failure_reasons`。
- 几何指标：`min_center_distance`、`mean_nearest_neighbor_distance`、`std_nearest_neighbor_distance`、`nearest_neighbor_cv`、`uniformity_score`、`max_center_radius`、`mean_center_radius`、`radial_std`、`x_axis_symmetry`、`y_axis_symmetry`、`origin_symmetry`。
- 默认 Pareto 外部字段：`N`。它来自 case/search 规格，不由几何评价器计算。

审计发现：

1. 约束和指标混在同一字典，调用方依赖字段差集自行区分。
2. 单位、方向、定义域和优化资格没有机器可读契约。
3. N < 2 的最近邻量直接返回 NaN；退化重合点的 CV 会成为 Infinity，并把 `uniformity_score` 人为设为 0。
4. `uniformity_score = 1/(1+CV_nn)` 与 CV 是同一信息的单调变换。二者保留是为了兼容和可读性，但不应同时作为独立信息重复进入一个 objective profile。
5. 最小中心距同时用于 overlap、spacing 和评价。这是共享几何量，不代表三个不同指标；约束层和指标层的用途不同。
6. 三个 symmetry 字段是固定坐标系、容差相关的离散集合判定，不是连续周向均匀性指标。x/y 反射标志不是一般旋转不变量。精确的原点反演关系与旋转可交换，但当前判定使用逐坐标 `np.isclose(..., atol=tolerance, rtol=0)`，其容差区域是 L∞ 方框；因此接近容差边界的 `origin_symmetry` 数值结果也可能随坐标朝向改变，元数据保守标记为非旋转不变量。
7. 原径向指标相对燃烧室原点计算。它们旋转不变，但平移点集会改变其值；这正是 chamber-centred 描述的预期语义。

## 3. 最终正式 metric inventory

设点为 p_i=(x_i,y_i)，中心半径 r_i=||p_i||，允许中心半径 U=R-d/2。N >= 2 时，每点最近邻距离为 q_i=min_{j != i}||p_i-p_j||。所有 std 都是总体标准差（`ddof=0`）。

| key | 数学定义 | 单位 | 方向 | 定义域 | 优化资格 |
| --- | --- | --- | --- | --- | --- |
| `min_center_distance` | min_i q_i，即所有无序点对距离的最小值 | mm | maximize | N >= 2 | 是；legacy |
| `mean_nearest_neighbor_distance` | mean(q_i) | mm | descriptive | N >= 2 | 否 |
| `std_nearest_neighbor_distance` | population std(q_i) | mm | descriptive | N >= 2 | 否 |
| `nearest_neighbor_cv` | std(q_i) / mean(q_i) | dimensionless | minimize | N >= 2 且 mean(q_i)>0 | 是；未进入正式 profile |
| `uniformity_score` | 1 / (1 + CV_nn) | dimensionless | maximize | CV_nn 已定义 | 是；legacy |
| `minimum_spacing_margin` | min_i q_i - max(d,s_min) | mm | descriptive | N >= 2 | 否 |
| `minimum_boundary_margin` | U - max_i r_i | mm | descriptive | N >= 1 | 否 |
| `max_center_radius` | max_i r_i | mm | descriptive | N >= 1 | 否 |
| `mean_center_radius` | mean(r_i) | mm | descriptive | N >= 1 | 否 |
| `radial_std` | population std(r_i) | mm | descriptive | N >= 1 | 否 |
| `normalized_max_center_radius` | max(r_i) / U | dimensionless | descriptive | N >= 1 且 U>0 | 否 |
| `normalized_mean_center_radius` | mean(r_i) / U | dimensionless | descriptive | N >= 1 且 U>0 | 否 |
| `normalized_radial_std` | std(r_i) / U | dimensionless | descriptive | N >= 1 且 U>0 | 否 |
| `centroid_offset` | ||mean(p_i)|| | mm | minimize | N >= 1 | 是；未进入正式 profile |
| `normalized_centroid_offset` | centroid_offset / U | dimensionless | minimize | N >= 1 且 U>0 | 是；未进入正式 profile |
| `second_moment_anisotropy` | (lambda_max-lambda_min)/(lambda_max+lambda_min) | dimensionless | minimize | N >= 2 且中心化总二阶矩 > 0 | 是；未进入正式 profile |
| `x_axis_symmetry` | 每点均存在 (x,-y) 容差内配对 | dimensionless boolean | descriptive | N >= 0 | 否 |
| `y_axis_symmetry` | 每点均存在 (-x,y) 容差内配对 | dimensionless boolean | descriptive | N >= 0 | 否 |
| `origin_symmetry` | 每点均存在 (-x,-y) 容差内配对 | dimensionless boolean | descriptive | N >= 0 | 否 |

各向异性使用点集自身质心中心化后的二维总体协方差/二阶矩矩阵，特征值满足 lambda_max >= lambda_min >= 0。结果范围为 0 到 1：理想二阶矩各向同性为 0，共线非退化点集为 1。它对输入排列、平移和旋转不变，但不等价于流场或温度场均匀性。

空集的三种离散集合对称性按全称命题返回 `True`，但全部数值分布指标为 `None`。N=1 时最近邻、spacing margin 和 anisotropy 未定义；质心/径向值定义。N=2 的两个不同点可定义 anisotropy（为 1）；重合退化点的 CV、uniformity 和 anisotropy 为 `None`。有限但无效的 R/d/s_min 仍可得到诊断报告；U<=0 时所有除法归一化值为 `None`。

## 4. 量纲、不变性和解释

- length-dependent：所有单位为 mm 的指标；将坐标、R、d、s_min 同比扩大 k 后，数值扩大 k。
- scale-normalized：CV、uniformity、归一化径向值、归一化质心偏移、二阶矩各向异性；同比缩放后不变。
- dimensionless discrete：三个 symmetry 标志；精确对称点集同比缩放时不变，数值容差应随长度尺度解释。
- 表中声明旋转不变的连续指标经整体旋转后不变。三个离散 symmetry 标志均不作一般数值旋转不变性承诺：x/y 反射依赖固定坐标轴；原点反演在精确数学上与旋转可交换，但实际逐坐标绝对容差会使临界点集的布尔结果随坐标朝向改变。
- 所有正式指标对输入点列表排列不变。生成器自身的坐标顺序契约完全没有改变。

低 `centroid_offset` 只说明喷嘴中心的算术质心更靠近燃烧室中心；低 `nearest_neighbor_cv` 只说明局部最近邻间距更一致；低 `second_moment_anisotropy` 只说明二阶矩更接近各向同性。它们不能直接推出更低 NOx、更稳定火焰、更均匀出口温度、更高氢转化率、更低压降或更低壁面热流。

上述物理关系必须在固定边界条件和求解设置下通过 CFD 与实验建立、交叉验证并给出不确定度。M4 不产生 CFD 性能指标。

## 5. API 与兼容性

    from geometry import (
        evaluate_geometry_metrics,
        metric_definitions,
        validate_layout_constraints,
    )
    from optimization import get_objective_profile

    validation = validate_layout_constraints(
        points, N=24, R=55, d=4, s_min=8,
    )
    metrics = evaluate_geometry_metrics(
        points, R=55, d=4, s_min=8,
    )
    definitions = metric_definitions()
    profile = get_objective_profile("default")

- `evaluate_geometry()` 的原字段、函数签名和组合报告语义保留；原字段顺序也保留，新字段追加在后。
- 三个旧脚本和 `LayoutCandidate.summary_row()` 显式选择 pre-M4 字段，所以原 CSV 列名、列顺序和 PNG 行为不变。完整新指标仍保留在 `LayoutCandidate.metrics`。
- 新 M2 run 的 `metrics` 字典包含完整正式 inventory；JSON 的 undefined 为 null。M2 schema、validation、case ID 输入和归档路径均不变。
- `verify_case()` 对新 inventory 要求完整键匹配；对键集合恰好等于 pre-M4 inventory 的已有归档执行单向兼容核验，因此旧 M2 archive 不因增加指标失效。
- M3 `rows` 包含完整 metrics 与独立 validation 字段，planned/unique/feasible/infeasible 不会由描述性 metric 改变。默认显式使用 legacy profile。
- 没有修改 `CaseSpec.normalized_spec`，几何评价结果仍不参与 case identity。

## 6. 测试策略与阶段边界

M4 测试以人工点集和 Python 标量数学为 oracle，覆盖 spacing 四统计量、两种 margin、质心、二阶矩、N=0/1/2、退化点集、None/JSON、缩放、排列、旋转和平移不变性，以及 metadata/value 漂移保护。M2/M3/M0 回归继续覆盖 manifest、默认 426/415/11/18、坐标、CSV 和 PNG。

M5 可研究文献排列及其参数化适用范围，但不属于本阶段。CFD 阶段需要另行定义工况身份、网格无关性、求解收敛、物理指标及几何—物理相关性验证。当前几何体系可以为这些研究提供可复现描述量，但不能预先替代物理结论。

## 7. 验收记录（2026-09-08）

- 清理前 `tests/test_geometry_metrics_m4.py` 含 16 个测试函数、pytest 收集为 18 项（其中一个参数化函数生成 3 项）；原 902 项保留。
- 本轮新增原点对称容差边界旋转反例，并将 M2 指标兼容测试升级为完整归档与验证链路；该文件现含 17 个测试函数、pytest 收集为 19 项。
- 最终原样执行 `python -m pytest -ra`：collected 921，921 passed，0 failed，0 skipped，0 warnings，46.71 秒。
- 独立执行默认 M3 搜索：426 planned、426 unique、0 duplicates、415 feasible、11 infeasible、18 Pareto。
- 在临时目录实际生成 3-case M2 代表 run，并执行 `verify_run()`；3 个 case 的 identity、坐标、validation、metrics、文件 hash 和 CFD identity 检查全部为 True。临时 run 已在验收后清理。
- M0 输出回归实际执行三个旧脚本并通过：旧 CSV header/顺序、UTF-8 BOM、坐标编号/顺序和 PNG 契约未改变。
- 八类排列生成文件、`experiments/spec.py` 和全部坐标 fixture 均未修改；M4 只增加评价与目标元数据/连接逻辑。
