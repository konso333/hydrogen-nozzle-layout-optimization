# M6：CFD 交接、工况身份与结果回填

M6 是独立、显式调用的数据契约层。它连接已验证的 M2 几何归档与外部 CFD execution，不调用 Fluent，不建立网格或求解器，不根据几何推断燃烧性能。

## 1. 实施前审计与兼容边界

已阅读 AGENTS、M1–M5 文档、`experiments/archive.py`、`experiments/spec.py`、`json_values.py`、CFD 读取器和相关测试，并检索 CFD、metrics、status、result 及 operating condition 实现。

| 现有位置 | 已有正式契约 | 尚属占位或未实现 |
| --- | --- | --- |
| `optimization/cfd_metrics.py` | `CFD_FIELDS` 五个键；`CFDResult(candidate_id, ...)`；CSV 必须包含所有列；空白读为 None；非有限数拒绝 | 没有单位、提取定义、工况身份、run 关联或写入接口 |
| `tests/test_cfd_metrics.py` | 一个测试证明 candidate_id、数值读取及空白保留 | 不证明工况或结果完整性 |
| M2 `case.json.cfd` | run_id、case_id、status、五个 null metrics、results_file；verify_run 校验关联、枚举和相对文件 | 默认 not_started 是占位；不能代表同一几何的多工况/多次执行 |
| M2 `CFD_STATUSES` | not_started、exported、running、completed、failed | 不含状态转换或 completed 必填数值检查 |
| M4 | 独立的 19 项 geometry metric metadata、legacy/default objectives | 不包含 CFD metric 或 CFD score |

以上生产文件、旧读取器和原测试均保留。M6 新建 `cfd/spec.py`、`cfd/metrics.py`、`cfd/handoff.py`，不修改 M2 schema、CaseSpec、M3 默认搜索、M4 Pareto 或 M5 文献 registry。旧 CSV 的物理定义不能由新元数据自动追认；其数据必须经人工确认单位、公式和来源，明确关联 run/case/condition 后才能转为 M6 record。不会只按 candidate_id 自动绑定。

## 2. 身份与规范化

| 概念 | 身份内容 |
| --- | --- |
| run_id | 一次 M2 几何/搜索实验，原定义不变 |
| case_id | M2 完整规范化 geometry specification，原定义不变 |
| operating_condition_id | SHA-256(schema_version + normalized operating_condition) |
| cfd_case_id | SHA-256(schema_version + case_id + normalized operating_condition + normalized simulation_config) |
| attempt_id | 独立 UUID，标识一次 execution；不进入任何物理规格摘要 |

三个规格身份保留完整 SHA-256。CFDSpec 保存不可变规范 JSON，`normalized_spec` 返回独立副本；重载要求所有已解析字段，禁止遗漏后悄悄采用默认值。

attempt_id 使用 UUID4，碰撞概率极低但不是数学上的绝对不可能。既有文件检查与独占写入继续防止覆盖；碰撞时明确失败，不建立中央 ID 服务。

所有 JSON 类型转换复用 `json_values.json_value`，规范编码复用 `experiments.spec.canonical_json`。字典递归排序，列表保序；等价 Python/NumPy 数字、整数值 float、负零归一；Decimal 转为项目 float 精度，不声称保留任意精度。NaN/Inf、未知对象、非字符串字典键拒绝；数值字段拒绝 bool 和数字字符串。

时间、路径、Git branch/commit、输出目录、status、结果值、solver_version、numerical_settings、citation/DOI 都不参与物理身份。常见这些保留键不能通过 extras 写入。Fluent 版本在每个 execution 的 provenance 记录，允许准备阶段为 null，但 running/completed 或带数值的 failed 必须提供版本。改变版本、迭代次数、收敛容限或重跑机器应创建新 attempt；改变物理边界、燃烧模型等创建新 CFDSpec。

## 3. Operating condition 与 simulation configuration

