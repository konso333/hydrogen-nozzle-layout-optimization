# M8B-1：单喷嘴工程试运行执行框架

M8B 新增独立 `fluent_execution` 层，默认 prepare / inspect / dry-run 不启动进程、不创建 attempt。只有 `execute(..., authorize=True)` 或 CLI `--execute` 在重新核验全部执行输入后才调用 Fluent。此次开发没有运行 Fluent、占用许可证、生成真实网格或 CAS/DAT。

## 架构与原契约

| 层 | 保留职责 | M8B 的使用方式 |
| --- | --- | --- |
| M2 | `run_id` 是归档批次，`case_id` 是完整二维几何规格 | 用原 rectangular N=1 生成中心点及 CSV/PNG/验证记录 |
| M6 | `cfd_case_id` 是 case + 物理工况/模型摘要；UUID `attempt_id` 表示执行 | 复用 load/export/create_attempt/import_cfd_results，不改变状态和结果规则 |
| M7 | `automation_digest` 标识准备输入和模板；journal 全部为注释 | 执行前重新验证完整准备包；原文件不改写 |
| M8A | H1 七喷嘴科研预检，`execution_allowed=false` | 复用 LocalFluentConfig、环境检查与科研缺项报告，保留 H1 身份及门控 |
| M8B | 固定 purpose=shakedown 的工程执行 | 审阅绑定实际输入、可执行 journal、命令参数、数值设置和环境；独立授权 |

M8A `preflight()` 硬性绑定 H1，且部分 M7 数值映射固定 pending，因此不能直接作为 N=1 的入口。M8B 使用原 `ResearchInputGate.initial()` 和 `record()` 接收本地契约 `research_review`，读取其正式条目状态，再通过 `plan.py` 的固定 `SHAKEDOWN_MAPPING` 映射 29 个单喷嘴必需项。每项必须同时为 `confirmed` 且对应包/适配器检查通过；缺少记录、unresolved、pending 或缺少映射均阻断。`research_ready_for_single_nozzle_shakedown=false` 必然使 `m8b_ready=false`。

映射覆盖运行模式、三维域、喷嘴长度/内部入口拓扑、入口组分/温度/湍流、物理模型/材料/压力/出口/壁面、工程网格策略及验收、初始化/迭代/耦合/离散/残差/收敛方案、结果子集与实际面区。报告逐项给出 `gate_status`、`mapping_status`、`ready`；必需项没有适配规则时为 `pending_mapping_contract`。其余 M8A 条目列入固定的 `outside_shakedown_scope`，不更改原条目、H1 注册或原门的 `pilot_ready=false`、`execution_allowed=false`。科研网格无关性、实验对齐和正式结果提取仍未验证。调用者不能添加豁免、`not_required`、force 或 skip；`scientific` purpose 一律拒绝。

没有修改 M2–M8A 生产文件、身份算法、H1 registry、旧 CSV 或默认 426-case 搜索。此处的审阅引用沿用项目的来源声明模式，不是数字签名，也不能由文件 SHA-256 证明物理设置正确。

## 单喷嘴 manifest

`fluent_execution/single_nozzle.json` 区分 confirmed_baseline、execution_baseline、future_research_ranges 和 unvalidated。

确认尺寸为 D=14、L=30、Da=10、Db=4、Lb=8、Rb=2、dH=1.2、XH=4 mm，n=8、alpha=90 deg，维度 3D。H2 与氧化剂入口均为 Velocity Inlet、10 m/s、300 K，湍流强度 0.05；H2 质量分数 1，O2/N2 氧化剂质量分数分别 0.4/0.6。质量分数保存在 M6 带单位 extras，**没有误写为摩尔分数**，未推算质量流率或当量比。

出口 Pressure Outlet、表压 0 Pa；壁面 no-slip/adiabatic；操作压力 101325 Pa。Pressure-Based、Energy、Realizable k-epsilon、Species Transport、Volumetric Reactions、EDM，反应 `2 H2 + O2 -> 2 H2O`，A=4、B=0.5。初始化 Hybrid，预算 500 次；数值策略在执行契约及 attempt 中，不进入物理 identity。

入口水力直径 1.2/10 mm 是由已确认 dH/Da 在圆形入口假设下得到的派生值，并非独立测得或独立确认的水力直径。manifest 的 `hydraulic_diameter_provenance` 分别记录 `derived_from_confirmed_circular_hole_diameter_dH` 和 `derived_from_confirmed_circular_channel_diameter_Da`。M6 `inlet_topology` 必须显式确认两个入口均为 `circular`，相关 M8A 条目也须确认；缺失或环形拓扑触发 `hydraulic_diameter_topology_unresolved`。若实际氧化剂入口为环形，不能沿用 10 mm，须另行修订基准及契约。

