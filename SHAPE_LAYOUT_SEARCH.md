# 圆形／扇形安装域：容量、功率模型与紧凑排布

2026-10-17新增：`schema_version=2`支持前后不同截面联合检查，
已用真实CAD尺寸完成两组条件性容量对照。文献依据、40 mm后安装边界、
逐截面净间隙与适用范围见[分截面装配约束与真实尺寸对照](ASSEMBLY_SECTION_SEARCH.md)。
以下v1接口、历史验证记录仍保留，原四个合成示例可继续使用。

## 研究问题与当前实现范围

老师提出两个优化问题：固定结构内最大化功率；给定功率时使燃烧室紧凑、高效。
本入口首先实现可复现的**安装端面几何搜索**与明确声明的功率模型：

| 模式 | 给定 | 目标 |
| --- | --- | --- |
| `fixed_space` | 圆形或扇形边界、喷嘴外径、两项余量、N与布局参数组合 | 有功率模型时最大化估算功率；没有时最大化可行N，功率保持null |
| `fixed_power` | 明确功率口径、单喷嘴能力模型、目标功率、离散半径与N | 在模型功率达到目标的候选中最小化实际安装域面积 |

最优仅限**输入的N、半径和布局参数组合**，不保证连续空间或所有排布的全局最优。
`fixed_power`搜索的是安装端面面积，不是完整三维燃烧室体积；腔长、火焰、公共供给和热负荷分布未定义。
效率和三维体积均保留null；不会把几何均匀性解释为燃烧效率。

原工程配置、M2–M8B身份和协议、原生成器、默认426组搜索均不修改。
此入口独立使用`shape_layout_v1_<完整SHA256>`，不得当作M2 case ID或M6 CFD ID交接。
它不启动Fluent、不创建attempt、CAD、网格、CAS/DAT或CFD数值。

## 安装域和完整喷嘴边界

坐标原点固定为圆心／扇形顶点，XY为安装面；长度mm，配置角度deg。
`circle`要求`start_angle_deg=0, sweep_angle_deg=360`。
`sector`沿逆时针从start扫过sweep，要求`0<sweep<360`，支持跨0°和大于180°的优扇形。

`shape_boundary.py`计算中心到外圆弧和两条**有限径向线段**的最近有符号距离。
喷嘴中心必须位于域内且距全部边界至少`外径/2+wall_clearance_mm`；仅检查中心角度不够。
优扇形不能用两条无限直线的半平面交集代替。
喷嘴中心间距另外要求`>=outer_diameter_mm+edge_gap_mm`。
数量、重叠与间距复用原几何校验；扇形边界余量单独计算，不改写M4圆域指标的含义。

## 功率模型

`power_model=null`表示未知，仅`fixed_space`允许。
有模型时显式填写`per_nozzle_power_kW`、`basis`、`source`、`assumptions`：

- `basis=fuel_input`：单喷嘴燃料热输入；若通过供氢流量与低位热值确定，需在来源中记录具体定义及依据。
- `basis=useful_output`：单喷嘴可用热输出的声明能力；不能把热输入直接标作可用输出。
- 估算总功率=`N × per_nozzle_power_kW`。模型假设单喷嘴能力相同、可以叠加；需要独立审阅总供给、热负荷、喷嘴相互作用等限制。
- `target_power_kW`沿用模型的basis；固定功率模式必须提供模型与正目标值。

