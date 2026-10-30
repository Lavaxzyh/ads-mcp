# ADS 2025 原生 EM 工作流

本文原生 EM 证据来自历史版本 `0.4.0+ads2025.20261030.1`，批次 `ads2025-em-20261030.1`。当前分享版本及本次重测范围见 [验证摘要](docs/VALIDATION.zh-CN.md)，本次没有重跑整套 EM/GUI 矩阵。
上游：Lavaxzyh/ads-mcp v0.4.0，commit `72e2793e0ebe8022903608e84cdb900f17ae81c0`。本地扩展不是上游新版本。
本机实测：Keysight ADS 2025，610.shp Aug 20 2024，ADS Python 3.12.2，PySide2 5.15.10。
无官方 ADS 2027 `bin/ads-mcp.exe`；使用本地原生 Python/AEL 与专用会话桥接，不使用 Computer Use。

## 六组功能及实际覆盖

| 功能 | 本机真实证据 | 边界 |
|---|---|---|
| 独立版本、自检 | 实际 MCP 进程报告版本/ADS/Python/依赖/64 个注册工具；同一进程中改测试副本磁盘文件，识别旧代码；识别缺失客户端工具 | 客户端目录只有传入观察到的工具名才能比较；不重启其他服务 |
| 原生 Layout/PSN | 原生 `de_dse_s2l` 使用 MLIN artwork；PSN 实际几何随 W/L 变化；保存重开后几何、端口仍一致 | 不是任意器件自动布线；缺 artwork 会报告遗漏，不能据此猜封装/焊盘 |
| Lookalike/Momentum | 原生 lookalike 两端口，MCP 派发/状态/日志/31 点数据/真实网格 | 本机实测平面 Momentum microwave、两端口、50 Ω；不是 RFPro/FEM |
| 原生 EM 联合仿真 | 原生 emModel/MomCmpt，完整复数 S 对比；串 R=5 Ω、并 C=0.5 pF 与独立 ABCD 计算一致 | 当前自动测试台为两端口实数 50 Ω SP；非线性器件/HB 未接入此链路 |
| 参数化 EM 优化 | 新尺寸生成新 EM 样本；缓存复用；原生 Gradient；最佳尺寸独立副本最终频带复算通过 | 限定拓扑/板材/参数范围/目标；MaxIters 不是硬 EM 求解次数限制；实际仅验证单变量单目标 |
| GUI 管理 | 识别本任务专用进程身份；排列/最大化/恢复；真实网格显示；完成优化器释放且不回标 | 只能管理登记且身份匹配的专用会话；不接管任意已打开的用户窗口 |

## 可以直接给助手的请求

```txt
先运行 ADS MCP 自检，核对本地发布版本和实际可见工具。
用独立测试工程和本任务专用会话验证原生 Layout、PSN、lookalike、Momentum、EM 联合仿真。
复用现有 EM-WORKFLOW.zh-CN.md 与验证脚本，不重做接口探索。
原工程只读，不改变我确定的拓扑、板材、参考面或端口条件。
先列出缺失的必要条件，再开始依赖这些条件的设计；不用 Computer Use。
```

```txt
将我选定的无源匹配网络复制到独立工程，按确认的 W/L 范围做参数化 Momentum/SP 优化。
保留未缓存尺寸的实际求解证据、缓存复用证据与优化历史；不自动回标原图。
把最佳尺寸生成独立结果副本，按指定频带与频率点重新求解，并核对完整复数 S 参数。
不要把 SP 结果称为功放输出功率、效率或稳定性验证。
```

## 标准调用顺序

所有路径和测试条件都应由当前任务指定，不能把下列验证板材或尺寸作为新工程默认值。

1. `ads_runtime_selfcheck(client_tool_names=[实际可见工具名])`。检查 version、code_root、fresh_server_required、缺依赖和工具差异。
2. `ads_manage_gui_session(action="list")`，或 `ads_list_gui_sessions()`；复用同任务同工作区专用会话。没有时调用 `ads_manage_gui_session(action="start", task_directory="独立任务目录", workspace="独立工程目录")`。
3. 下列 GUI 操作都明确传 `session_id` 和 `workspace`。MCP 后台引擎不能同时打开该工作区；先保存并关闭本任务后台工作区。其他任务的工作区不得接管。
4. 对已保存、核对过器件/参数/连接的电路，调用 `ads_define_cell_parameters` 定义参数（无量纲 REAL，用 `W*1 mm` 等明确换算），再 `ads_generate_layout(parameterized=true)` 生成原生 PSN。固定电路可不启用 PSN。检查返回的 omitted artwork、单位、实际几何与端口。
5. `ads_generate_symbol(source_view="layout", style="lookalike")`，核对 symbol 端口与 layout 端口。符号生成不代表 EM 数据生成。
6. 以确认的 substrate 创建 `ads_configure_momentum_setup`，然后 `ads_configure_em_model`、`ads_configure_momentum_mesh`。该 Setup 生成器限定 50 Ω、底部地、单引脚端口、线性频率表；其他条件不能静默替换。若返回 requires_workspace_reload，保存并重开本任务工程后再用模型。
7. `ads_inspect_momentum_setup` 后调用 `ads_run_momentum`，保存 task_id；用 `ads_get_sim_status(task_id)` 直到 done 或明确 error。核对实际频率、复数数据、geometry_sha256、mesh-proof 和求解日志。
8. `ads_create_em_testbench` 生成新的 MomCmpt 测试台；`ads_run_simulation(backend="gui", design="lib:bench:schematic", session_id=..., workspace=...)`。按任务 ID 轮询；用 `ads_compare_sparameters` 比较同条件的原始 EM 数据与测试台所有 Sij。
9. 参数化优化用 `ads_create_em_optimization`，随后 `ads_run_simulation(..., mode="optimize")`。等待 done，保存 dataset、最终参数、EF/迭代记录及每个新 EM 样本；使用 `ads_release_optimizer(task_id=...)` 释放，选择不更新设计的原生路径。旧版本没有结果 SHA 的未释放任务不能走新版安全释放，需先核验结果。
10. `ads_create_em_candidate` 显式传全部选定参数并使用新 cell 名；它生成保留原始 subnetwork 依赖的独立 cell，不复制旧 EM 缓存。保留源 cell 不变，不能单独删除依赖。然后生成该候选的原生 Layout/lookalike/独立 Setup，以最终规定频率点重新求解。
11. 保存并关闭本任务已用完的设计窗口；确认求解器空闲后 `ads_manage_gui_session(action="stop", session_id=...)`。停止接口遇到设计窗口/未确认对话框或活动求解器会拒绝退出，不强制杀进程。