没有从现有二维几何推断全部 Fluent 工况。工况字段均可省略，规范快照显式保存 null；空 extras 保存 `{}`。这表示规格中的未知字段，不表示已经满足真实求解前提。补全未知边界条件会改变 CFD identity，不能把未指定条件解释成默认空气或默认壁温。

| operating_condition 字段 | 数值契约 |
| --- | --- |
| inlet_temperature | `{value: 正数, unit: "K"}` |
| inlet_pressure | `{value: 正数, unit: "Pa"}`；入口绝对压力 |
| mass_flow_rate | `{value: 正数, unit: "kg/s"}`；总入口质量流率 |
| equivalence_ratio | `{value: 正数, unit: "1"}`；燃料/氧化剂比相对化学计量比 |
| fuel_mole_fractions / oxidizer_mole_fractions | species → 摩尔分数；固定无量纲单位 1，范围 [0,1]，和在 1e-12 内等于 1，不自动重标定 |
| extras | 结构化项目边界说明，例如各入口条件、壁面热边界、压力参考、湍流入口设置 |

单一入口标量适用于所声明的总入口；多入口温度/压力不同时应将公共标量保留 null，在 extras 中按命名入口明确记录。schema 不验证化学计量、物种库、质量守恒或字段间物理一致性。

simulation_config 支持可选的 solver、model_name、turbulence_model、combustion_model 文本，dimensionality 为 `2D / axisymmetric / 3D`，steady_or_transient 为 `steady / transient`，另有 extras。没有自动选择模型或声称其已通过研究验证。

extras 允许字典、列表、文本、布尔设置、null；所有数字必须使用 `{value, unit}` 叶节点，无量纲也明确为 `1`。单位字符串是调用者声明，不做任意单位换算；同一物理量应始终使用同一单位，否则产生不同身份。时间步长若作为数值策略，放 attempt.provenance.numerical_settings；物理激励频率、边界和燃烧模型放物理规格。真实网格和复杂求解完整性验证留待后续阶段。

extras 只用于尚未进入正式 schema、但已由调用者确认属于物理工况或模型规格的扩展，例如带单位 1 的 swirl_number、radiation_model 或 species_transport_option。合法扩展保留全部内容并参与 cfd_case_id；未知字段的科研合理性由调用者负责。程序仅保证结构、单位、有限性、保留键检查和确定性规范化，不能自动判别任意自定义字段的科学含义。

`cfd.spec.RESERVED` 定义不区分大小写的精确保留键，递归检查两个 extras 的所有字典，包括列表中的字典；非法键立即报错，不修改或静默丢弃输入。策略覆盖：

- 路径：output_path、output_dir、output_directory、working_directory、workdir、absolute_path、installation_path、path。
- 时间：created_at、updated_at、started_at、finished_at、timestamp、solver_execution_time、execution_time、wall_clock_time、elapsed_time、runtime。
- Git/环境：git、git_commit、git_branch、git_dirty、branch、platform、hostname、machine、machine_label、computer_name。
- 执行/来源：attempt_id、run_id、status、result、results、results_file、solver_version、python_version、extraction_tool_version、numerical_settings、execution_environment、provenance、citation、doi。

该策略拒绝上述字段，不承诺识别任意改名或把数值藏进文本的语义。原本误把这些执行字段写入 extras 的规格现在明确拒绝；其余合法规格的规范 JSON 和摘要不变。

## 4. 显式选择与 package

`select_cases(run_manifest, [case_id, ...])` 验证整个 M2 run 后按明确 ID 选取合法几何，拒绝空选择、重复 ID、未知 ID。M3 已归档方案可直接使用其 run.json 和 rows 中的 case_id；M6 不自动选择 Pareto 或全部合法方案。

```python
from cfd import CFDSpec, export_cfd_package, load_cfd_package

spec = CFDSpec.create(case_id,
    {"equivalence_ratio": {"value": 0.4, "unit": "1"}},
    {"solver": "Fluent", "dimensionality": "3D"})
package_path = export_cfd_package(run_manifest, spec,
    output_root="outputs/cfd", required_metrics=["pressure_loss"])
package = load_cfd_package(package_path)
```

