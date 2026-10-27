# ads-mcp

![Clones](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/Lavaxzyh/ec682cff6f482626fa5348189838e66b/raw/clones.json) ![Views](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/Lavaxzyh/ec682cff6f482626fa5348189838e66b/raw/views.json) ![Installs](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/Lavaxzyh/ec682cff6f482626fa5348189838e66b/raw/installs.json)

**ads-mcp v0.4.0** 提供 49 个 Keysight ADS MCP 工具，核心流程是 **原理图 → 原生 Layout → Momentum → 原生 emModel → 电路联合仿真**。

[English](README.md) · 简体中文

[![Python](https://img.shields.io/badge/Python-3.14_(ADS%E8%87%AA%E5%B8%A6)-3776AB?logo=python&logoColor=white)](https://www.python.org/) [![MCP](https://img.shields.io/badge/Protocol-MCP_stdio-8A2BE2)](https://modelcontextprotocol.io/) [![ADS](https://img.shields.io/badge/Tested_on_Keysight_ADS_2027-red)](https://www.keysight.com/) [![License](https://img.shields.io/badge/License-MIT-green)](LICENSE) [![Tools](https://img.shields.io/badge/%E5%B7%A5%E5%85%B7%E6%95%B0-49-4c1)](docs/v040-tools.md)

> 本版包含原生 artwork 与 EM/电路联合仿真、完整金属区域的参数化天线优化、官方厂商模型下载/导入三条主线。另提供网格加密配置和在指定 ADS 进程中执行的 PySide6 窗口管理。电路仿真默认仍使用独立 `hpeesofsim` 后端。
>
> 本项目采用 MIT 许可证，是第三方独立项目，不是 Keysight 官方产品。ADS 软件、专有模板、厂商库和论文 PDF 不随仓库分发。

## Python API 与依赖包

- **已验证环境：Windows、ADS 2027 v6.5.0、ADS 自带 Python 3.14**，需有效的 ADS/Momentum 许可证；其他版本和平台尚未验证。
- 使用 ADS 解释器创建虚拟环境并复用其 Python 包，以匹配 cp314 原生模块。Keysight 包由本机 ADS 安装提供。
- MCP 客户端需支持 stdio，如 Claude Desktop、ZCode 或 Cursor。
- Momentum、GUI 联合仿真和 Optim 依赖本机官方 `bin/ads-mcp.exe` 桥接及打开目标工程的 ADS GUI 会话；EM 求解还需已保存的原生 EM Setup。

| Python 包 | 来源与用途 |
|---|---|
| `keysight.ads.de` | ADS 内置：工作区、设计库、原理图、Layout 与 AEL 接口。 |
| `keysight.ads.subst` | ADS 内置：介质叠层、导体与过孔映射。 |
| `keysight.ads.dataset` | ADS 内置：读取仿真数据集。 |
| `keysight.edatoolbox` | ADS 环境提供：网表与电路仿真调用。 |
| `PySide6` | 使用 ADS 配套版本：在 GUI 进程中管理窗口与 Qt 回调。 |
| `mcp<2` | pip 安装：MCP 服务与 stdio 通信。 |
| `numpy`、`pandas`、`matplotlib` | pip 安装或复用环境：数值计算、数据整理与绘图。 |

## 安装与配置

下方 `<ADS_INSTALL>`、`<REPO>` 等为通用路径占位符，请替换为自己的路径。

```text
git clone https://github.com/Lavaxzyh/ads-mcp.git
cd ads-mcp
<ADS_INSTALL>/tools/python/python.exe -m venv --system-site-packages .venv
.venv/Scripts/python.exe -m pip install "mcp<2" pandas numpy matplotlib
```

MCP 客户端配置：

```json
{
  "mcpServers": {
    "ads-mcp": {
      "command": "<REPO>/.venv/Scripts/python.exe",
      "args": ["<REPO>/mcp_server.py"],
      "env": {
        "HPEESOF_DIR": "<ADS_INSTALL>",
        "ADS_MCP_WORKROOT": "<USER_OUTPUT>"
      }
    }
  }
}
```

| 配置项 | 用途与默认值 |
|---|---|
| `HPEESOF_DIR` | ADS 安装目录，默认是系统 Program Files 下的 `Keysight/ADS2027`。 |
| `ADS_MCP_WORKROOT` | 用户输出根目录，默认是用户主目录下的 `ads_mcp_demo`。 |
| `ADS_MCP_STATE_FILE` | 可选任务状态 JSON，默认是 `<USER_OUTPUT>/.ads-mcp/session_state.json`，位于仓库之外。 |

运行状态在使用时生成，仓库不包含 `state/` 或 `promo/` 目录。已有任务可通过原来的 `ADS_MCP_STATE_FILE` 继续查询；默认位置调整不会迁移或丢弃旧状态。

## 工具列表：49 个

下方列出**全部已注册工具**，包含 v0.4.0 新增的 17 个工具。详细参数结构由 MCP `tools/list` 返回；原生操作的补充说明见 [v0.4.0 工具](docs/v040-tools.md)。

### 环境与工作区 (7)

| 工具 | 作用 |
|---|---|
| `ads_check_installed` | 检查 ADS 安装、仿真器和 DE 引擎。 |
| `ads_create_workspace` | 创建或打开工作区。 |
| `ads_open_workspace` | 打开已有工作区。 |
| `ads_close_workspace` | 关闭 MCP 持有的工作区。 |
| `ads_session_status` | 查询会话与持久化任务状态。 |
| `ads_open_in_gui` | 在 ADS GUI 打开已保存工作区。 |
| `ads_list_gui_sessions` | 通过官方桥接发现 GUI 会话和工作区标识。 |

### 原理图和 Subnetwork 构建 (13)

| 工具 | 作用 |
|---|---|
| `ads_create_library` | 创建并挂接设计库。 |
| `ads_create_schematic` | 创建原理图并设为后续调用的当前设计。 |
| `ads_open_design` | 检查已有视图，可在指定 GUI 中显示。 |
| `ads_browse_components` | 查询已安装库、cell 和已知参数。 |
| `ads_place_component` | 按朝向和参数放置器件。 |
| `ads_set_instance_parameters` | 修改已有参数，可显式选择仿真视图。 |
| `ads_add_wire` | 添加导线，可设置 net 名称。 |
| `ads_add_var` | 添加 VAR，可设置优化范围。 |
| `ads_add_term` | 放置带接地的仿真 Term，与层次端子不同。 |
| `ads_add_design_port` | 在实例引脚的实际 net 上添加 Subnetwork 端子。 |
| `ads_add_ground` | 放置接地符号。 |
| `ads_define_cell_parameters` | 在 itemdef.py 持久化 REAL/NO_UNIT Subnetwork 形式参数。 |
| `ads_list_instances` | 列出当前原理图实例。 |

### 原生 Layout 和衬底 (6)

| 工具 | 作用 |
|---|---|
| `ads_create_substrate` | 创建介质、导体映射、可选底部地和过孔映射。 |
| `ads_create_layout` | 使用毫米坐标创建矩形/多边形几何。 |
| `ads_generate_layout` | 使用 update_design_from 从原理图原生生成/更新 Layout，可设置 PSN PCell。 |
| `ads_add_layout_ports` | 添加编号版图 pin 和 net，保留已有几何。 |
| `ads_generate_symbol` | 生成 Dual/Quad 矩形符号或原生 Layout-lookalike 符号。 |
| `ads_configure_momentum_setup` | 创建关联本地 Layout 和衬底的原生微波 Setup。 |

### Momentum 和 emModel (7)

| 工具 | 作用 |
|---|---|
| `ads_inspect_momentum_setup` | 检查已保存的 Layout 关联、微波模式、频率计划和运行锁。 |
| `ads_run_momentum` | 通过指定 GUI 会话派发一次校验后的原生求解。 |
| `ads_configure_momentum_mesh` | 设置每波长网格数、边缘网格和可选参考频率。 |
| `ads_configure_momentum_options` | 设置一段线性 RF 扫频、可选独立 DC 点和 Auto/TML/None 端口馈电。 |
| `ads_configure_em_model` | 配置原生模型自动创建/更新；重载并求解后生成数据。 |
| `ads_inspect_em_model` | 检查原生文件、参数名、样本缓存和几何插值。 |
| `ads_configure_em_parameters` | 设置几何插值/缓存分辨率，不删除样本。 |

### 电路仿真和原生优化 (6)

| 工具 | 作用 |
|---|---|
| `ads_generate_netlist` | 生成当前原理图网表以供检查。 |
| `ads_run_simulation` | 默认运行独立仿真，也可显式运行 GUI 仿真/Optim。 |
| `ads_get_sim_status` | 查询独立电路、GUI 电路或 Momentum 任务及归档结果。 |
| `ads_add_goal` | 添加优化表达式、限值、范围和权重。 |
| `ads_add_optim` | 放置 Optim 控制器。 |
| `ads_release_optimizer` | 释放已完成 GUI Optim 的仿真器并关闭 Cockpit，不自动写回最佳值。 |

### ADS 窗口管理 (3)

| 工具 | 作用 |
|---|---|
| `ads_list_windows` | 列出进程内窗口 ID、设计标识和屏幕几何。 |
| `ads_arrange_windows` | 用 setGeometry 拼铺、逐窗最大化或恢复工具保存的窗口状态。 |
| `ads_set_mesh_visibility` | 加载/清除原生 MomentumMesh 显示对象，不保存设计。 |

### 厂商库导入 (1)

| 工具 | 作用 |
|---|---|
| `ads_import_library` | 注册本地厂商 lib.defs；重开工作区执行库初始化。 |

### 原理图审计 (2)

| 工具 | 作用 |
|---|---|
| `ads_audit_schematic` | 检测器件/文字/导线重叠与悬空端点。 |
| `ads_autolabel` | 调整标注位置，不移动器件。 |

### 结果与导出 (4)

| 工具 | 作用 |
|---|---|
| `ads_get_results` | 读取 S 参数摘要和降采样幅相预览。 |
| `ads_export_touchstone` | 按完整精度导出 N 端口矩阵为 .sNp。 |
| `ads_plot_sparams` | 绘制指定 S 参数的 dB 幅度曲线。 |
| `ads_compare_sparameters` | 在一致频率网格比较完整复数矩阵，不插值。 |

## 原生 Layout 与电路联合仿真

1. 搭建并保存金属源原理图。`ads_add_design_port` 在实例实际 net 上建立层次端子；仿真 Term/TermG 本身不等于 Subnetwork 端子。
2. 原理图原生 artwork 转换使用 `ads_generate_layout`；坐标几何创建使用 `ads_create_layout`。前者调用 `update_design_from`，`parameterized=true` 可创建 PSN PCell。源器件必须具有相应 artwork，不会自动推断任意集总器件的封装/焊盘。
3. 配置衬底、过孔、pin 和原生 Setup。`ads_generate_symbol` 独立使用 `SymbolGenerator` 生成 Dual/Quad 矩形符号，或生成原生 Layout lookalike；符号生成不会产生 EM 数据。
4. `ads_configure_em_model` 配置原生模型自动创建/更新。重载工作区；先关闭 MCP 持有的工程，再在 ADS GUI 打开已保存副本并派发 Momentum。
5. 使用 `ads_list_gui_sessions` 选择会话，检查 Setup，派发一次 Momentum，再轮询 `ads_get_sim_status`。当前支持微波模式下的一段线性 RF 扫频和可选的独立 DC 点。
6. 检查模型文件，将电气器件接回对应 EM 安装端口；测试台网表应出现 `MomCmpt` 和正确的 `model.ndx`。使用 `ads_compare_sparameters` 在一致网格比较原始 EM 与模型结果。

原生 emModel 自动生成走的是**保存 Setup 配置＋原生求解**路线，不是单独生成符号。修改网格后需要重载工作区并重新求解；工具不会自动清空旧模型缓存。网格显示工具加载/清除原生网格对象，不能替代几何样本对应性检查。

```python
# MCP 调用示意；替换占位符，并使用发现到的实际会话 ID。
ads_run_simulation(
    backend="gui", session_id=12345, workspace="<WORKSPACE>",
    design="my_lib:cosim_tb:schematic", mode="simulate"
)
# 用返回的 task_id 轮询 ads_get_sim_status。
```

`backend="standalone"` 仍是默认值，可通过 `data_directory` 显式指定厂商 S2P 数据搜索目录；GUI 使用初始化后的工作区厂商库路径。GUI 操作通过官方桥接执行固定内部函数，要求选定会话打开匹配工程，不自动切换工作区。桥接超时不会自动重派。Momentum 和 GUI 电路任务可在服务重启后继续监控；进程内独立仿真线程不能跨重启恢复。

详见 [原生联合仿真](docs/native-cosimulation.md)、[EM 建模](docs/em-modeling.md)和 [Momentum 执行与恢复](docs/momentum.md)。

## 示例工程与脚本

| 示例 | 内容与用途 |
|---|---|
| [联合仿真反射式移相器](examples/pin_phase_shifter_1p4ghz/README.md) | 原生金属源原理图、Layout、32 端口 emModel、PIN_diode/偏置两态测试台和基准曲线/指标；不再次优化该移相器。 |
| [微带贴片天线 EM 优化](examples/microstrip_patch_em_optimization/README.md) | 参数化 matched_patch、全带宽验证用 emModel、原生 Optim 用 emModelOpt、最终尺寸 Layout 和参考结果。 |
| [AVX 0402 3.9 pF 流程](examples/vendor_avx_0402/README.md) | 官方下载脚本、来源/哈希记录和型号选择指南，不附带原厂库。 |

移相器是对 “1.4 GHz Low-Cost PIN Diode Phase Shifter for L-Band Radiometer Antenna,” IEEE Access, 2019 的 **180°单比特电路的大致复现**（[IEEE 8752217](https://ieeexplore.ieee.org/document/8752217/)，DOI `10.1109/ACCESS.2019.2926140`），并非论文原始工程、完整四比特复现或实测样机。

**集总器件的模型范围：**联合仿真反射式移相器使用原生非理想 `PIN_diode`，包含偏置相关响应、有限电阻、结电容和引线电感，并通过附加电阻修正反偏 RF 损耗，按论文等效阻抗校准。当前 L/C 仍采用理想电感/电容，470 Ω 电阻采用 ADS 集总电阻模型；尚未加入这些无源器件的厂商 Q/SRF、封装寄生和容差模型。金属走线、焊盘与过孔由 Momentum 建模。单独的 AVX vendor 模型示例尚未接入此移相器。参数与边界详见[移相器说明](examples/pin_phase_shifter_1p4ghz/README.md#集总器件模型)。

复制或解压示例后，先在**工程关闭时**重绑定发布包中的通用路径：

```text
.venv/Scripts/python.exe examples/rebind_workspace.py examples/pin_phase_shifter_1p4ghz/workspace
```

然后在 ADS GUI 打开该工作区，检查或运行：

```text
.venv/Scripts/python.exe examples/run_example.py pin_phase_shifter_1p4ghz --action inspect
.venv/Scripts/python.exe examples/run_example.py pin_phase_shifter_1p4ghz --action simulate
```

天线示例将名称替换为 `microstrip_patch_em_optimization`，仅天线支持 `--action optimize`。脚本读取最佳参数并在完成后调用 `ads_release_optimizer`，不会自动将最佳值写回原理图。如果 ADS 在关闭 Cockpit 时询问是否更新设计，除非明确需要应用最佳值，否则保留已保存的初始 VAR。选定最终尺寸后应再做完整频带复算。

`Wm/Lm` 是 REAL/NO_UNIT 的 **Subnetwork 形式参数**，不是名为 subnet 的数值类型，通过 `Wm*1 mm`、`Lm*1 mm` 驱动 artwork。关闭几何插值后，缓存分辨率内的已有样本可复用；未缓存尺寸经 GUI 原生服务触发 Momentum。独立 hpeesofsim 进程不能替代该服务。详见 [参数化 EM 优化](docs/parameterized-em-optimization.md)。

AVX 流程需动态查找 `C0402SE3r9.s2p`，注册 `lib.defs`、重开工程、顶层只放一次 `AVX_INCLUDE`，先做两端口 RF 验证，再接入 EM 安装端口。已测试数据从 50 MHz 开始，没有物理 DC 数据；DC 外推及电容隔直假设需要单独核对。详见 [厂商模型](docs/vendor-models.md)。

其他可执行构建脚本：

- `demo/build_native_line.py <NEW_WORKSPACE>`：通过 MCP 从最小原理图生成原生 Layout 和模型输出配置，再交接 GUI Momentum。
- `demo/build_momentum.py --workspace <NEW_WORKSPACE> --run`：按 `demo/models/stepped_lpf.json` 创建平面低通几何并运行 Momentum。
- `demo/run_momentum.py --workspace <WORKSPACE> --setup-file <SETUP_XML> --session-id <ID>`：求解已有 Setup；同一状态文件下可用 `--resume-task` 恢复查询。
- `demo/step_by_step.py` 和 LC 综合脚本：保留的电路示例。执行前检查其输出目录；分步示例会删除并重建自己的示例目录。

## 验证与限制

单元/回归检查和协议测试：

```text
.venv/Scripts/python.exe -m pip install -r requirements-test.txt
.venv/Scripts/python.exe -m unittest discover -s tests -p "*_unit.py"
.venv/Scripts/python.exe tests/test_stdio.py
```

**106 项单元/回归检查通过**，覆盖输出/状态路径、Setup XML 保留、DC 计划、任务新鲜度、回调生命周期，以及经 scikit-rf 回读验证的完整 1/2/3/32 端口 Touchstone。stdio 测试注册了全部 49 个工具并构建小型原理图。协议/集成脚本需要 ADS，并生成自己的测试输出。

ADS 原生验证结果：

- 新建原理图生成的微带线完成 DC＋3 个 RF 点 Momentum，生成原生模型；两端口 MomCmpt 测试台同 RF 网格最大复数误差为 `1.77e-16`。
- 天线 GUI 完整频带联合仿真完成；原生 Optim 复现 `FinalEF=0`、1 次迭代、7 次评估，`Wm=0.7327668436`、`Lm=20.5802420310`。另一个未缓存几何生成了新 `data.008` 样本。交互 Optim 没有独立完成日志时，以新最终数据集＋原生 Cockpit 停止状态判定完成。
- 天线最终尺寸独立求解并取得实际网格。历史全带宽验证中，2.4 GHz S11 由 −26.357 dB 改善到 −48.315 dB；目标是单频匹配，不是增益/效率/带宽优化。
- 移相器基准：1.4 GHz 相差 179.94°，两态插损 0.250/0.341 dB；采用理想 L/C、校准 PIN 假设和假定焊盘，本轮未重新 Optim。40→80 网格相差变化约 0.87°，不是严格收敛精度保证。
- 官方 AVX 3.9 pF 型号、单个顶层 Include 和 21 点 RF 仿真通过，不宣称厂商模型具备完整 DC 行为。
- ADS 窗口拼铺、最大化/恢复及原生网格对象加载已实测；显示、加密配置和求解完成是不同操作。

任意 PDK 转换、任意集总封装生成、RFPro/FEM、通用多段扫频和跨多个服务进程调度不在当前范围内。示例不代表制造、容差或实测验证。

## 可迁移性与发布内容

示例保留 OA 几何及 `.sio/.sam/.ds/CSV` 数值数据。文本和嵌套求解日志 ZIP 中的本机路径、主机信息替换为通用路径/主机标记。复制后应运行重绑定脚本；该脚本不会修改 OA 二进制或清空样本缓存。运行状态和宣传物料位于发布仓库之外。

公开仓库链接、论文引用和许可证署名予以保留。清理验收见 [审查记录](REVIEW.zh-CN.md)，版本变化见 [CHANGELOG](CHANGELOG.md)。

## 仓库统计与贡献

![clones history](https://gist.githubusercontent.com/Lavaxzyh/ec682cff6f482626fa5348189838e66b/raw/clones-history.png)

统计保留已记录的每日历史，并区分待补数据与零值；定义见 [访问统计](docs/traffic.md)。欢迎通过 GitHub Issue/PR 提交问题和改进。后续方向包括更多 PDK/版本支持与电路设计类型拓宽；v0.4.0 已包含指定 GUI 会话的原生操作。

## 许可证与致谢

[MIT](LICENSE)。Keysight 和 ADS 是 Keysight Technologies 的商标，ADS 与厂商数据适用各自许可。原理图审计/标注参考了 KiCad 的包围盒和候选位置思路，未包含 KiCad 源代码。KiCad 版权归其贡献者所有，采用 GPL-3.0。
