# M3：可配置搜索空间与批量实验

M3 新增显式 JSON 实验设计、生成前预检查/计数、按 M2 身份去重、批量生成评价、现有 Pareto 筛选和 M2 归档连接。不新增排列、指标、CFD 建模或求解。旧脚本和生产 API 保持原行为。

## 现有架构与默认规格来源

原链路是 `search_variable_n → default_layout_specs → evaluate_spec → generate_layout → evaluate_geometry → mark_pareto_candidates`。M2 的 `CaseSpec` 提供规范化身份；`archive_run` 负责 run/case manifest、坐标、图片、验证、指标和 provenance。

旧工厂内部的实验选择包括：矩形间距倍数 `[1, 1.25, 1.75, 2.25]`，六角倍数 `[1, 1.25, 1.5, 2]`；外环为有效中心半径的 0.98 倍；双环内半径 28，三环内半径 20/36；环点数按 N 的约 1/3、0.18、0.32 分配；sector/spoke 枚举 3..min(N,16) 的整除因子，各组数量分别不超过 6/4，另有径向间距筛选和相位公式。N=12..40、R=55、d=4、s_min=8 还分别存在于脚本默认参数和 GeometryConfig。

默认 29 个 N 的各家族规格数量：

| 家族 | 数量 |
| --- | ---: |
| rectangular | 116 |
| hexagonal | 116 |
| ring | 29 |
| staggered_ring | 29 |
| nonuniform_ring | 29 |
| deterministic_irregular | 29 |
| sector | 44 |
| radial_spoke | 34 |
| 合计 | 426 |

`default_search_space()` 复用现有工厂，一次性把这套设计物化为显式的有序参数组。没有复制其公式，也没有改变旧工厂。每条默认组保留其 N 和原 `N{N:03d}_{layout_type}_{index:03d}` 编号。保存后的 JSON 包含实际参数，不依赖下一次调用工厂重新推导半径或点数；可以直接编辑参数组或几何值。修改 R 不会自动缩放保存的环半径，需按实验意图显式修改相应组。

加载已保存配置后，修改 R、d、s_min 不会重新调用 `default_layout_specs()`，也不会自动修改显式 parameter_sets 中的 ring_radii、spacing、inner_radius、outer_radius、radial_levels 或 sector 参数。如果研究意图要求布局随几何变化重新设计，应显式编辑 parameter_sets 或重新生成实验设计。

## 配置 API 与组合语义

`ExperimentSearchSpace.from_dict(data)` / `.load(path)` 在构造时预检查全部规格；`.plan()` 返回 M2 `CaseRequest`、来源路径和数量；`.planned_count` 在没有生成坐标的情况下报告去重前计划数。模型内部保存不可变 JSON；`to_dict()` 返回独立副本，`save(path)` 保存 UTF-8 JSON。

完整小型实例见 [examples/m3_small_search.json](examples/m3_small_search.json)。其 2 个 R × 2 个 N × 2 个家族构成 8 个规格。顶层字段为：

| 字段 | 语义 |
| --- | --- |
| schema_version | 搜索设计格式版本，严格整数 1；与 M2 schema 各自管理 |
| name / description | 实验名称（必填非空）和说明（可选） |
| symmetry_tolerance | 几何对称评价容差，默认 1e-6 |
| blocks | 按列表顺序展开的设计块 |

每个 block 包含 `geometry_space` 和 `groups`。几何四轴 `R / d / s_min / tolerance` 必须全部显式提供非空列表，按这个固定轴顺序做笛卡尔积；最后一轴变化最快。跨块按列表拼接，可通过多个块表达成组几何实验，避免不想要的几何交叉组合。

block 只控制哪些 geometry/group 组合被展开，不构成独立 Pareto 分组。多个 block 的合法 unique cases 默认仍进入同一个全局 Pareto。

