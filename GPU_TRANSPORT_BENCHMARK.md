# 原混合律 GPU 小算例与重启验证

用途：在正式喷嘴计算之前，确认 GPU 实际采用了原理想气体混合黏度和导热率公式，并验证保存后重新启动仍有效。本节点已经完成小算例、保存文件独立核对和自动脚本验证，2026-10-23 经用户审核并批准上传 GitHub；正式喷嘴三档网格研究仍停止。

## 实际结果

采用 256 个规则六面体单元、10 mm × 10 mm × 10 mm 气体区域。左壁 300 K、右壁 310 K，其余壁面绝热，重力与反应关闭，速度为零。氢气质量分数固定为 0.1，其余为空气：O2 为 0.2097、N2 为 0.6903、H2O 为 0。各组分常数物性与原喷嘴模型相同，全部使用双精度 GPU 计算，1 个 CPU 设置进程。

这个算例的理论导热量为 `Q = k A ΔT / L`，单元温度为 `T(x) = 300 + 1000x`（x 以米计）。核对实际保存的黏度和壁面导热量，可以区分两种混合律。

| 阶段 | 保存步数 | 理论导热量 W | 保存的热壁导热量 W | 黏度 Pa·s |
| --- | ---: | ---: | ---: | ---: |
| GPU 质量加权控制组 | 25 | 0.003858388 | 0.003858388 | 1.6344932e-5 |
| GPU 原公式 | 50（新增 25 步） | 0.009297509886 | 0.009297509886 | 1.5812984303e-5 |
| 保存后重新启动 | 75（新增 25 步） | 0.009297509886 | 0.009297509886 | 1.5812984303e-5 |

原公式及重启的最大导热量相对误差约 1.72e-14，保存黏度与原公式的最大相对偏差约 4.29e-16。全部单元的组分、温度、速度及有限性检查通过；温度与线性解析解一致。逐阶段读取全部 25 步残差，最终各方程残差小于本算例规定的 1e-10。热量检查使用 1e-5 相对门限，原平台网格与守恒门限没有修改。

导热量由 data 文件的 `SV_HEAT_FLUX` 按边界面求和得到，它在本保存格式中是每个面的 W 值，不能再乘一次面积；控制组保存值已与 Fluent 在线热量报告独立交叉核对。黏度读取实际 `SV_MU_LAM`，不是 UDF 自己输出的预期数值。单元位置由保存网格的面与节点重新计算，未假定导入后单元顺序保持。

## 解决的运行问题

本机 Fluent 25.2 使用 Numba 0.57.0。诊断发现其旧 ctypes 绑定会优先选择 CUDA 13 新增的 `cuCtxGetDevice_v2`，但仍按旧接口的参数调用，离线复现了访问异常。NVIDIA 上游已对 `cuCtxGetDevice` 和 `cuCtxSynchronize` 的入口选择作相应修正。

新增 `gpu_numba_compat.py` 仅在明确启用的实验进程中，将这两个函数绑定到与旧参数匹配的原入口。修正检查 Numba 版本、绑定类型及函数参数，遇到未知组合拒绝；没有修改 Ansys 安装文件、升级软件、切换驱动或改变计算精度。生成函数的默认行为仍不启用此修正，正式平台路径没有新增 UDF 选项。

修正后两个 GPU 函数在 Fluent 自带编译器中完成离线编译。不过，同步迭代接口仍在 UDF 重载处停滞；改由 Fluent 自己的原生 journal 命令循环执行后，实际编译、计算、保存和重启均已完成。因此不能把此前停滞全部归结于网格规模，也不能仅凭离线编译通过认定求解已通过。

公开运行器使用 journal 自动加载、迭代及保存，再只读核对结果，不在计算结束后建立额外的求解器连接。它保留总时限 200 秒、原生脚本等待 120 秒、每次新增 25 步、1 个 CPU 设置进程及不回退 CPU 的限制。设备与空闲显存在启动前核查；生成代码、输入 case/data 和本次实现分别记录哈希。

重启验证从上一次公开脚本生成的 case/data 直接读取，不再手动调用 `gpuapp-add-udf`，并核对两个 Python 文件完全相同。保存包必须同时保留 `ideal_transport.py` 和 `gpu_numba_runtime_fix.py`；仅有 case/data 不足以完整复现该实验。

## 代码与复现