```text
outputs/cfd/<storage_key>/
    cfd_case.json
    geometry/coordinates.csv
    attempts/attempt_<uuid>.json
```

storage_key 是 `(run_id, cfd_case_id)` 规范 JSON 的完整 SHA-256，仅用于目录定位，不是新的科学身份。原建议的 run/case/cfd 三层完整 ID 路径在本机触发 Windows 路径长度错误，故采用扁平目录。三个完整 ID 仍保存在 manifest；同 case 多工况、同 CFD spec 跨 run 均有独立目录。不截断科学 ID。

manifest 保存 schema、run/case/cfd/condition IDs、normalized_spec、package status、时间、geometry 引用与单位、文件相对路径、坐标及源 case manifest hash、Git/环境 provenance 和 expected result_contract。源 M2 run/case 仅引用，不复制指标、PNG、文献或完整几何规格；交接 CSV 按原字节复制，保留编号、点序、mm、z=0 与 BOM。Fluent 使用米时必须在后续建模层显式转换。

所有文件引用相对 package。仅源 run/case 两项只读引用允许 `..`，且必须对应经过 verify_run 的同一 run 索引与 geometry case；坐标、attempt、日志不得逃出 package。禁止 Windows 盘符、UNC 和 POSIX 绝对引用。源 run 与 CFD root 必须能建立相对引用，Windows 跨盘导出会在写文件前失败。搬迁应保留两棵目录间的相对结构；单独复制 CFD 目录可交付 CSV，但无法完成依赖 M2 原归档的完整核验。

load_cfd_package 重算 CFD identity，核对 result metadata、源 manifest hash、M2 完整核验和 CSV hash。同名 package 再导出明确拒绝；重跑应加载已有包、创建新 attempt。写出失败可能留下无 cfd_case.json 的不完整目录，不作为有效 package；不会覆盖已有归档。hash 防意外损坏，不是数字签名。

## 5. Execution 与状态

package.status 固定 `prepared`，只表示交接文件已生成；实际求解状态以 `attempts/*.json` 为准，没有多个 execution 的含混聚合状态。零个 attempt 表示尚未登记执行，创建 attempt 初始 `not_started`，metrics 全 null。

允许状态保留 M2 全部五项并新增 prepared：

```text
not_started -> prepared / exported / running / failed
prepared    -> exported / running / failed
exported    -> running / failed
running     -> completed / failed
completed、failed 为终态
```

非终态允许同状态更新；未开始阶段拒绝数字。completed 必须满足当前 package 显式选择的全部 required metrics，不能跳过 running。终态完全相同的规范记录重导入可幂等成功，其余修改拒绝。失败后重跑新建 attempt，不覆盖旧失败。

attempt 保留四个关联 ID、创建时间、status、独立 CFD metrics、error、provenance。provenance 包括 solver_version、data_kind、带单位的 numerical_settings 和可选 execution_environment。data_kind 必须明确 `external_cfd` 或 `synthetic/test-only`；同一 attempt 不可改分类。进入 running 后数值设置和 solver_version 固定，改变应新建 attempt。时间只记录创建，不构建运行时长或调度日志。

numerical_settings 有意留在执行层，不进入 geometry case_id 或 cfd_case_id。同一 physical CFD case 的不同 attempts 可以采用不同 discretization scheme、convergence criteria、time step 或 under-relaxation settings。因此相同 cfd_case_id 不表示数值方法相同，也不保证数值实验等价；比较或复现时必须检查 attempt 的设置。未声明设置仍允许空对象，不代表已记录全部求解默认值。

handoff creation provenance 继续记录创建包时的 Git、Python、platform 与时间。execution_environment 描述调用者明确声明的实际求解机及执行/提取工具环境，**不从包创建机器或结果导入机器自动推测**：

