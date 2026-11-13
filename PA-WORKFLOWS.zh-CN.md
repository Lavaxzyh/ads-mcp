# ADS 2025 功放与匹配工作流

本页适用于包含这些入口的本地开发版本。先用 `ads_runtime_selfcheck` 和 MCP 工具目录确认客户端加载的版本。以下路径和电气量均为调用示例，工程值取自当前设计与台架。

## 两个客户端分做输入、输出匹配

1. 在一份独立整机副本上，用 `ads_design_workflow(action="run")` 的 `import` 或 `build` 绑定一张原理图。先保存 GUI 中拟采用的基线。请求文件使用 UTF-8 JSON。专用会话可复用已绑定的任务目录，也可用内联 `request` 传入单原理图操作。
2. 在整机任务执行 `fork_matching`，请求为 `{"batch":"match01"}`。将返回的 `tasks.input` 交给输入端客户端，`tasks.output` 交给输出端客户端。每个客户端使用自己的任务目录、ADS HOME、会话状态和工作区。各自在分配的副本上改动目标原理图，保存后再合并。
3. 回到整机任务执行 `plan_merge`，请求为 `{"batch":"match01"}`。两边都改了一个共享变量时，返回的 `conflicts` 给出变量名和两侧值，`choices` 列出各组变更。例如保留输入侧的共享变量、只接收输出侧的一项，可传 `{"batch":"match01","selected":{"output":["variable.VAR1.L_TL2"]}}`。省略某一侧表示选择该侧全部，空列表表示该侧全不选。每侧器件与布线的结构改动作为 `topology` 整组选择。
4. 审查选定改动，使用返回的 `next_call` 在整机任务执行一次 `run`。它验证基线、两份副本的指纹、修订号和审核文件，保存后重开核对器件、参数、引脚电气连接和布线。相同请求可重放。改动涉及其他 cell、共享库文件或未能读取的原生字段时，返回具体差异供检查。
5. 需要与结果对照时，用 `compare` 传入左右快照路径和各自 SHA-256。查看 `schematic_diff.parameters/devices/connections/ports/wires`。端口列出编号、阻抗、已声明的参考面和相连引脚。快照存在读取缺口时，回执保留缺口和已有的结构比较结果。

### 备份和仿真前检查

- 每批 `run/edit` 或 `run/merge` 在写入前创建磁盘检查点。第一份保存工作区原生文件基线，后续保存变化的内容与删除清单，相同内容按 SHA-256 复用。备份记录保存在任务目录 `backups/`，操作回执的 `checkpoint` 可用于恢复。工作区外的模型、GUI 未保存的编辑和仿真器临时锁文件均在备份范围以外。
- 在同一任务调用 `plan_restore`，传入 `{"checkpoint":"<检查点摘要>"}`。检查 `changes.restore/overwrite/remove` 后再调用 `restore`，传入返回的 `review` 和 `review_sha256`。恢复过程验证文件与原生图，预览后又发生的更改会使原预览失效。
- 用 `check_connections` 传入完整预期引脚分组，可用 `bind:true` 绑定到任务。导入的工程从设计意图提供分组。按结构计划建图的任务可沿用 `expected_nets`。绑定后，仿真入口会先检查已保存的原理图，包括缓存命中的路径。返回的异常给出类型、实例引脚、坐标和原生网络。每个引脚须在预期网络或允许悬空的列表中出现一次。

```json
{
  "bind": true,
  "expected": {
    "expected_nets": [
      {"name":"input","pins":["Term1:1","R1:1"]},
      {"name":"output","pins":["R1:2","Term2:1"]},
      {"name":"ground","pins":["Term1:2","Term2:2","G1:1","G2:1"]}
    ],
    "open_pins":[]
  }
}
```

## Load-pull、匹配和容差

已保存数据的分析使用 `ads_review_rf(action=..., request_file=...)`。在 `workflow` 配置下，`common_load` 默认返回合格点数、最优候选和限制频点，`em_comparison` 返回各 S 参数的最大复数差以及两个带边和中心频点的差值。回执中的 `report` 指向磁盘上的完整结果。显式指定 `detail="full"` 可让本次调用返回完整结果，其他配置也可按次选择 `detail="summary"`。