每个 group 包含 `N`、一个 `layout_type` 和 `parameter_sets` 列表。多个 group 表达多个布局家族。N 接受单个正整数、离散整数列表、或含首尾的 `{"start":12,"stop":40,"step":1}` 范围；step 可省略且必须是正整数。拒绝 bool、4.0、4.9、倒序或空范围。

组内 N 与 parameter_sets 交叉；**每个参数字典内部不再做笛卡尔积**。例如环半径 `[20,40]` 和点数 `[4,8]` 是同一套配对环数据，不是两个搜索轴。sector 的分区数量和每区数量也应一起定义在同一字典内。依赖 N 的环分配/行列/分组数量应放在不同 N 的 group 中；不合法组合立即报错，不自动跳过或补全。只有适用家族签名接受的参数才能进入 CaseSpec。

展开顺序固定为 block → 几何组合 → group → N → parameter_sets。字典键顺序不影响展开或身份；所有列表保留原顺序，包括环半径、环点数和相位。JSON 不包含可执行表达式，不使用 eval/exec、YAML、数据库或新依赖。

`legacy_candidate_id` 为可选标签，只允许用于单 N、单参数集的组。旧默认预设保持原编号。新实验默认不制造 legacy 编号；跨几何值重复的 legacy 标签不作为唯一身份。

## 验证、去重与异常

构造配置和生成前计划均执行全量预检查：几何合法输入、N 类型、家族签名、M2 参数解析，以及 M1 行列乘积、环计数/长度、扇区/辐条分组和参数范围关系。环列表检查复用现有 `_validate_ring_inputs`。预检查不生成坐标，不用扩大容差来判定参数是否有效。

新增家族关系检查位于搜索空间模块，不修改旧生成器；将来生成器参数契约改变时需要同步检查及测试。CaseSpec 保留默认值解析和完整快照的唯一职责。没有另建 case 身份或 case 结果模型。

去重基于 `CaseSpec.case_id`，保留首次出现的 request；报告 `sources[case_id]` 记录所有原始设计路径，包括重复来源。相同规格的 `12` 与 `12.0` 间距、显式默认参数与省略默认参数会归并；N 的整数契约先检查，不能用浮点规范化绕过。原始配置保留所有路径及 legacy 标签，不会覆盖同名 case 文件。

重复来源的 legacy 标签不同时，结果行保留首次标签；其余标签通过 sources 路径及原 search_space 配置恢复。单独保存 search_report 不等于保存了全部原始标签。

`planned = unique + duplicates`；`unique = feasible + infeasible`。重复规格只执行一次。几何不满足边界、非重叠或最小间距约束属于不可行，记录 case_id、原因或验证报告。只有生成调用中的 `LayoutConstraintError` 被捕获；输入错误、未知类型、RuntimeError/TypeError 等原异常向上传播。评价阶段异常不被归入几何不可行。CaseSpec 原有解析异常同样向上传播，不转换为“不可行”。

## 批量与 M2 归档

```python
from experiments.search_space import ExperimentSearchSpace
from experiments.batch import run_batch

space = ExperimentSearchSpace.load("examples/m3_small_search.json")
plan = space.plan()
assert plan.planned == 8
report = run_batch(space, output_root="outputs/runs")
```

`run_batch` 复用原坐标生成、公共几何验证/评价及 `mark_pareto_candidates`。默认在整个实验的所有去重合法规格上执行原三目标 Pareto；包含多个 R/d/s_min 时也全局比较，使用者应据研究目的分开实验解读。`pareto=False` 可关闭。没有新增评分或燃烧性能结论。

tolerance 是数值几何验证容差，不是物理设计变量、燃烧性能指标或“越大越优”的参数。不同 tolerance 导致的 feasible/infeasible 差异应作为数值验证设置的敏感性谨慎解释，不能据此宣称设计或燃烧性能改善。跨 R、d、s_min、tolerance 的全局 Pareto 只表示所列规格在当前三目标下的非支配关系，不自动证明同等物理条件下更优。CLI 输出报告中已有的 `pareto_scope="all_unique_feasible_cases"`；关闭 Pareto 时该字段为 null。

