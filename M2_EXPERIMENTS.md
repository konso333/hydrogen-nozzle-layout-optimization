# M2：实验身份、case 身份与结果归档

M2 在现有生成、几何验证、评价和导出接口之上增加独立归档层。没有改动八类排列数学公式、坐标顺序、搜索规格或 Pareto 逻辑，没有新增几何评价指标，也没有执行 CFD。M0/M1 的 708 项测试全部保留。

## 架构与兼容范围

现有链路为 `search_variable_n → search_layouts_for_n → evaluate_spec → generate_layout`，生成器通过公共几何层进行硬约束检查；`evaluate_geometry` 返回硬约束和描述性指标，随后旧导出器生成 CSV/PNG。

新增链路为 `CaseSpec → CaseRequest → archive_run → generate_case → 现有生成/验证/评价/导出`。归档使用 JSON 文件，不使用数据库，不增加依赖。`reproduce_case` 只读取 case manifest 后重新调用生成器；`verify_case` 将新生成结果与该 case 的局部文件比较；`verify_run` 独立检查完整批次关联并核验全部 case。

| 文件 | 变化及原因 |
| --- | --- |
| `experiments/__init__.py` | 新增独立实验包，不自动改变旧脚本 |
| `experiments/spec.py` | 新增不可变规格快照、默认值解析、规范化、SHA-256 身份和参数重载 |
| `experiments/provenance.py` | 新增有超时的只读 Git 查询，以及 Python、平台和直接依赖版本记录 |
| `experiments/archive.py` | 新增请求/生成结果对象、旧候选桥接、批次归档、结构化失败记录、局部复现和完整关联核验 |
| `json_values.py` | 中立 JSON 类型转换；身份规范化和旧 CSV 数字表示分开处理，不触发 IO 包或绘图初始化 |
| `scripts/archive_experiment.py` | 新增可运行的三 case 示例，以及单 case 核验命令 |
| `tests/test_experiment_identity.py` | 共 62 项身份、类型、provenance、旧 JSON 兼容及导入副作用测试 |
| `tests/test_experiment_archive.py` | 共 52 项归档、全部内置排列复现、完整关联及异常路径测试 |
| `M2_EXPERIMENTS.md` | 新增设计、使用说明、边界及验收记录 |
| `optimization/layout_search.py` | 仅给 `LayoutCandidate.summary_row()` 的参数 JSON 补充 NumPy/Decimal 转换 |
| `io_utils/export_summary.py` | 仅给嵌套 dict/list/tuple JSON 补充同样的转换 |
| `README.md` | 增加独立归档入口及本文链接 |

旧公共函数签名、`LayoutCandidate` 字段和 `candidate_id` 均不变。三个旧脚本无需切换归档模式。CSV 列名、列顺序、UTF-8 BOM、默认目录和 PNG 行为不变。原生 Python 参数仍使用原 JSON 数字表示、空格和排序；例如 `25.0` 不会在旧 CSV 中变成 `25`。新增转换拒绝不能表示的对象，不使用 `default=str`。

## 身份和规范化契约

`run_id = run_<UTC时间到微秒>_<16位随机十六进制后缀>`，标识一次实验运行。后缀只用于批次目录防碰撞；`mkdir(exist_ok=False)` 独占预留目录，碰撞时重试。它不作为科学规格的身份依据。

`case_id = case_v1_<完整64位SHA-256十六进制摘要>`。摘要输入为完整 `normalized_spec` 经 UTF-8 编码的规范 JSON：键递归排序、紧凑分隔符、禁止 NaN/Infinity。

| 参与 case 身份的字段 | 含义 |
| --- | --- |
| `schema_version` | 规格格式和规范化规则版本，当前为 1 |
| `layout_type` | 请求的排列类型，包括固定 baseline 名称 |
| `generator_type` | 实际复现所用的底层内置生成器类型 |
| `N` | 严格正整数喷嘴数量 |
| `geometry.R/d/s_min/tolerance` | 完整几何配置，包含已解析容差 |
| `layout_parameters` | 此生成器的全部参数和已解析默认值，含有数学语义的列表顺序 |
| `symmetry_tolerance` | 已解析的几何对称性评价容差，默认 `1e-6` mm |
| `units` | `length=mm`、`angle=rad`、`count=1`；指数等纯数无量纲 |