需要新求解时使用 `ads_submit_rf_job(workflow=..., request_file=...)`，随后用 `ads_rf_job(action="wait", job_id=...)` 等待同一作业。每次计算使用新的 `output_dir`。`workflow` 和 `rf` 配置提供这三个入口，`em` 配置也提供 `ads_review_rf`。

| 目的 | 操作与必要输入 | 输出及适用条件 |
|---|---|---|
| 10 多频公共负载 | `common_load` 读取 `analysis_manifest`。在 `limits` 设定 `min_pout_dbm`、`min_pae_pct`、`min_gt_db` 中需要的门限。 | `common_loads.csv` 按跨频点、跨指标的最小余量排序。只取各频点实际共有的 R/X。核对 `status`、`qualified` 和 `best[0].meets_all_limits`。无合格解时，`best` 如有记录，表示最接近门限的失败点。 |
| 11 Smith 图 | `smith_overlay` 传入保存的 SP `case_directory`、`region_file`，可选逐频 `targets`，每个目标指定 `freq_hz/r_ohm/x_ohm`。 | 图片和逐点 CSV 同时标出实际网络阻抗、目标及合格区域。最大偏离用同频复阻抗差的模度量。点目标偏差和距区域的距离分别输出。 |
| 12 阻抗约定 | 在 `evaluation` 明确 `input_port`、`reference_plane`、`reference_ohms`、`load_r_ohm/load_x_ohm`，端接加在另一端口。观察方向为 `into_network_from_selected_port`。 | `presented_impedance` 使用给定目标；`conjugate_of_device_impedance` 对给定器件阻抗取一次共轭。Load-pull 区域使用自身坐标。搜索、`plan_candidate`、`materialize` 和结果报告传递同一约定，可选 `verify_same_conditions` 复算完整复数 S。 |
| 13 拓扑比较 | 作业 `topologies` 传入共享 `evaluation`、`manufacturing`、`simulation_budget` 和 `alternatives`。每个候选给出名称、拓扑、网表与布局尺寸。不同拓扑使用相同候选数。 | `comparison.csv` 和 `cases.json` 给出采样合格带宽、匹配参考端口下的插损、声明面积和逐案例数据。尺寸来自输入的布局依据，制造约束按声明值核对。 |
| 14 实际寄生 | 作业 `parasitics` 传入 `netlist_path`、`evaluation` 及 `replacements`。例如 `{"instance":"C1","esr_ohm":0.15,"esl_nh":0.35}`。选厂商模型时给出 `subcircuit_file` 和 `subcircuit`，模型须是自包含两引脚 ADS 网表，可明示 `reverse_pins`。 | 在相同频率与端接下比较理想和替换后电路。谐振判据为所选端口的输入电抗过零。两边各有一个可定位零点时给出频移，同时报告带边插损和采样通带变化。 |
| 15 相关容差 | 在 `tolerance` 作业的参数中给出同名 `correlation_group` 和 `correlation_sign`（1 或 -1）。各参数的 `nominal` 和 `absolute` 或 `relative_pct` 单独填写。 | 同组参数共用一个扰动量，未分组参数独立。支持 OAT、全角点和带种子的 Monte Carlo。`sensitivity.csv` 给出敏感度，`factor_limit_violations` 给出单因素越限工况、频点和未满足的指标。 |

公共负载请求示例。默认余量尺度为 1 dB 或 1 个百分点。可用 `margin_scales` 明示每个启用指标的尺度，以便有意义地比较不同单位。

```json
{
  "analysis_manifest":"C:/rf-task/loadpull/manifest.json",
  "limits":{"min_pout_dbm":30,"min_pae_pct":40,"min_gt_db":10},
  "margin_scales":{"Pout_dBm":1,"PAE_pct":2,"Gt_dB":1},
  "output_dir":"C:/rf-task/common-load"
}
```

拓扑候选的 `layout` 包括 `length_mm/width_mm/minimum_trace_mm/minimum_gap_mm/source`。共享的 `manufacturing` 包括 `board/minimum_trace_mm/minimum_gap_mm/maximum_length_mm/maximum_width_mm`。`evaluation.max_error_ohm` 定义阻抗合格门限。在相同采样网格上比较连续合格采样段。输出保留完整的电气轨迹，便于另行复核复杂端接下的功率传递。

