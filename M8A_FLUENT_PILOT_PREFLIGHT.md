# M8A：Fluent 单案例试运行预检与科研输入门控

M8A 只准备第一次 H1 pilot 的输入清单、环境检查和结构化启动计划。当前研究门 `pilot_ready=false`、`package_physics_ready=false`、`package_ready_for_pilot=false`、`ready_to_execute=false`、`execution_allowed=false`。不启动 Fluent、GUI，不构造三维 CAD/mesh，不求解，不产生 CAS/DAT、CFD 数值或任何 attempt。真实执行属于 M8B，本次不开始 M8B。

## 修改前审计和最小架构

已阅读 AGENTS、M5/M6/M7 文档、`fluent/` 与 `cfd/` 全部实现、M5 spacing JSON 和测试、M7 示例，并检索 capability/executable/working directory/launch/subprocess。

M2 提供完整 CaseSpec、几何验证、CSV/PNG 归档与 case_id；M5 H1 通过 opt-in JSON 复用原 hexagonal generator。M6 提供唯一物理规格 CFDSpec、工况身份、交接包、五项结果契约及独立 attempt。M7 从可信 M6 重新派生五层准备规格、注释 journal 和静态核验产物；capability 仅探测文件，不核验版本或 license。M7 没有经过核验的 2025 R2 CLI helper、CAD/mesh/physics/solver/extraction adapter。

新增独立 `fluent_pilot/`，不修改 M7 生产文件或 M2–M6 实现：

| 文件 | 职责 |
| --- | --- |
| `local.py` | LocalFluentConfig、本机检查、FluentLaunchPlan |
| `target.py` | 从 M5 选择 H1 S10 CaseRequest，独立核验拓扑与硬约束 |
| `research.py` | 固定必需项清单、证据声明、阶段门控、按来源查询缺项 |
| `preflight.py` | 只读核验 M6/M7、检查 pilot 身份、组装本地报告 |
| `__init__.py` | 显式调用的公共 API |
| `scripts/fluent_pilot_preflight.py` | 现有包只读预检，或显式新建空物理规格准备示例 |
| `tests/test_fluent_pilot_preflight.py` | 身份、几何、环境、门控、篡改及无进程回归 |

## 本机配置与身份隔离

`LocalFluentConfig` 是 frozen dataclass，包含 executable、working_directory、dimension、precision、processes、plan_create_directory，另有可选 declared_product/declared_release/installation_hint。3D、double、2 processes 是本次已明确的本地 pilot 启动选项，不能被解释为已经确认的 M6 三维物理模型。

使用 `--fluent-executable` / `--working-directory` / `--processes`，或 `FLUENT_EXECUTABLE` / `FLUENT_WORKING_DIRECTORY` / `FLUENT_PROCESSES`；CLI 非空值优先。不要求修改 PATH，不扫描磁盘，不保存真实本机路径。`.local/`、`local_config.json` 和 `outputs/*` 默认 ignored；未实现配置文件加载，环境变量与 CLI 足够。

用户已人工声明 ANSYS Fluent 2025 R2、安装标识 v252，并报告 3D/double/2 processes 的 GUI 启动和 license 成功。程序不会自动写入这些声明；显式传入声明参数后才记录 `user_declared:2025 R2`，`version_verified=false`、`license_verified=false` 始终保留。不执行 `-v` 或 `--version`。真实本机盘符和安装路径不写入本文、源码、测试 fixture 或科学 manifest。

可执行文件须为当前平台的本地绝对路径、存在的文件、名称 fluent.exe/fluent；UNC 路径拒绝。异平台 Windows 路径可作为本地配置字符串接受，但不能在非 Windows 主机被误报为已检测。目录须是本地绝对路径且可写；用 UUID 名称和 xb 独占创建空探测文件，单次尝试，随后立即尝试删除。不存在目录仅在显式 plan_create_directory 时检查最近现有父目录可写并报告 planned_creation，不实际创建。

