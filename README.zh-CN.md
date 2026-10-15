# ads-mcp

![Clones](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/Lavaxzyh/ec682cff6f482626fa5348189838e66b/raw/clones.json) ![Views](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/Lavaxzyh/ec682cff6f482626fa5348189838e66b/raw/views.json) ![Installs](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/Lavaxzyh/ec682cff6f482626fa5348189838e66b/raw/installs.json)

**面向 Keysight ADS 的开源 MCP Server**，提供射频与微波电路的原理图搭建、电路仿真、Momentum 仿真及 S 参数处理工具。

[English](README.md) · 简体中文

![Python](https://img.shields.io/badge/Python-3.14_(ADS自带)-3776AB?logo=python&logoColor=white)
![MCP](https://img.shields.io/badge/Protocol-MCP_stdio-8A2BE2)
![ADS](https://img.shields.io/badge/Tested_on_Keysight_ADS_2027-red)
![License](https://img.shields.io/badge/License-MIT-green)
![Tools](https://img.shields.io/badge/工具数-28-4c1)

> 本项目为第三方开源项目，非 Keysight 官方产品。使用前需自行安装 Keysight ADS 并具备有效许可证。本仓库不分发 ADS 软件及其专有组件。

## 项目概述

**ads-mcp v0.3.0** 将 ADS 中的设计与仿真操作封装为 **28 个 MCP 工具**，为每个工具提供明确的参数定义，并记录会话与仿真任务状态。

电路仿真流程覆盖原理图搭建、网表生成、`hpeesofsim` 求解和 S 参数处理。Momentum 流程通过指定的 ADS GUI 会话运行**已有且已保存的原生 EM Setup**，监控求解进度，检查新生成的数据集及其频率网格。

Momentum 功能调用本机 ADS 安装目录中的 `bin/ads-mcp.exe`。使用前需在 ADS 中准备并保存版图、衬底、端口和原生 EM Setup，详见 [Momentum 工作流与支持范围](docs/momentum.md)。

## 主要功能

- **原理图搭建**：创建工作区与设计库，放置元件、变量、端口、接地符号和导线。
- **参数检查**：自动命名微带衬底，保护字符串引用参数，并通过 `ads_browse_components` 查询元件及参数名称。
- **异步电路仿真**：提交仿真后返回 `task_id`，通过 `ads_get_sim_status` 查询状态和数据集路径。
- **原生 Momentum 仿真**：检查已保存的 EM Setup、发现 ADS GUI 会话、启动仿真，并监控频点进度和新结果。
- **任务状态持久化**：将会话与任务记录保存为 JSON。Momentum 任务可在 MCP 服务重启后继续监控；进程内电路任务中断后会标记为错误。
- **结果处理与导出**：读取 S 参数摘要和预览，导出双端口 Touchstone `.s2p` 文件及 PNG 曲线。
- **滤波器示例**：提供基于 g 参数的 Butterworth、Chebyshev LC 梯形滤波器综合与搭建示例，覆盖低通、高通、带通和带阻响应。
- **原理图审计**：检测元件、文字与导线重叠及悬空线端点，并支持文字标注自动避让。

## 快速开始

本项目已在 Windows 与 Keysight ADS 2027（v6.5.0）环境中验证，其他 ADS 版本尚未验证。请使用有效的 ADS 许可证和 ADS 自带的 Python 3.14；所用 `keysight.ads.de` 原生引擎需要匹配 cp314。

以下命令以默认安装路径为例，请根据本机 ADS 安装位置调整路径。

```bat
git clone https://github.com/Lavaxzyh/ads-mcp.git
cd ads-mcp

:: 使用 ADS 自带的 Python 创建虚拟环境，并复用其 Python 包
"C:\Program Files\Keysight\ADS2027\tools\python\python.exe" -m venv --system-site-packages .venv
.venv\Scripts\python.exe -m pip install "mcp<2" matplotlib pandas
```

### MCP 客户端配置

```json
{
  "mcpServers": {
    "ads-mcp": {
      "command": "<仓库路径>\\.venv\\Scripts\\python.exe",
      "args": ["<仓库路径>\\mcp_server.py"],
      "env": { "HPEESOF_DIR": "C:\\Program Files\\Keysight\\ADS2027" }
    }
  }
}
```

将配置中的仓库路径和 ADS 安装路径替换为本机绝对路径，保存后重启 MCP 客户端（如 Claude Desktop、ZCode 或 Cursor），即可加载 28 个 `ads_*` 工具。

### 路径配置

| 配置项 | 用途 | 未配置时 |
|---|---|---|
| `HPEESOF_DIR` | ADS 安装目录，电路工具与 Momentum 桥接共用 | 系统 `ProgramFiles` 下的 `Keysight/ADS2027` |
| `ADS_MCP_WORKROOT` | 示例输出根目录，以及省略 `ads_create_workspace.path` 时使用的根目录 | 当前用户主目录下的 `ads_mcp_demo` |
| `ADS_MCP_STATE_FILE` | MCP 会话与任务状态 JSON 文件 | 仓库中的 `state/session_state.json` |

可在 MCP 客户端的 `env` 中设置这些变量。直接运行脚本时，也可在 PowerShell 中先设置：

```powershell
$env:HPEESOF_DIR = "C:\Program Files\Keysight\ADS2027"
$env:ADS_MCP_WORKROOT = Join-Path $HOME "ads_mcp_demo"
```

请按实际安装位置调整 `HPEESOF_DIR`。显式传入的工作区或输出路径优先于默认路径；Momentum 始终使用调用时指定的已有工程。

### 运行示例脚本

也可直接运行示例脚本，无需配置 AI 客户端：

```bat
.venv\Scripts\python.exe demo\step_by_step.py
```

该脚本分 15 步演示滤波器搭建、仿真和结果导出，并在 ADS GUI 中打开生成的工程。输出位于 `ADS_MCP_WORKROOT`（默认当前用户主目录下的 `ads_mcp_demo`）。运行前请检查脚本中的 `WS`、`OUT` 子目录；脚本会删除并重建这两个示例目录。

## 工具列表（28 个）

### 环境与会话（5 个）

| 工具 | 功能 |
|---|---|
| `ads_check_installed` | 检查 ADS 安装、`hpeesofsim.exe` 和 ADS DE 引擎，建议在会话开始时调用。 |
| `ads_create_workspace` | 在指定路径创建并打开工作区；若工作区已存在，则直接打开。 |
| `ads_open_workspace` | 打开已有工作区。 |
| `ads_close_workspace` | 关闭当前工作区。 |
| `ads_session_status` | 查询会话状态、当前工作区、当前原理图及历史仿真任务。 |

### 原理图搭建（9 个）

| 工具 | 功能 |
|---|---|
| `ads_create_library` | 在工作区内创建设计库并挂载。 |
| `ads_create_schematic` | 创建或打开原理图 `库:单元`，并将其设为后续操作的当前设计。 |
| `ads_browse_components` | 查询元件库、元件及参数名称。微带元件位于 `ads_tlines`，控制器和端口位于 `ads_simulation`，VAR 位于 `ads_datacmps`。 |
| `ads_place_component` | 按坐标放置元件，可指定名称、旋转角度和参数。MSUB 自动命名为 `MSub1`；`Subst` 等字符串引用参数不接受直接赋值，以保护网表引用格式。 |
| `ads_add_wire` | 添加折线连线，可带网络标号。 |
| `ads_add_var` | 在 VAR 块定义原理图变量，元件参数可按名引用。 |
| `ads_add_term` | 放置 50 Ω S 参数端口及接地符号，并根据名称设置端口编号。 |
| `ads_add_ground` | 放置地符号。 |
| `ads_list_instances` | 列出当前原理图中的全部实例。 |

### 仿真（3 个）

| 工具 | 功能 |
|---|---|
| `ads_generate_netlist` | 保存原理图并生成网表，返回网表文本供检查。 |
| `ads_run_simulation` | 保存原理图、生成网表，在后台运行 `hpeesofsim`，并返回 `task_id`。 |
| `ads_get_sim_status` | 查询电路或 Momentum 仿真任务状态，完成后返回数据集（`.ds`）路径。Momentum 任务还提供频点进度、新结果检查和服务重启后的继续监控。 |

### 原生 Momentum（3 个）

| 工具 | 功能 |
|---|---|
| `ads_list_gui_sessions` | 通过本机 Keysight 桥接程序发现 ADS GUI 会话 ID 和工作区路径。 |
| `ads_inspect_momentum_setup` | 只读检查原生 EM Setup、本地版图路径、单段线性扫频和已有运行锁。 |
| `ads_run_momentum` | 核对指定 GUI 会话的工作区及 EM Setup 关联后启动仿真，返回 `task_id`。后续通过状态与结果工具查询、读取、绘图和导出。 |

使用前需准备并保存版图、衬底、端口和原生 EM Setup，并在 ADS GUI 中打开相应工作区。如果本 MCP 服务器已打开工作区，需先调用 `ads_close_workspace` 释放。本版支持 ADS 2027 上已验证的原生计算类型 `5` 和单段线性扫频。自动创建 EM 版图、衬底和 Setup，以及 RFPro/FEM 执行尚未支持。操作与恢复方法见 [Momentum 工作流](docs/momentum.md)。

### 优化（2 个）

| 工具 | 功能 |
|---|---|
| `ads_add_goal` | 添加优化目标，指定表达式、上下限类型、约束值、评估频带和权重。 |
| `ads_add_optim` | 添加优化控制器，收集已设置优化范围的变量及优化目标。 |

`ads_add_var` 的 `opt_min`、`opt_max` 参数用于设置变量的优化范围。

### 原理图审计（2 个）

| 工具 | 功能 |
|---|---|
| `ads_audit_schematic` | 检查已保存或当前设计中的元件、文字和导线重叠，按碰撞类型与严重程度分类，并检测悬空线端点；支持连通性相关豁免。 |
| `ads_autolabel` | 调整实例文字标注的位置以减少重叠，优先选择位移较小的候选位置，保持元件位置不变。 |

### 结果与导出（4 个）

| 工具 | 功能 |
|---|---|
| `ads_get_results` | 读取数据集，返回各 S 参数的 dB 极值、频带端点值、首个低于 −3 dB 的频点，以及降采样后的幅度与相位预览。 |
| `ads_export_touchstone` | 导出双端口 Touchstone `.s2p` 文件，格式为 `HZ S RI R 50`。 |
| `ads_plot_sparams` | 将所选 S 参数的幅度（dB）随频率变化曲线保存为 PNG。 |
| `ads_open_in_gui` | 启动 ADS GUI 并打开当前工作区，GUI 进程独立于 MCP 服务运行。 |

当前注册工具数：**28**（原有 25 个 + Momentum 新增 3 个）。

## Momentum 使用示例

先在 ADS GUI 中打开准备好的工程，用 `ads_list_gui_sessions` 获取会话 ID，然后运行：

```bat
.venv\Scripts\python.exe demo\run_momentum.py --workspace C:/ADS/lpf_wrk --setup-file C:/ADS/lpf_wrk/si_lpf_lib/lpf5o/em%Setup/emStateFile.xml --session-id 12345
```

请将工程路径和会话 ID 替换为实际值。示例通过本项目的 MCP 工具启动仿真，待新数据集生成并通过检查后读取结果，导出 PNG 和 Touchstone。可通过 `--state-file` 参数或 `ADS_MCP_STATE_FILE` 环境变量指定任务状态 JSON 文件；保留同一文件，并使用 `--resume-task` 指定原任务 ID，可在服务重启后继续监控。

## 示例工作流

以下为六段微带线构成的阶跃阻抗低通滤波器调用示意，采用 FR4 衬底，目标截止频率约为 1 GHz。此处省略了部分调用，仅用于说明工具顺序；完整代码见 [`demo/step_by_step.py`](demo/step_by_step.py)。

```text
ads_check_installed()
ads_create_workspace(path="C:/ADS/ads_mcp_demo/lpf_wrk")
ads_create_library(name="si_lpf_lib")
ads_create_schematic(lib="si_lpf_lib", cell="lpf5")
ads_add_var(name="Wh", value="0.2 mm")          # 高阻抗线宽(~串联电感)
ads_add_var(name="Wl", value="8 mm")            # 低阻抗线宽(~并联电容)
ads_add_var(name="Lh", value="23 mm")
ads_add_var(name="Ll", value="6.5 mm")
ads_place_component(lib="ads_tlines", cell="MLIN", x=0.5, y=0, instance_name="TL1",
                    params={"W": "Wh", "L": "Lh"})      # 高 ─┐
ads_place_component(lib="ads_tlines", cell="MLIN", x=2.0, y=0, instance_name="TL2",
                    params={"W": "Wl", "L": "Ll"})      # 低  │ 高低交替重复
ads_place_component(... "TL3".."TL6" ...)               #     ┘ 高-低-高-低-高-低
ads_add_term(name="Term1", x=0, y=0)
ads_add_term(name="Term2", x=9.5, y=0)
ads_add_wire(points=[[0, 0], [0.5, 0]])                 # ...逐段接通
ads_place_component(lib="ads_tlines", cell="MSUB", x=5, y=-2.5)   # 自动命名 MSub1
ads_place_component(lib="ads_simulation", cell="S_Param", x=9.5, y=-2.5,
                    instance_name="SP1",
                    params={"Start": "0.05 GHz", "Stop": "6 GHz", "Step": "25 MHz"})
ads_generate_netlist()                                  # 自检
ads_run_simulation()          -> task_id                # 非阻塞
ads_get_sim_status(task_id)   -> 数据集路径
ads_get_results()             -> S21 通带 −0.08 dB,−3 dB 截止 1.025 GHz,
                                 6 GHz 处抑制 −38 dB
ads_plot_sparams(columns=["S[1,1]", "S[2,1]"])
ads_export_touchstone()
ads_open_in_gui()                                       # 在 ADS 中查看原理图
```

示例中的响应数值来自此前的电路仿真验收，实际结果取决于设计参数与仿真设置。此示例的电路扫频设置独立于下文 Momentum 的 396 点验收设置。

## 仓库访问统计

![clones history](https://gist.githubusercontent.com/Lavaxzyh/ec682cff6f482626fa5348189838e66b/raw/clones-history.png)

克隆与浏览数据由 [ghtraf](https://pypi.org/project/github-traffic-tracker/) 每日采集并保存至公开 Gist，用于保留长期历史。README 顶部徽章使用同一数据源。

## 环境要求

- Windows 与具有有效许可证的 Keysight ADS。本项目已在 ADS 2027（v6.5.0）上验证，其他版本尚未验证。
- ADS 自带 Python 3.14，以及 `keysight.ads.de`、`keysight.ads.dataset` 等 ADS Python 包。
- 通过 MCP 调用时，需使用支持 stdio 传输的 MCP 客户端，如 Claude Desktop、ZCode 或 Cursor。
- Momentum 需要本机 Keysight `bin/ads-mcp.exe` 桥接程序、已打开目标工程的
  ADS GUI 会话，以及已保存的原生 EM Setup。

## 验证记录

ADS 2027 上的电路/拓扑验收（`tests/test_full_flow.py`）及 v0.3.0 Momentum
验收（`demo/run_momentum.py`）：

- 28 个工具通过 stdio 注册，并检查工具定义。
- 电路搭建：放置 13 个实例、检查网表，并通过任务 ID 提交和查询仿真。
- 电路示例结果：通带约 −0.08 dB，首个低于 −3 dB 的频点为 **1.025 GHz**（设计目标 1 GHz），6 GHz 处响应约 −38 dB。
- 结果交付：Touchstone 与 PNG 导出，以及在 ADS GUI 中打开工程。
- LC 滤波器示例：验证 Butterworth、Chebyshev 两类响应与四种滤波类型的 8 种组合。示例结果包括 Chebyshev 低通带内纹波约 0.10 dB、Butterworth 截止频率约 1.00 GHz，审计未报告元件本体碰撞。
- 单元与回归检查：56 项通过，覆盖路径配置、状态恢复、重复启动防护、结果归档和导出等行为。
- 原生 Momentum：0.05–4.00 GHz、396 点，频点网格核对、服务重启后继续
  监控及结果导出。

## 路线图

- [ ] 参数扫描与 MeasEqn 工具
- [x] 优化器对接（设计参数与响应指标优化）
- [x] 原理图审计与标注自动避让（参考 KiCad eeschema）
- [x] 已有原生 EM Setup 的 Momentum 执行流程
- [ ] EM 版图、衬底和 Setup 自动创建
- [ ] 多会话注册表（并发 ADS 进程的连接与审计）
- [ ] API 文档检索工具（与官方服务器的 `search_docs` 互补）
- [ ] ADS 支持范围内的 Linux 支持

## 社区

欢迎通过 Issue 反馈问题，或通过 Pull Request 提交改进。核心实现由 MCP 工具、ADS 引擎、Momentum 和任务状态四个 Python 模块组成。基于本项目实现的电路综合、PCB 协同仿真或负载牵引等流程，也欢迎以示例脚本的形式贡献。

## 致谢

原理图审计（`ads_audit_schematic`）与标注自动避让（`ads_autolabel`）参考了 KiCad eeschema 字段自动放置（`eeschema/autoplace_fields.cpp`）的算法思路，包括包围盒碰撞检测与候选位置分级。本项目未包含 KiCad 源代码，采用 MIT 许可证。KiCad 的版权归其贡献者所有，采用 GPL-3.0 许可证。

## 许可证

本项目采用 [MIT 许可证](LICENSE)。Keysight 与 ADS 为 Keysight Technologies 的商标。运行时使用本机已授权 ADS 安装提供的 Python/AEL 接口及本地桥接程序；ADS 软件及其组件的使用受 Keysight 相应许可条款约束。