报告记录 planned、unique、duplicates、feasible、infeasible、pareto_count、run_id、全部 case IDs、来源、不可行原因、完整展开规格和合法方案的几何评价行。未定义指标按 M2 约定保存 null；N=1 不伪造指标或 Pareto 资格。

传入 output_root 时，把**所有合法规格**交给原 `archive_run`，由 M2 再生成并保存。保留这个额外生成成本以避免复制归档逻辑或修改 M2 API。没有只归档 Pareto 集而丢掉其余合法 case。原 M2 run 的 planned_case_count 表示交给归档的合法规格数；搜索计划总数和淘汰数保存在 M3 报告。

因此，归档 case ID 序列严格对应 search_report 的合法 `rows`，而 `report.case_ids` 是包含不可行规格的全部 unique ID 序列；混合实验中不应要求两者相等。

```text
outputs/runs/<M2 run_id>/
    run.json                  # 原 M2 格式和 provenance
    cases/<case_id>/...        # 原 M2 坐标、PNG、验证、指标、case manifest
    search_space.json          # 原实验设计
    search_report.json         # M3 搜索结果及 M2 run_id，相对 run.json 引用
```

M3 两个附加文件不改变 M2 manifest/schema/provenance。`verify_run()` 继续验证 M2 文件、身份和再生成结果，**不核验附加文件**；测试另行重载 search_space，比较 case ID 序列及报告。附加文件写入失败会抛异常，不能把 M2 的 completed 状态解释为两个附加文件也已成功写入。没有实现续跑或事务数据库。

CLI 使用 `M2 archive verification (verify_run)` 标明核验范围。search_space 保存实验设计意图，CaseSpec 保存单 case 的完整可复现快照。搜索配置的 round-trip 在相同代码/默认参数环境下确定；跨版本时省略的生成器默认值可能变化，单 case 的长期复现仍以完整 M2 CaseSpec/manifest 为准。

零合法 case 时返回 `archive_status="no_feasible_cases"`、run_id=null，不调用要求非空请求的 M2，不伪造空 run。调用方可用 `space.save()` 和 `save_report()` 保存这类实验；CLI 支持 `--save-config` 和 `--report`。程序异常中断时不返回成功搜索报告；归档内部异常沿用 M2 的失败记录/传播规则。

## 独立入口与示例

```bash
# 默认只预检查和计数，不生成坐标
python scripts/run_search_experiment.py

# 保存默认设计，之后从 JSON 重复展开，不再调用默认工厂
python scripts/run_search_experiment.py --save-config outputs/m3_default.json
python scripts/run_search_experiment.py --config outputs/m3_default.json --execute --report outputs/m3_default_report.json

# 实际小型实验、M2 归档、自动 verify_run
python scripts/run_search_experiment.py --config examples/m3_small_search.json --execute --archive-root outputs/runs
```

归档目录沿用已有忽略规则；JSON 示例是设计文件，可以纳入版本控制。`generate_baselines.py`、`compare_layouts.py`、`search_variable_n.py` 均不需要切换。

## 文件范围与验收

新增 `experiments/search_space.py`、`experiments/batch.py`、`scripts/run_search_experiment.py`、`examples/m3_small_search.json`、`tests/test_search_space.py` 和本文。旧文件仅 README 增加入口说明。八类数学算法、旧公共接口、M2 身份/归档、原测试及旧 CSV/PNG/summary 均未修改。

验收记录（2026-09-08）：

