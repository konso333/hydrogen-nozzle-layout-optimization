# M1：输入与验证契约修补

本阶段只修补已确认的五类漏洞。未修改排列数学公式、点顺序、默认配置、搜索空间或输出格式；未实施 M2/M3、文献排列或 CFD 工作。

## 调用链与改动

原调用链：`search_variable_n → search_layouts_for_n → evaluate_spec → generate_layout → 注册生成器`。内置生成器通过 `finalize_layout → ensure_layout_feasible → validate_layout_constraints` 验证；随后搜索计算几何指标并进行 Pareto 筛选。原公共入口没有验证扩展生成器输出，搜索还会将任意 `ValueError` 转成 `None`。

现在 `generate_layout` 在调用生成器前检查公共输入，并对最终返回值再次调用统一验证。内置生成器保留直接调用时的最终验证；新增前置输入检查，避免在网格分配、整数转换或三角函数运算之后才发现错误。搜索只捕获生成阶段的 `LayoutConstraintError`，指标计算位于该捕获范围之外。

| 漏洞 | 生产代码变化 | 新增测试 |
| --- | --- | --- |
| A：NaN/Inf 参数绕过检查 | `validation.py`、`config.py`、各生成器、`geometry/constraints.py`、`geometry/symmetry.py` | `test_config.py`、`test_layout_input_contracts.py`、`test_constraints.py`、`test_generation_validation.py` |
| B：数量小数/bool 静默转换 | `validation.py`、`layouts/_common.py`、各生成器、`geometry/constraints.py`、两个搜索模块 | `test_layout_input_contracts.py`、`test_constraints.py`、`test_search_contracts.py` |
| C：注册生成器缺少最终验证 | `layouts/__init__.py` 统一调用 `finalize_layout`；几何层拒绝错误坐标结构 | `test_generation_validation.py`、`test_search_contracts.py` |
| D：搜索吞掉输入错误和程序异常 | `optimization/layout_search.py` 缩小捕获范围；`layouts/hexagonal.py` 将晶格容量不足归为几何不可行；未知类型使用独立异常 | `test_search_contracts.py` |
| E：未定义目标进入 Pareto | `optimization/objectives.py` 在比较前检查目标资格，排除无效行 | `test_objectives.py` |

## 每个生产文件的修改原因

| 文件 | 修改原因 |
| --- | --- |
| `validation.py`（新增） | 集中实现有限实数、非负有限容差、整数数量及数量序列契约；定义 `InputValidationError` |
| `config.py` | 在原有取值范围检查前拒绝非有限值；输入错误使用明确子类 |
| `geometry/constraints.py` | 公共检查拒绝非有限参数和非法容差；期望数量必须为整数；形状错误不能因数组元素总数为零而绕过检查；保留有限非法几何配置的失败报告 |
| `geometry/symmetry.py` | 校验容差，复用有限坐标与形状校验，避免无限容差误报对称 |
| `layouts/_common.py` | 复用公共输入检查，并统一整数规则；保留最终几何验证 |
| `layouts/__init__.py` | 生成前检查公共输入，生成后统一验证和规范化输出；定义未知类型异常 |
| `layouts/rectangular.py` | 校验 N、几何参数、节距、行列整数及参数组合 |
| `layouts/hexagonal.py` | 去除 `row_counts` 的任意 `int()` 转换；明确晶格容量不足的异常类别 |
| `layouts/ring.py` | 去除各环数量的任意 `int()` 转换；连续参数先转 float 再检查有限性；中心点开关支持 Python/NumPy 布尔标量 |
| `layouts/radial.py` | 校验辐条数、每条数量和有限半径/角度 |
| `layouts/sector.py` | 校验扇区数、每区数量、径向层数及有限半径/角度 |
| `layouts/irregular.py` | 在构造前校验 N、半径、指数和角度增量 |
| `optimization/layout_search.py` | 检查搜索数量；只有明确几何不可行返回 `None` |
| `optimization/search_n.py` | 在 `range()` 前校验两个端点的整数契约和顺序 |
| `optimization/objectives.py` | 目标先转 float 再检查有限性；明确未定义目标处理，保留原支配关系和容差公式 |