Git commit、branch、clean/dirty、Python 版本、平台、依赖版本、时间、实验名称、run ID、旧 candidate ID、文件路径、CFD 状态均不参与 case 身份。Git 和运行环境保存在 provenance；当前项目没有独立声明的发布版本，`project_version=null`。没有引入算法版本身份，不能把不同提交中的同一 case ID 理解为已经证明坐标相同。

数值规范化规则：

1. 数量参数先沿用 M1 的严格整数检查，不能先把 `24.0` 转为 `24` 再绕过契约。
2. NumPy integer/floating/bool 转成 Python int/float/bool；tuple 转 list，递归处理嵌套字典；布尔值不与数字 0/1 合并。
3. 身份模式将有限且整数值的 float 转成 int，因此 `55`、`55.0`、`5.5e1` 具有相同编码，`-0.0` 与 `0` 一致。其他 float 不做十进制截断或容差舍入，`nextafter(55,56)` 仍能改变身份。
4. 环排列沿用 M1 的有限 float 转换，因此合法的数字字符串、Decimal 半径/角度按实际计算精度规范化。Decimal 不承诺超越现有 float 生成器的额外精度。
5. 字典键只允许字符串并稳定排序；不排序列表。不同环半径顺序会改变身份及点顺序。
6. 输入 NaN/Inf 和不支持的对象明确报错。结果中的未定义数值单独采用 `null`，不会进入身份，也不会填成零。

默认值从当前内置生成器签名解析并显式保存；baseline 的固定参数展开为底层生成器参数，复现使用归档值。可选参数的 `null` 是生成器真实的模式标志：例如矩形 `rows/columns=null` 表示选取最近的网格点，环 `ring_offsets=null` 表示无附加相位，非均匀环 `ring_radii=null` 表示按保存的内外半径及指数生成。没有伪造行数、半径等不适用值，也没有复制这些算法的数学公式到归档层。

工厂、manifest 重载和直接 JSON 构造统一经过 `_complete_snapshot` 检查。只有工厂接受省略参数并解析默认值；直接构造和重载必须提供完整快照，缺少 `angular_offset` 等默认字段立即拒绝。数量类型在整数值 float 规范化之前检查，不能通过直接 JSON 构造绕过 M1。`generate()` 再检查一次完整性，未来签名新增字段时明确失败，不静默采用新默认值；已有参数仅改变默认值时，仍显式传入快照中的旧值。SHA-256 输入格式及正常 case ID 保持不变。

身份表示“完整规格”，不是“相同点集”的等价类：固定 baseline 与等价的通用排列可以有不同 ID；显式给出的冗余参数也不会被擅自删除。同一规范化规格必须具有相同 ID，参数字段发生变化可改变 ID，即使该变化只影响约束或评价、不改变坐标。

## 使用方法

独立示例会创建三个 case 并立即核验：

```bash
python scripts/archive_experiment.py
python scripts/archive_experiment.py --name "M2 repeat" --output-root outputs/runs
```

自定义批次：

```python
from config import GeometryConfig
from experiments.spec import CaseSpec
from experiments.archive import CaseRequest, archive_run

config = GeometryConfig(R=55, d=4, s_min=8, tolerance=1e-9)
cases = [
    CaseRequest(CaseSpec.create("A_Rectangular", 24, config)),
    CaseRequest(CaseSpec.create("ring", 24, config, {
        "ring_radii": [25, 48], "points_per_ring": [8, 16],
    })),
]
run_path = archive_run(cases, name="N24 comparison", description="Geometry only")
```

归档已有搜索候选：

```python
from optimization.search_n import search_variable_n

candidates, marked_rows, pareto_rows = search_variable_n(12, 40, config)
requests = [CaseRequest.from_candidate(candidate, config) for candidate in candidates]
run_path = archive_run(requests, name="Default feasible candidates")
```

`from_candidate` 必须传原始 `GeometryConfig`。它核对重新生成的有序坐标和指标，但不能从点集反推原来的 R/d/s_min/tolerance。保存的是完整几何规格，不将旧序号当作唯一科研身份。M2 不重构搜索，不自动持久化/重算 Pareto 排名；旧汇总继续负责排序结果，新运行保存所交入的完整 case 列表。

## 归档与失败行为

