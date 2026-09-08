# M5：文献—几何模型—代码实现可追溯关系

M5 建立轻量、可查询的文献证据登记表，并新增一个 opt-in 的五喷嘴十字布局和一个七管正六角间距实验。它只复现论文明确支持的二维拓扑或几何参数，不声称复现燃烧器硬件、边界条件、流动、火焰或热声结果。

## 1. 文献选择与证据边界

优先采用能够核对 DOI、题名、出版物与直接研究对象的期刊论文。原始实验/数值研究记为 `primary_research`；综述记为 `secondary_review`。综述可支持研究背景、技术类别和已有阵列研究，但不能代替原始论文证明某一具体尺寸或性能结论。

布局 provenance 使用三个状态：

- `literature_backed`：来源直接支持所声明的拓扑或工程排列概念；不等于完整硬件复现。
- `literature_inspired`：代码只抽象了来源中的工程思想，缺少足以称为几何复现的硬件细节。
- `engineering_derived`：为本项目的确定性几何搜索自行构造；没有被包装成已有文献布局。

`evidence_level` 进一步使用 `direct_primary`、`secondary_review`、`engineering_abstraction` 或 `project_defined`。这些字段只描述科研来源，不进入 M2 的 geometry identity。

## 2. 文献登记表

机器可读元数据位于 [`literature/layout_references.json`](literature/layout_references.json)，查询层位于 [`literature/registry.py`](literature/registry.py)。登记表只保存 citation metadata、DOI、URL、保守的 supported claim 和 abstraction notes，不保存论文 PDF。