- 完整执行 `python -m pytest -ra`：**897 passed，43.66 秒，0 failed / skipped / warnings**。原 822 项全部保留，新增 M3 75 项。首轮新增测试中有 3 个文件 fixture 因 Windows 沙箱拒绝访问既有 `outputs/test_tmp` 而失败；获准在沙箱外运行后通过，没有修改临时目录配置或降低断言。
- 独立运行默认新入口，得到 **426 planned / 426 unique / 0 duplicates / 415 feasible / 11 infeasible / 18 Pareto**。测试逐项比较全部 426 个 M2 规格身份、legacy 编号、可行状态，以及全部合法候选的完整有序坐标；Pareto 编号及顺序与旧搜索一致。
- 实际执行文档中的小型 JSON CLI：**8 planned / 8 feasible / 0 infeasible / 4 Pareto**，归档为 `outputs/runs/run_20260908T025605038699Z_ba555e07a8f3fb66/run.json`，CLI 自动 `verify_run()` 返回 True。8 个 case 均包含原 M2 的 CSV、PNG、验证、指标和 manifests；同目录保存 search_space/search_report。
- 新测试覆盖多 R/d/s_min/tolerance、N 单值/离散/范围、八家族成组参数、全量预检查、数量类型、家族隔离、不同路径同 ID 去重、键序与列表序、默认 JSON round-trip、M1 异常传播、全不可行和 N=1、批量 M2 归档及再生成核验。
- `git diff --check` 和新增文件的 `git diff --no-index --check` 无空白错误；生成 run 与测试产物均被现有规则忽略，没有新 PNG/CSV/cache 纳入本次改动。分支为 `codex/m3-search-config`；HEAD 和 main 保持 `8d42b26ec6fdaed30df1ea0ec98d90a4f7579b25`，未 commit/push/merge/rebase。

M3 验收满足，具备进入 M4 的工程基础，本次在 M3 停止。没有发现新的验收阻塞问题；上文已说明的范围限制（附加文件核验/写入边界、零合法结果无 M2 run、无续跑、跨几何 Pareto 解读）不应被当作已实现能力。M2 的 dirty provenance 边界仍适用：本轮未提交源码不能仅凭现有 commit hash 重现。

## 小范围提交前清理验收（2026-09-08）

- 本次只修改 CLI、README、本文和 `tests/test_search_space.py`。搜索空间模型、run_batch、默认 pareto=True、Pareto 算法、M2 身份/归档及八类数学构造均未改动。
- CLI 帮助说明显式快照、geometry 不自动重推布局参数、全局 Pareto 及 M2 核验边界；执行摘要增加已有 pareto_scope，核验输出标注 `M2 archive verification (verify_run)`。
- 原有 897 项全部保留，新增 5 个普通测试函数，M3 测试共 20 个函数 / 80 项。完整 `python -m pytest -ra`：**902 passed，50.81 秒，0 failed / skipped / warnings**。
- 不同 legacy 标签的重复规格测试得到 planned=2、unique=1、duplicates=1，记录两条来源且只评价一次；保存重载和字典键重排后 planned、unique、ID 顺序、sources 及两个原标签保持一致。
- 混合归档测试得到 planned=3、unique=2、duplicates=1、feasible=1、infeasible=1；归档 ID 序列严格等于合法 rows ID 序列，不等于全部 unique case_ids，实际 M2 归档通过 verify_run。
- 多 block 测试只展开 (R=50,d=3) 和 (R=60,d=4)，保持 block 顺序；第二个 block 的方案在全局 Pareto 中支配第一个，而各自单独运行时均为 Pareto，证明 block 没有隐式分组。
- CLI 帮助断言及实际单 case CLI 归档测试通过，输出包含全局 pareto_scope 和明确的 M2 核验成功信息。默认全量逐项兼容测试继续证明 **426 planned / 426 unique / 415 feasible / 11 infeasible / 18 Pareto**，case ID、legacy ID、有序坐标、可行状态及 Pareto 顺序均不变。
- 原三个入口、CSV/BOM/PNG/summary 与默认输出路径回归通过。git diff/check 检查无空白错误；输出和缓存未进入版本跟踪，main/HEAD 未改变，未 commit/push/merge/rebase。没有发现新的提交阻塞问题，在 M3 停止。