```text
outputs/runs/<run_id>/
    run.json
    cases/<case_id>/
        case.json
        coordinates.csv
        layout.png
        validation.json
        metrics.json
```

`run.json` 保存 schema、run ID、UTC 时间、实验名称/说明、状态、provenance、完整请求规格、计划数量、已完成数量和 case manifest 相对路径。通过 `cases` 可以找回所有已完成 case。`requested_cases` 也保留尚未完成的请求，便于诊断失败批次。

`case.json` 保存 run/case ID、legacy candidate ID、完整规格、单位、硬约束验证、几何指标、文件相对路径、各文件 SHA-256、provenance 引用和 CFD 状态。case 文件路径相对 case 目录；run 索引路径相对 run 目录。provenance 引用为 `../../run.json` 的 `/provenance` JSON pointer。完整 run 目录可搬迁，不依赖机器的绝对路径。

`validation` 完整复用公共硬约束报告：数量、边界、重叠、间距、可行性及失败原因。`metrics` 只保存其余几何描述指标，包括最近邻、径向分布和对称性；没有重复混入硬约束字段，没有新增物理评价。

一次 run 内拒绝重复 case ID，避免覆盖或丢失不同 legacy ID；重复生成请创建另一个 run。同一 case 可以出现在任意多个 run 中。

归档先记录 `running`，逐 case 更新索引，成功后标记 `completed`。生成或写出出现普通异常时，保存 `failed` 和结构化错误，原异常继续传播；已经完成的 case 保留可读。错误包含 `error_type`、兼容字段 `type`、可用时的 `error_code`、`message`、`case_id` 和相对 run 目录的操作路径 `relative_path`；生成阶段尚无文件时路径为 null。OSError 使用不含 filename/filename2 的 strerror，并对所有消息继续清理 Windows 盘符、UNC 和 POSIX 绝对路径，使用 `[absolute path omitted]` 标记。对无法精确划分的带空格路径保守清理，不将原始异常字符串直接写入长期记录；原异常仍完整抛给调用方。

进程被强制终止或存储设备不可写时可能留下 `running` 或未完成目录，这种批次不应当作完成结果；当前不实现续跑。JSON 采用同目录临时文件替换，避免正常写入时读到半份 JSON。

Git 查询使用参数列表、只读命令和每条 5 秒超时；Git 缺失、无提交、权限错误或超时均记录 `unavailable`、null 字段和错误类型，不中断几何计算。依赖记录覆盖项目直接使用的 NumPy、pandas、Matplotlib 和 pytest。没有保存整套操作系统/虚拟环境，也没有自动安装依赖。

用于论文、答辩、正式 CFD 交接的 run，应尽量在包含生成代码的 **clean Git working tree** 下生成，并记录依赖环境。`dirty=True` 仅表示存在未提交修改，当前系统不保存未提交源码快照，commit hash 本身不足以完全复现 dirty run。clean 状态也不能替代相应依赖环境。

## 重新生成与核验

```python
import json
from experiments.archive import reproduce_case, verify_case, verify_run

run = json.loads(run_path.read_text(encoding="utf-8"))
case_path = run_path.parent / run["cases"][0]["manifest"]
fresh = reproduce_case(case_path)  # 只读 case.json，不读取旧坐标或图像
assert verify_case(case_path)["overall_match"]
assert verify_run(run_path)["overall_match"]
```

或者使用：

```bash
python scripts/archive_experiment.py --verify outputs/runs/<run_id>/cases/<case_id>/case.json
```

重载要求规格 schema/字段完整、单位一致、默认参数齐全、case ID 匹配；缺少参数时不静默用当前默认值填补。`reproduce_case` 仅凭 manifest 加项目生成代码重新计算点和指标，即使 CSV/PNG 不存在也能工作。

`verify_case` 只比较该 case 的数量、有序 XY 坐标、喷嘴编号/Z、硬约束、指标、文件校验和及 CFD ID 的局部一致性，不要求所属 run 存在，也不声称完成 provenance/整个归档核验。坐标比较 `atol=1e-9 mm, rtol=0`；几何数值指标 `atol=1e-9, rtol=1e-12`；布尔和 null 精确比较。路径不能越出 case 目录。缺失文件或格式错误会抛出异常；可比较但不一致的内容返回检查项 False。CLI 对不一致返回非零退出码。