当前高级 API 示例（字段均为真实接口；示例是已验证微带线条件，不是功放设计默认值）：

```json
{
  "tool": "ads_create_em_optimization",
  "arguments": {
    "lib": "flow_lib", "cell": "line", "testbench": "my_new_em_optim",
    "start_ghz": 1.37, "stop_ghz": 1.43, "points": 3,
    "variables": [{"parameter": "width_mm", "initial": 0.8, "lower": 0.6, "upper": 1.8}],
    "parameters": {"length_mm": "5"},
    "goals": [{"row": 1, "column": 1, "comparison": "at_most", "bound_db": -35.0}],
    "max_iterations": 6,
    "session_id": "替换为本任务实际整数 PID", "workspace": "替换为独立工程绝对路径"
  }
}
```

不要直接把示例 session_id 字符串传给工具；取启动/发现返回的整数。控制器创建本身不运行求解器。

```json
{
  "tool": "ads_create_em_candidate",
  "arguments": {
    "lib": "flow_lib", "cell": "line", "candidate": "my_new_final",
    "parameters": {"width_mm": 1.3875160221786806, "length_mm": 5.0},
    "session_id": "替换为本任务实际整数 PID", "workspace": "替换为独立工程绝对路径"
  }
}
```

## 公开验证范围

历史 ADS 2025 独立案例覆盖 MLIN/PSN、lookalike、Momentum、原生 emModel、R/C 联合仿真、单变量 Gradient 优化及专用 GUI 会话管理。公开条件、误差和证据边界见 [验证摘要](docs/VALIDATION.zh-CN.md)。原生工作区、模型、日志、进程标识及本机部署记录不随源码分享。

## 失败处理与尚未覆盖

- 请求观察超时不能等同求解失败。保留原 task_id/request_id，先查询原任务/响应，禁止重发同一次派发。已实测超时后执行一次、相同请求不重复执行、签名不符拒绝。
- 跨客户端共享 solver-active 预留，其他客户端不能编辑/派发/关闭同一工程。客户端退出不等于求解器退出；错误预留保守保留，需核对原进程、原状态文件、结果后恢复，不能盲删锁。未知外部 GUI/手工改动并不受此协议锁控制，故仅用专用工程。
- 第一次 ADS 原生保存 Setup 会补默认字段并规范数值。当前先通过原生 Save，核对已存在有效设置保持，再建立求解指纹；求解期间配置变化仍拒收结果。失败尝试不计入成功验证。
- ADS 2025 的 Python PCell 改参可能留下旧几何，改用原生 AEL 提交并回读 bbox/端点；仅改参数文字不算 PSN 验收。
- 原生优化 FinalAnalysis 必须是带引号的分析名；SaveAllTrials 与 SaveAllIterations 不能同时开启。失败原始记录保留。
- 已修复固定候选缺 itemdef、GUI 环境误导入宿主 pydantic、SDK 保存后 GUI 仍标脏等兼容问题；未安装第三方包到 ADS 内置 Python。
- 自动测试台目前两端口实数 50 Ω、SP、Gradient 和 Sij dB 目标；多端口/差分/非 50 Ω、复杂 PDK/多层过孔/封装、HB 与有源 PA、宽带多目标多变量的规模性能、优化强制取消均未完成本机端到端验证。
- 本地具备某模型目录不代表已有授权可修改，更不代表模型/偏置/封装条件已选定。接入功放前确认：器件与模型版本/具体 cell、EM 与模型边界（避免封装/焊盘重复）、真实板材叠层和铜粗糙度、接地与过孔、输入输出及谐波参考面/端口、偏置、拓扑、优化变量范围、功率/增益/PAE/带宽目标与预算。模型与库由使用者自行提供，不包含在发布包中。

## 安装与版本检查

安装步骤和必要依赖见 README.zh-CN.md。通过 HPEESOF_DIR 指定 ADS，工作输出放在仓库外。当前服务已启动不等于已加载新代码；使用 ads_runtime_selfcheck 核对版本、code_root 和 fresh_server_required。使用者可在本会话空闲时重新连接，或启动独立专用客户端，不自动重启其他会话。
