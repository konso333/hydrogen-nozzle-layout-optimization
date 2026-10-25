# M17：单会话 GPU 核查与平台验证报告页

用途：核查同一初值下单会话与保存重启的差异，取得 Fluent 实际总压报告，并把审核结果接入平台界面。本轮新增 100 个 GPU 迭代，单会话 11 个保存点通过独立检查；正式收敛和完整组分通量核查仍未通过。本节点已于 2026-10-25 经用户审核并批准上传 GitHub。

## 软件上新增了什么

主界面右上角新增“仿真验证报告”，地址 `/validation.html`。页面读取本机已审核的 M16／M17 记录，分别显示保存数据、数值收敛和物理适用范围，提供指标表、真实趋势图、原始报告查看及审核 ZIP 下载。缺失指标显示“未取得”，不填成 0；文件缺失、哈希不匹配或来源不一致时，停止展示通过状态和下载入口。

这是只读的历史验证报告页，打开、刷新、切换和下载都不会启动 Fluent。记录对应真实单喷嘴冷流，不会自动套用到用户当前生成的新阵列。原物性 GPU 正式模式和三档研究仍关闭；没有增加未验收的自动求解按钮。页面仅读取轻量审核包，浏览时不加载大型 case/data。

本机审核入口：`http://127.0.0.1:8765/validation.html`。查看服务保留运行，全部 Fluent 求解进程已退出。代码上传后，未携带本机记录的新环境显示空状态；可以用下述导出脚本安装自身的审核记录。

## 单会话对照实际完成情况

使用与 M16 字节完全相同的第 2050 步 case/data、同一 851803 单元网格、原混合物性和 UDF 文件。组分松弛因子保持 0.1、二阶格式、绝对线性容差 1e-20、组分相对线性控制 1e-6、内迭代上限 100，双精度 GPU 0、1 个 CPU 设置进程，不回退 CPU 求解。

在一个原生 Fluent 会话中完成 2051～2150 步，保存第 1、2、3、24、25、49、50、74、75、99、100 个新增步骤。全部单元与边界检查通过，单元组分和最大偏差约 **4.00e-15**，原 1e-8 门限保持；初期和四个批次末态的实际黏度与相邻组成的原公式吻合。没有裁剪、归一化或修补保存字段。

所有求解完成并保存最终 case 后，才在同一会话内重新读取各个保存点、生成总压报告和诊断组分报告。报告命令不插入求解轨迹。实际原生阶段约 **233.25 秒**，包含启动、读取、编译、保存和后处理，不是纯计算测速；原生上限 240 秒、整个任务上限 360 秒。没有追加这 100 步以外的求解。

| 新增步数 | 氢气对流不平衡 | 出口场时间变化 | 内部截面时间变化 | 同步数相对 M16 的全单元速度场差异 |
| ---: | ---: | ---: | ---: | ---: |
| 25 | 5.0619% | 7.5246% | 5.0952% | 0.006826% |
| 50 | 3.9789% | 15.2559% | 2.4347% | 0.010285% |
| 75 | 5.6401% | 12.7442% | 2.0957% | 0.008610% |
| 100 | 0.05988% | 15.3898% | 1.4534% | 0.003048% |

时间变化是相邻 25 步端点的共同物理位置采样，取速度矢量与氢质量分数相对 L2 变化的较大者；最后一列是单会话与 M16 重启链在同一步数的差异，不能混为一类。首个 25 步在额外重启发生前就存在小差异，因此不能把所有差异都归因于重启，也没有用一次对照宣称位级重复或收敛解一致性。

本轮两条轨迹都存在相近的未收敛变化。单会话末批出口／内部变化约 15.39%／1.45%，远高于原 1e-4 的漂移门限；速度和能量的批内峰值残差也未达标。仅 100 个新增步骤不满足本轮至少 200 步及连续三批的严格验证要求，没有借用原 CPU 2000 步补足。正式数值、物理适用性、CPU/GPU 收敛解对照和网格独立性标志继续为 false。

## 实际总压与组分报告核查

总压使用原生 `report/surface-integrals/area-weighted-avg` 对三个边界输出，解析时核对 Total Pressure、Pa、唯一边界和有限数值。初值及每个 25 步保存端点均保留原始文本和哈希，保留 Fluent 打印精度，不把打印值伪称逐面双精度原始场。最终燃料、空气入口至出口的实际总压差约 **5.77291 Pa、8.38340 Pa**；静压没有冒充总压。

三个先行报告探针均为零新增迭代：前后计数保持 2050。第二次在本机 PyFluent 字段清单接口差异处退出，所有失败与清理记录保留，之后按本机 0.42.1 的公开接口修正。零步探针的另存数据与输入核对，除组分最大约 7.73e-14 的差异外，已检查的速度、压力、温度、密度及黏度字段完全一致；没有覆盖原输入。