| execution_environment 字段 | 类型与含义 |
| --- | --- |
| platform | 可选文本，实际求解平台 |
| hostname / machine_label | 可选文本，实际主机名或研究者使用的机器标签 |
| git_commit / git_branch | 可选文本，所用执行/提取代码版本与分支，不自动复制项目当前 Git 信息 |
| git_dirty | 可选 bool，执行/提取代码工作区状态；接受 NumPy bool，拒绝 0/1 冒充 |
| python_version | 可选文本，实际使用的 Python 工具版本 |
| extraction_tool_version | 可选文本，实际提取工具版本 |

所有字段未知时为 null，缺少整个 execution_environment、显式 null、空对象或全 null 对象均规范为 null；部分声明的对象补齐其余 null 字段。未知字段、非文本版本/平台、不合法布尔值或绝对本机路径明确拒绝。不自动检查 Git 对象是否真实存在，环境内容是来源声明。

solver_version 的**唯一正式位置仍为 attempt.provenance.solver_version**，原 create_attempt(solver_version=...) API 保留。execution_environment 内同名字段一律报错，无优先级合并或隐式覆盖，即使两份值相同也拒绝重复声明。新增 create_attempt(..., execution_environment={...}) 是可选关键字参数；上述环境字段与版本均不参与 CFD identity。

在进入 running 前可修正环境声明；running 阶段允许把未知环境字段补成已知值，包括在回填 completed 时补录提取工具版本，但不允许覆盖或删除已知值。数值设置和 solver_version 的原锁定规则不变，终态仍不可修改。旧 attempt JSON 缺少 execution_environment 时只在返回的对象中视为 null，不要求重写原文件；旧终态记录保持幂等可读、可重导入。

## 6. 正式结果契约

现有键名保持，M6 的定义是项目显式选定的后处理约定，不代表旧 CSV 已符合这些约定。每项 metadata 有 key、description、unit、direction、definedness、source、required、null_policy。所有方向为 descriptive，不加入 optimization profile。

| key | 单位 | M6 定义与提取域 |
| --- | --- | --- |
| hydrogen_conversion | 1 | `1 - outlet H2 mass flow / inlet H2 mass flow`；对声明的所有入口/出口求通量和，入口 H2 流量须正且无回流，范围 [0,1] |
| outlet_temperature_mean | K | 出口静温面积加权均值 `Σ(A_i T_i)/ΣA_i`，出口总面积须正 |
| outlet_temperature_std | K | 同一出口、同一样本的总体面积加权标准差 `sqrt(Σ[A_i(T_i-T_mean)^2]/ΣA_i)` |
| pressure_loss | Pa | 入口面积加权总压减出口面积加权总压，保留符号；两端面积正且压力参考相同 |
| max_wall_heat_flux | W/m^2 | 声明燃烧室全部壁面上的局部法向总热流密度绝对值最大值 |

每次 export 必须显式提供非空 `required_metrics` 子集，其余为 optional；没有全项目默认必填结果。每个非空结果为 `{value, unit, source}`，unit 必须匹配，source 必须说明具体面区/样本时刻或平均时间窗及提取方式。稳态或瞬态报告必须明确时间样本；比较不同 attempt 前需确认提取域一致。软件只能核对声明格式和数值定义域，不能由这几个标量证明 Fluent 已收敛、域选择正确或公式实际执行。

NOx 单位、干/湿基准和氧修正尚未定义，记为 pending_contract，拒绝数值导入。没有另增 pressure_drop 别名，也没有含糊的 temperature_uniformity、CFD score 或 geometry/CFD 混合 registry。

缺失、未提取、不适用均用 null；JSON round-trip 保持 null。numeric entry 中不使用 value=null，而将整个 entry 设为 null。真实零保留 `{value: 0, unit, source}`，不会与缺失混淆。未知 metric、NaN/Inf、bool、错误单位/来源及非法范围拒绝。completed 仅证明所选数据字段完整，不证明 CFD 的科研有效性。