## 输入和异常契约

- 通用几何配置与非环生成器的实数检查仍使用 `numbers.Real` 标量，要求有限；容差必须非负，生成入口保留正半径、正直径等原有限制。本轮未扩大这些入口的类型范围。
- 环排列连续参数（`ring_radii`、`angular_offset`、`ring_offsets`、`delta_theta`、内外半径和径向指数）接受可转换为有限 float 的值，包括有限数字字符串、Decimal。先转换再检验，无法转换的值及转换后 NaN/±Inf 均拒绝；原有取值范围和数学公式不变。
- 数量接受 `numbers.Integral`，包括 Python `int`、NumPy 有符号/无符号整数；拒绝 `4.0`、`4.9`、Python/NumPy bool、字符串、None、非有限值、负数和零。仅在验证整数类型后规范化为 Python `int`。
- 几何检查的可选期望数量仍允许 `N=None`，也允许 `N=0` 检查空点集；生成与搜索的 N 必须为正。`row_counts=None` 保留自动晶格模式；`points_per_ring=None` 无效。环/行数量序列必须非空。
- `include_center` 只接受 Python `bool` 和 NumPy `bool_` 标量，规范化后使用；拒绝 0/1 数字、字符串、None、容器及布尔数组。布尔开关与严格整数数量是两种独立契约。
- `validate_layout_constraints` 和几何评价对有限但非法的 R、d、s_min 返回 `feasible=False` 及失败原因。例如 `s_min=-1` 返回 `spacing_ok=False`；生成入口仍抛输入异常。非有限参数、非有限坐标、错误结构、非法期望数量及负容差仍抛异常，不转换成正常报告。
- 公共生成入口按请求的 `tolerance` 验证点数、完整喷嘴边界、非重叠和中心距；返回有序的 `list[tuple[float, float]]`。合法扩展可返回可转换的 `(N, 2)` 数组，统一规范化后顺序不变。

| 情况 | 结果 |
| --- | --- |
| 合法规格和几何 | 返回 `LayoutCandidate` |
| 越界、重叠、间距不足、输出数量不符、晶格容量不足 | `LayoutConstraintError(ValueError)`；`evaluate_spec` 返回 `None` |
| 已明确检查的输入取值或参数组合错误 | `InputValidationError(ValueError)`，向上传播 |
| 未注册排列类型 | `UnknownLayoutTypeError(InputValidationError)`，向上传播 |
| 缺少/多余关键字，或部分错误容器结构 | 原生 `TypeError` 等异常向上传播 |
| 输出非有限坐标、错误形状或无法转换的坐标 | 明确的 `ValueError` 向上传播，不当作几何不可行 |
| 生成器或指标计算中的普通程序异常 | 原异常继续抛出 |

这是兼容原返回结构的最小异常改进，未给所有 Python 调用错误另建异常包装层。扩展生成器应仅在确实几何不可行时抛出 `LayoutConstraintError`；搜索无法判断扩展是否误用了这一类型。

## Pareto 契约与兼容影响

仅检查所选择的目标列，并按既有 `float(value)` 协议进行转换。`4`、`4.0`、`"4"`、`"1.0"`、`Decimal("10")` 等转换结果有限的目标可正常参与比较；bool 目标按同一协议转换成 0/1，这不放宽生成或搜索的数量参数契约。缺失目标、None、无法转换的字符串/对象、转换溢出及转换后 NaN/±Inf 都无资格参与支配比较：`pareto_frontier` 排除该行，`mark_pareto_candidates` 保留原始指标值并将 `pareto_candidate=False`。直接调用 `dominates` 比较无效目标会抛出 `UndefinedObjectiveError(ValueError)`，错误信息包含目标名。

N=1 的最近邻指标仍为 NaN，几何仍可合法，但不能进入默认三目标 Pareto 前沿；不以零或其他人为数值填补缺失指标。若显式只选择已定义目标（例如 N），则按所选目标判定资格。完全相同的有限目标互不支配，保留所有并列候选及输入行顺序。

