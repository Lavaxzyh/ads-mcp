# 频变阻抗目标、预算搜索与容差分析

新增的多指标约束、HB 复核与持久化队列见 [RF-JOBS.zh-CN.md](RF-JOBS.zh-CN.md)。本页扫描、搜索和容差接口仍可调用，后台执行已进入持久化队列；原有客户端保留完整结果，新文件请求入口默认返回简要结果。

这组工具处理保存好的双端口线性 SP 网表和已导出的 Load-pull 数据。输入工程保持不变；网表、每组参数、仿真数据与评分保存在独立结果目录中。它们不把候选参数自动写回原理图。

## 直接怎样说

可以直接给助手这些任务，附上对应工程/网表或分析结果路径：

> 用这些频点的 Load-pull 数据生成满足 Pout、PAE 门限的阻抗区域，检查匹配网络的阻抗轨迹。参考面是……，另一端接……；插值区域要与实际仿真采样点分开说明。

> 在这份 SP 网表上优化 Lm 和 C1.C，L 用 E12、C 用 E24，最多评估 32 组。先粗扫，再局部细扫，允许复用校验通过的缓存。用独立输出目录，不写回原工程。

> 对候选网络做容差分析：L、C 各 ±5%，线宽 ±0.05 mm，板材 Er ±2%。先报告单参数敏感度，再做指定次数的蒙特卡洛；把失败样本和未通过目标的样本计入统计。

必须先确认实际参考面、端口朝向、另一端负载、频率、单位和网络边界。Load-pull 的器件模型、偏置、驱动、源端和谐波端接条件也需要一致。参考面文字相同只能防止误用标签，不能证明物理参考面已经校准或去嵌。

## 工具和输出

| 工具 | 输入与作用 | 主要输出 |
|---|---|---|
| `ads_build_loadpull_regions` | 从新版 `ads_analyze_loadpull` 的 manifest、全量 CSV 和门限生成分频阻抗区域；不启动仿真 | `regions.json`、每频点采样数、合格数、三角形剔除数 |
| `ads_run_matching_sweep` | 原有扫描工具新增 `target_table`、`region_file`，并支持 `model+parameter` | 每组 `.ds`、`.s2p`、原始复数 S 参数、逐频评分、排名 |
| `ads_search_matching_network` | 有预算的粗扫和局部细扫，支持标称值、尺寸步长、缓存 | `cases.json`、`ranking.csv`、`summary.json`、每组仿真文件 |
| `ads_analyze_matching_tolerance` | 标称值与正负单参数扰动，再选全角点或蒙特卡洛 | `sensitivity.csv`、`tolerance_statistics.json`、全部仿真文件 |

后三项返回 `task_id`，用 `ads_get_sim_status` 查看进度和终态。`partially_completed` 或任务 `error` 表示有失败/未完成项，不能按全部通过解释。

## 频变目标与区域的准确含义

`MatchingEvaluation` 的三个目标入口互斥：

1. `target_r_ohm` 和 `target_x_ohm`：原有固定复阻抗目标。
2. `target_table`：按频率给出的复阻抗目标，列名为 `freq_hz`、`r_ohm`、`x_ohm`。默认 `target_interpolation="exact"`，只评价表中且位于指定频带内的频率。SP 数据必须有对应频点，容许 1 Hz 的数值误差。显式指定 `"linear"` 后，才在 R/X 两个分量上做线性频率插值；目标表必须覆盖频带，禁止外推。
3. `region_file`：生成的 Load-pull 区域文件，只评价有 Load-pull 数据的对应频率；不在不同频率的区域之间插值。任务启动时冻结一份区域文件，运行中的源文件变化不会混进同一批评分。

目标表写法，例如：

```json
{
  "start_hz": 2200000000, "stop_hz": 2600000000,
  "reference_ohms": 50, "load_r_ohm": 50, "load_x_ohm": 0,
  "input_port": 1, "reference_plane": "请填写实际输入参考面",
  "target_table": [
    {"freq_hz": 2200000000, "r_ohm": 8, "x_ohm": 4},
    {"freq_hz": 2400000000, "r_ohm": 7, "x_ohm": 5},
    {"freq_hz": 2600000000, "r_ohm": 6, "x_ohm": 6}
  ],
  "target_interpolation": "exact", "max_error_ohm": 2
}
```

上面的阻抗仅用于说明格式，不是用户功放的设计结论。区域模式删除目标表，改为 `"region_file": "实际 regions.json 的绝对路径"`。

区域默认 `mode="samples"`：合格的实际 ADS 仿真采样点构成离散集合，不能把相邻点间的空白自动视作合格。`mode="triangulated"` 才在 R/X 平面进行局部三角剖分，然后按全部 Pout/PAE/Gt 门限裁剪。插值直接作用于导出列的 dBm、百分比、dB 数值。含无效顶点的三角形被剔除；过长的边也被剔除，默认最大边长为该频点采样坐标的中位最近邻距离乘 2.5，可用 `max_edge_ohm` 明确指定。被剔除区域和未采样空洞不构成已验证的可用区域。

逐频 `target_evidence` 区分：

- `stored_sample`：命中合格的原始 ADS 仿真采样点，几何数值容差为 1e-9 Ω；不代表台架实测。
- `interpolated_region`：位于插值形成的合格多边形内。
- `outside_region`：区域外，同时输出到合格集合的最短欧氏距离。
- `supplied_target` / `interpolated_target`：直接提供的目标 / 显式频率插值得到的目标。
- `unscored`：该频点没有参与当前目标评分。