## 7. 安全回填

```python
from cfd import create_attempt, load_attempt, import_cfd_results

attempt_path = create_attempt(package_path, solver_version="实际使用的版本",
    data_kind="external_cfd", numerical_settings={
        "max_iterations": {"value": 1000, "unit": "1"}})
record = load_attempt(package_path, attempt_path.stem)
record["status"] = "running"  # 仅在外部执行确已开始后登记
import_cfd_results(package_path, record)
# 外部执行后，把真实提取值及 source 写入 record["metrics"]，
# 再设 completed 或附结构化错误设 failed，调用相同接口。
```

接口接受完整 dict 或 JSON 文件名，检查 package/M2 source、run_id、case_id、cfd_case_id、attempt_id、创建时间、状态转换、必填字段、单位和 provenance，全部通过后才写入已存在的 attempt。外部不能凭一个新 attempt_id 创建记录。身份错配、非法结果均不改变原文件。每次只原子替换一个 JSON，使用独占短期文件锁防并发写入；进程崩溃可能留下 `.write.lock`，需核查无写入者后人工处理，不自动抢锁或续跑。

failed 必须附 `error_type / message / solver_exit_status / relative_log_path`。后两项可以 null，日志非空时须为 package 内存在文件；消息复用 M2 的绝对路径清理。失败数值可部分保留，但必须有实际 solver version；缺失仍为 null。

当前没有可信执行签名，`external_cfd` 是调用者的来源声明，不是软件确认启动过 Fluent。synthetic/test-only 是测试记录，不能用于科研结果。M2 原 cfd 占位永不回写，它没有多工况聚合含义；查询实际状态应读取 M6 attempts。

## 8. 示例、验收与阶段边界

```bash
python scripts/cfd_handoff_example.py
# 或选择已有 M2/M3 run（case_id 必须来自该 run）：
python scripts/cfd_handoff_example.py --run-manifest outputs/runs/<run_id>/run.json --case-id <case_id>
python -m pytest -ra
```

示例默认先用原 M2 API 归档一个 cross_5，再为同一 case 建立 equivalence_ratio=0.4 和 0.5 两个 synthetic/test-only 工况，验证不同 CFD ID、同条件重复 ID 相同、两个独立 package、M2 核验通过。只创建 not_started attempts，全部结果 null，不产生 synthetic numerical result。默认输出位于已忽略的 outputs/m6_example；使用已有 run 时重跑相同输出根会拒绝覆盖。

新增 `tests/test_cfd_handoff.py` 覆盖规范化/不可变快照、单位/非法输入、各物理参数身份变化、多工况/重跑、状态、零与 null、身份错绑、损坏/路径/搬迁、安全回填和 M2/M4 隔离。原 958 项测试不删除或降低断言。

未实现 Fluent GUI、journal、启动求解、三维硬件构建、mesh、收敛和网格无关性验证、自动后处理、旧 CSV 自动迁移、结果相关性/因果分析、机器学习或自动 CFD 排名。真实自动化阶段还需确认边界/物种、模型适用性、面区、时间统计、压力参考、NOx 定义及数值验证方案。不开始 M7，不 commit/push/merge/rebase。

## 9. 最终验收记录（2026-09-08）

