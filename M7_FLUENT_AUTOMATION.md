# M7：Fluent 自动化准备与 dry-run

M7 将经过 M6 完整核验的交接包转换成可审计准备文件。当前仅生成几何中间规格、注释式 journal 模板、五层参数映射和 readiness 报告，不启动 Fluent，不创建网格、CAS/DAT、数值结果或 completed attempt。静态检查通过不表示可执行。

## 修改前审计与边界

已有 `optimization/cfd_metrics.py` 读取旧结果 CSV；`cfd/spec.py` 定义工况、模型和 CFD 身份；`cfd/handoff.py` 验证 M2 关联、复制原坐标并管理 attempt/result；`cfd/metrics.py` 定义五项物理指标；`scripts/cfd_handoff_example.py` 演示空结果交接。原仓库没有 Fluent capability 检测、journal、CAD/mesh builder 或 solver 启动实现。

M2 保存几何规格和归档，M3 批量设计与搜索复用 M2，M4 将几何硬约束、指标和目标区分，M5 添加文献来源和 opt-in cross_5；M7 不修改这些实现。新增 `fluent` 包是显式调用的独立准备层，M6 仍为物理规格唯一来源。没有修改 M6 文件、schema、哈希或公共接口。

新增文件职责：

| 文件 | 职责 |
| --- | --- |
| `fluent/spec.py` | 完整 M6 包核验、显式输入契约、五层映射和不可变 FluentAutomationSpec |
| `fluent/capability.py` | 只读环境变量/PATH/显式文件存在性探测 |
| `fluent/artifacts.py` | 确定性 journal、静态校验、导出、从 M6 重新派生的磁盘核验 |
| `fluent/__init__.py` | opt-in 公共 API |
| `scripts/fluent_prepare_example.py` | synthetic/test-only 示例及已有包准备/核验 CLI |
| `tests/test_fluent_automation.py` | 身份、单位、映射、篡改、无进程调用及兼容测试 |

## 身份与 FluentAutomationSpec

`build_spec(package_path, mesh=None, numerical_settings=None)` 首先调用原 `load_cfd_package`，核验完整 M2 run、所选 case、CFD identity、result contract、几何 manifest 和 CSV checksum。读取后的坐标与 manifest 再次核对 checksum，发现变化即拒绝。

`FluentAutomationSpec` 只通过工厂构造，保存不可变规范 JSON；`to_dict()` 返回副本。顶层引用原 `run_id / case_id / cfd_case_id`，保存原 `source_cfd_spec`、显式 `automation_inputs`、schema_version=1、template_version=`m7-preparation-1`，以及 geometry、mesh、physics、solver、post_processing 五层。物理原始规格的嵌套结构保留用于审计，实际映射按五层分开。

每个映射项均含 `status` 和 `source`；已提供的值单独存入 `value`，未提供时没有 value 字段，未解决或不支持时提供 reason：

- `resolved`：已能从来源提取该概念或参数，不表示已经应用到 Fluent。
- `unresolved`：尚缺研究者输入或必要的几何/边界证据。
- `unsupported`：当前没有对应 adapter/builder/extractor，保留已声明来源和值。

`automation_digest` 是上述规范 JSON 的 SHA-256，包含模板版本，仅标识准备产物内容，不是 CFD physical identity。它不含本机安装路径、探测版本或当前时间。相同 spec 和模板版本产生相同 journal 字节。原 run_id 是固定来源引用；新建 M2 run 的 run_id 会变，因此不同 run 的 journal 不承诺相同。

物理条件（含轴向长度、物种、模型）只能通过 M6 修改并重新导出，按 M6 原契约产生新的 CFD ID。M7 不提供覆盖物理参数的旁路。网格与数值准备设置只影响 automation digest；将来实际执行须记录到 attempt 的 numerical_settings 中，以支持同一物理 case 的数值方法比较。

## Geometry 与单位