`writable_probe.status` 区分 not_attempted、create_failed、cleanup_failed、writable。创建失败不删除任何已有文件；名称碰撞安全失败。清理失败报告 error_type 和 residual_probe_path，environment_ready=false，不重试、不删除其他文件，不声称没有残留；该路径只在本地报告中。成功路径无残留。写权限是时点检查，不保证未来磁盘容量、权限、资源或 license。M8B 应增加剩余磁盘空间、资源和许可预检，本轮不设经验硬阈值；2 processes 不表示许可已验证或 CPU 最优。

| 身份/数据 | 保持的含义 |
| --- | --- |
| run_id / case_id / cfd_case_id | 原 M2/M6 科学关联；本机路径、进程数不参与 |
| automation_digest | 原 M7 准备规格/模板摘要；本地配置从未传入 build_spec |
| attempt_id | 原 M6 执行身份；M8A 不生成或更新 |
| 本地报告/launch plan | 可能含本机路径，仅留在终端或 ignored 位置，不是科学 manifest |

相同来源包下变更安装路径、工作目录、进程数时上述三个身份和 automation_digest 不变。补齐 M6 的真实物理参数将按原契约改变 cfd_case_id；新建 run 会改变 run_id，继而可改变 M7 automation_digest。这不是本地环境污染。

## Pilot：复用 H1 hex7 S10

`select_pilot_request()` 加载 `examples/m5_hex7_spacing_study.json`，明确选择 N=7、spacing=10、d=4 的唯一现有请求，调用原 CaseSpec.generate。没有复制六角生成公式，也没有修改旧 generator、H1 registry、默认搜索或 M4 指标。

独立验证一个中心点、六个半径 10 mm 的外围点、相邻角差 60°、最近中心距 10 mm、d=4 mm、S/d=2.5，并复用原几何硬约束验证。预检要求 M6/M7 的 case_id 正好对应这个 M5 CaseSpec，并再次核验交接 CSV。

H1 证据范围及 project d 映射沿用 [M5 文档](M5_LITERATURE_LAYOUTS.md)，不增加文献主张。R=55 mm 等仍是二维项目有效域，不等于完整真实燃烧室。该选择仅用于独立 pilot，不加入默认 426 个候选。

示例 case_id 为 `case_v1_5e073b9a0a01cdc830700a471f2f254d700798732281c14341ddcf12b2c85413`。空物理规格的 cfd_case_id 为 `cfd_v1_df00d347c70f801e93af1d37449a03189441dba78addcded5eed327200696008`；这表示未知条件的交接身份，不是正式求解工况。

## ResearchInputGate

Gate 只保存准备状态、来源引用和适用性决策 provenance，没有物理数值字段，不作为第二套 physical specification。每项包含 key、status、source、reason、blocking_stage、category、evidence_reference、只读 required_for_pilot / applicability_policy；报告附 acceptance_requirement。固定完整清单不能删除、重复或更换阻塞阶段；空清单拒绝。清理后为60项，初始54项阻塞和6项 NOx pending。

status 为 confirmed、unresolved、not_required、pending_contract。source 区分 project_existing、group_1_single_nozzle、group_3_experiment、researcher_decision、literature、pending。初始没有任何尚未收到的科研输入被确认。二维几何的已验证证据独立位于报告 pilot_target，不能使三维 geometry gate 自动就绪。