| ID | 来源 | 类型 | M5 使用范围 |
| --- | --- | --- | --- |
| H1 | *Effects of Array Spacing on Thermoacoustic Instability and Heat-Release Dynamics in a Hydrogen Micromix Array Combustor*, Aerospace Science and Technology (2026), DOI: [10.1016/j.ast.2026.112865](https://doi.org/10.1016/j.ast.2026.112865) | primary | 直接支持纯氢 micromix、7 tubes、regular hexagonal configuration、8/10/12 mm spacing 和 S/D=2.0/2.5/3.0。热声结论只适用于论文工况。 |
| H2 | A. Durocher et al., *Characterization of a 5-nozzle array using premix/micromix injection for hydrogen*, Applications in Energy and Combustion Science 18 (2024) 100260, DOI: [10.1016/j.jaecs.2024.100260](https://doi.org/10.1016/j.jaecs.2024.100260) | primary | 直接支持氢 premix/micromix 实验中的 five-injector cross-shaped array，以及其作为多单元系统 sector 的用途。只支持 `cross_5`，不支持任意 `cross_N`。 |
| H3 | J. Berger, *Scaling of an Aviation Hydrogen Micromix Injector Design for Industrial GT Combustion Applications*, Aerotecnica Missili & Spazio 100 (2021) 239–251, DOI: [10.1007/s42496-021-00091-5](https://doi.org/10.1007/s42496-021-00091-5) | primary | 直接描述 annular combustor 中喷注单元沿周向并排成环、沿径向逐排堆叠的工程结构。 |
| H4 | *Development of an Additive Manufactured Novel Hydrogen Combustor on Basis of the Proven Micromix Principle*, Journal of Engineering for Gas Turbines and Power (2026), DOI: [10.1115/1.4070243](https://doi.org/10.1115/1.4070243) | primary | 直接描述 Ring-Burner 的周向氢分配和 RS-Burner 经多条 spokes 的径向氢分配；只用于 `radial_spoke` 的工程启发来源。 |
| H5 | L. Beltrán et al., *Hydrogen combustion in micromix burners: Present stages, opportunities, and challenges*, International Journal of Hydrogen Energy 96 (2024) 622–638, DOI: [10.1016/j.ijhydene.2024.11.371](https://doi.org/10.1016/j.ijhydene.2024.11.371) | review | 用作 micromix 技术、多喷嘴阵列研究和矩形阵列示例的二级来源，不用作精确参数或硬件复现依据。 |

H1 和 H4 的作者字段暂留空，因为本阶段未对完整作者列表做独立可靠核验；没有据不完整搜索结果补写作者。

查询示例：

```python
from literature import get_layout_provenance, get_reference

hex_evidence = get_layout_provenance("hexagonal")
h1 = get_reference("H1")
```

加载时会检查 reference/layout ID 唯一性、DOI 和 canonical DOI URL、枚举、引用完整性，以及 `literature_backed`/`literature_inspired` 的最小证据要求。

## 3. 九类参数化布局的映射

| layout_type | 数学构造 | status / evidence | 证据与抽象边界 |
| --- | --- | --- | --- |
| `rectangular` | 以原点为中心的矩形网格；可指定 rows×columns，或取中心最近的 N 个方格点 | literature_backed / secondary_review / H5 | H5 只作矩形/多喷嘴阵列的二级来源；N、行列、节距和点序是项目实现。 |
| `hexagonal` | 二维三角晶格；可显式给定各行点数，或取允许圆内距原点最近的 N 个晶格点 | literature_backed / direct_primary / H1 | H1 直接支持 N=7 正六角及三组 spacing；一般 N 晶格族是项目扩展。代码名称 `hexagonal` 实际表示 triangular-lattice/hexagonal-packing projection，并非任意同心六边形环。 |
| `ring` | 显式半径、点数和相位的一个或多个同心圆环，可选中心点 | literature_backed / direct_primary / H3 | H3 支持周向成环和径向成排的硬件概念；本项目半径、数量和相位不是论文硬件尺寸。 |
| `staggered_ring` | 在 `ring` 上给每个后续圆环增加确定性相位 `k*delta_theta` | engineering_derived / project_defined | 为几何搜索建立，M5 未指定直接来源。 |
| `nonuniform_ring` | 显式圆环半径，或在内外半径之间用幂律生成非均匀径向间隔 | engineering_derived / project_defined | 幂律和参数范围为项目定义，不宣称物理最优。 |
| `sector` | 在等角分区内重复同一扇形点阵，并按径向层分配点数 | engineering_derived / project_defined | 这是项目二维扇形构造，不等于 H2 的真实 multi-element hardware sector。 |
| `radial_spoke` | 在等角射线上放置等径向位置点 | literature_inspired / engineering_abstraction / H4 | 只抽象“radial spokes”概念；未复现 spoke width、供氢通道、孔径、airgate 或增材制造硬件。 |
| `deterministic_irregular` | 按确定性径向幂律与角增量生成非网格序列 | engineering_derived / project_defined | 作为可复现对照族，不主张文献最优性。 |
| `cross_5` | 中心点加沿 ±x/±y 的四个等距点 | literature_backed / direct_primary / H2 | H2 支持五喷嘴十字拓扑；坐标轴朝向与自由 `pitch` 是项目参数化。 |

`engineering_derived` 不是删除理由。这些布局仍可作为受统一硬约束控制、可复现的几何对照或搜索族；准确标注来源比假称其为文献布局更重要。它们能否带来物理性能收益必须另行验证。

## 4. Paper hardware、literature topology 与项目模型

| 层次 | 包含内容 | M5 是否复现 |
| --- | --- | --- |
| Paper hardware | 三维喷注器/燃烧器、供氢和空气通道、壁厚、孔径、spoke/airgate 形状、材料和制造结构 | 否 |
| Literature topology | 论文明确描述的 5 点十字、7 管正六角、周向环、径向 spokes 等空间关系 | 对明确声明的部分建立映射 |
| Project mathematical abstraction | 圆形有效域内的二维喷嘴中心点、N、pitch/spacing/半径/相位及固定点序 | 是 |
| Project engineering extension | 任意支持 N 的晶格、错位环、非均匀环、扇区和确定性非规则族 | 是，但标为项目构造 |

因此 `literature_backed` 也不自动等于 `exact reproduction`。相同二维几何不会因为引用文字改变而产生新 case ID；引用是科研 provenance，不是 geometry identity。

## 5. `cross_5` 数学定义与系统接入

`cross_5` 只接受 `N=5`。对项目自由参数 `pitch=p>0`（mm），固定有序坐标为：

```text
(0, 0), (+p, 0), (-p, 0), (0, +p), (0, -p)
```

H2 直接支持“五喷嘴十字拓扑”。H2 没有在本项目中被解释为给出任意 `cross_N`，也没有从论文图片反推固定 pitch。本项目选择 axis-aligned 坐标、点顺序和自由 `pitch`，以便做确定性二维几何研究。

`cross_5` 通过公共 `generate_layout()` 复用 M1 输入检查和几何硬约束；通过 `CaseSpec` 将 topology、N、pitch、R/d/s_min/tolerance 纳入 M2 case identity；通过显式 M3 JSON group opt-in；通过公共 M4 evaluator 获得同一套 19 项指标。改变 pitch 会改变 case ID；改变登记表中的 citation 不会。

运行示例：

```python
from layouts import generate_layout

points = generate_layout("cross_5", 5, 55, 4, 8, pitch=10)
```

## 6. H1 七管正六角 spacing 实验

[`examples/m5_hex7_spacing_study.json`](examples/m5_hex7_spacing_study.json) 显式定义三个 case：

| N | d (mm) | spacing S (mm) | S/D |
| ---: | ---: | ---: | ---: |
| 7 | 4 | 8 | 2.0 |
| 7 | 4 | 10 | 2.5 |
| 7 | 4 | 12 | 3.0 |

H1 直接报告 `S=8/10/12 mm` 和 `S/D=2.0/2.5/3.0`，这些比值在数值上对应 `D=4 mm`。在本项目的二维几何抽象中，使用项目字段 `d=4 mm` 构建这些 spacing-ratio cases。这里的 `d` 是 project geometry mapping parameter；这一映射不表示项目 `d` 与实验燃烧室中全部真实 tube、injector 或 combustor hardware diameter 定义具有完整尺寸等价关系。

对现有生成器逐坐标审计得到：

```text
(0,0), (-S/2,-sqrt(3)S/2), (+S/2,-sqrt(3)S/2),
(+S,0), (+S/2,+sqrt(3)S/2), (-S/2,+sqrt(3)S/2), (-S,0)
```

所以它精确满足“一个中心点 + 六个等半径点、相邻方位角 60°、最近中心距 S”的二维 regular-hexagonal topology。没有修改旧 `hexagonal` 算法或点顺序。

示例中的 `d=4 mm` 映射、`R=55 mm`、`s_min=8 mm`、`tolerance=1e-9 mm`、二维圆形验证域和 `symmetry_tolerance` 均是本项目二维建模设置；除 `d=4 mm` 是由 H1 已报告的 S 与 S/D 数值关系建立的映射外，不把这些项目字段解释为论文给出的完整硬件参数。这个示例只能称为 literature geometry spacing-ratio case，不能称为 H1 热声结果复现，因为尚未复现 acoustic boundary conditions、Reynolds number、equivalence ratio、完整 combustor hardware、flame dynamics 和 measurement system。

执行：

```bash
python scripts/run_search_experiment.py --config examples/m5_hex7_spacing_study.json --execute --archive-root outputs/runs
```

## 7. 阶段边界与后续候选

M5 有意没有新增 `cross_7`、`cross_9`、任意 `cross_N` 或 `concentric_hexagonal`，也没有把 H2 的 hardware sector 改写为当前 `sector` 算法。更多环形、分级、扇区或非均匀阵列候选必须先取得能直接支持其二维定义或明确工程抽象的来源，再进入后续阶段。

几何合法性和 19 项几何指标只描述点阵。低 NOx、氢转化率、火焰稳定性、出口温度均匀性、压损、壁面热流和热声响应等结论，必须等待固定工况下的 CFD 与实验验证。M5 不执行 CFD，也不改变 M4 的 legacy objective profile。