读取 M6 `geometry/coordinates.csv`：严格要求列顺序 `nozzle_id,x_mm,y_mm,z_mm`，编号从 1 连续递增、点数等于 M2 N、z=0、数值有限、长度单位 mm。CSV 本身不含 run/case/cfd ID；其身份关联来自 manifest、源 M2 关联和 checksum，不能虚构 CSV 中存在 ID 列。

`coordinates_mm.csv` 按原字节复制，保留 BOM、顺序与数值。`geometry_input.json` 包含原 ID、来源、chamber_radius（M2 R）、nozzle_diameter（M2 d）、有序 centers_mm 与逐点 `centers_m = centers_mm × 0.001`，明确记录 `1 mm = 0.001 m`。R/d 保持带单位 mm；下游 builder 必须读取单位，不能把它们当作 m。没有改变任何原始文件。

轴向长度唯一已登记的物理扩展是 `simulation_config.extras.axial_length = {"value": 正数, "unit": "mm"}`，已提供时 resolved；缺失或 null 时 unresolved，其他单位/非正值/不支持的结构标 unsupported。没有猜测三维燃烧室尺寸，也没有声称 M2 有效圆域等同完整燃烧器硬件。

稳定 symbolic roles 在 `BOUNDARY_ROLES` 中定义为 fuel_inlet、oxidizer_inlet、outlet、wall、symmetry。当前没有已构建并验证的三维面区，所以不生成任何实际 boundary names，`geometry.boundary_names` 保持 unresolved；角色清单不是存在性证据。后续 builder 必须根据实际几何建立面区对应关系，只有实际存在的边界才可绑定名称。

## Mesh 与 solver 输入契约

所有输入均为研究者显式值，source 指向 `automation_inputs`；没有研究数值默认值，也没有把未记录的 Fluent 默认值当成 resolved。未知字段、错单位、非有限数、bool 冒充数字、非正尺寸、非法文本均拒绝。

| 层 | 字段 | 输入 |
| --- | --- | --- |
| mesh | global_size、local_nozzle_size | 正数 quantity，单位 mm |
| mesh | growth_rate | quantity，单位 1，值至少 1 |
| mesh | boundary_layer_request | bool；只表示请求，不代替层数/首层高度设计 |
| mesh | strategy | 非空便携文本；声明策略，不表示 mesher 已实现 |
| solver | discretization | 非空便携文本 |
| solver | convergence_criterion | 正数 quantity，单位 1；初版单标量声明，逐方程标准仍需后续扩展 |
| solver | time_step | 正数 quantity，单位 s |
| solver | max_iterations | 正整数 quantity，单位 1 |

未传字段保留 unresolved；即使全部提供，mesh.builder 与 solver.command_adapter 仍 unsupported。稳态 case 缺少 time_step 也会出现在保守缺项清单中；本版不进行适用性推理或推断默认收敛策略。后续必须细化初始化、压力速度耦合、逐方程离散/残差、瞬态采样等执行契约。

## Physics 映射

M6 已声明的 inlet_temperature、inlet_pressure、mass_flow_rate、equivalence_ratio、fuel/oxidizer mole fractions 按原单位和值映射。simulation_config 的湍流/燃烧模型和 model_name 保留原声明；dimensionality 位于 geometry，steady_or_transient 位于 solver。非 Fluent solver 明确 unsupported。

这些 resolved 项仅表示可审计的 Fluent 概念输入。模型名是自由文本，没有声称任意模型名在某版本可执行；`physics.model_adapter` 明确 unsupported。材料库、反应机理、模型适用性仍需核实。

公共入口量不能唯一确定多入口设置：equivalence_ratio 不能单独决定每股流量和组分；mole fractions 不自动变成 mass fractions；公共压力不能自动确定 gauge/absolute 或压力边界类型。`physics.boundary_setup` 因缺少真实面区、入口角色/分流、压力参考、出口/壁面条件保持 unresolved。

