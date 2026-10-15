# Parameterized planar EM modeling

v0.3.0 includes four parameterized construction tools alongside three native
Momentum execution tools, for **32 MCP tools** in total.

Construction uses the workspace opened by this MCP server. All geometry and
substrates are saved before the workspace is handed to ADS GUI for Momentum
microwave execution. Native defaults are read from the locally installed
`em/setupTemplates/RFBoard.emSetupT`; no vendor template is distributed here.

## Supported inputs

| Tool | Inputs and result |
|---|---|
| `ads_create_substrate` | Bottom-to-top finite dielectric layers (`height_mm`, `er`, `loss_tangent`), drawing-layer conductor mappings (`layer`, `interface`, `thickness_um`, optional `conductivity_s_per_m`), and an optional ideal bottom ground. |
| `ads_create_layout` | A new cell/view with rectangles or simple polygons on named drawing layers. Coordinates in `points_mm` are converted to the library's actual layout units. |
| `ads_add_layout_ports` | Distinct terminal names/numbers, `x_mm`, `y_mm`, direction in degrees, drawing layer and net. Existing layout geometry is retained. |
| `ads_configure_momentum_setup` | Local substrate name, positive start/stop frequencies in GHz, point count, and a new setup name. Creates a native microwave setup, connects the layout, and returns `setup_file`. |

Dielectrics are isotropic with frequency-independent loss; layers are ordered
from bottom to top. A conductor `interface` of 1 is above the first dielectric,
2 above the second, and so on. The top boundary is open air. With
`ground_bottom=True`, the bottom boundary is a perfect-conductor ground cover;
otherwise the bottom boundary is open. `conductivity_s_per_m=null` selects a
perfect conductor; a positive value defines finite conductivity. Zero metal
thickness creates a sheet, and positive thickness creates finite metal.

Geometry is explicit data, not inferred PDK artwork. Rectangles require a
lower-left and upper-right corner. Polygons require at least three distinct
vertices without a repeated closing vertex, nonzero area, and no crossing or
touching non-adjacent edges. Supported limits are 32 dielectric layers,
32 distinct conductor mappings, 1,000 shapes/10,000 total vertices, and
64 numbered layout terminals. Coordinates must be finite numbers.

The setup builder uses one point pin per terminal, consecutive terminal
numbers starting at 1, and **50-ohm ground-referenced ports** with a bottom
ground cover (`ground_bottom=True`). Open-bottom substrates can be constructed,
but require a different port-reference configuration and are rejected by this
setup builder. It does not
construct vias, differential port groups, anisotropic materials, or arbitrary
schematic/PDK geometry. RFPro/FEM and Momentum RF mode are not execution targets
of this adapter. Touchstone export remains limited to two ports.

## Tool sequence

Create/open the MCP workspace and a local design library first:

```python
ads_create_workspace(path="C:/ADS/em_demo_wrk")
ads_create_library(name="em_demo_lib")

ads_create_substrate(
    lib="em_demo_lib", name="fr4_demo",
    dielectrics=[{"height_mm": 1.6, "er": 4.4, "loss_tangent": 0.02}],
    conductors=[{"layer": "cond", "interface": 1, "thickness_um": 0}],
)

ads_create_layout(
    lib="em_demo_lib", cell="line",
    shapes=[{"kind": "rectangle", "points_mm": [[0, -0.5], [10, 0.5]]}],
)

ads_add_layout_ports(
    lib="em_demo_lib", cell="line",
    ports=[
        {"name": "P1", "number": 1, "x_mm": 0, "y_mm": 0, "angle": 180},
        {"name": "P2", "number": 2, "x_mm": 10, "y_mm": 0},
    ],
)

ads_configure_momentum_setup(
    lib="em_demo_lib", cell="line", substrate="fr4_demo",
    start_ghz=0.05, stop_ghz=4.0, points=396,
)
# Save the returned setup_file for execution.

ads_close_workspace()
ads_open_in_gui(workspace="C:/ADS/em_demo_wrk")
ads_list_gui_sessions()
# Select the discovered session for this workspace, then use
# ads_run_momentum(session_id=..., workspace=..., setup_file=...).
```

Each construction call saves its output. New substrates, layout views and
setup names must not already exist. Port names/numbers must not collide with
existing terminals. Use a new setup name for another sweep while retaining
the previous setup. Native dataset naming follows the cell name; completed
tasks retain separate dataset/log captures, as described in the
[execution workflow](momentum.md).

## GUI handoff

Finish construction, then call `ads_close_workspace` before opening the
saved workspace in GUI. ADS caches substrate materials in an open GUI
workspace; opening a project only after construction loads the new material
definitions. Building behind an already open GUI workspace is not a supported
handoff. The tools do not close or switch the user's GUI workspace.

`ads_open_in_gui(workspace=...)` accepts an explicit path after the MCP workspace
has closed. It uses the ADS launcher with the workspace as its working directory.
Its `gui_pid` is the launcher PID; use `ads_list_gui_sessions` to obtain the
actual session ID for `ads_run_momentum`.

The setup builder selects microwave mode in both the legacy calculation field
and `SimSetupOverview/simulator`. The RFBoard template defaults to RF mode,
so setting only the legacy field is insufficient. Completion also checks
`Simulation Mode: uW` (or the corresponding microwave marker) in the solver log.

## Complete command-line example

```bat
.venv\Scripts\python.exe demo\build_momentum.py --workspace C:/ADS/em_demo_wrk --run
```

This example creates a **new** workspace, builds the five-section microstrip
filter from [the JSON model](../demo/models/stepped_lpf.json), creates its
substrate/ports/setup, closes the construction workspace, opens ADS GUI,
discovers its session, runs 0.05–4.00 GHz at 396 points, and exports results.

- Use `--model path/to/model.json` for different geometry or materials.
- Omit `--run` to build/save only, or use `--open-gui` to build and inspect.
- Existing workspace directories are rejected and are never deleted.
- State is stored under the workspace's `.ads-mcp/pipeline_state.json`.
- After an MCP restart during a solve, resume using `demo/run_momentum.py`
  with that state file and `--resume-task` rather than rebuilding or redispatching.

Invalid JSON inputs are rejected before their native operation. Geometry edits
use ADS transactions and append mode. Native construction failures can leave
a newly created partial object; inspect the reported workspace before retrying.
Construction does not automatically retry a failed mutation or delete objects.

## 中文说明

四个新工具将衬底、版图、端口和原生 Momentum Setup 创建纳入结构化 MCP
流程。几何、材料、端口与频带均通过参数传入，不绑定本机工程路径。

先在 MCP 工作区完成建模并保存，再关闭该工作区，通过 ADS GUI 加载后求解。
这是为了让 GUI 正确加载新材料，并释放构建进程持有的库。新工具保留同名
已有对象，追加端口时保留原几何；可用新的 Setup 名保存不同扫频设置。

当前支持平面矩形/简单多边形、分层介质与导体映射、50 Ω 对地单引脚端口
及微波模式线性扫频。任意原理图/PDK 的几何推导、过孔、差分端口组与
RFPro/FEM 尚未包含在这个适配器中。