传统 `species-mass-flow` 命令在零步探针及本次求解结束后都返回燃料入口氢气流量 0，并将约 6.47e-7 kg/s 记在 N2 项，与纯氢入口的保存组分矛盾。该输出保持为诊断原文，**没有用于完整氢气守恒验收**。另一方面，`get_flow_rate(report_of='h2')` 得到的入口约 6.4696007e-7 kg/s 与保存边界的对流积分吻合；它仍只是对流项。当前未取得独立可靠的完整组分通量，界面／JSON 保持为空。

原 reference 和实际保存 case 同时确认 `species/inlet-diffusion?` 为 false。不能用“遗漏了入口扩散”解释本工况的收支偏差，也不能由此推断域内扩散关闭或所有边界扩散都已独立验证。可查询清单只有 `diffl-h2` 等扩散系数，没有据此自行构造未经核验的完整通量。

## 实现与复现

* `nozzle_platform/native_reports.py`：受限原生报告命令、实际总压解析及文件哈希。
* `nozzle_platform/nozzle_gpu_probe.py`、`scripts/probe_nozzle_gpu_transport.py`：默认 25 步及原时限保持，显式增加 100 步单会话、配对保存点和求解后报告读取；100 步路径要求完整的 0.1 二阶重启参数。
* `scripts/audit_single_session_gpu.py`：只读独立核对全部保存点、来源链、实际控制、黏度、总压与共同截面，并与冻结 M16 证据比较。
* `nozzle_platform/review_reports.py`、`scripts/export_verification_report.py`：固定本机目录、只读查询、来源与文件哈希、不可覆盖的轻量报告包。
* `nozzle_platform/server.py`、`static/validation.*`：报告入口、独立状态卡、缺失项、真实图表和下载；旧建模、网格与任务接口保留。

原始数据在 `E:/大创赛仿真结果/M17_20261024/`，包括 `reports_probe_1/2/3` 和 `single_session_1`。日期后缀沿用本轮起始记录编号，日志保留实际跨日运行时间。独立审核、CSV 和图在 `outputs/platform_single_session_review/final_audit_20261024/`；报告包安装于本机平台输出根目录的 `verification_reports/`，不会上传大型原始数据。

```powershell
python scripts/probe_nozzle_gpu_transport.py --cpu '<CPU源任务>' --gpu '<GPU兼容源任务>' --restart '<M15合格0.1末态>' --species-relaxation 0.1 --linear-absolute-tolerance 1e-20 --strict-species-linear --capture-startup --session-steps 100 --capture-reports --output 'E:/my_new_single_session'
python scripts/audit_single_session_gpu.py --candidate 'E:/my_new_single_session' --m16-audit '<M16独立审核JSON>' --m16-manifest '<M16冻结清单>' --domain-job '<CPU源任务>/job.json' --output 'outputs/my_new_single_audit'
python scripts/export_verification_report.py --kind m17 --audit 'outputs/my_new_single_audit/audit.json' --report-id 'm17_new_review' --output-root '<平台输出根目录>/verification_reports'
python scripts/run_platform.py --no-browser
```

所有输出使用新目录。新试验仍要求至少 4 GiB 可用内存、5 GiB 磁盘空间和 4 GiB 显存，并为父／子程序留启动余量；报告页不启动求解，不自动延长预算。

## 测试与审核

相关离线／回环回归 **271 项通过（11.00 秒）**，覆盖旧平台接口、25 步兼容、100 步预算、报告解析、缺失项、来源与文件篡改、下载路径、GPU 与收敛门限。日志 `.local/m17_related_tests_20261024.log`，源码／日志哈希在同名 JSON；不是全仓库或真实 CAD 完整回归。首次 HTTP 测试受沙箱回环访问限制，移至允许 127.0.0.1 的测试环境后通过，未为此降低应用边界检查。

真实浏览器核对了 M16／M17 切换、三个独立状态、完整通量“未取得”、原生报告文本、桌面／390 px 手机布局，以及审核 ZIP 实际下载和哈希。错误状态测试通过拦截测试浏览器响应完成，生产文件没有被改写；校验失败会隐藏通过状态与下载入口。首轮测试遗漏先展开折叠区，修正测试操作顺序后通过；原截图保留。最终证据在 `outputs/platform_single_session_review/browser_review_final_20261024/`。

最终冻结清单在 `outputs/platform_single_session_review/milestone_review_20261024/`。结论限定为单会话对照和报告接入完成；完整通量、严格收敛、真实喷嘴导热耦合、多喷嘴燃烧及网格独立性仍待验证，正式三档继续停止。

依据：[Fluent 物种输运与入口扩散](https://ansyshelp.ansys.com/public/Views/Secured/corp/v252/en/flu_ug/flu_ug_chp_finrate.html)、[收敛与稳定性判断](https://ansyshelp.ansys.com/public/views/secured/corp/v252/en/flu_ug/flu_ug_sec_solve_conv_stab.html)。本节点的接口矛盾、实际数值和对照结论来自本机原始记录，不是厂商对根因的确认。
