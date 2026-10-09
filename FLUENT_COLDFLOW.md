# 已有 case/data 的层流冷态通流入口

`scripts/fluent_coldflow.py` 将 2026-10-09 本机已跑通的 PyFluent 层流对照整理成配置入口。
它适合已有三维网格、已配置边界且已初始化/求解过的 `.cas.h5` + `.dat.h5`。
启动后独立完成读取、设置层流、分批求解、逐批保存、诊断和退出，无需人工逐步操作 Fluent。

这是独立的 `engineering_coldflow` 工程流程。它保留原 M8B 入口、含反应的旧基准和 M8A 门控，
不创建 M6 attempt，不声称正式 M8B readiness 已通过，不把结果自动导入科研 CFD 指标或优化数据。
不接收 CAD/STEP 或只有网格的输入，不重新初始化，不启用反应，不创建燃烧域。

## 安装和配置

在安装了 Fluent 2025 R2、具有可用许可证的电脑上，用独立 Python 环境安装可选依赖：

```powershell
python -m pip install -r requirements-fluent-coldflow.txt
New-Item -ItemType Directory -Force .local
Copy-Item examples/coldflow.template.json .local/coldflow.json
```

模板没有本机路径和文件 hash，不能直接运行。编辑 `.local/coldflow.json`：

- `input.case_file` / `data_file`：已匹配的已有冷态 case/data 的绝对路径；可以位于中文目录，运行前复制到 ASCII 运行目录。
- `input.case_sha256` / `data_sha256`：对应文件的 SHA-256，小写十六进制。检查前和复制后重新验证；结束后复核原文件未改动。
- `fluent.executable`：本机 `<安装目录>/fluent/ntbin/win64/fluent.exe`。
- `fluent.license_server`：默认 `null`，沿用本机环境；需要时填写本机已核验的许可证设置。
- `output_root`：ASCII 绝对路径，例如 `D:/fluent-coldflow-runs`；每次运行新建独立子目录，不覆盖旧结果。
- `fluent.processes`：默认 2；`timeout_seconds` 是包含启动和求解的总时限。
- `iterations.maximum`：**本次新增**步数预算，不是 Fluent 累计步数。每次调用重新授权，失败不自动重试。
- `iterations.block_size`：默认每 50 步先保存 case/data，再检查诊断。
- `expected_conditions`：核验已有 case 的工况，**不会修改入口速度、组分、温度或压力**。
  换工况时先在 Fluent 中准备并另存相匹配的 case/data，再更新路径、hash 和预期工况。

获取文件 hash：

```powershell
(Get-FileHash -LiteralPath 'D:/your-input/coldflow.cas.h5' -Algorithm SHA256).Hash.ToLowerInvariant()
(Get-FileHash -LiteralPath 'D:/your-input/coldflow.dat.h5' -Algorithm SHA256).Hash.ToLowerInvariant()
```

当前适配范围固定为 25.2.0、3D、double、premium、稳态压力基求解器、Energy + Species Transport、
单相、无重力、无辐射、无反应；转换为层流，其余材料、区域、边界及有效离散格式继承并核对。
支持一个燃料入口区、一个氧化剂入口区、一个出口区、一个壁面区；一个面区可含多个不连通孔。
仅接受 H2/O2/H2O/N2 混合物和质量分数输入，N2 为隐式最后物种。
默认燃料纯 H2，氧化剂 O2/N2=0.4/0.6；两路 10 m/s、300 K，出口表压 0 Pa、回流 300 K，操作压力 101325 Pa。
源 case 已经开启反应、边界不匹配或存在额外入口时，在迭代前停止。

## 使用

先检查，**不启动 Fluent、不占用许可证、不创建运行目录**：

```powershell
python scripts/fluent_coldflow.py --config .local/coldflow.json --inspect
```

省略 `--inspect` 仍只检查。配置通过只说明本地文件/配置通过静态检查；许可证及实际模型在执行时检查。

明确启动一次试算：

```powershell
python scripts/fluent_coldflow.py --config .local/coldflow.json --execute
```

保持启动命令的 Python 进程运行直到结束。电脑关机、休眠或终止该进程会中断计算；此入口没有跨重启自动续算。
下一次可选上次完整保存的 checkpoint 作为输入，更新路径和 hash，在新目录继续。

每次目录包含 `config.json`、`run.json`、`solver_status.json`、`source_state.json`、`configured_state.json`、
`stdout.log`、`stderr.log`、`fluent.trn`、`history.csv`、`summary.md`、配置后和逐批及最终 case/data。
`run.json` 是最外层终态；超时/报告错误时 worker 状态可能停在最后保存点，以 `run.json` 为准。
报告中的 `pending_requested_iterations` 非零表示中断时某批的实际完成步数尚未确认，不能把它算作零计算量。

数值检查使用全局缩放且不归一化的残差：Energy 默认 1e-6，其余所列方程 1e-4。
为保证分批步数可核对，Fluent 内部残差自动停止关闭，由脚本检查配置中的残差、总质量、H2 对流通量守恒、总压差稳定性，
达到最少步数且连续两批通过才提前停止。压力差相对变化的分母为 `max(abs(当前压差), 1 Pa)`。
H2 守恒是有符号对流通量，未单独计算扩散通量；入口到出口压差是面积加权总压差。
若 Fluent 其他停止条件提前结束某批，脚本保留 checkpoint 并以步数不符终止，不擅自补跑。

退出码：`0`=工程数值检查通过；`3`=预算用尽、数值检查尚未全部通过；`2`=配置/文件检查拒绝；
`1`=执行、超时或报告失败；`130`=用户中断。预算用尽不表示收敛，数值检查通过不代表实验或网格无关性验证。

进程监督复用现有 M8B 的超时和 Windows Job Object；worker 另记录本次新建的 Fluent 进程，
退出时按 PID、创建时间及安装路径核对后清理，不终止既有 Fluent 或许可证服务。
`all_tracked_processes_exited` 只针对已记录进程；不保证纳管脱离进程树的所有 MPI 子进程。

## GitHub 交付范围与验证

可提交：`coldflow_trial/`、CLI、通用 JSON 模板、可选依赖、本文和测试。
本机 `.local/` 配置以及 `outputs/` 下运行目录沿用忽略规则，CAS/DAT/MSH 也已忽略；外部运行目录不属于仓库。
不上传本机许可证设置、服务连接信息、完整 CAD 或仿真二进制结果。GitHub 负责保存代码；实际计算在有 Fluent 和许可证的电脑上执行。

整理前的专用脚本已真实求解。本入口的整理验证使用 fake solver 生命周期测试、原保存文件/日志的离线回放及现有测试套件；
**整理后的入口未再次启动 Fluent，不能称为新的真实求解验收**。平面流场图片和原试算对比图仍保留在本地分析目录，当前通用入口生成表格和诊断报告。

实现参照 [PyFluent 官方启动说明](https://fluent.docs.pyansys.com/version/stable/user_guide/session/launching_ansys_fluent.html)，
调用方式以本机 0.42.1 已验证脚本及安装源码为依据。

```powershell
python -m pytest -ra
git diff --check
```
