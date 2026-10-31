# ADS 2025 射频工作流扩展

基础工具为 `ads_analyze_loadpull` 与 `ads_run_matching_sweep`，通过 MCP 标准工具调用。
现已扩展 `ads_build_loadpull_regions`、`ads_search_matching_network`、`ads_analyze_matching_tolerance`；频变目标、标称值/尺寸约束、缓存和容差的详细契约见同目录 `RF-ADVANCED.zh-CN.md`。
公开验证范围见 [验证摘要](docs/VALIDATION.zh-CN.md)，原始工程与运行记录不随源码发布。

如果桌面端现有连接尚未刷新工具列表，可使用 `rf_workflow_client.py`：

```powershell
& '<REPO>\.venv\Scripts\python.exe' -B `
  '<REPO>\rf_workflow_client.py' '请求文件.json' --ads-root '<ADS_INSTALL>'
```

请求文件包含 `tool` 与 `arguments`；工具名接受上述五个射频工作流工具。
入口仍通过标准 MCP 协议调用安装版服务，使用独立状态和临时 HOME，不复用当前桌面会话状态。
扫描会自动等待完成，失败返回非零退出码。运行日志路径随结果返回。

## Load-pull 全点分析

自然语言示例：

> 使用 ads-mcp，分析这几份 Load-pull 数据。保留全部点，按 Pout 不低于指定值、PAE 不低于指定值筛选，输出 CSV 和统计。原数据只读。

输入 `datasets` 为一个或多个 `.ds` 的绝对路径。`output_dir` 必须是不存在的新目录，且不能位于输入数据所属 ADS 工作区内；省略时在 `ADS_MCP_WORKROOT` 创建唯一目录。

`limits` 可包含 `min_pout_dbm`、`min_pae_pct`、`min_gt_db`。没有提供的门限不参与筛选；零合格点是有效的分析结果，不会偷偷放宽指标。异常点仍保留在 `all_points.csv`，并保留 `numeric_valid`、`issue` 和 `hb_convergence_verified`。

输出包含全部点、合格点、每个输入文件的 CSV、来源 SHA256 和 manifest。
开启 `compare_common` 后额外输出 `common_loads.csv`，按精确 R/X 坐标取交集，计算各数据集最差 Pout/PAE/Gt，并优先按满足门限、最差 PAE、最差 Pout 排序。没有插值、自动去重或连续空间全局最优保证。

跨文件比较要求 `reference_plane` 和 `conditions`，记录器件模型、源端和谐波端接等条件；这些文字是调用者提供的条件，不是程序独立验证结论。程序另行检查文件中的 Pavs、VDD、VGG、Z0 和谐波阶数是否一致，不会混合不同驱动或偏置条件。

当前模板范围：原有 `HB1Tone_LoadPull` 坐标与探针约定，包括 `imag_indexs11/real_indexs11`、`P_Load.p`、`V_In/I_In.i`、`Vs_high/Is_high.i`、`Vs_low/Is_low.i`。不支持任意第三方 tuner、改名探针或固定负载功率扫描。数值有效不代表逐点 HB 收敛、稳定性或器件安全性已验证。

`loadpull_exporter.py` 实现模板约定的只读全点导出。运行时 CSV 会记录源文件本地路径；分享结果前须另行脱敏，源码包不包含用户数据。

## 匹配网络批量扫描

自然语言示例：

> 使用 ads-mcp，对测试工程导出的两端口 SP 网表扫描 L1 的电感和 C1 的电容。说明器件参考面、另一端实际终端负载、目标阻抗和评价频带，按带内最大阻抗误差排序。输出每组结果，不把参数写回原工程。

输入为已导出的 ADS 文本网表 `netlist_path`。可以使用 `ads_generate_netlist` 的结果；对已有原工程应先在独立副本中准备测试台。扫描只修改新目录中的网表副本，不打开、保存或改写原理图，不自动把最佳参数应用回工程。

扫描参数可以选择顶层变量，或者明确的实例和参数。`scope` 可指定网表内的子电路定义名，默认顶层。变量必须有单独的 `name=value` 赋值行；实例参数必须显式存在，重复或歧义目标会被拒绝。

```json
{
  "parameters": [
    {"variable": "Lm", "values": [1.6, 2.0, 3.0], "unit": "nH"},
    {"instance": "C1", "parameter": "C", "values": [1.0, 1.6, 2.0], "unit": "pF"}
  ],
  "evaluation": {
    "start_hz": 1800000000,
    "stop_hz": 2200000000,
    "reference_ohms": 50,
    "load_r_ohm": 50,
    "load_x_ohm": 0,
    "target_r_ohm": 25,
    "target_x_ohm": 0,
    "input_port": 1,
    "reference_plane": "独立 LC 测试网络的端口 1，端口 2 接 50 Ohm",
    "max_error_ohm": 6
  }
}
```

这些数值仅是独立测试示例，不是用户功放的设计目标。变量的 `unit` 表示写入赋值的 ADS 单位；如果该变量在器件参数表达式中已经乘了单位，应选择空单位，避免重复缩放。

每个组合单独串行仿真。默认最多 64 组，可显式提高到 256 组；每组默认超时 120 秒，范围 1–600 秒。默认遇错即停，`stop_on_error=false` 可保留失败记录并继续。结果中的失败和未执行组合不会标成成功。

工具立即返回 `task_id`，使用 `ads_get_sim_status` 查询进度。每组保留网表、仿真日志、原始 `.ds`、完整 `.s2p`、全频点 CSV；总目录包含 `request.json`、`cases.json`、`ranking.csv` 和 `summary.json`。

评价使用另一端实际复负载终止后的输入阻抗：

```text
GammaL = (ZL-Z0)/(ZL+Z0)
GammaIn = S11 + S12*S21*GammaL/(1-S22*GammaL)
Zin = Z0*(1+GammaIn)/(1-GammaIn)
主评分 = 评价频带内 max(|Zin-Ztarget|)，次评分为 RMS 误差
```

`input_port=2` 时先交换端口方向。目标阻抗目前为常数复阻抗；不做频率插值。`max_error_ohm` 省略时只排名，不给出工程合格结论。S11/S21 辅助指标是在参考阻抗匹配终止下的指标，不能当成复负载条件下的换能增益。

当前范围为单一频率轴、两端口线性 SP；两端口 Z 必须显式且相同，为正实数，并与 `reference_ohms` 一致。支持 LC、微带等已有 SP 模型的显式参数；不提供 HB、原生优化器、GUI、自动布局或全局优化。外部模型数据需要显式指定 `data_directory` 并另行验证模型。

当前后台沿用持久 RF 队列；恢复与停止规则见 RF-JOBS.zh-CN.md。保留原任务 ID，超时不自动重派。

## 首批基础功能验证记录

- 原有与新增单元检查 119 项通过。
- 独立 ADS 工程中 LC 3×3 共 9 组、微带线 3 组真实 SP 扫描通过。
- LC 的全部复数 S 参数和输入阻抗与独立解析公式核对；排名与独立计算一致。
- 微带参数未变的组合与保存原理图通过原有 MCP 运行得到的结果一致。
- 三份真实 Load-pull 数据副本，共 867 个已保存点；核对 PAE、阻抗、门限和共同负载统计。
- 覆盖已存在输出目录、重复输入、缺失比较条件、运行中重复启动、超出扫描上限等拒绝路径。
- 扫描源网表、测试原理图和原始 Load-pull 数据保持不变。