物理基准逐字段严格匹配，燃料物种集合只能为 H2，氧化剂只能为 O2/N2；不允许额外 Ar 等物种。质量分数须为有限、非布尔的 `{value, unit: "1"}`，位于 [0,1]，总和为 1，分别匹配 1 与 0.4/0.6；总和及指定比例仅容许 `1e-12` 绝对舍入误差。

prepare 示例中的二维 R=7 mm 是 D/2 喷嘴投影包络，d=14 mm、N=1、中心 (0,0)。spacing=14 mm 只是旧 rectangular API 所需参数，单点不存在两喷嘴间距。它不是燃烧室半径或外部流场，不定义腔长、出口位置等三维计算域。单喷嘴内部几何通过 M6 simulation_config.extras.nozzle_geometry 进入既有 CFD identity。research ranges 不进入任何基准身份。

## 首次准备与检查

```powershell
python scripts/fluent_execute_pilot.py --prepare-example
```

只生成 `outputs/m8b/` 下的 M2、M6、M7 包，打印 package 和 automation_manifest 路径，attempts 为空。默认选择 pressure_loss 是满足 M6 非空结果子集的准备选择，提取仍 unresolved。

```powershell
python scripts/fluent_execute_pilot.py --package '<cfd_case.json>' --automation-manifest '<automation_manifest.json>' --fluent-executable '<FLUENT_EXECUTABLE>' --working-directory '<ABSOLUTE_IGNORED_RUNTIME_DIRECTORY>' --inspect
```

本地路径通过 CLI、`FLUENT_EXECUTABLE` / `FLUENT_WORKING_DIRECTORY` / `FLUENT_PROCESSES` 或 `--local-config .local/fluent.json` 传入，优先级 CLI > local JSON > 环境变量。local JSON 字段来自原 LocalFluentConfig，包含 executable、working_directory、processes、declared_release、plan_create_directory 等。不将本机路径传给 M6 spec 或 M7 build_spec。

工作目录在仓库内时必须位于 `.local/` 或 ignored outputs 子目录，排除保留代表性结果的 figures/coordinates/summaries；推荐 `outputs/m8b/runtime`。也可以使用仓库外的本地绝对目录。准备阶段不创建计划中的工作目录。

## 真实执行前必须补齐的输入

1. 提供真实、非空的 `.msh/.msh.h5`（existing_mesh）或 `.cas/.cas.h5`（existing_case）。本版不含 CAD/meshing builder；geometry_artifact 可在 CLI 指明，但明确报 unsupported_input_mode，不能启动。
2. 根据真实三维域补齐 M6：`simulation_config.steady_or_transient`；`simulation_config.extras` 中的 computational_domain、density_model、gravity_decision、radiation_model_decision、species_material_database；`operating_condition.extras.outlet_backflow`。数字继续使用原 `{value, unit}` 结构。不要把数值策略或文件路径塞入物理 extras。使用原 `CFDSpec.create/from_normalized`、`export_cfd_package` 生成新 CFD 身份与包，然后重新 `export_automation`。
3. 提供经本机 Fluent 2025 R2 审阅的**完整可执行 journal**，包含实际输入读取、单位、面区、物理模型、材料/反应、边界、初始化、数值策略、监测、迭代、输出与退出。M7 prepare.jou 全是注释，不能替代这个文件。journal 按运行目录相对文件名读取 `input.msh`、`input.msh.h5`、`input.cas` 或 `input.cas.h5`，输出至当前运行目录；不能依赖未声明外部 include 文件。M8B 不生成或猜测 TUI 命令。
4. 将 `examples/m8b_launch_contract.template.json` 复制到 `.local/`，完成下述审阅契约。模板为 null/空参数，故意不可执行。
5. 先 inspect，审计实际报告与 journal；由用户单独添加 `--execute`。一次授权只作用于本次调用，重跑创建新 M6 attempt。

## 本地 launch / adapter 契约

M6 补充物理字段采用以下明确结构；布尔值不能替代对象或说明，`not_required` 等豁免文本不作为有效定义：

| 字段（未特别注明者位于 simulation_config.extras） | 必需结构 |
| --- | --- |
| computational_domain | `{dimension: "3D", description: 非空说明, geometry_source: 便携来源引用}` |
| density_model | 非空模型说明 |
| gravity_decision | `{enabled: 布尔值, specification: 非空说明}` |
| radiation_model_decision | `{enabled: 布尔值, model: 非空说明}` |
| species_material_database | `{species: ["H2", "O2", "N2", "H2O"], reference: 便携引用}`，物种顺序不限 |
| inlet_topology | `{fuel_inlet: "circular", oxidizer_inlet: "circular"}` |
| operating_condition.extras.outlet_backflow | `{temperature: 正值 K 数量, mass_fractions: 归一化质量分数对象, turbulence_specification: 非空说明}`；仅允许 H2/O2/N2/H2O |