| 类别 | 当前缺项 | 对应门 |
| --- | --- | --- |
| geometry | 轴向长度、喷嘴物理长度/内部结构或等效入口、真实三维构造、实际面区映射、阵列与腔室集成 | geometry_ready |
| boundary_conditions | 氢/氧化剂入口、各股质量流量、温度/压力、出口、壁面热条件、压力参考；分级定义及分配 | physics_ready |
| physics | 湍流、燃烧模型、氢机理、物种/材料库、稳定运行范围、辐射决策、baseline 范围；新增 physical_model_configuration、operating_mode | physics_ready |
| mesh | 全局策略、喷嘴细化、边界层、网格无关性计划及第一组参考；新增 mesh_quality_acceptance_criteria | mesh_ready |
| numerical_solver | 压力速度耦合、离散、逐方程残差、初始化、迭代/时间步、收敛评估及参考 | solver_ready |
| post_processing | 实际入口/出口/壁面、稳态样本、瞬态时间窗；新增 required_result_subset | postprocessing_ready |
| experiment_alignment | 共用工况、H2/空气流量、当量比、热负荷、分级比、火焰测点，以及待定 NOx 合同 | pilot_ready；NOx 合同另行保留 |

阶段门仅描述对应阶段的输入齐备，不声称已完成建模或数值验证。`pilot_ready` 汇总所有 required_for_pilot 项，任何 unresolved/pending_contract 必需项都会阻塞。当前六个门全部 false。稳态/瞬态适用性不能从经验推断；选定模式后，对不适用的时间窗或样本须明确记 not_required 并提供证据。

`record()` 确认条目必须提供来源、非空原因和便携证据，禁止直接写 not_required。固定策略把核心项设为 always_required；只有分级项、稳态/瞬态互斥样本和新增 inlet_turbulence_specification 是 conditionally_not_required。geometry、基础入口/出口/壁面/压力参考、核心物理模型、mesh策略与质量验收、数值策略/收敛、正式结果子集等不能豁免。NOx 固定 pending_contract。程序不审查 reason 的自然语言含义；reason="unknown" 不能绕过固定策略。

证据允许 meeting:2026-09-09-group-sync、docs/group1_nozzle_geometry.md、commit:abc123、H1、experiment-sheet:003 等文本，不要求文件存在。拒绝 Windows 盘符/UNC/POSIX 绝对路径及控制字符。证据仍是研究者声明，软件不认证其真实性，也不因出现引用就修改 M6/M7。没有 force、ignore_missing、skip_validation 或执行 override。

程序化查询示例：

```python
from fluent_pilot import ResearchInputGate

gate = ResearchInputGate.initial()
missing_group_1 = gate.group_dependency_report()["group_1_single_nozzle"]
missing_group_3 = gate.group_dependency_report()["group_3_experiment"]
decisions = gate.group_dependency_report()["researcher_decision"]
stages = gate.readiness()
```

第一组需正式交付喷嘴结构、经验证的湍流/燃烧模型、氢机理、物种材料库、稳定运行包线和网格/收敛参考。第三组需交付统一基准工况、流量/当量比/功率、分级方案、火焰测量定义及 NOx 测量合同。研究者需决定腔长、阵列集成、边界和 baseline 范围，以及本项目网格与数值/后处理方案。不自动联系任何人。

## 分级和 NOx

默认保留 stage_definition、pilot_main_stage_assignment、stage_flow_split、stage_equivalence_ratio、stage_activation_logic、staging_ratio 为 unresolved。未替用户决定第一例就是非分级。

明确批准非分级 baseline 后调用 `nonstaged_baseline(reason=..., evidence_reference=...)`：原子记录 nonstaged_baseline 适用性决策、confirmed baseline 范围及六个 not_required 分级项。整个 gate 验证它们的来源、原因、证据一致；不能只传字符串前缀，也不能丢掉决策或更改 baseline 却保留旧豁免。不会伪装成已取得第三组数据。

其他有限条件使用 `declare_applicability(kind, reason=..., evidence_reference=...)`，仅接受 steady_sampling（豁免瞬态窗）、transient_sampling（豁免稳态样本）、laminar_inlet（豁免入口湍流量）和 nonstaged_baseline。互斥采样决策拒绝。适用性分类不是物理参数覆盖：包级检查仍要求当前 M6 分别声明 steady、transient、laminar 或 extras.pilot_baseline_scope="non_staged"；缺失是 mapping_unresolved，冲突是 conflict。

