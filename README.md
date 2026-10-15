# ads-mcp

![Clones](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/Lavaxzyh/ec682cff6f482626fa5348189838e66b/raw/clones.json) ![Views](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/Lavaxzyh/ec682cff6f482626fa5348189838e66b/raw/views.json) ![Installs](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/Lavaxzyh/ec682cff6f482626fa5348189838e66b/raw/installs.json)

**An open-source MCP server for Keysight ADS**, providing tools for RF and microwave schematic construction, circuit simulation, Momentum simulation, and S-parameter processing.

English · [简体中文](README.zh-CN.md)

![Python](https://img.shields.io/badge/Python-3.14_(ADS_bundled)-3776AB?logo=python&logoColor=white)
![MCP](https://img.shields.io/badge/Protocol-MCP_stdio-8A2BE2)
![ADS](https://img.shields.io/badge/Tested_on_Keysight_ADS_2027-red)
![License](https://img.shields.io/badge/License-MIT-green)
![Tools](https://img.shields.io/badge/Tools-28-4c1)

> This is an independent open-source project, not an official Keysight product. A separately installed copy of Keysight ADS with a valid license is required. This repository does not distribute ADS software or its proprietary components.

## Overview

**ads-mcp v0.3.0** exposes ADS design and simulation operations through **28 MCP tools**, each with defined input parameters. It also records session and simulation task state.

The circuit workflow covers schematic construction, netlist generation, `hpeesofsim` execution, and S-parameter processing. The Momentum workflow runs an **existing saved native EM Setup** in a specified ADS GUI session, monitors solver progress, and checks the newly generated dataset and its frequency grid.

Momentum support uses `bin/ads-mcp.exe` from the local ADS installation. Prepare and save the layout, substrate, ports, and native EM Setup in ADS before using these tools. See [Momentum workflow and supported scope](docs/momentum.md).

## Features

- **Schematic construction:** create workspaces and libraries, then place components, variables, ports, grounds, and wires.
- **Parameter checks:** automatically name microstrip substrates, protect string reference parameters, and query component and parameter names with `ads_browse_components`.
- **Asynchronous circuit simulation:** submit a simulation and receive a `task_id`; query status and the resulting dataset path with `ads_get_sim_status`.
- **Native Momentum simulation:** inspect saved EM Setups, discover ADS GUI sessions, start simulations, and monitor frequency progress and new results.
- **Persistent task records:** save session and task state as JSON. Momentum monitoring can resume after an MCP server restart; interrupted in-process circuit tasks are marked as errors.
- **Results and export:** read S-parameter summaries and previews, and export two-port Touchstone `.s2p` files and PNG plots.
- **Filter examples:** synthesize and build Butterworth and Chebyshev LC ladders from g-parameters, covering low-pass, high-pass, band-pass, and band-stop responses.
- **Schematic audit:** detect component, text, and wire overlaps and dangling wire endpoints, with automatic annotation placement.

## Quick Start

The project has been validated on Windows with Keysight ADS 2027 (v6.5.0). Other ADS versions have not been validated. Use a valid ADS license and the ADS-bundled Python 3.14 interpreter; the native `keysight.ads.de` engine requires a matching cp314 runtime.

The commands below use the default installation path. Adjust it to match your ADS installation.

```bat
git clone https://github.com/Lavaxzyh/ads-mcp.git
cd ads-mcp

:: Create a virtual environment with the ADS interpreter and reuse its packages
"C:\Program Files\Keysight\ADS2027\tools\python\python.exe" -m venv --system-site-packages .venv
.venv\Scripts\python.exe -m pip install "mcp<2" matplotlib pandas
```

### MCP Client Configuration

```json
{
  "mcpServers": {
    "ads-mcp": {
      "command": "<repo>\\.venv\\Scripts\\python.exe",
      "args": ["<repo>\\mcp_server.py"],
      "env": { "HPEESOF_DIR": "C:\\Program Files\\Keysight\\ADS2027" }
    }
  }
}
```

Replace the repository and ADS paths with local absolute paths, save the configuration, and restart your MCP client, such as Claude Desktop, ZCode, or Cursor, to load the 28 `ads_*` tools.

### Run the Example Script

You can also run the example script directly, without configuring an AI client:

```bat
.venv\Scripts\python.exe demo\step_by_step.py
```

The script demonstrates filter construction, simulation, and result export in 15 steps, then opens the generated project in ADS GUI. Check its `WS` and `OUT` paths before running: the script deletes and recreates both example directories.

## Tool Reference (28 Tools)

### Environment & session (5)

| Tool | What it does |
|---|---|
| `ads_check_installed` | Check the ADS installation, `hpeesofsim.exe`, and the ADS DE engine. Recommended at the start of a session. |
| `ads_create_workspace` | Create an ADS workspace at a path and open it (opens instead of failing if it exists). |
| `ads_open_workspace` | Open an existing workspace. |
| `ads_close_workspace` | Close the current workspace. |
| `ads_session_status` | Snapshot of session state: state-machine phase, open workspace, current schematic, past simulation tasks. |

### Schematic construction (9)

| Tool | What it does |
|---|---|
| `ads_create_library` | Create a design library inside the open workspace and attach it. |
| `ads_create_schematic` | Create or open schematic cell `lib:cell` and set it as the current design for subsequent operations. |
| `ads_browse_components` | Query component libraries, cells, and parameter names. Microstrip components are in `ads_tlines`, controllers and ports in `ads_simulation`, and VAR in `ads_datacmps`. |
| `ads_place_component` | Place a component at (x, y), with optional name, rotation, and parameters. MSUB is named `MSub1`; direct assignment to string reference parameters such as `Subst` is rejected to preserve netlist quoting. |
| `ads_add_wire` | Add a polyline wire, optional net label. |
| `ads_add_var` | Define a schematic variable in the VAR block; component parameters reference it by name. |
| `ads_add_term` | Place an S-parameter port (Term, 50 Ω) with its ground; auto-numbers the port from the name. |
| `ads_add_ground` | Place a ground symbol. |
| `ads_list_instances` | List all instances in the current schematic. |

### Simulation (3)

| Tool | What it does |
|---|---|
| `ads_generate_netlist` | Save the schematic, generate a netlist, and return its text for inspection. |
| `ads_run_simulation` | Save the schematic, generate a netlist, run `hpeesofsim` in the background, and return a `task_id`. |
| `ads_get_sim_status` | Poll circuit or Momentum tasks; Momentum includes frequency progress, fresh-output checks and monitoring after server restarts. Completion carries the dataset (`.ds`) path. |

### Native Momentum (3)

| Tool | What it does |
|---|---|
| `ads_list_gui_sessions` | Discover running ADS GUI session IDs and workspace paths through the installed Keysight bridge. |
| `ads_inspect_momentum_setup` | Read a saved native EM Setup, validate its local layout paths and one linear frequency sweep, and report an existing run reservation. |
| `ads_run_momentum` | Validate the selected GUI workspace and setup link, launch once, and return a task ID. Poll with `ads_get_sim_status`; read/export/plot the dataset with the existing result tools. |

Prepare and save the layout, substrate, ports, and native EM Setup, then open the workspace in ADS GUI. If this MCP server has a workspace open, close it with `ads_close_workspace` before dispatch. This release supports native calculation type `5` and one linear sweep, as validated on ADS 2027. Automated EM layout, substrate, and setup creation, and RFPro/FEM execution, are not yet supported. See [Momentum workflow](docs/momentum.md) for usage and recovery steps.

### Optimization (2)

| Tool | What it does |
|---|---|
| `ads_add_goal` | Add an optimization goal with an expression, lower or upper bound, evaluation band, and weight. |
| `ads_add_optim` | Add an optimization controller and collect variables with optimization ranges and the defined goals. |

Use the `opt_min` and `opt_max` parameters of `ads_add_var` to set a variable's optimization range.

### Schematic audit (2)

| Tool | What it does |
|---|---|
| `ads_audit_schematic` | Check a saved or current design for component, text, and wire overlaps and dangling wire endpoints. Classify findings by collision type and severity, with connectivity-aware exemptions. |
| `ads_autolabel` | Adjust instance annotations to reduce overlaps, preferring candidate positions with smaller displacement while preserving component positions. |

### Results & export (4)

| Tool | What it does |
|---|---|
| `ads_get_results` | Read a dataset and return S-parameter dB extrema, band-edge values, the first frequency below −3 dB, and downsampled magnitude and phase previews. |
| `ads_export_touchstone` | Export a two-port Touchstone `.s2p` file in `HZ S RI R 50` format. |
| `ads_plot_sparams` | Plot \|S\| dB vs frequency (selectable columns) to a PNG. |
| `ads_open_in_gui` | Open the current workspace in the ADS GUI (detached process, survives server restarts). |

Current MCP registration: **28 tools** (25 existing + 3 Momentum tools).

## Momentum Example

Open the prepared workspace in ADS GUI, discover the session ID with `ads_list_gui_sessions`, then run:

```bat
.venv\Scripts\python.exe demo\run_momentum.py --workspace C:/ADS/lpf_wrk --setup-file C:/ADS/lpf_wrk/si_lpf_lib/lpf5o/em%Setup/emStateFile.xml --session-id 12345
```

Replace the paths and session ID with actual values. The example uses this project's MCP tools to start a simulation, check the new dataset, read results, and export PNG and Touchstone files. Use `--state-file` or the `ADS_MCP_STATE_FILE` environment variable to select a task-state JSON file. Keep the same file and specify the original task ID with `--resume-task` to continue monitoring after a server restart.

## Example Workflow

The following call sequence outlines a six-section stepped-impedance microstrip low-pass filter on FR4, with a target cutoff near 1 GHz. Some calls are omitted for brevity. See [`demo/step_by_step.py`](demo/step_by_step.py) for the complete executable example.

```text
ads_check_installed()
ads_create_workspace(path="C:/ADS/ads_mcp_demo/lpf_wrk")
ads_create_library(name="si_lpf_lib")
ads_create_schematic(lib="si_lpf_lib", cell="lpf5")
ads_add_var(name="Wh", value="0.2 mm")          # high-Z line width  (~series L)
ads_add_var(name="Wl", value="8 mm")            # low-Z  line width  (~shunt C)
ads_add_var(name="Lh", value="23 mm")
ads_add_var(name="Ll", value="6.5 mm")
ads_place_component(lib="ads_tlines", cell="MLIN", x=0.5, y=0, instance_name="TL1",
                    params={"W": "Wh", "L": "Lh"})      # hi  ─┐
ads_place_component(lib="ads_tlines", cell="MLIN", x=2.0, y=0, instance_name="TL2",
                    params={"W": "Wl", "L": "Ll"})      # lo   │ repeat the ladder
ads_place_component(... "TL3".."TL6" ...)               #      ┘ hi-lo-hi-lo-hi-lo
ads_add_term(name="Term1", x=0, y=0)
ads_add_term(name="Term2", x=9.5, y=0)
ads_add_wire(points=[[0, 0], [0.5, 0]])                 # ...join the ladder
ads_place_component(lib="ads_tlines", cell="MSUB", x=5, y=-2.5)   # auto-named MSub1
ads_place_component(lib="ads_simulation", cell="S_Param", x=9.5, y=-2.5,
                    instance_name="SP1",
                    params={"Start": "0.05 GHz", "Stop": "6 GHz", "Step": "25 MHz"})
ads_generate_netlist()                                  # sanity check
ads_run_simulation()          -> task_id                # non-blocking
ads_get_sim_status(task_id)   -> dataset path
ads_get_results()             -> S21 passband −0.08 dB, −3 dB cutoff 1.025 GHz,
                                 −38 dB rejection @ 6 GHz
ads_plot_sparams(columns=["S[1,1]", "S[2,1]"])
ads_export_touchstone()
ads_open_in_gui()                                       # inspect the schematic in ADS
```

The response values shown are from earlier circuit simulation acceptance checks. Actual results depend on design parameters and simulation settings. This example's circuit sweep is separate from the 396-point Momentum validation below.

## Repository Traffic

![clones history](https://gist.githubusercontent.com/Lavaxzyh/ec682cff6f482626fa5348189838e66b/raw/clones-history.png)

Clone and view statistics are collected daily by [ghtraf](https://pypi.org/project/github-traffic-tracker/) and saved to a public Gist for historical tracking. The badges at the top of this README use the same data source.

## Requirements

- Windows and a Keysight ADS installation with a valid license. Validated on ADS 2027 (v6.5.0); other versions have not been validated.
- ADS-bundled Python 3.14 and the ADS Python packages, including `keysight.ads.de` and `keysight.ads.dataset`.
- For MCP access, a client supporting stdio transport, such as Claude Desktop, ZCode, or Cursor.
- For Momentum: the installed Keysight `bin/ads-mcp.exe` bridge, an ADS GUI
  session with the target workspace open, and a saved native EM Setup.

## Validation

Circuit/topology acceptance (`tests/test_full_flow.py`) and v0.3.0 Momentum
validation (`demo/run_momentum.py`) on ADS 2027:

- 28 tools registered over stdio, with tool definitions checked.
- Circuit construction: 13 instances placed, netlist checked, and simulation submitted and polled by task ID.
- Circuit example response: approximately −0.08 dB in the passband, first sampled frequency below −3 dB at **1.025 GHz** (1 GHz design target), and approximately −38 dB at 6 GHz.
- Result delivery: Touchstone and PNG export, and opening the project in ADS GUI.
- LC filter examples: eight combinations of Butterworth and Chebyshev responses with four filter types. Example results include approximately 0.10 dB Chebyshev low-pass ripple and a 1.00 GHz Butterworth cutoff, with no component body collisions reported by the audit.
- Unit and regression checks: 44 passed, covering state recovery, duplicate dispatch prevention, result capture, and export behavior.
- Native Momentum: 0.05–4.00 GHz, 396 points, frequency-grid verification,
  server-restart monitoring, and result exports.

## Roadmap

- [ ] Parameter sweeps & MeasEqn tools
- [x] Optimizer hooks (geometry → spec closed-loop tuning)
- [x] Schematic audit & annotation auto-placement (KiCad eeschema-inspired)
- [x] Native Momentum execution with an existing saved EM Setup
- [ ] Automated EM layout/substrate/setup creation
- [ ] Multi-session registry (attach/audit concurrent ADS processes)
- [ ] API doc search tool (complements the official server's `search_docs`)
- [ ] Linux support where ADS supports it

## Community

Report problems through Issues or contribute improvements through Pull Requests. The implementation has four core Python modules for MCP tools, the ADS engine, Momentum, and task state. Contributions of example scripts for circuit synthesis, PCB co-simulation, load-pull analysis, or other workflows are also welcome.

## Acknowledgments

The schematic audit (`ads_audit_schematic`) and annotation placement (`ads_autolabel`) draw on the algorithmic approach of KiCad eeschema field placement (`eeschema/autoplace_fields.cpp`), including bounding-box collision detection and candidate position ranking. This project contains no KiCad source code and uses the MIT license. KiCad is copyright its contributors and licensed under GPL-3.0.

## License

This project uses the [MIT license](LICENSE). Keysight and ADS are trademarks of Keysight Technologies. At runtime, the project uses the Python/AEL interfaces and local bridge from a licensed ADS installation. Use of ADS software and its components is subject to the applicable Keysight license terms.