`verify_run(run_path)` 只接受 `completed` 批次，核对版本、计划/完成数量、完整请求规格和唯一 case 索引；逐一核对 case 与 run 的 run ID、case ID、legacy ID。case manifest 路径必须相对且位于 run 内；provenance 文件必须存在并指向当前 run.json 的 `/provenance`，其内容必须存在。CFD 状态必须属于 `CFD_STATUSES`，CFD 的 run/case ID 必须与所属 case 相同，`not_started` 不允许填入指标数值或结果文件。若提供 CFD 结果文件引用，必须为 case 内存在的相对文件。随后调用 `verify_case` 核验全部局部文件和再生成结果。元数据缺失/错配明确报错；可比较的文件差异返回 `overall_match=False`。完整核验不会创建文件、修复归档或执行 CFD。

```bash
python scripts/archive_experiment.py --verify-run outputs/runs/<run_id>/run.json
```

无参数示例归档完成后自动调用 `verify_run`。`reproduce_case` 的独立再生成能力不受这些完整核验要求影响。

文件 SHA-256 用于发现意外修改，不是数字签名，也不替代参数复现测试。PNG 只检查保存文件的一致性，不要求跨 Matplotlib/字体环境重新绘制时字节一致。

## CFD 交接契约

`case.json.cfd` 初始内容包含本 case 的 `run_id`、`case_id`、`status="not_started"`、`results_file=null`，以及现有五个 CFD 字段的 null 值：氢转化率、出口平均温度、出口温度标准差、压损、最大壁面热流。几何导出成功不等于已经运行 CFD，因此仍为 `not_started`。

允许的状态定义在 `CFD_STATUSES`：`not_started`、`exported`、`running`、`completed`、`failed`。`verify_run` 强制核对该枚举，`banana` 等非法状态报错；这不包含求解或状态调度器。

未来 CFD 结果应以 `(run_id, case_id)` 关联具体生成实例，以 `case_id` 跨 run 比较同一规格；CFD 结果文件另行记录求解配置、单位和来源，`results_file` 应相对 case 目录。不同 CFD 工况应拥有各自的求解记录身份，不能覆盖同一几何 case 下的其他工况。

现有 `optimization.cfd_metrics.load_cfd_results` 和 `CFDResult(candidate_id, ...)` 保持原格式。导入旧 CFD 表时，应先明确选择 run，再用其 `legacy_candidate_id → case_id` 映射关联，并检查映射唯一；缺失或歧义映射应报错，不能跨 run 仅按 candidate ID 自动连接。M2 尚未新增 CFD 表导入器、自动建模或求解，也不计算/补造 CFD 数值。

## 验收记录（2026-09-07）

- 修改后的原始 708 项测试先全部通过，耗时 16.10 秒。
- 首轮增加 85 项 M2 测试后执行 `python -m pytest -ra`：793 passed，0 failed，0 skipped，0 warnings，22.00 秒。首次沙箱内测试因 Windows 拒绝访问既有 `outputs/test_tmp` 而出现 fixture 错误，获准在沙箱外运行后全部通过；未修改测试目录配置或降低断言。
- 默认搜索仍为 **426 specifications / 415 feasible / 18 Pareto**。新测试还遍历全部 426 个规格，证明规范化后合法性与有序坐标保持一致；Pareto IDs、顺序和每 N 数量由既有 M0 测试保护。
- 八类参数化排列、四个 baseline，以及非均匀环的幂律分支均通过实际 CSV/PNG 归档和 manifest 重生测试。新测试的 13-case run 每个 case 都重新读取 JSON、生成坐标并检查指标。
- 原三个入口在 M0 测试中以默认参数实际执行，仅重定向输出位置；415 个搜索坐标 CSV、26 张旧 PNG、全部旧汇总及编号/顺序/BOM 回归通过。
- 独立执行 `python scripts/archive_experiment.py`，生成并核验 3 个代表 case，保存在 `outputs/runs/run_20260907T132952656153Z_40cca8856abc2c62/`。示例含坐标、图像、验证、指标、case/run manifests 和真实 Git/环境信息，均由既有忽略规则排除，不加入版本跟踪。
- 测试覆盖数值语义等价、全几何字段变化、N/类型/环/扇区参数变化、列表顺序、默认值、NumPy/Decimal、单点 null、跨 run 共存、目录冲突、错误规格/文件路径、归档损坏、失败批次记录、Git 不可用，以及旧 JSON 字节兼容。
- 分支为 `codex/m2-experiment-identity`；没有修改 main，没有 commit/push/merge/rebase。只修改三个旧文件（两处序列化和 README），既有测试、数学代码和输出文件均未修改。
- 最终执行 `git status`、`git diff --stat`、`git diff`、`git diff --check`，并对全部新文件执行 `git diff --no-index --check`；无空白错误，无被跟踪的 `__pycache__`/pyc，无误跟踪的临时 CSV/PNG。独立 CLI 核验示例 case 的全部检查项为 True，代表性扇区 PNG 已人工查看。