NOx measurement definition、unit、dry/wet basis、oxygen correction、sampling location、instrument information 始终 pending_contract，M8A 不允许确认或导入其数值。它们是未来 NOx 实验合同，`required_for_pilot=false`，不额外扩张 M6 五项指标下首次求解的必要条件；仍完整出现在 items 和 group_dependency_report/missing_inputs 中。blocking_inputs 仅列真正阻止本次 pilot 的缺项。全部必需项得到正式证据后 research pilot_ready 可以为 true，但即使如此 M8A 的 execution_allowed 仍为 false。

## 运行、报告与输出

PowerShell 中由用户在当前会话设置本地环境变量，以下仅使用占位文本：

```powershell
$env:FLUENT_EXECUTABLE = '<FLUENT_EXECUTABLE>'
$env:FLUENT_WORKING_DIRECTORY = '<PILOT_WORKING_DIRECTORY>'
python scripts/fluent_pilot_preflight.py --prepare-example --declared-product 'ANSYS Fluent' --declared-release '2025 R2' --installation-hint v252
```

目录尚不存在且明确计划创建时加 `--plan-create-directory`。示例沿原 M2 API 归档一个 H1 case，生成关联 CSV/PNG/config/validation/metrics；原 M6 API 导出空物理条件交接，原 M7 API 导出注释准备文件，均位于 ignored `outputs/m8a/`。`required_metrics=["pressure_loss"]` 只是示例显式选择，用于满足 M6 非空子集契约，不代表已获科研批准；五项指标定义保持原样，真实 M8B 应确认所需子集。没有使用常见经验值、合成当量比或 CFD 数字。

示例准备阶段沿用原归档层读取 Git provenance 的 subprocess；只读 preflight API 和已有包 CLI 没有任何进程调用。既有 M2/M6/M7 文件不会被预检更新。新建准备示例是单独 opt-in 操作，M6 创建的 attempts 目录为空。

已有包只读调用：

```powershell
python scripts/fluent_pilot_preflight.py --package '<cfd_case.json>' --automation-manifest '<automation_manifest.json>'
```

CLI 打印 JSON，不默认保存本机报告。可将终端 JSON 保存到 `.local/`，不得放入科学 manifest 或跟踪配置。CLI 返回 0 仅表示检查和报告完成，绝不表示允许执行；自动化消费者必须读取报告布尔字段。损坏包、错误 H1 target 或错误输入明确异常退出；缺软件、科研数据未齐全则返回结构化检查失败/缺项清单。

报告包括原 identity、pilot_target、fluent_installation、M7 capability_audit、environment_ready、automation_static_valid、automation_audit、research_gate、blocking_inputs、group_dependency_report 和 launch_plan。环境文件存在且目录可写时 environment_ready 可以 true，同时 pilot_ready 仍 false。

## 研究声明与包级 readiness（提交前清理）

保留 `research_gate.pilot_ready` 原义，并在顶层给出 `research_ready`，二者只表示科研声明齐备。新增字段不改 M2/M6/M7 身份或任何原生产模块：

| 字段 | 条件/含义 |
| --- | --- |
| physical_spec_status | 所有物理内容均未知时 preparation_placeholder；否则 incomplete 或 mapped_inputs_present |
| package_physics_ready | 当前已核验 M6 几何/边界/物理输入满足已登记的存在性映射，且不是空占位包 |
| package_input_mapping | 每个条目的 gate_status、package_mapping_status、source_path 和缺项原因；不复制物理值到 gate |
| package_review_status | exact cfd_case_id 与 automation_digest 的审阅关联 matched / unresolved / conflict |
| compatibility | dimensionality、solver_family；均区分 compatible/unresolved/conflict；adapter_verified=false |
| package_ready_for_pilot | research_ready、package_physics_ready、兼容性、精确审阅关联和所有必需映射均通过 |
| nox_research_ready | 固定 false，不把非 NOx pilot 就绪等同低排研究就绪 |