`nearest_stored_sample_ohm` 同时保留与实际合格采样点的距离。排名按已评价频点的最大距离，再按 RMS 距离；区域内距离为零。没有显式 `max_error_ohm` 时，区域模式以全部所评价频点落入集合为通过条件；固定/频变点目标没有门限则不宣称通过。设置非零区域距离门限允许区域外的近邻点通过该距离条件，`inside_all_regions` 仍独立报告是否真正落入集合。

## 搜索的边界与缓存

每个搜索参数指定 `minimum`、`maximum` 和单位，可再任选一种约束：

- `allowed_values`：用户给定的可用标称值列表。
- `e_series="E12"` 或 `"E24"`：所选数值单位下的标准标称值系列。
- `step`：以 `minimum` 为起点的可加工步长。例如线宽 1.4～2.0 mm，步长 0.1 mm。

变量用 `variable`，元件/微带线用 `instance+parameter`，基板模型用 `model+parameter`；子电路加 `scope`。例如：

```json
[
  {"variable":"Lm", "unit":"nH", "minimum":1.5, "maximum":2.7, "e_series":"E12"},
  {"instance":"C1", "parameter":"C", "unit":"pF", "minimum":1, "maximum":2.2, "e_series":"E24"}
]
```

设置例如 `{"budget":32,"coarse_points":3,"fine_points":5,"refine_top":2,"seed":0}`。默认一半预算用于粗扫；组合过多时使用有限的分层抽样，随后在最好的若干粗扫结果附近细化。最多 8 个参数、256 次评估。预算包含缓存命中，因此真实 ADS 仿真尝试数不会超过预算。不会为了用完预算而重复同一搜索组合，不保证全局最优，也不自动检查 PCB 净距或元件封装可采购性。

缓存保存原始复数 S 参数及 `.ds`，目标变化时重新计算评分。命中前检查文件散列；网表、核心求解器、安装文件清单或用户配置变化会使缓存失效。缓存文件损坏则重新仿真。当前仅对可识别、无外部数据依赖的基本 R/L/C、传输线和 MSUB 等受支持网表启用缓存；检测到 `data_directory`、文件模型或未识别指令时自动关闭缓存并报告原因，仿真本身仍可执行。`use_cache=false` 可强制重新运行。默认缓存位于工作输出根目录的 `matching_cache_v1`；也可指定外部独立 `cache_dir`，不能放进输入 ADS 工程。

## 容差与敏感度

每项必须给出 `nominal`，以及 `relative_pct` 或 `absolute` 二者之一。`absolute` 使用该参数的 `unit`。可选 `minimum`/`maximum` 限制物理范围；越界会在运行前报错，不截断，也不把实际扰动重新舍入到 E 系列。

```json
[
  {"variable":"Lm", "unit":"nH", "nominal":2, "relative_pct":5, "minimum":0},
  {"instance":"TL1", "parameter":"W", "unit":"mm", "nominal":1.8, "absolute":0.05, "minimum":0},
  {"model":"MSub1", "parameter":"Er", "nominal":3.48, "relative_pct":2, "minimum":1},
  {"model":"MSub1", "parameter":"H", "unit":"mm", "nominal":0.8, "absolute":0.02, "minimum":0}
]
```

这些参数必须实际存在于同一网表中；示例不是要求把不同测试电路混在一起。

- `method="oat"`：标称值 + 每个参数的正负扰动，合计 `1+2N` 组。
- `method="corners"`：再计算全部 `2^N` 个角点；总数必须符合预算。角点不是实际生产分布，也不能保证非单调网络在区间内部没有更坏点。
- `method="monte_carlo"`：再计算 `samples` 组；种子 `seed` 可复现。`distribution="uniform"` 为容差区间内独立均匀分布；`"truncated_normal"` 为标准差等于容差/3、以正负容差为边界的截断正态分布。暂不支持参数相关性。

敏感度主排名依据规定容差下最大 `|Z扰动−Z标称|`，同时输出复阻抗对参数的局部中心差分和目标分数差分。这样不会把最优点附近接近零的分数导数误解成“不敏感”。改变给定容差范围可能改变排名；这不是全局敏感度指数。

合格比例的分母包含全部计划试验样本，失败/未执行样本不算通过，另外明确报告其数量；分位数只基于成功仿真的样本。没有通过门限则不输出合格比例。结果描述给定假设下的 SP 阻抗鲁棒性，不能直接当作量产良率、PAE 良率或大信号稳定性结论。

## 当前对话未刷新工具列表时

已安装的 `rf_workflow_client.py` 支持以上全部工具，使用独立 HOME 和状态文件启动临时 MCP 服务，并自动轮询后台任务。请求文件格式为 `{"tool":"ads_search_matching_network","arguments":{...}}`。例如由助手执行：

```powershell
& '<REPO>\.venv\Scripts\python.exe' -B '<REPO>\rf_workflow_client.py' '实际请求.json的绝对路径' --ads-root '<ADS_INSTALL>'
```

无需为一次调用停止其他 ADS 窗口或改动原工程。真实测试使用 ADS 原生运行库；在受限沙箱中初始化不成功时，运行环境可能要求工具级授权。
