# M15：非均匀组分 GPU 导热核查与喷嘴启动过渡

用途：验证原混合导热率在空间变化的组分场中是否真正参与 GPU 能量求解，并尝试修复 M14 首次转入 GPU 时的组分超调。新增独立运行与只读审核脚本，不改变正式平台的物性选项或验收门限。本节点已于 2026-10-24 经用户审核并批准上传 GitHub。

## 已完成的非均匀导热验证

采用 10 mm 立方体、256 个六面体单元，沿 y 分成四条组成带，氢气质量分数分别为 0、0.1、0.5、1，其余为干空气。两端 x=0 / 10 mm 的温度为 300 / 310 K，侧壁绝热。流动和三种组分输运方程冻结，只求解能量，并明确关闭组分扩散能量源；保存 case 中的实际开关由审核器再次读取。初始温度统一为 305 K，没有预设最终线性温度分布。

解析解为 `T=300+1000*x`，每个带内的端面热量为 `Q=k*A*1000`。从实际保存网格重建单元位置、端面位置与面积，再读取 Fluent 保存的逐面热量，独立反算导热率。保存字段 `SV_HEAT_FLUX` 在此算例中为每面热量 W，按面直接求和，不再次乘面积；单位依据沿用 M13 已与 Fluent 在线热量报告核对的结果。没有把 UDF 自己算出的物性写回字段冒充独立证据。

质量加权对照、原公式及原公式保存后重启均各运行 25 个 GPU 能量迭代。前两组从字节相同的准备 case/data 出发；重启直接读取原公式的最终 case/data，未手动重新挂接 UDF。源文件哈希、相同网格、完整能量残差序列和进程退出记录均已核对。

| 带内氢气质量分数 | 质量加权导热率 W/(m·K) | 原公式导热率 W/(m·K) |
| ---: | ---: | ---: |
| 0 | 0.0242932 | 0.02428964933 |
| 0.1 | 0.03858388 | 0.09297509886 |
| 0.5 | 0.0957466 | 0.15250245549 |
| 1 | 0.1672 | 0.1672 |

原公式及其重启的实际逐面热量与解析值最大相对偏差约 **9.38e-14**，质量加权对照约 **7.97e-14**；温度线性解的最大保存误差为 0 K（本次保存精度下），四条组成带各 64 个单元且组成保持不变。三组用错误混合律审核时均被拒绝，因此总热量偶然吻合不能替代逐带、逐面检查。热量验收门限仍为 1e-5，未按本次结果放宽。

这验证了四条冻结非均匀组成带中的实际导热响应及其保存重启。真实喷嘴中存在对流、组分输运和迭代耦合，该算例不能直接证明真实喷嘴导热率场、收敛解一致性或网格独立性已经通过。

原始数据位于 `E:/大创赛仿真结果/M15_20261024/nonuniform_conductivity/`。准备阶段的三次失败保留：寄存器名称转换、patch 命令要求求解器组分索引、以及先 patch 氢气导致中间状态被 Fluent 归一化；它们均未运行新的求解迭代。第四次准备先 patch 氧气和水，再 patch 氢气，保存组成最大误差约 5.55e-17，审核通过后才用于三组能量计算。失败数据没有修补后冒充正确输入。

最终只读审计：`outputs/platform_nonuniform_heat_review/final_audit_20261024/audit.json`；同目录包含逐带 CSV 和 `nonuniform_conductivity_validation.png`。此前审核目录保留不覆盖。

## 真实喷嘴启动过渡

沿用 M14 的 851803 单元网格、CPU 第 2000 步初值、原物性、边界及二阶格式；绝对线性容差 1e-20，组分相对线性控制 1e-6、内迭代上限 100。实验仅在启动段将三种组分松弛因子设为 0.01，并增加第 2、3 步保存点。每次总步数仍为 25，原始组分闭合门限仍为 1e-8；没有裁剪或归一化输出。

保守启动的第 1、2、3、24、25 步已通过完整单元及边界检查，实际黏度与相邻保存组成的原公式吻合。组分松弛因子由保存 case 直接读取核对。该实验仍不是收敛计算，也不验证所有未保存步骤。

恢复原松弛因子 1.0 的两种路径均未通过，因此启动过渡尚未解决，不能把低松弛因子的短测通过当作正式修复：

| 路径 | 首步单元闭合最大偏差 | 末步最大偏差 | 五个保存点整体结论 |
| --- | ---: | ---: | --- |
| 0.01，25 步 | 9.47e-13 | 1.41e-11 | 通过；受限短测 |
| 0.01 末态直接恢复 1.0，25 步 | 4.82e-8 | 5.24e-9 | 拒绝；首步失败 |
| 从 0.01 末态改为 0.1，25 步 | 5.06e-12 | 7.73e-14 | 通过；受限短测 |
| 从 0.1 末态恢复 1.0，25 步 | 6.97e-8 | 5.39e-8 | 拒绝；首步及第 24／25 步失败 |