以上物理对象需经原 M6/M7 导出 API 重新生成包及身份。数值策略保留在本地契约中，不写入物理 identity。

| 字段 | 必须内容 |
| --- | --- |
| identity | 精确 run_id、case_id、cfd_case_id、automation_digest；从 inspect 获取 |
| launch | 明确 argv 参数列表、executable_sha256、release=`2025 R2`、3D/double、便携 evidence_reference |
| adapter_review | input/journal SHA-256、输入模式、3D、四类实际边界名称列表，以及 geometry/physics/mesh/numerics/output evidence |
| expected_outputs | 非空、不重复的平面相对文件名；不得覆盖输入、日志或本框架文件 |
| numerical_settings | Hybrid Initialization、500 次，以及耦合、离散、逐方程残差、质量/温度/物种/压损监测和收敛评估方案；瞬态另需 time_step/time_steps |
| resource_review | 显式 minimum_free_bytes、maximum_processes、license_evidence、resource_evidence |
| diagnostics | null 或 `{artifact, evidence_reference}`，artifact 必须在 expected_outputs 中 |
| research_review | 固定映射中全部 29 个 M8A key 的正式审阅记录；每项 `{status: "confirmed", source, reason, evidence_reference}`，由原 M8A API 校验 |

复制现有不可执行模板后，还须自行补入 `research_review` 和 `adapter_review.mesh_acceptance={checked: true, criteria: 非空验收标准}`；没有自动确认或默认豁免。`numerical_settings.residuals` 为非空的“方程名 → 正值 unit=1 数量”对象；四种 monitor 均为 `{definition: 非空定义, sample: 非空采样说明}`；耦合、离散、收敛评估须为非空文本。瞬态另需正值秒数 `time_step` 和正整数 `time_steps`。

launch.arguments 是**参数列表**，没有拼接 shell 命令；只支持 `{journal}`（固定 run.jou）和 `{processes}` 两种替换，两者必须出现。具体开关必须由本机官方文档或已审阅本地配置提供，本项目没有默认 flags。此配置是可执行内容的信任边界，只有受信任用户可以提供；它不是任意不可信 journal 的沙箱。

adapter_review 必须审阅 actual fuel_inlet/oxidizer_inlet/outlet/wall，不允许同一面名跨角色重复。文件扩展名和 hash 不能证明网格真为 3D 或面区存在，相关证据需来自真实 Fluent/CAD 输入检查。mesh_classification 固定 engineering shakedown mesh，不声称生产网格已验证。numerical_settings 与 M7 已声明设置冲突时拒绝，M7 缺少的细化设置由 M8B 审阅契约及 M6 attempt provenance 保存，不扩充或覆盖 M7 摘要语义。

磁盘余量按操作者明确预算检查，不设经验阈值；进程数按明确资源上限检查。许可证只记录人工检查证据，不调用许可证工具或占用 license，不能保证启动时服务器仍可用。release 也是绑定 executable hash 的人工审阅声明，M6 正式 solver_version 记录 `user-reviewed:2025 R2`，不伪称自动测得版本。

本次只读安装核查找到了环境变量 AWP_ROOT252 所指安装中的 fluent.exe、`FLUENT.Build.win64` 与 `fluent25.2.0/info/readme.txt`，后者说明版本 25.2.0，构建信息属于 release-25.2。存在本地帮助目录，但未完成实际 argv、journal、模型和输出映射验证。因此当前无已验证的可运行启动配置，报告 `unverified_launch_contract`。没有运行 `fluent --help` 或任何 Fluent 程序。

## 执行生命周期、输出与失败

execute 每次重新加载 M6/M7，检查输入与审阅，然后在 ignored 工作目录建立独占 staging 目录。复制输入和 journal、核对 hash、保存 contract.json、打开 stdout/stderr 后再做最终核验；只有即将进入 process runner 才调用原 create_attempt。目录随机名只用于文件存储，不是新增科学或执行身份；执行身份仍是 M6 attempt_id。

成功 Popen 后才将 M6 标 running。shell=False，stdin=DEVNULL，stdout/stderr 流式写文件，保存退出码。超时、启动失败、非零退出、缺失产物、诊断解析错误及 KeyboardInterrupt 都保存报告并重新抛出异常，M6 attempt 终止为 failed。staging 或最终门失败时不创建 attempt，运行目录保留 `execution.json` 说明失败，不静默清理证据。

