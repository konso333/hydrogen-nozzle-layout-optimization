# 氢燃烧器喷嘴阵列几何布局优化

本项目在圆形燃烧器端面内生成和比较喷嘴中心坐标。当前阶段只研究几何可行性与几何分布特征，为后续 Fluent CFD 建模提供候选方案。

本程序不会根据几何指标推断氢转化率、燃烧完全度、温度场、压损或壁面热流，也不会自动生成任何 CFD 数值。因此，输出中的“Pareto 候选”仅表示几何层候选，不表示燃烧性能最优。

## 安装

建议使用 Python 3.10 或更高版本：

```bash
python -m pip install -r requirements.txt
```

所有命令均在 `nozzle_layout_optimization/` 目录执行。

## 支持的布局

- `A_Rectangular`：固定 N=24 的 6×4 矩形 baseline，节距 18 mm。
- `B_Hexagonal`：固定 N=24 的 4-5-6-5-4 三角晶格 baseline，最近邻节距 16 mm。
- `C_Double_Ring`：固定 N=24 的 8+16 双环 baseline。
- `D_Triple_Ring`：固定 N=24 的中心点+7+16 三层 baseline。
- `rectangular`：可参数化矩形网格。
- `hexagonal`：可参数化二维三角晶格。
- `ring`：显式设置每个环的半径、喷嘴数和相位角。
- `sector`：单扇形或多扇区布局，支持多个径向层和整体旋转。
- `radial_spoke`：等角度径向辐射布局。
- `staggered_ring`：相邻圆环按 `delta_theta` 依次错位。
- `nonuniform_ring`：显式或幂律设置非均匀环半径和各环数量。
- `deterministic_irregular`：由确定性径向律和角度增量生成的非网格对比布局。

所有生成器使用统一接口：

```python
from layouts import generate_layout

points = generate_layout(
    layout_type="sector",
    N=24,
    R=55,
    d=4,
    s_min=8,
    num_sectors=4,
    points_per_sector=6,
    inner_radius=18,
    outer_radius=46,
    sector_angle=3.141592653589793 / 6,
    angular_offset=3.141592653589793 / 16,
    radial_levels=3,
)
```

角度参数均使用弧度。相同参数始终产生完全相同的坐标。生成器返回：

```text
[(x1, y1), (x2, y2), ...]
```

生成阶段会检查点数、圆形边界、喷嘴重叠和最小中心距。`s_min` 表示最小中心距；实际硬约束为：

```text
任意两个喷嘴中心距 >= max(d, s_min)
任意喷嘴中心半径 <= R - d/2
```

## 生成 N=24 baseline

```bash
python scripts/generate_baselines.py
```

可覆盖几何参数：

```bash
python scripts/generate_baselines.py --R 55 --d 4 --s-min 8
```

四个 baseline 使用原项目已经验证过的数学构造，不使用随机采样。

## 比较 N=24 扩展布局

```bash
python scripts/compare_layouts.py
```

该脚本比较四个 baseline，以及 sector、radial、交错环、非均匀环和确定性非网格示例。

## 可变 N 搜索

默认搜索 N=12～40：

```bash
python scripts/search_variable_n.py
```

也可指定范围和几何约束：

```bash
python scripts/search_variable_n.py --N-min 12 --N-max 40 --R 55 --d 4 --s-min 8
```

程序对每个 N 构造多个确定性候选，过滤越界、重叠、间距不足或数量不正确的方案，然后计算几何指标。搜索使用三个相互独立的最大化目标：

1. N；
2. `uniformity_score`；
3. `min_center_distance`。

程序采用非支配解/Pareto frontier，不把三个量随意相加，也不输出唯一“最佳 N”。

## 几何评价指标

`geometry.metrics.evaluate_geometry()` 计算：

- 喷嘴数量；
- 最小中心距；
- 最近邻距离均值和标准差；
- 最大、平均中心半径及径向标准差；
- 边界、重叠、最小间距和期望数量检查；
- x 轴、y 轴和原点中心对称性；
- 最近邻距离变异系数与几何均匀性分数。

对每个喷嘴 i，先计算最近邻距离 `q_i`。定义：

```text
CV_nn = std(q_i) / mean(q_i)
uniformity_score = 1 / (1 + CV_nn)
```

分数范围为 0～1，越高表示各喷嘴的局部最近邻距离越一致。它是空间规则性的几何代理指标，不是燃烧效率。单一圆环也可能具有很高的最近邻规则性，因此分析时应同时查看中心半径、径向标准差和布局类型。

## 输出目录

程序只向 `outputs/` 写结果：

```text
outputs/
├─ figures/       PNG 布局图和 Pareto 权衡图
├─ coordinates/   喷嘴坐标 CSV
└─ summaries/     指标、可变 N 结果和 Pareto 候选 CSV
```

主要汇总文件：

- `outputs/summaries/baseline_layout_summary.csv`
- `outputs/summaries/layout_comparison.csv`
- `outputs/summaries/variable_n_results.csv`
- `outputs/summaries/pareto_candidates.csv`
- `outputs/figures/variable_n/pareto_tradeoffs.png`

### GitHub 中保留的代表性结果

为避免仓库包含数百份可重复生成的坐标和图片，`.gitignore` 默认排除批量输出。仓库只保留以下代表性结果：

- `outputs/figures/A_Rectangular.png`
- `outputs/figures/B_Hexagonal.png`
- `outputs/figures/comparison_E_Sector_4x6.png`
- `outputs/figures/variable_n/pareto_tradeoffs.png`
- 上述 A、B、sector 示例的坐标 CSV
- `outputs/summaries/` 中的五个汇总 CSV

`outputs/coordinates/variable_n/` 中的逐候选坐标、其余布局图和 `outputs/test_tmp/` 不进入 Git。它们均可通过前述三个脚本重新生成；忽略规则不会删除本地结果。

## Fluent CFD 接口

`optimization/cfd_metrics.py` 只读取未来 Fluent 后处理得到的 CSV，不估算或填充缺失值。CSV 需要包含：

```text
candidate_id
hydrogen_conversion
outlet_temperature_mean
outlet_temperature_std
pressure_loss
max_wall_heat_flux
```

读取方式：

```python
from optimization.cfd_metrics import load_cfd_results

results = load_cfd_results("fluent_results.csv")
```

未知 CFD 指标应在 CSV 中留空，加载后保持为 `None`。未来可用 `candidate_id` 将 CFD 结果与 `variable_n_results.csv` 中的几何候选关联，再建立几何与物理性能联合优化。

## 测试

```bash
pytest
```

测试覆盖 baseline 数量与边界、最小距离、对称性、ring/sector 数量、确定性、非法可变 N 候选过滤，以及 CFD 空值读取。

项目通过 `pytest.ini` 将 pytest 临时文件固定在 `outputs/test_tmp/`，避免依赖 Windows 用户临时目录的访问权限。该目录仅包含测试期间生成的临时文件。

## Literature-inspired layouts

当前布局是可参数化的几何研究族。尚未为这些形式指定论文来源，也不声称它们已被文献证明最优。

TODO:

- Add literature reference for sector layout
- Add literature reference for staged/ring combustor layouts
- Add literature reference for nonuniform injector arrangements

只有在阅读并核对真实论文后，才应补充引用、适用工况和参数来源。