这是输入声明下的容量／功率估算，不是测量或反应CFD结果，也不从冷态流场推算燃烧效率。
来源和假设必须非空，软件不认证文字真实性。没有设置经验单喷嘴功率、效率或项目额定工况。
热值口径参考[Cantera热值说明](https://cantera.org/stable/userguide/heating-value.html)。

## 蜂巢、向日葵与对照族

所有族共用同一安装域、外径与余量；每个`layouts`条目声明一组参数，可重复同一家族以比较旋转、相位、间距等。
所有默认值仅为数学构造设置，不是未知工程参数的默认值。

| family | 复用与构造 | 参数 |
| --- | --- | --- |
| `honeycomb` | 调用原hexagonal的交错行网格，在覆盖安装域的有限方形点池中筛选 | 必填spacing_mm；可选rotation_deg、offset_x_mm、offset_y_mm |
| `rectangular` | 调用原rectangular构造覆盖域的方格点池 | 同上 |
| `sunflower` | 调用原deterministic_irregular；零内半径，默认平方根径向律、黄金角 | 可选radius_fraction、pool_multiplier、angular_increment_deg、radial_exponent及旋转平移 |
| `ring` | 调用原ring生成单环对照 | 可选radius_fraction、pool_multiplier及旋转平移 |

蜂巢指六角密排的**三角晶格中心**，不是把喷嘴放在六边形所有顶点。
[Hales蜂巢定理](https://arxiv.org/abs/math/9906042)讨论等面积分区的最小周长，不是喷嘴燃烧性能定理。
向日葵采用`r_i = outer_radius * ((i+0.5)/pool_count)^radial_exponent`，
`theta_i=i*angular_increment`；默认角度为`180*(3-sqrt(5))≈137.507764°`。
这沿用已有代码的半格偏移形式，是Vogel思路的项目参数化。
[相关几何公式](https://link.springer.com/article/10.1186/s43593-021-00005-9)仅用于数学来源，不作为燃烧器实证来源。

环形和向日葵点池大小=`N*pool_multiplier`，默认1；扇形可显式增加倍数以得到裁剪后的候选。
点池整体仍需满足原生成器的实际喷嘴间距约束；不通过放松外径、间距或删除重叠点使其通过。
随后执行显式刚体旋转、平移，按真实边界筛选，取半径／角度排序的前N个。
不足N、过密、越界均记录为不可行，不自动缩放边界、调整N或更换参数。
相同N、外径、域面积下，合法喷嘴的投影占用率恒为`N*pi*(d/2)^2/area`；它不直接表示混合或燃烧效率。

网格旋转／平移和向日葵裁剪都是有限参数族；应明确搜索参数，不能仅凭某次演示判断某种族普遍优于另一种。
计算预算：N和环形／向日葵点池最多2000点；网格覆盖半径（含平移）与节距比不超过15；最多20000组请求。
这些限制控制当前成对距离算法的内存与工作量，不是物理容量上限。

## 输入和示例

完整字段见[待确认模板](examples/shape_search.template.json)。未知安装半径、间隙仍为null，模板会拒绝运行。
`counts`和固定功率下的`radius_candidates_mm`使用明确非空列表，排序去重。
固定空间使用唯一`boundary.radius_mm`，半径列表为空、target为null。
固定功率使用`boundary.radius_mm=null`，提供半径列表和target。
所有布局和参数在坐标生成、目录创建前完成预检，非法输入不会留下输出。

四个可运行格式示例均标为**SYNTHETIC DEMO ONLY**。
其中14mm外径沿用项目当前外径；60/100mm半径、2/3mm余量、2kW单喷嘴及20kW目标均为测试值，不能解释成实际工程输入。

```powershell
python scripts/search_shape_layouts.py --config examples/shape_fixed_space_circle.synthetic.json --output-dir outputs/shape_demo/new_circle
python scripts/search_shape_layouts.py --config examples/shape_fixed_space_sector.synthetic.json --output-dir outputs/shape_demo/new_sector
python scripts/search_shape_layouts.py --config examples/shape_fixed_power_circle.synthetic.json --output-dir outputs/shape_demo/new_compact_circle
python scripts/search_shape_layouts.py --config examples/shape_fixed_power_sector.synthetic.json --output-dir outputs/shape_demo/new_compact_sector
```

输出目录必须不存在。`search_report.json`最后写入，保存完整解析输入、逐候选规格与坐标、不可行原因、
目标未达标但几何合法的候选、所有全局主目标并列优选ID，以及逐家族最优代表。
`candidates.csv`包含全部几何合法候选；每个家族的一个代表另导出`candidate.json`、`layout.png`、
原格式`nozzle_id,x_mm,y_mm,z_mm`坐标，z=0。逐家族主目标并列时按完整ID确定展示代表；不是二次物理排序。
所有候选均保留于报告，未选中的坐标不会丢失。
Git、依赖环境独立保存为provenance，不进入布局身份；dirty记录不能仅凭commit重现未提交源码。

退出码：有满足本模式目标的候选为0；零可行或功率全部不足仍保存完整报告、返回1；输入／导出／核验错误为2。
I/O失败可能留下不完整目录，不能作为完成结果，另选新目录重试。

```powershell
python scripts/search_shape_layouts.py --verify outputs/shape_demo/new_circle/search_report.json
```

核验只读重放配置，检查候选、身份、指标、选择与不可行记录；复核固定产物清单和文件校验和，
并核对CSV与candidate.json的实际内容。PNG只做完整性核验，不认证其科研有效性；哈希和输入来源声明都不是数字签名。
未来CAD/M6交接需另行明确适配真实安装边界；本入口不会绕过旧工程链的圆形输入限制。

## 下一阶段

取得实际圆形／扇形尺寸、安装间隙和壁面余量，并确认功率是热输入还是可用输出及单喷嘴能力来源。
之后运行真实几何搜索，选择少量代表，定义公共燃烧室和数值验证方案，再独立授权反应CFD。
效率、温度场、压损、壁面热流和稳定运行条件需正式评价契约与计算／实验支持；本阶段保持未验证。

## 本次验证记录（2026-10-14）

- 新增定向测试：36 passed。覆盖圆弧／直边、优扇形／跨0°、解析内切圆、数量／间距／重叠、
  两种模式的独立目标核对、黄金角复用、未知功率、不可行／功率不足、身份、完整预检、导出及篡改拒绝。
- 完整原样回归（仅指定工作区内较短的新basetemp）：1690 passed、1 failed、2 skipped。
  唯一失败为旧M8A UNC路径用例在沙箱中的WinError 65；同一用例在沙箱外单独复跑1 passed。
  两项跳过为缺少可选CAD依赖的真实内核测试。没有改旧测试、pytest配置或旧生产实现。
- 首次较长临时路径触发旧工程归档复制的Windows路径长度问题，已保留失败目录；
  使用短目录复跑后相关测试通过，不截断任何科学ID。
- 四个合成示例均实际生成，并通过只读重放；逐家族PNG及对照总图已目视检查。
  本地输出位于`outputs/shape_design_20261014/`，该目录被原忽略规则覆盖。
- 新增文件语法／空白检查与git diff --check通过。真实工程配置的null、原单喷嘴基准及旧算法保持原值。
  本轮没有启动真实Fluent、创建真实执行attempt或生成CFD结果。