除已登记 axial_length 外，两个 M6 extras 中每个顶层扩展保留完整内容、来源并列为 unsupported，嵌套数据不会被静默删除。需要新的明确 adapter 才能改变映射状态。

## Journal、静态检查与产物核验

`generate_journal(spec)` 返回无 BOM 的 UTF-8/LF bytes。header 注释记录原 IDs、schema、模板版本和 ready_to_execute=false；后续逐层逐键稳定排序，记录每项值、状态、来源和原因。只引用当前准备目录内 `geometry_input.json`，不嵌入本机绝对路径、随机数或当前时间。

本版 `prepare.jou` **全部为注释**，是可审阅的 journal/template 准备骨架，不是声称可执行的 TUI 实现。没有编造或依赖特定 Fluent 版本命令语法。

`validate_journal` 检查编码、行结束、非有限 token、非法 placeholder、绝对 Windows 路径、注释白名单，并逐字节比较根据可信 spec 重新生成的模板。因此任何 ID、单位转换、边界名称、来源、路径或命令变更（含 POSIX/UNC 路径）均不能通过。请求 executable_ready=True 必定失败；所有 M7 报告都保持 ready_to_execute=false。

`export_automation` 只创建新目录，拒绝覆盖；输出：

```text
automation_manifest.json  # 最后写出的完成标志、IDs、digest、逐文件 SHA-256
automation_spec.json      # 完整来源与五层映射
geometry_input.json       # IDs、原 mm 坐标和派生 m 坐标等几何说明
coordinates_mm.csv        # 原 CSV 的逐字节副本
prepare.jou               # 注释式准备模板
readiness.json            # ready/unresolved/unsupported/warnings/errors
```

`verify_automation(package_path, automation_manifest)` 重新核验 M6，读取保存的显式 mesh/numerical 输入，重新构造 spec 与全部输出，再逐字节比较六个文件；不只相信保存的 checksum 或 readiness=true/false。核验拒绝逃出目录的产物 symlink；不修复文件或执行 solver。改变全部设置并重新生成一致产物是新的有效准备方案，不是可信数字签名；校验主要防止意外漂移，不证明来源真实性。

读写失败可以留下没有有效完成标志的目录，下一次不能自动覆盖或续跑。移交时应保留 M2 与 M6 相对目录结构；M7 不记录机器绝对路径，核验 CLI 由调用者显式指定 M6 包位置。

readiness 中 `ready` 是已解析字段列表；`static_valid` 表示产物符合当前模板；`unresolved` 和 `unsupported` 提供逐项路径，完整原因在 spec。它们都不能替代真实执行、几何建模有效性、收敛或网格无关性验证。报告 warnings 是显式范围说明，与 pytest 的 Python warnings 无关。

## 五项后处理计划

计划直接引用 M6 result_contract 的公式、单位、定义域和 required 标志，附以下数据需求，均标 unsupported，不输出数值结果：

| Metric | 未来 Fluent report/surface/field 需求 |
| --- | --- |
| hydrogen_conversion | 声明全部入口/出口面，H2 组分质量流率、流向/回流检查，入口 H2 流量为正 |
| outlet_temperature_mean | 出口面元面积与静温，面积加权报告 |
| outlet_temperature_std | 与均温相同面区和样本的面积/静温导出，计算总体面积加权标准差 |
| pressure_loss | 入口/出口面积及总压、共同压力参考，两端面积加权报告之差 |
| max_wall_heat_flux | 所有声明燃烧室壁面的局部法向总热流密度，导出后取绝对值最大值 |

每项仍需研究者确认具体面区和稳态样本/瞬态时间窗。NOx 继续 pending_contract，不实现提取，不进入五项正式结果。

## Capability 与 execution provenance

`audit_capability(executable=None, environment=None)` 不执行版本命令、GUI 或任何外部程序。显式路径只检查该路径；未指定时按 FLUENT_EXECUTABLE、FLUENT_EXE、AWP_ROOT### 和 PATH 查找文件。AWP_ROOT### 的标准候选位置为 fluent/ntbin/win64/fluent.exe；这不是全磁盘扫描，其他安装布局可显式传路径。