environment_ready 独立于上述包级研究/输入检查。环境=true、科研声明=true、空物理包仍会得到 preparation_placeholder、package_physics_ready=false、package_ready_for_pilot=false。execution_allowed 始终 false。

`preflight()` 重用 M6 load 和 M7 verify/build，只读重建后核对 digest。`package_review` 是可选 API 参数，仅含 cfd_case_id、automation_digest、evidence_reference；不是第二套物理值。研究者对该精确包及 gate 证据的关联审阅应记录在该便携引用中；字段缺失/绝对引用拒绝，ID或digest不符报告 conflict。没有 review 时不能 package_ready。CLI 默认不提供 review，仍能报告全部缺项。

真实温度/压力/当量比读取 M6 正式字段，湍流/燃烧/模式读取 simulation_config；轴向长度读取既有 extras.axial_length。其他几何/物理扩展按报告 source_path 从 M6 extras 读取，仍受原带单位结构契约保护。入口还要求对应 fuel/oxidizer mole fractions。新增模型配置要求显式 energy_equation、species_reaction_enablement、density_compressibility、gravity_decision；operating_mode 要求 dimensionality、steady_or_transient、solver。outlet_definition 要求 boundary_type 与 backflow_specification，后者按实际类型声明适用性或所需温度/组分/湍流数据，不设默认值。

新增 inlet_turbulence_specification 按已选模型声明；pressure_reference 细化绝对/表压处理，wall_thermal_condition 细化适用的固体材料/传热，initialization 包括点火，convergence_assessment 包括守恒/物理监测。字段存在性不是物理一致性、收敛或 adapter 正确性的证明，模型适用性仍由科研证据与后续 adapter 审核承担。

M7 已有 strategy/local_nozzle_size/boundary_layer_request/discretization 从其 automation_inputs 读取。对 M7 当前无法正式表达的网格质量、逐方程残差、初始化/点火、耦合、完整迭代策略/监测和样本定义，保守报告 pending_mapping_contract 并阻止 package_ready，不把数值参数塞进 M6 physical extras，不把普通 evidence 当作实际数值。当前 M7 注释准备契约因此仍不能通过全部包级输入门；未来必须先正式补齐相应准备契约/映射，才可使受控 M8B 执行入口通过，本轮不实现那些 adapter 或修改 M7。

网格无关性计划、第一组 mesh/convergence 参考、稳定运行包线、实验基准和火焰测点属于参考审阅项，须 gate confirmed 且 package_review 精确匹配。实际工况/几何仍由独立 M6 映射检查。required_result_subset 须 gate confirmed，并通过精确包审阅关联当前 M6 required metrics；单独存在示例 pressure_loss 不会自动批准。改变 required subset 会按原 M7 语义改变 digest，即使 cfd_case_id 相同也会使旧 review 失效。

M6=3D 且 Local=3D 才维度 compatible；2D/axisymmetric 与本地3D冲突；M6缺值 unresolved。M6 solver=Fluent 仅表示家族 compatible，非 Fluent conflict，未定义 unresolved。不会用本地字段补写物理参数。

FluentLaunchPlan 只描述 executable、working_directory、3D、double、processes 和声明元数据；command_preview=null。当前没有仓库内可靠 2025 R2 CLI 语法证据，不猜 flags，不生成可运行命令。execution_allowed 是固定 false 的属性，没有可执行方法。prepare.jou 继续全部为注释。

## M8B 的开始条件

当前不能真实 solve：二维点阵不是三维硬件，第一组和第三组正式数据未收到，边界与物理模型未确认，网格/数值/后处理输入未齐备，M7 执行 adapter 未实现。