* `nozzle_platform/transport_benchmark.py`：生成确定性小网格，读取完整保存字段，检查实际热量、单元位置和解析结果。
* `nozzle_platform/gpu_numba_compat.py`：受版本及 ABI 检查约束的进程内修正。
* `nozzle_platform/transport_audit.py`：原公式与兼容混合律，以及显式选择的 UDF 生成。
* `scripts/benchmark_gpu_transport.py`：从已保存的小算例基准自动运行原公式或重新启动，输出目录必须是新目录，失败返回非零退出码。
* `scripts/audit_gpu_transport_benchmark.py`：独立只读复核控制组、原公式与重启三段原始文件和来源链，不启动 Fluent。

以下命令使用本机已经生成并核对的 256 单元基准。基准目录须包含 `configured.cas.h5`、`baseline.dat.h5`、`mesh.json`、`reference_configured.json`、`spec.json` 和清理记录；`spec.json` 中的 Fluent/Python 配置来自本机可信任务。该脚本目前面向已保存的小算例，尚不是自动创建任意基准模型的通用入口。基准准备源码和当时的实现已冻结在所属原始任务目录中。

```powershell
python scripts/benchmark_gpu_transport.py `
  --source 'E:/大创赛仿真结果/M13_20261022/gpu_transport_slab_review/7e6ec17a75d344bf8ae364fd51fb90c1' `
  --output 'E:/大创赛仿真结果/M13_20261022/gpu_transport_slab_review/my_original_law_run'

python scripts/benchmark_gpu_transport.py `
  --source 'E:/大创赛仿真结果/M13_20261022/gpu_transport_slab_review/my_original_law_run' `
  --restart `
  --output 'E:/大创赛仿真结果/M13_20261022/gpu_transport_slab_review/my_restart'
```

本次公开脚本实测输出为 `public_runner_20261022` 和 `public_restart_20261022`。原公式运行的启动、计算及保存原生段约 16.3 秒；重新启动的整个控制任务约 19.5 秒。这是小算例任务时间，不是正式喷嘴的速度预估。

最终独立审计、对比图及字段 CSV 在 `outputs/platform_gpu_transport_slab_review/final_audit_20261022/`。数据和审计保留于本机，原始文件在 E 盘，不随代码上传。相关公式、ABI、保存字段、启动隔离、原工作流及网格研究限制共 **211 项检查通过（3.15 秒）**，日志 `.local/gpu_slab_related_tests.log`；不是全仓库完整回归。

上传前复核（2026-10-23）：平台接口、GPU、物性、网格及导出相关回归共 **356 项通过（13.49 秒）**，日志 `.local/m13_publish_tests_20261023.log`。不含真实 CAD 回归，未重新启动 Fluent；原 211 项记录作为当时小算例节点的检查证据保留。

## 失败记录与结论边界

首两次小网格设置试验在初始化 API 检查处零步停止。后续两次同步 UDF 重载试验达到等待上限，其中控制组的 25 步和完整 data 已保存；控制组检查点经独立审计可用，其后续失败状态保持。首次 journal 试验实际完成原公式计算及保存，但末尾的额外接口连接因主机名识别而失败，该任务仍保留失败记录。之后的公开脚本移除了该连接步骤，原公式运行及其自身的重启链均正常结束。所有试验源码快照和日志保留，没有用新代码改写旧记录。

本节点证明的是**固定组成、静止导热小算例中的原公式 GPU 实际物性与保存重启**。它没有验证非均匀组分的混合层、真实喷嘴压降、黏性流动性能、燃烧或 CPU/GPU 收敛喷嘴解的一致性。`formal_physics_validated`、`numerical_criteria_met`、`scientific_eligible`、`grid_independence_verified` 在整体项目中仍为 false。

下一步先做真实粗网格的有界原物性 GPU 试算，逐单元检查非均匀组成下的物性和保存通量，再验证收敛；正式三档研究保持停止。原喷嘴结果、此前检查点及现有平台接入测试均保留，仿真进程已退出，平台审核页面保留运行。

依据：[Fluent 25.2 GPU Python UDF](https://ansyshelp.ansys.com/public/Views/Secured/corp/v252/en/fluent_beta_doc/flu_beta_gpgpu_solver_udf.html)、[NVIDIA CUDA 13 上下文 API](https://docs.nvidia.com/cuda/archive/13.0.2/cuda-driver-api/group__CUDA__CTX.html)、[NVIDIA 上游入口选择修正](https://github.com/NVIDIA/numba-cuda/commit/6e08c9d08e9de59c7af28b720289debbbd384764)。