Windows 使用 kill-on-close Job Object，POSIX 使用独立进程组，尽力清理已纳管进程并回收 launcher。Windows 在 Popen 后分配 job，创建到分配之间存在竞态窗口；窗口内创建或脱离的子进程未验证为完全纳管。首次人工 shakedown 必须核查该安装的 MPI/launcher 作业行为。新增 mock 覆盖 `WindowsJob.assign` 失败时的 launcher kill、wait 回收、异常传播与 M6 failed 终态。本次没有真实 MPI 完整进程树回收验证，不作完全保证。硬断电或进程被强制杀死可能留下 running 记录，需人工核查；没有实现自动恢复或抢锁。

每次目录保存 `input.*`、run.jou、contract.json、stdout.log、stderr.log、execution.json 和期望产物。报告包含原身份、attempt_id、purpose、开始/结束时间、退出码及产物大小/hash。本机绝对路径只在本地报告。M6 package 内 `execution_logs/<attempt_id>.json` 保存便携关联、主错误、辅助错误及可获得的文件 hash；M6 error.relative_log_path 在关联日志成功时指向它，失败时为 null。原 attempts 目录只保留标准 attempt JSON。

失败处理保留主异常和退出码，逐项捕获文件摘要、最终 execution.json、关联日志写入的辅助异常，再在独立 `finally` 中尝试原 M6 failed 终态更新。此顺序为了将辅助错误一并写入不可变终态；M6 更新不以任何辅助报告成功为前提。已有主异常时重新抛出原异常并附 `secondary_reporting_failures`，M6 错误消息也包含辅助失败；仅辅助失败时抛出 `ExecutionFinalizationError`。若 M6 终态更新本身失败，显式抛出 `AttemptTerminalUpdateError`，同时携带原执行错误/退出码、辅助报告错误、终态写入错误；不能宣称此时 attempt 已落为 failed。诊断解析失败同样进入终态处理。磁盘/权限故障下报告文件只能尽力保存。

**solver_status=completed 只表示进程返回 0 且期望产物存在。** 本版五项正式指标都为 unresolved_result_mapping/null。M6 completed 要求其必需指标有值，因此本版成功工程进程的 M6 attempt 仍以 failed/unresolved_result_mapping 终止；不是数值求解失败，也不能把它伪装 completed。CLI inspect 返回 0 只表示报告成功；执行拒绝返回 2；进程成功但正式指标未提取返回 3；其他异常非零退出并留报告。

## 诊断数据与科研隔离

经审阅的外部 journal 可以输出以下版本化 JSON；不是对未知 Fluent 日志的正则猜测。未配置 exporter 时 diagnostics=null。

```json
{
  "schema_version": 1,
  "sample": "明确的迭代号或采样时刻",
  "metrics": {
    "iteration_count": {"value": 500, "unit": "1", "source": "实际报告及采样说明"}
  }
}
```

允许 maximum_temperature(K)、mass_balance_error(1)、iteration_count(1 整数)、residual_summary(1，按方程名存值)。上例只是格式示意，不是运行数据。所有值要求有限、非负并带 source；质量平衡误差的归一化定义由具体 exporter evidence 记录。只收集诊断，不自动判定收敛；mass balance、temperature、species、pressure-loss monitor 的具体定义和收敛评估方案必须在 numerical_settings 中明确。

五项 M6 科研 metric 均不制造值；NO/NO2 为 unsupported，NOx 为 pending_contract，值均 null。purpose=shakedown 与 scientific_eligible=false 同时进入执行报告、关联日志和 attempt numerical_settings。没有向 AI 数据集、旧 CFD CSV 或优化搜索自动导入的路径。

## 文件职责与验证

| 文件 | 职责 |
| --- | --- |
| fluent_execution/single_nozzle.json | 用户确认的物理/工程基准、未来范围、未验证项 |
| fluent_execution/baseline.py | 复用 M2/M6/M7 创建单喷嘴准备包 |
| fluent_execution/plan.py | ExecutionPlan、输入/审阅/资源门、inspect/dry-run |
| fluent_execution/execution.py | 显式授权、staging、M6 attempt、产物/错误关联 |
| fluent_execution/process.py | subprocess 与超时/进程树清理 |
| fluent_execution/results.py | 诊断导出解析、正式指标未解决状态 |
| fluent_execution/__init__.py | opt-in 公共 API |
| scripts/fluent_execute_pilot.py | prepare/inspect/dry-run/execute CLI |
| examples/m8b_launch_contract.template.json | 故意不可执行的本地审阅模板 |
| tests/test_fluent_execution.py | 所有 solver 路径均 fake/mock 的执行测试 |

运行 `python -m pytest -ra` 和 `git diff --check`。现有 `.gitignore` 已忽略 outputs/m8b、.local 及 Fluent 二进制产物，故不添加更宽泛规则，不改旧 fixtures。最终验收计数与当前缺项见本轮交付报告。