- 最终原样运行 `python -m pytest -ra`：**1030 passed，77.47 秒，0 failed / skipped / warnings**；原 958 项全部保留，新增 M6 72 项。第一次沙箱内测试遇到既有 outputs/test_tmp 的 Windows 拒绝访问，获准后在沙箱外完成；未改变 pytest 配置或降低测试断言。
- 独立执行默认 M3 run_batch：**426 planned / 426 unique / 415 feasible / 11 infeasible / 18 Pareto**。M0 默认搜索及 CSV、PNG、summary、candidate_id、BOM 回归全部通过；19 个 M4 geometry metrics 与 legacy/default profile 未改，M5 registry 和 cross_5 未改。
- 实际执行 `python scripts/cfd_handoff_example.py`，生成 outputs/m6_example 下一个 M2 geometry run、两个独立 CFD packages 和两个 not_started attempts。随后以最终代码重载两包，确认所有指标仍为 null，M2 verify_run 成功。geometry case_id 为 `case_v1_4d9f068ca11d6f4145df3b7c445488b9c9c8dca1f431ba826a53d78f43602a6d`；两个 cfd_case_id 分别为 `cfd_v1_87affd44e3d0060e663010f7e859ec467884c1ec31e163b509d3b25b32afa387` 与 `cfd_v1_48b41de25dc39851943b12679196ac17e3c2c0943260742505ef249273af95d7`。同条件重复构造身份一致。
- synthetic 数值仅位于明确标记的测试 fixture；示例没有 CFD 数值。旧 M2 run/case/CSV/PNG 在回填前后逐字节保持不变；package 搬迁及错误身份/错误单位/缺少 required/畸形 manifest 拒绝测试通过。
- 文件范围：新增 cfd 四个模块、示例脚本、M6 测试与本文；既有文件仅 README 更新入口说明，共 8 个文件。未修改任何原算法、geometry CaseSpec、M2 schema、原测试或基准 fixture。
- 检查 git status、diff/stat、diff/check，并对未跟踪新增文件执行 no-index diff/check；无空白错误。示例和测试输出被既有规则忽略，未把 mesh、cas/dat、PDF、CSV/PNG、临时 package 或 pyc/cache 加入变更。
- 当前分支 `codex/m6-cfd-handoff`，HEAD 与 main 均保持 `dfc0f27d2436dca4ad3046e25b4b731882f4328c`。未 commit/push/merge/rebase，没有发现新的 M6 提交阻塞问题；源归档搬迁、未提交代码 provenance、残留写锁和真实 CFD 有效性边界如上所述。

## 10. 提交前局部清理

本轮只修改 cfd/spec.py、cfd/handoff.py、tests/test_cfd_handoff.py 和本文。四层身份、schema_version、五个 CFD metrics、状态枚举/转换表、storage_key 和交接目录均保留。规范化、canonical_json 和摘要算法没有修改；执行元数据通过非法 extras 进入身份的情况改为明确拒绝。

测试新增 13 个函数、33 个执行项，原 M6 19 个函数/72 项及此前所有测试保留。M6 测试现为 32 个函数/105 项。新增保护包括：两个 extras 中的顶层/嵌套/列表保留键拒绝、合法 swirl_number 身份变化、不同平台/版本/Git 的 attempts 保持同一 CFD ID、未知环境与后续补录、旧 attempt 无需重写、数值 provenance 原校验保留、四种独立身份错配、终态不可重启，以及失败后新 attempt 独立完成。

非法跳转测试先通过全部非状态转换的 record 校验，再断言 not_started → completed 的 transition 错误；completed/failed → running 则分别使用其他字段合法的记录，断言终态不可变错误。它们不会借缺少 solver_version、required metric 或错误 identity 提前失败来冒充状态机保护。

固定身份 fixture 是测试文件中的静态 `CANONICAL_CFD_JSON` 与 `CANONICAL_CFD_ID`，内容为上述 cross_5 geometry case、equivalence_ratio=0.4、solver=Fluent、dimensionality=3D，其余正式可选字段为 null、extras 为空；expected ID 固定为 `cfd_v1_87affd44e3d0060e663010f7e859ec467884c1ec31e163b509d3b25b32afa387`。清理前已对照既有 package 核对；测试运行时不使用被测哈希函数生成 expected。

再次读取清理前保存的两个 example packages 与 attempts：两个合法 CFD ID 均不变，旧 attempts 返回 execution_environment=null，所有已有文件字节不变。独立默认搜索仍为 426 planned / 426 unique / 415 feasible / 11 infeasible / 18 Pareto。没有新增 CFD 数值示例或求解产物。