受控 M8B 执行前的顺序：取得科研输入 → 有证据地确认 gate/适用性 → 把真实物理值写入 M6 CFDSpec → 按旧契约得到正式 cfd_case_id → 重新导出 M6 handoff → 重新生成 M7 automation → 对精确 CFD ID/digest 记录 package_review → M8A 检查 gate、实际输入、准备映射和兼容性 → package_ready_for_pilot=true。之后 M8B 才可在其受控入口调用旧 create_attempt；当前 M7 尚缺的正式准备映射和真实执行 adapter 必须先补齐并审核。

M6 create_attempt 原 API 没有研究门，不在本轮修改。不能绕过上述入口，不能只改 gate 却继续使用空物理 cfd_case_id。M8A 即使收到未来完整输入仍 execution_allowed=false，无执行方法。实际启动后才登记 running、真实提取后才回填结果；本轮没有创建或更新 attempt。NOx 合同继续待定。

## 验证

运行 `python -m pytest -ra`，保留原 1126 项；新增测试覆盖 synthetic executable 检测、目录计划/不可写、Windows 本机路径隔离、独立 H1 几何、完整门控与证据、baseline/NOx、M7 篡改拒绝、错误 pilot 拒绝、源文件逐字节不变、CSV/PNG、空物理规格以及 monkeypatch subprocess.run/Popen/os.system 后的只读完整流程。

首次实现历史验收（2026-09-09，以下1170项/55项为清理前记录；本轮结果另记于文末）：

- 原样运行 `python -m pytest -ra`：**1170 collected / 1170 passed，168.93 秒，0 failed / skipped / warnings**。原 1126 项全部保留，新增 M8A 44 项。初次沙箱运行遇到既有 outputs/test_tmp 的 WinError 5，经批准后在沙箱外完成；没有改 pytest 配置或旧测试。开发期间修正了新增测试对旧列表返回值的类型假设，以及 Windows 标准临时文件权限失败可能大量重试的问题；最终目录探测只做一次独占创建，拒绝时立即返回，成功时立即清理。回归专门验证一次失败探测不会重试。
- 独立默认 run_batch：**426 planned / 426 unique / 415 feasible / 11 infeasible / 18 Pareto**。
- 使用用户给定本机路径经 CLI 注入，完成 H1 空物理规格准备示例和现有包预检。本地报告位于 ignored `outputs/m8a_local_preflight.json`；生成的 M2/M6/M7 文件位于 ignored `outputs/m8a/`。工作目录写探测初次被沙箱拒绝，获准后仅创建并删除空探测文件，最终 `environment_ready=true`、installation detected、`automation_static_valid=true`。版本为 user_declared:2025 R2，未自动验证版本/license。
- 当前共有 55 项科研清单，49 项 pilot 阻塞缺项、6 项 pending NOx 合同；六个阶段门全部 false。`ready_to_execute=false`、`execution_allowed=false`。没有自动确认来自第一组/第三组的数据。
- 示例 run 为 `run_20260909T052733133265Z_d02ad272048842d2`，CFD/M7 存储键为 `fae0a2477ad806e1345e35f327f74b2ce76b74c779f9d021a599d26e97c77486`；本次 automation_digest 为 `e903375f87526de0bb862a1ea4e10ebe545b8770baa7cab7d0e27b1d942c1afe`。它与本次 run 关联，不要求重建新 run 时摘要相同。
- M2 verify_run 通过，7 点 CSV 与 M7 副本逐字节相同，1 张 PNG 已目视核验；点数/间距/角度/边界/重叠由程序验证。attempt 目录为空；没有 CAD、mesh、CAS/DAT 或 CFD 数值。
- git status、diff/stat、diff/check 及全部新增文件的 no-index 空白检查通过；M2–M7 原生产文件、全部旧测试和参考输出未修改，真实路径、输出和缓存未被跟踪。分支保持 `codex/m8-fluent-pilot-preflight`，未 commit/push/merge/rebase，未修改 main。
- 首次实现曾判断未发现提交阻塞；后续只读审计发现豁免及包关联不足，本轮按上述规则修复。科研缺项和执行 adapter 仍是后续明确前置条件，不由 M8A 伪造补齐。