## 有源台架与 EM

| 目的 | 操作 | 验证和输出 |
|---|---|---|
| 16 功率扫描 | `hb_power_sweep` 传入已有 HB `job_directory`、`backoff_db`，可用 `reference_pout_dbm` 指定参考输出。 | 每个频率、偏置、方案单独列增益压缩、输入/输出 P1dB、采样峰值 PAE 和指定回退处的 PAE、漏极/供电效率。默认以最大已接受采样输出为回退基准。低驱动前两点增益相差不超过 0.2 dB 才建立压缩基准。插值列出相邻案例号，缺点、超范围或回退多解保留状态。 |
| 17 工作点诊断 | 读 HB 作业的 `operating_points.json` 或上述分析的 `diagnostics.json`。 | 对每个请求的频率、驱动和偏置保留结果。区分未运行、求解未收敛、探针或功率检查失败。失败项给出原生日志、可用行号、探针和后续检查。 |
| 18 四种匹配组合 | 作业 `hb_matching` 使用现有单音 HB 模板、`contract`、`operating_points`；`input_network` 和 `output_network` 各含 `reference_plane` 和一组 `{target,value}` 参数，也可指定相同有序引脚的匹配子电路替换。 | 在相同器件、偏置、驱动、负载及谐波条件下分别求解原始、仅输入、仅输出、两侧均改四种方案。`matching_comparison.csv` 直接列出每点增益、Pout、PAE 与相对基线变化。 |
| 19 Doherty 支路 | `doherty` 读 `source`（原生 `.ds` 需 `varblock`，也支持导出 CSV），指定 `frequency_hz/drive_dbm/harmonic_order` 的列名。可按 `select/group_by` 区分工况。 | `carrier/peak` 分别指定 `plane`、`voltage_v/current_a` 列及 `current_direction`。原生复数列填 `{"real":"列名"}`，CSV 实虚部分列则同时给出 `real/imag`。电流统一朝合路器，基波 V/I 输出有效负载。低于 `min_current_a` 时抑制比值与相位；用 `turn_on_current_a` 报告峰值支路首次越过指定电流的采样区间。还须声明 `phasor_scaling` 为 `peak` 或 `rms`。 |
| 20 小信号稳定性 | `stability` 的 `before` 和可选 `after` 各指定 `file/planes/reference_ohms/bias`。支持二端口 Touchstone、ADS 数据集（加 `varblock`）或保存的 SP 矩阵。填写 `start_hz/stop_hz`。 | 给出 K、&#124;Δ&#124;、μ、μ′ 和各指标最差频点。比较按相同偏置、端口顺序和参考面；可用有序的 `frequencies_hz` 明示包括两端的共有实测点，逐点选取。精确单向网络按两端反射判断。结论限于所给偏置与已采样频段。 |
| 21 电路与 Momentum | `em_comparison` 的 `circuit/momentum` 各提交二端口结果、`geometry`、`substrate` 和 `model_source`。再提供 `features`、`momentum_log`，以及三个 `frequencies_hz`（下带边、中心、上带边）。 | 核对两侧板材、端口、Z₀、几何声明和实际频率网格，读取完结的 Momentum 求解日志，输出四个复数 S 的全频对照 CSV、三处指定频点和每个参数最大差异。几何及参考面的物理对应关系需由案例给出。 |

EM 板材字段为 `stackup/er/height_mm/copper_um/conductivity_s_per_m/loss_tangent`。两侧的 `planes` 按 S 矩阵端口顺序填入。`features` 取 `bend`、`tee`、`open_stub`。复数原值、差值模、幅度差 dB 和相位差共同保留，近零响应优先比较复数差值。

ADS 2025 的五项原生回归可在配置好 MCP 依赖的 ADS Python 环境执行。输出目录须为新目录，运行结果见 `report.json`。包含工作区保护、引脚、解析电阻 SP、匹配变量修改，以及从本机 ADS 安装目录的 ClassC 示例提取的单音 HB 工况。

```text
python -B tools/run_ads_regression.py --ads-root "<ADS 2025 安装根目录>" --output "<新验收目录>"
```