函数签名、合法返回结构、CSV 列和 PNG 行为保持不变；新增异常类属于公共能力补充。已有 `except ValueError` 调用仍可捕获这些子类，但曾依赖搜索吞掉任意错误、数量强制转换或无效目标成为候选的调用方必须调整。多数原取值错误消息保留；新增消息包含参数名/序列索引。有限非法几何配置的诊断返回语义已恢复；生成入口的严格配置检查没有撤销。

为保持汇总列不变，没有新增 `pareto_eligible` 或排除原因列；现有 False 标志既可能表示被支配，也可能表示目标无资格。可查看原始目标或直接调用 `dominates` 定位未定义目标。

## 兼容性清理与验收记录

- 提交前审计发现严格实数类型策略误伤有限可转换输入，以及综合几何诊断从失败报告变为异常。本轮仅清理这两类兼容性回退并明确布尔开关规则，五项 M1 核心修复全部保留。
- 本轮修改 8 个文件：`optimization/objectives.py`、`geometry/constraints.py`、`layouts/ring.py`，以及三个对应测试文件、README 和本文；其他 M1 生产文件未修改。未实施 M2/M3 或新增排列。
- M0 基线为 88 项；兼容性清理前为 595 项，清理新增 7 个测试函数，调整已有参数化值后总数增加 113 项。
- 最终执行 `python -m pytest -ra`：collected 708，708 passed，0 failed，0 skipped，0 warnings，耗时 12.61 秒。
- 相对 M0 共新增 50 个 test function：35 个参数化函数展开为 605 项，15 个普通函数对应 15 项，共新增 620 项执行项；加上原有 88 项得到 708 项。没有把函数数和参数化展开数混称。
- 原有 24 项和 M0 的 64 项全部保留；M0 文件及静态 fixture 未改动，没有降低原断言。
- 默认 R=55、d=4、s_min=8、N=12..40 搜索仍为 426 个规格、415 个合法候选、18 个 Pareto 候选；原每 N 数量、Pareto ID 和顺序回归通过。
- 四基准、九方案完整有序坐标和指标、两种坐标 CSV、旧/新汇总表、PNG、确定性不规则与非均匀环分支回归全部通过。
- 实际运行三个脚本，生成并检查 26 张 PNG，以及 415 个搜索坐标 CSV。示例在 `outputs/test_tmp/m0_baselines0/`、`m0_comparison0/`、`m0_search0/`，属于既有忽略目录，不进入 Git。

| 测试文件 | 相对 M0 新增函数 | 相对 M0 新增执行项 |
| --- | ---: | ---: |
| `test_config.py` | 4 | 23 |
| `test_constraints.py` | 9 | 68 |
| `test_generation_validation.py` | 6 | 32 |
| `test_layout_input_contracts.py` | 12 | 398 |
| `test_search_contracts.py` | 11 | 33 |
| `test_objectives.py` | 8 | 66 |

本轮保留原有公共参数矩阵，未为减少 collected 数量而裁剪覆盖；目标测试改为比较数值/NaN 语义，不再要求无意义的对象身份一致。新增测试同时证明有限非法几何配置可以被诊断、却仍不能通过生成入口。

## 剩余限制和后续里程碑

参数记录仍有既有边界：`LayoutSpec.parameters` 原样交给 `json.dumps`。例如 `points_per_ring=[np.int64(4)]` 可以合法生成坐标，但 `LayoutCandidate.summary_row()` 会报 `TypeError: Object of type int64 is not JSON serializable`；Decimal 参数也需要 JSON 规范化。汇总代码本次未修改，生成器内部的浮点转换不会重写保存的参数。当前批量汇总参数应使用 Python 原生 `int`/`float`、字符串和 JSON 可序列化容器；参数记录的统一规范化纳入 M2，本阶段不增加归档实现。

搜索异常目前不包装成携带完整规格的诊断对象，Pareto 排除原因也未增加持久化字段；若 M2 需要保存这些诊断，可在结果记录契约中设计。默认搜索仍使用既有固定半径与规格构造，参数化重构留给 M3。

M1 五项验收已满足，具备进入 M2 的条件。本次在 M1 停止，未开始 M2。