找到文件仅返回 detected；版本保守返回 unknown。环境变量的 ### 可记录 installation_version_hint，但未验证，不能冒充实际执行版本。找不到时返回 not_detected、executable=null、version=unknown，M7 继续正常工作。

探测结果仅供当前调用返回/CLI 显示，不写入确定性产物或 CFD identity。未检查 license、软件能否启动、插件或模型是否可用。

**M6 既有契约差异：**任务概述将 Fluent version 统称 execution environment；仓库 M6 已明确规定唯一正式位置是 `attempt.provenance.solver_version`，并拒绝在 `execution_environment` 再放版本。本实现遵守现有 API：真实平台/主机/提取工具信息进入 `attempt.provenance.execution_environment`，实际 Fluent 版本通过 `create_attempt(solver_version=...)` 记录。M6 不接受 executable path 字段，路径保留执行端本地配置，不擅自扩展旧结果契约。

## 运行示例与首次执行的准备

```bash
# 新建 synthetic/test-only M2/M6 示例，无 CFD 数值或 attempt
python scripts/fluent_prepare_example.py

# 现有 M6 包：显式指定一个全新的输出位置
python scripts/fluent_prepare_example.py --package <cfd_case.json> --output-root outputs/fluent_review

# 只读核验已有产物
python scripts/fluent_prepare_example.py --package <cfd_case.json> --verify <automation_manifest.json>

python -m pytest -ra
```

显式 mesh/numerical 示例（仅 synthetic/test-only，不是研究推荐值）：

```python
from fluent import export_automation, verify_automation

manifest = export_automation(package_path, "outputs/fluent_explicit",
    mesh={"global_size": {"value": 1, "unit": "mm"}},
    numerical_settings={"max_iterations": {"value": 10, "unit": "1"}})
report = verify_automation(package_path, manifest)
assert report["static_valid"] and not report["ready_to_execute"]
```

首次真实 Fluent 执行前仍需：研究者确认三维几何与实际边界；通过 M6 完成物理工况、模型和化学机理；确认 mesh 与逐方程数值策略；实现并针对目标版本审核 CAD/mesher/TUI adapter；确认面区、采样和后处理；核实实际运行环境与 license。当前的注释模板不能直接承担首次求解。

未来真正执行时才调用原 `create_attempt` 登记实际 numerical_settings 和 execution provenance；实际启动后通过 `import_cfd_results` 标记 running；提取真实值后按 M6 `{value, unit, source}` 回填 required metrics 并标 completed，失败填结构化 error。不得跳过 running 或用 prepared 标志制造完成结果；重跑创建新 attempt。M7 本次不创建或修改任何 attempt，不开始 M8。

## 本次验收记录（2026-09-08）