## 剩余边界与下一阶段

提交前审计发现的四处边界已局部修复：规格完整性旁路、完整关联核验不足、JSON 工具导入的绘图副作用、错误记录绝对路径。仍需区分以下边界：旧候选需要调用方保留原配置；Git dirty 只说明存在未提交改动，不会自动打包这些代码；复现依赖相应项目代码和数值环境。示例真实记录了生成时的 commit 及 dirty 状态，不能声称该 commit 单独包含未提交的 M2 实现。

归档 v1 支持本项目八类内置生成器和四个 baseline；动态注册扩展需要以后定义自身的完整参数和单位契约，旧注册接口仍正常工作。规范化/schema 或生成器签名发生不兼容变化时，需要显式迁移，不能静默解释旧规格。

M2 的身份、归档、参数复现和兼容验收已满足，具备进入 M3 的基础条件。本次在 M2 停止，未开始 M3。

## 小范围提交前清理验收

- 保留全部原有 708 项和首轮 M2 85 项测试，新增 29 项，且补强原数量类型、微小浮点变化和 baseline 默认参数测试。最终完整执行 `python -m pytest -ra`：**822 passed，0 failed，0 skipped，0 warnings，22.88 秒**。
- 新增用例覆盖直接构造缺少默认字段、修改真实生成器默认值后重载旧规格/manifest、19 种 run/case/provenance/CFD 错配、归档搬迁、禁止 CSV 读取的再生成、独立解释器导入后端检查，以及 Windows/UNC/POSIX/OSError 错误路径清理。
- 清理前后对比全部 426 个默认搜索规格及 9 个代表方案（共 435 项）：case ID 列表 SHA-256 均为 `cc3cb2e5d66a892b874096f1a3c8220df27d6cc8b794d3dad4a1ba4aeb5b80d4`；有序坐标/不可行状态列表 SHA-256 均为 `6aa7594236893eda42d1ef67cf2f45234e48f2d007dd45498e76a90261b5f529`。没有更新预期身份来迎合实现。
- 默认搜索仍为 **426 / 415 / 18**，旧三入口的默认 CSV/PNG/summary/candidate ID 回归全部通过。清理前保存的三 case 示例 run 也通过新的 `--verify-run` 全部核验。
- JSON 工具从尚未提交的 `io_utils/json_values.py` 移至根部 `json_values.py`；所有使用方已更新。其他改动仅涉及 `experiments/spec.py`、`experiments/archive.py`、两个旧序列化调用方、新归档脚本、两个 M2 测试文件、README 和本文；无新增框架、数据库、算法或另一份验证文档。
- `CaseSpec` 和旧生产 API 的签名不变；不完整的直接规格构造现在明确拒绝。新增 `verify_run` 和 CLI `--verify-run` 均为 opt-in。错误记录保留旧 `type/message` 键并新增结构化字段，初始 CFD 语义不变。
- 实际运行更新后的独立示例入口，在 `outputs/runs/run_20260907T141554962220Z_52b4a342eb5bfbd8/` 生成三 case，并自动通过完整归档关联及几何再生成核验。
- 最终 `git status`、`git diff --stat`、`git diff`、`git diff --check` 及新文件空白检查通过。实验/测试输出继续被忽略，没有 PNG/CSV/cache 或绝对本机路径 fixture 进入本次修改；main 与 HEAD 仍指向 `e06a5b9c650d32dcf0a1adfbc6a01c2e6ff5321b`，未 commit/push/merge/rebase。本轮四项清理无已知提交阻塞问题，在 M2 停止。