最后一组首步、倒数第二步、末步分别有 11475、35078、33946 个单元超过 1e-8。两次恢复任务均完成 25 步，但保存场审核拒绝，原状态仍为 `failed`；没有因成功迭代或较低残差而宣称物理通过。不能把变化唯一归因于松弛因子本身：不同路径改变了到达恢复时的数值状态，仍需后续针对线性求解、离散有界性和保存组分的进一步定位。

原始目录为 `E:/大创赛仿真结果/M15_20261024/nozzle_startup/` 下的 `conservative_1`、`restored_2`、`intermediate_3`、`restored_4`。完整低松弛因子链的独立审核在 `outputs/platform_nozzle_startup_review/intermediate_chain_audit_20261024/`；两次失败审核为同级 `restored_rejected_audit_20261024/` 和 `final_restored_rejected_audit_20261024/`。合格重启及失败对照均未手动重新挂接 UDF，各自的代码、输入、输出及清理哈希保留。

本节点没有将实验设置写入平台默认值，也没有自动恢复正式三档。当前已停止新增求解，保留结果供审核；下一步应进一步定位恢复原数值控制时的组分超调，并设计能验证真实喷嘴导热耦合及严格收敛的有界试验。

## 运行与复核

新增 `nozzle_platform/nonuniform_heat.py`、`scripts/benchmark_nonuniform_heat.py`、`scripts/audit_nonuniform_heat.py` 和相关测试；真实喷嘴脚本增加显式 `--species-relaxation`、`--capture-startup` 选项及实际保存设置核对，默认仍为 1.0，旧的 1/24/25 保存方式保留。

```powershell
python scripts/benchmark_nonuniform_heat.py --mode prepare --source 'E:/大创赛仿真结果/M13_20261022/gpu_transport_slab_review/public_runner_20261022' --output 'E:/my_new_heat/prepare'
python scripts/benchmark_nonuniform_heat.py --mode mass --source 'E:/my_new_heat/prepare' --output 'E:/my_new_heat/mass'
python scripts/benchmark_nonuniform_heat.py --mode ideal --source 'E:/my_new_heat/prepare' --output 'E:/my_new_heat/ideal'
python scripts/benchmark_nonuniform_heat.py --mode restart --source 'E:/my_new_heat/ideal' --output 'E:/my_new_heat/restart'
python scripts/audit_nonuniform_heat.py --mass 'E:/my_new_heat/mass' --ideal 'E:/my_new_heat/ideal' --restart 'E:/my_new_heat/restart' --output 'outputs/my_new_heat_audit' --plot
```

所有输出须使用新目录。小算例最低 2 GiB 可用内存、2 GiB 空间、1 GiB 显存，计算段最多 120 秒、整个任务最多 240 秒；真实喷嘴仍要求至少 4 GiB 可用内存、5 GiB 磁盘空间和 4 GiB 显存，原生阶段 180 秒／整个任务 240 秒。均使用双精度 GPU 0、1 个 CPU 设置进程及所属进程清理，无 CPU 回退。原始文件保留在 E 盘。

正式物理、数值收敛、科学适用性、CPU/GPU 收敛解一致性和网格独立性标志继续为 false，正式三档保持停止。

本轮累计 4 次零新增迭代的准备任务、7 次各 25 步的 GPU 求解短测，共 175 个新增 GPU 迭代，全部所属原生进程退出；没有启动平台查看服务器，也没有 CPU 求解回退。相关离线回归 **301 项通过（341.47 秒）**，涵盖新增导热核查、完整字段／组分、实际数值设置、GPU 兼容、进程隔离、收敛门限和结果导出；不包含全仓库或真实 CAD 完整回归。日志及源码哈希在 `.local/m15_related_tests_20261024.log` 和 `.local/m15_related_tests_20261024.json`。

最终审核快照在 `outputs/platform_nonuniform_heat_review/milestone_review_20261024/`，包括源码快照、四份成功／失败审核、喷嘴启动对照 CSV 和 `review_manifest.json`。原始运行代码快照与最终审核实现分别保留；最终实现增加了实际能量开关读取及第 2／3 步核查，不将较早运行快照伪称为完全相同版本。

依据：[Fluent 25.2 GPU Python UDF 的支持范围、保存重启及物性后处理限制](https://ansyshelp.ansys.com/public/Views/Secured/corp/v252/en/fluent_beta_doc/flu_beta_gpgpu_solver_udf.html)。图形界面显示的材料常数和 UDF 挂接日志不能单独证明实际求解采用的导热率；本节点的定量结论来自保存温度与壁面热量。