- 完整 `python -m pytest -ra`：**collected 1116，1116 passed，136.40 秒，0 failed / skipped / warnings**。保留全部原 1063 项，新增 M7 53 项。初次沙箱内运行遇到既有 outputs/test_tmp 的 Windows 拒绝访问，经批准在沙箱外运行；没有改变 pytest 配置或降低断言。
- 独立运行默认 M3 run_batch：**426 planned / 426 unique / 415 feasible / 11 infeasible / 18 Pareto**。M0 旧入口、CSV/PNG/summary、candidate ID、BOM 和点序回归全部通过。
- 最终代码实际执行 `python scripts/fluent_prepare_example.py`，输出并完整复核 `outputs/fluent/prepared/8d91dc8c7af108c6035168d2d3cb1a484b8396966bdc1af7d1d181ae05e0204e/automation_manifest.json`；对应 M6 包位于 `outputs/fluent/cfd/8d91dc8c7af108c6035168d2d3cb1a484b8396966bdc1af7d1d181ae05e0204e/cfd_case.json`。
- 示例为 synthetic/test-only；static_valid=true、ready_to_execute=false，8 项 resolved、21 项 unresolved、10 项 unsupported。探测结果 not_detected、version=unknown；没有执行 Fluent，没有 CFD 数值或 attempt。原 cross_5 case_id 和固定 cfd_case_id 的兼容断言通过。
- 测试覆盖原源文件逐字节不变、mm→m 逐点转换、显式网格/数值与轴向长度、未知物理扩展、搬迁确定性、六个文件篡改、ID/单位/边界/命令/编码篡改，以及 monkeypatch 外部进程入口后的完整 M7 API。
- 执行 git status、git diff --stat、git diff、git diff --check，并对全部新增文件进行 no-index whitespace 检查；无空白错误。旧文件仅 README 和 .gitignore 更新；M6、算法、旧测试与参考输出未修改。生成 outputs/fluent、pyc/cache 和 solver 二进制扩展均被忽略，无误跟踪产物。
- 分支保持 codex/m7-fluent-automation，没有 commit/push/merge/rebase，没有修改 main。没有发现本次 M7 提交阻塞；当前未提交代码仍需保留源码，不能仅凭旧 Git commit 重现。真实执行缺项见上节，本次止于 M7 准备层。

## 提交前局部清理（2026-09-09）

本轮只修改 `tests/test_fluent_automation.py` 和本文：修正示例 unresolved 数量为 21，保留原测试并新增 4 个测试函数、10 个参数化执行项。没有修改 `fluent/artifacts.py` 或其他生产实现，没有改变任何身份、输入语义或 journal 模板。

新增测试覆盖坐标 CSV 修改、geometry_input 的 source/unit/三个身份字段修改，以及 readiness 的可执行标志或缺项状态修改。每次先同步保存的 manifest SHA-256，并独立断言全部文件均满足保存的 hash，再要求 `verify_automation()` 拒绝。原始 M2/M6 文件另做前后逐字节比较，保持不变。这些用例保护从可信 M6 重新派生的行为，不能被仅检查保存 hash 的实现满足；manifest hash 是完整性记录，不是信任根或签名。

路径测试分别覆盖 `automation_spec.json` 的读取前检查和 `readiness.json` 的逐文件检查。外部文件与包内原文件内容完全相同，所有保存 hash 均匹配，拒绝只由解析后的目录越界引起。测试优先尝试真实 symlink，仅在 Windows 返回权限拒绝（WinError 5/1314）时模拟该子文件的 `Path.resolve()` 返回外部路径；其他路径正常解析，其他错误直接抛出，不 skip。本机定向测试实际采用两项 resolve 模拟，原因是 Windows symlink 权限不足；不将此结果称为真实 symlink 测试通过。

定向执行新增测试：10 passed、53 deselected，无失败、跳过或警告。当前生产核验实现通过全部新增测试，未发现对应绕过，因此不做实现修复。独立默认搜索仍为 426 planned、426 unique、415 feasible、11 infeasible、18 Pareto。

最终完整 `python -m pytest -ra`：**collected 1126，1126 passed，136.82 秒，0 failed / skipped / warnings**；原 1116 项全部保留。M7 测试现为 17 个函数、63 个执行项。固定 case_id/cfd_case_id、原坐标和注释式 journal/no-process 保护继续通过，M2–M6、默认搜索、几何评价和文献布局均未修改。

检查 git status、diff/stat、diff/check 与未跟踪文件，并检查新增文件空白：无错误或无关新增。52 个 outputs/fluent 生成文件和 72 个 pyc 存在但全部 ignored，没有跟踪 solver 产物或缓存。生产模块文件哈希与本轮开始时一致，HEAD/main 仍为 `1a7c057f8e18dcffbb3de22535efdb4904f18fd0`。没有 commit/push/merge/rebase，没有启动 Fluent 或开始 M8；本轮两项清理完成，未发现剩余 M7 提交阻塞。
