# ads-mcp

![Clones](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/Lavaxzyh/ec682cff6f482626fa5348189838e66b/raw/clones.json) ![Views](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/Lavaxzyh/ec682cff6f482626fa5348189838e66b/raw/views.json) ![Installs](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/Lavaxzyh/ec682cff6f482626fa5348189838e66b/raw/installs.json)

**开源的 headless 优先 Keysight ADS MCP 服务器** —— 不打开 ADS 图形界面，让 AI 代理完成射频微波电路的原理图搭建 → 网表 → 仿真 → S 参数读取全流程。

[English](README.md) · 简体中文

![Python](https://img.shields.io/badge/Python-3.14_(ADS自带)-3776AB?logo=python&logoColor=white)
![MCP](https://img.shields.io/badge/Protocol-MCP_stdio-8A2BE2)
![ADS](https://img.shields.io/badge/Tested_on_Keysight_ADS_2027-red)
![License](https://img.shields.io/badge/License-MIT-green)
![Tools](https://img.shields.io/badge/工具数-25-4c1)

> 与 Keysight Technologies 无关联、未获其认可。需要你自有的正版 ADS 许可证——本仓库不含任何 Keysight 代码或二进制。

## 为什么做这个

ADS 2027 自带官方 MCP 服务器(`bin\ads-mcp.exe`):闭源、定位是**薄通用 REPL**——一个 `execute_python` 工具暴露整套 ADS Python API。能力很强，但要求代理已经会写 ADS Python，每一步都得自己编码。

**ads-mcp 反其道而行**:把设计工作流本身做成 **25 个具名、带 schema 文档的工具**，并内置防呆护栏。从未见过 ADS Python API 的代理，用对话就能跑通"原理图 → 网表 → hpeesofsim → S 参数"全闭环。

| | 官方 `ads-mcp.exe` | 本项目 |
|---|---|---|
| 源码 | 混淆二进制 | 开源可审计 |
| 哲学 | 薄 REPL，暴露全量 API | 结构化领域工具 |
| 代理需要会 ADS Python? | 需要 | 不需要 |
| 工作流护栏 | — | 基板命名/参数保护/参数名表 |
| 长仿真 | — | task id + 轮询，不阻塞工具调用 |
| 会话状态 | 内存态 | JSON 状态机落盘，重启可恢复 |
| 脱离 GUI | 本地会话需拉许可证 | headless automation 模式，始终无界面 |

两者都是 stdio MCP 服务器，可并列注册:本项目 25 个工具覆盖设计闭环，官方 `execute_python` 兜底其余操作。

## 亮点

- **headless 优先** —— 全程无需打开 ADS 界面;最后用 `ads_open_in_gui` 打开欣赏成果即可。
- **25 个领域工具** —— 微带设计闭环每一步都是具名工具，schema 即文档，见下方工具面。
- **护栏内置** —— 基板自动命名、字符串引用参数保护、`ads_browse_components` 提供权威元件/参数名表。
- **异步仿真** —— `ads_run_simulation` 立即返回 task id，`ads_get_sim_status` 轮询;长仿真不再卡死工具调用。
- **会话状态落盘** —— JSON 状态机(idle → building → simulating → analyzing)，服务器重启可恢复、任务可追溯。
- **真实导出** —— Touchstone `.s2p`、matplotlib PNG 曲线、数据集 → pandas。
- **拓扑库** —— Butterworth/Chebyshev LC 梯形(LPF/HPF/BPF/BSF)由 g 参数综合、headless 搭建,每格对照理论验证。
- **布局审计** —— KiCad 式重叠审计(`ads_audit_schematic`)+ 标注自动避让(`ads_autolabel`):交付原理图零本体碰撞、无悬空线头、无文字压符号。

## 快速开始

前提:Windows 及已授权安装的 Keysight ADS——已在 ADS 2027(v6.5.0)上完整验证;所用 Python API 基于 Keysight 自 2025U2 起提供的 `keysight.ads.de`，更早版本未经测试。原生 DE 引擎仅支持 cp314，必须用 ADS 自带 Python 3.14。

```bat
git clone https://github.com/<you>/ads-mcp.git
cd ads-mcp

:: 用 ADS 自带解释器建 venv(复用其 Python 包)
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

重启客户端(Claude Desktop / ZCode / Cursor / …)，25 个 `ads_*` 工具即出现。

不用代理也可以直接体验:

```bat
.venv\Scripts\python.exe demo\step_by_step.py
```

该脚本分 15 步旁白式搭完一个滤波器、仿真、导出，并在 ADS GUI 中打开结果。

## 工具面(25 个)

### 环境与会话(5)

| 工具 | 功能 |
|---|---|
| `ads_check_installed` | 自检 ADS 安装、`hpeesofsim.exe`、headless DE 引擎;会话第一步。 |
| `ads_create_workspace` | 在指定路径创建工作区并打开(已存在则直接打开)。 |
| `ads_open_workspace` | 打开已有工作区。 |
| `ads_close_workspace` | 关闭当前工作区。 |
| `ads_session_status` | 会话快照:状态机阶段、打开的工作区、当前原理图、历史仿真任务。 |

### 原理图搭建(9)

| 工具 | 功能 |
|---|---|
| `ads_create_library` | 在工作区内创建设计库并挂载。 |
| `ads_create_schematic` | 创建/打开原理图 `库:单元` 并设为当前设计，后续放置类工具作用于它。 |
| `ads_browse_components` | 枚举组件库/元件及权威参数名(微带族在 `ads_tlines`，控制器/端口在 `ads_simulation`，VAR 在 `ads_datacmps`);放不熟悉的元件前先查。 |
| `ads_place_component` | 在坐标放置实例，可设名称/旋转/参数字典。MSUB 自动命名 `MSub1`;字符串引用参数(`Subst`)拒绝赋值以保护网表引号。 |
| `ads_add_wire` | 添加折线连线，可带网络标号。 |
| `ads_add_var` | 在 VAR 块定义原理图变量，元件参数可按名引用。 |
| `ads_add_term` | 放置 S 参数端口(Term，50 Ω)并自动配地;按名称自动编号端口。 |
| `ads_add_ground` | 放置地符号。 |
| `ads_list_instances` | 列出当前原理图全部实例，确认搭建结果。 |

### 仿真(3)

| 工具 | 功能 |
|---|---|
| `ads_generate_netlist` | 保存原理图并返回网表文本——搭建中途最可靠的自检手段。 |
| `ads_run_simulation` | 保存 → 网表 → 后台线程运行 `hpeesofsim`;立即返回 `task_id`。 |
| `ads_get_sim_status` | 轮询仿真任务;完成后携带数据集(`.ds`)路径。 |



### 布局审计(2)

| 工具 | 功能 |
|---|---|
| `ads_audit_schematic` | KiCad 式原理图重叠审计:对已保存或当前设计两两比对实例包围盒(本体/文字标注)并纳入导线,按严重度分级(body-body / body-text / text-text / text-wire / body-wire),含连通性豁免与悬空线端点(ERC 式)检测 |
| `ads_autolabel` | 标注自动避让:对文字级碰撞按候选方位(右/左/上/下,小位移优先)只挪标注不动元件 |

### 结果与导出(4)

| 工具 | 功能 |
|---|---|
| `ads_get_results` | 读数据集:各 S 参数 dB 极值/带边值、首个 −3 dB 穿越(截止频率提示)、降采样幅度/相位预览。 |
| `ads_export_touchstone` | 导出标准 `.s2p`(HZ S RI R 50)。 |
| `ads_plot_sparams` | 绘制 \|S\| dB–频率曲线(可选列)输出 PNG。 |
| `ads_open_in_gui` | 在 ADS GUI 中打开当前工作区(脱离进程树，服务器重启不影响)。 |

当前注册工具数:**25**。

## 示例工作流

在 FR4 上设计 5 阶阶跃阻抗微带低通滤波器，目标截止 ~1 GHz:

```text
ads_check_installed()
ads_create_workspace(path="~/ads_mcp_demo/lpf_wrk")
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
ads_plot_sparams(columns=["S[1,1]", "S[1,2]"])
ads_export_touchstone()
ads_open_in_gui()                                       # 打开 GUI 欣赏
```

这套构建的完整旁白版在 [`demo/step_by_step.py`](demo/step_by_step.py)。

## 环境要求

- Windows 及已授权安装的 Keysight ADS——已在 ADS 2027(v6.5.0)上完整验证;所用 Python API 基于 Keysight 自 2025U2 起提供的 `keysight.ads.de`，更早版本未经测试
- ADS 自带 Python 3.14(`keysight.ads.de` 原生引擎);结果读取包(`keysight.ads.dataset`，cp310–cp314)随 ADS wheelhouse 提供
- 任意 stdio MCP 客户端(Claude Desktop、ZCode、Cursor 等)

## 验证

MCP 层端到端验收(`tests/test_full_flow.py`，ADS 2027):

- ✅ 25 个工具经 stdio 注册并可调用
- ✅ 对话式搭建:放置 13 个实例、网表核对、task id 提交/轮询仿真
- ✅ SI-LPF 响应:通带 −0.08 dB，−3 dB 截止 **1.025 GHz**(设计目标 1 GHz)，6 GHz 处阻带 −38 dB
- ✅ Touchstone + PNG 导出、GUI 交接
- ✅ LC 拓扑库:8 个滤波器(LPF/HPF/BPF/BSF × Chebyshev/Butterworth)搭建并验证——Chebyshev LPF 带内纹波 0.10 dB、Butterworth 截止精确 1.00 GHz,每格布局 0 碰撞

## 路线图

- [ ] 参数扫描与 MeasEqn 工具
- [x] 优化器对接(几何 → 指标闭环调优)
- [x] 布局审计与标注自动避让(参考 KiCad eeschema)
- [ ] EM(Momentum)流程
- [ ] 多会话注册表(并发 ADS 进程的挂接与审计)
- [ ] API 文档检索工具(与官方服务器的 `search_docs` 互补)
- [ ] ADS 支持范围内的 Linux 支持

## 社区

欢迎 Issue 与 PR——代码库刻意保持很小:三个模块，无框架。如果你在上面搭出了设计流程(综合、PCB 协仿真、load-pull…)，一个 demo 脚本就是最好的 PR。

## 致谢

重叠审计(`ads_audit_schematic`)与标注自动避让(`ads_autolabel`)的算法思路参考自 KiCad eeschema 的字段自动摆放(`eeschema/autoplace_fields.cpp`):包围盒碰撞检测 + 候选方位严重度分级。本项目仅参考算法思路、未复制任何代码，保持 MIT 许可。KiCad © 其贡献者，采用 GPL-3.0 许可。

## 许可证

[MIT](LICENSE) —— Keysight 与 ADS 为 Keysight Technologies 商标;本项目仅调用你已授权安装所附带的公开 Python API。
