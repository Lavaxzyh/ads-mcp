# ads-mcp

![Clones](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/Lavaxzyh/ec682cff6f482626fa5348189838e66b/raw/clones.json) ![Views](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/Lavaxzyh/ec682cff6f482626fa5348189838e66b/raw/views.json) ![Installs](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/Lavaxzyh/ec682cff6f482626fa5348189838e66b/raw/installs.json)

**ads-mcp v0.4.0** provides 49 MCP tools for Keysight ADS. The core workflow is **schematic → native Layout → Momentum → native emModel → circuit cosimulation**.

English · [简体中文](README.zh-CN.md)

[![Python](https://img.shields.io/badge/Python-3.14_(ADS-bundled)-3776AB?logo=python&logoColor=white)](https://www.python.org/) [![MCP](https://img.shields.io/badge/Protocol-MCP_stdio-8A2BE2)](https://modelcontextprotocol.io/) [![ADS](https://img.shields.io/badge/Tested_on_Keysight_ADS_2027-red)](https://www.keysight.com/) [![License](https://img.shields.io/badge/License-MIT-green)](LICENSE) [![Tools](https://img.shields.io/badge/Tools-49-4c1)](docs/v040-tools.md)

> Three workflows are included: native artwork and EM/circuit cosimulation, parameterized complete-layout antenna optimization, and official vendor-model download/import. Mesh refinement and PySide6 window arrangement run through the selected ADS process. The default circuit backend remains standalone `hpeesofsim`.
>
> This is an independent MIT-licensed project, not an official Keysight product. ADS software, proprietary templates, vendor kits, and paper PDFs are not redistributed.

## Environment requirements and Python packages

- **Validated environment: Windows, ADS 2027 v6.5.0, and ADS-bundled Python 3.14**, with valid ADS/Momentum licenses. Other versions and platforms have not been validated.
- Create a virtual environment with the ADS interpreter and reuse its packages to match the native cp314 modules. Keysight packages come from the local ADS installation.
- The MCP client must support stdio, such as Claude Desktop, ZCode, or Cursor.
- Momentum, GUI cosimulation, and Optim require the installed official `bin/ads-mcp.exe` bridge and an ADS GUI session with the target workspace open. EM solving also requires a saved native EM Setup.

| Python package | Source and purpose |
|---|---|
| `keysight.ads.de` | ADS-bundled: workspaces, libraries, schematics, Layout, and AEL access. |
| `keysight.ads.subst` | ADS-bundled: dielectric stacks, conductor and via mappings. |
| `keysight.ads.dataset` | ADS-bundled: reading simulation datasets. |
| `keysight.edatoolbox` | Provided by the ADS environment: netlisting and circuit simulation calls. |
| `PySide6` | Use the ADS-matched version: window management and Qt callbacks inside the GUI process. |
| `mcp<2` | Installed with pip: MCP service and stdio communication. |
| `numpy`, `pandas`, `matplotlib` | Installed with pip or reused from the environment: numerical processing, data handling, and plotting. |

## Installation and configuration

Replace generic path placeholders such as `<ADS_INSTALL>` and `<REPO>` with your own paths.

```text
git clone https://github.com/Lavaxzyh/ads-mcp.git
cd ads-mcp
<ADS_INSTALL>/tools/python/python.exe -m venv --system-site-packages .venv
.venv/Scripts/python.exe -m pip install "mcp<2" pandas numpy matplotlib
```

MCP client configuration:

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

| Setting | Purpose and default |
|---|---|
| `HPEESOF_DIR` | ADS installation; defaults to `Keysight/ADS2027` under the system Program Files directory. |
| `ADS_MCP_WORKROOT` | User output root; defaults to `ads_mcp_demo` under the user's home directory. |
| `ADS_MCP_STATE_FILE` | Optional task-state JSON; defaults to `<USER_OUTPUT>/.ads-mcp/session_state.json`, outside the repository. |

Runtime state is generated during use. The repository contains neither a `state/` directory nor a `promo/` directory. Existing monitors remain available by selecting their original `ADS_MCP_STATE_FILE`; the default-location change does not move or discard old state.

## Tool reference: 49 tools

The following tables list **every registered tool**, including the 17 tools added in v0.4.0. Full input schemas are returned by MCP `tools/list`; detailed native-operation notes are in [v0.4.0 tools](docs/v040-tools.md).

### Environment and workspaces (7)

| Tool | Purpose |
|---|---|
| `ads_check_installed` | Check the ADS installation, simulator, and DE engine. |
| `ads_create_workspace` | Create or open a workspace. |
| `ads_open_workspace` | Open an existing workspace. |
| `ads_close_workspace` | Close the server-owned workspace. |
| `ads_session_status` | Inspect session and persisted task state. |
| `ads_open_in_gui` | Launch ADS GUI for a saved workspace. |
| `ads_list_gui_sessions` | Discover official-bridge GUI sessions and workspace identities. |

### Schematic and Subnetwork construction (13)

| Tool | Purpose |
|---|---|
| `ads_create_library` | Create and attach a design library. |
| `ads_create_schematic` | Create a schematic and select it for subsequent calls. |
| `ads_open_design` | Inspect an existing view; optionally show it in a selected GUI. |
| `ads_browse_components` | Browse installed libraries, cells, and known parameters. |
| `ads_place_component` | Place a component with orientation and parameters. |
| `ads_set_instance_parameters` | Edit existing parameters and optionally select a simulation view. |
| `ads_add_wire` | Add a wire with optional net label. |
| `ads_add_var` | Add a VAR entry, optionally with optimization bounds. |
| `ads_add_term` | Place a simulation Term with ground; distinct from a Subnetwork terminal. |
| `ads_add_design_port` | Bind a Subnetwork terminal to an actual instance-pin net. |
| `ads_add_ground` | Place a ground symbol. |
| `ads_define_cell_parameters` | Persist REAL/NO_UNIT Subnetwork formal parameters in itemdef.py. |
| `ads_list_instances` | List instances in the current schematic. |

### Native Layout and substrate (6)

| Tool | Purpose |
|---|---|
| `ads_create_substrate` | Create dielectrics, conductor mappings, optional ground and vias. |
| `ads_create_layout` | Create explicit rectangle/polygon artwork from millimeter coordinates. |
| `ads_generate_layout` | Generate/update native schematic artwork using update_design_from; optionally make a PSN PCell. |
| `ads_add_layout_ports` | Add numbered layout pins and nets without replacing geometry. |
| `ads_generate_symbol` | Generate Dual/Quad rectangular or native Layout-lookalike symbols. |
| `ads_configure_momentum_setup` | Create a native microwave Setup linked to local Layout and substrate. |

### Momentum and emModel (7)

| Tool | Purpose |
|---|---|
| `ads_inspect_momentum_setup` | Validate the saved Layout link, microwave mode, frequency plans, and run reservation. |
| `ads_run_momentum` | Dispatch one validated native solve through the selected GUI session. |
| `ads_configure_momentum_mesh` | Set cells per wavelength, edge mesh, and optional reference frequency. |
| `ads_configure_momentum_options` | Set one linear RF plan plus optional discrete DC, and Auto/TML/None port feeds. |
| `ads_configure_em_model` | Configure native model auto-create/update output; reload and solve to produce data. |
| `ads_inspect_em_model` | Inspect native files, parameter names, sample caches, and geometry interpolation. |
| `ads_configure_em_parameters` | Set geometry interpolation/cache resolution without deleting samples. |

### Circuit simulation and native optimization (6)

| Tool | Purpose |
|---|---|
| `ads_generate_netlist` | Return the selected schematic netlist for inspection. |
| `ads_run_simulation` | Run standalone simulation by default, or GUI simulation/Optim explicitly. |
| `ads_get_sim_status` | Poll circuit, GUI circuit, or Momentum tasks and archived results. |
| `ads_add_goal` | Add an optimization expression, bounds, range, and weight. |
| `ads_add_optim` | Place an Optim controller. |
| `ads_release_optimizer` | Release a completed GUI Optim task and close its cockpit; do not apply best values. |

### ADS window management (3)

| Tool | Purpose |
|---|---|
| `ads_list_windows` | List process-local windows, IDs, design identities, and screen geometry. |
| `ads_arrange_windows` | Tile with setGeometry, maximize each, or restore tool-saved window states. |
| `ads_set_mesh_visibility` | Load/clear native MomentumMesh display objects; never save the design. |

### Vendor library import (1)

| Tool | Purpose |
|---|---|
| `ads_import_library` | Register local vendor lib.defs; reopen the workspace to initialize the kit. |

### Schematic audit (2)

| Tool | Purpose |
|---|---|
| `ads_audit_schematic` | Detect body/text/wire overlaps and dangling endpoints. |
| `ads_autolabel` | Reposition annotations without moving components. |

### Results and export (4)

| Tool | Purpose |
|---|---|
| `ads_get_results` | Read S-parameter summaries and downsampled magnitude/phase previews. |
| `ads_export_touchstone` | Export a complete N-port matrix to .sNp, with full numerical precision. |
| `ads_plot_sparams` | Plot selected S-parameter magnitudes in dB. |
| `ads_compare_sparameters` | Compare complete complex matrices on matching grids, with no interpolation. |

## Native Layout and circuit cosimulation

1. Build/save the metal-source schematic. `ads_add_design_port` creates a hierarchical terminal on the actual instance net; a simulation Term/TermG alone does not define a Subnetwork terminal.
2. Use `ads_generate_layout` for native artwork conversion. `ads_create_layout` instead creates explicit coordinate geometry. Generation uses `update_design_from`; `parameterized=true` creates a PSN PCell. Artwork must exist for the source components; packaging/pads for arbitrary lumped devices are not inferred automatically.
3. Configure substrate, vias, pins, and a native Setup. `ads_generate_symbol` independently generates Dual/Quad symbols through `SymbolGenerator`, or a native Layout lookalike; it does not generate EM data.
4. `ads_configure_em_model` sets native auto-create/update output. Reload the workspace, close the MCP-owned workspace, and open the saved copy in ADS GUI before dispatching Momentum.
5. Select the GUI session using `ads_list_gui_sessions`; inspect the Setup, run Momentum once, and poll `ads_get_sim_status`. Supported plans are one linear RF sweep plus an optional native discrete DC point, in microwave mode.
6. Inspect model files and connect the electrical devices to the EM model's matching mounting ports. The circuit netlist must reference `MomCmpt` and the intended `model.ndx`. Use `ads_compare_sparameters` for equal-grid raw/model checks.

Native emModel creation is the **saved Setup + native simulation** route, not a standalone symbol-creation operation. Mesh configuration changes require workspace reload and a new solve; configuration tools do not silently erase model caches. Mesh display loads/clears native mesh objects and does not verify that a sample matches the intended geometry.

```python
# Illustrative MCP calls; replace placeholders and use the discovered session ID.
ads_run_simulation(
    backend="gui", session_id=12345, workspace="<WORKSPACE>",
    design="my_lib:cosim_tb:schematic", mode="simulate"
)
# Poll the returned task_id with ads_get_sim_status.
```

`backend="standalone"` remains the default. Its optional `data_directory` supplies the model-data search directory for vendor S2P files; GUI runs use initialized workspace kit paths. GUI operations use the installed official bridge and fixed internal functions, require the matching workspace, and never switch it. Bridge timeouts do not trigger automatic redispatch. Momentum and GUI-circuit monitors persist across server restarts; in-process standalone workers do not survive one.

See [native cosimulation](docs/native-cosimulation.md), [EM construction](docs/em-modeling.md), and [Momentum execution/recovery](docs/momentum.md).

## Packaged examples and scripts

| Example | Included content and purpose |
|---|---|
| [Reflection phase shifter](examples/pin_phase_shifter_1p4ghz/README.md) | Native metal-source schematic, Layout, 32-port `emModel`, PIN_diode/bias testbenches for two states, reference curves and metrics. No new phase-shifter optimization is run. |
| [Microstrip patch EM optimization](examples/microstrip_patch_em_optimization/README.md) | Parameterized `matched_patch`, `emModel` for full-band verification, `emModelOpt` for native Optim, final-geometry Layout and reference results. |
| [AVX 0402 3.9 pF workflow](examples/vendor_avx_0402/README.md) | Official downloader, provenance/hash recording and model-selection guide; the vendor kit is not bundled. |

The phase shifter is an **approximate reproduction of the 180-degree single-bit circuit** in “1.4 GHz Low-Cost PIN Diode Phase Shifter for L-Band Radiometer Antenna,” IEEE Access, 2019 ([IEEE 8752217](https://ieeexplore.ieee.org/document/8752217/), DOI `10.1109/ACCESS.2019.2926140`). It is not the paper's original files, a complete four-bit reproduction, or a measured prototype.

**Lumped-device model scope:** the reflection phase shifter uses the native nonideal `PIN_diode`, with bias-dependent response, finite resistance, junction capacitance and bond inductance, plus an external resistor correcting reverse-bias RF loss. It is calibrated to the paper's equivalent impedance. L/C still use ideal inductor/capacitor primitives, and the 470-ohm resistor uses an ADS lumped resistor model; vendor Q/SRF, package parasitics and tolerance models for these passive components are not included. Momentum models the metal traces, pads and vias. The separate AVX vendor-model example is not integrated into this phase shifter. See the [device-model details](examples/pin_phase_shifter_1p4ghz/README.md#集总器件模型).

Before opening a copied example in ADS, rebind its generic release paths **while the workspace is closed**:

```text
.venv/Scripts/python.exe examples/rebind_workspace.py examples/pin_phase_shifter_1p4ghz/workspace
```

Then open that workspace in ADS GUI and inspect/run it:

```text
.venv/Scripts/python.exe examples/run_example.py pin_phase_shifter_1p4ghz --action inspect
.venv/Scripts/python.exe examples/run_example.py pin_phase_shifter_1p4ghz --action simulate
```

For the antenna, substitute `microstrip_patch_em_optimization`. `--action optimize` is supported only by that example. Its runner reads best parameters and invokes `ads_release_optimizer` after completion; it does not automatically apply the best values to the schematic. If ADS asks whether to update the design when closing its cockpit, retain the saved initial VAR values unless you explicitly intend to apply the optimum. Follow with a full-band simulation of the chosen final dimensions.

`Wm/Lm` are REAL/NO_UNIT **Subnetwork formal parameters**, not a numeric type named “subnet.” They drive artwork through `Wm*1 mm` and `Lm*1 mm`. With geometry interpolation disabled, a cached sample can be reused at the configured resolution; uncached dimensions trigger Momentum through the native GUI service. An independent hpeesofsim process is not a replacement for that service. See [parameterized EM optimization](docs/parameterized-em-optimization.md).

For AVX, resolve `C0402SE3r9.s2p` dynamically in the official library, register `lib.defs`, reopen the workspace, and place one top-level `AVX_INCLUDE`. Validate a two-port RF testbench before connecting it to EM mounting ports. The tested file starts at 50 MHz and has no physical DC data; DC extrapolation and any explicit capacitor DC-block assumptions must be reviewed. See [vendor models](docs/vendor-models.md).

Additional executable builders:

- `demo/build_native_line.py <NEW_WORKSPACE>`: minimal schematic → native Layout/model-output setup through MCP; then hand off to GUI Momentum.
- `demo/build_momentum.py --workspace <NEW_WORKSPACE> --run`: explicit planar low-pass geometry from `demo/models/stepped_lpf.json`, then Momentum.
- `demo/run_momentum.py --workspace <WORKSPACE> --setup-file <SETUP_XML> --session-id <ID>`: solve a saved Setup; `--resume-task` resumes monitoring with the same state file.
- `demo/step_by_step.py` and LC synthesis scripts: legacy circuit demonstrations. Check their output directories first; the step-by-step example deletes/recreates its own example directories.

## Validation and limits

Current unit/regression checks and protocol test:

```text
.venv/Scripts/python.exe -m pip install -r requirements-test.txt
.venv/Scripts/python.exe -m unittest discover -s tests -p "*_unit.py"
.venv/Scripts/python.exe tests/test_stdio.py
```

It has **106 passing unit/regression checks**, including output/state paths, Setup XML preservation, DC plans, task freshness, callback lifetime, and complete 1/2/3/32-port Touchstone roundtrips through scikit-rf. The stdio test registered all 49 tools and built a small schematic. Protocol/integration scripts require ADS and create their own test outputs.

Native ADS validation:

- A new schematic-derived line completed DC + three RF Momentum points, generated a native model, and matched a two-port `MomCmpt` testbench with maximum complex error `1.77e-16` on equal RF grids.
- Antenna GUI full-band cosimulation completed; native Optim reproduced `FinalEF=0`, one iteration and seven evaluations, with `Wm=0.7327668436` and `Lm=20.5802420310`. A separate uncached geometry generated a new `data.008` sample. Optim completion uses a fresh final dataset plus stopped native cockpit evidence when no standalone completion log is produced.
- Final antenna dimensions were independently solved for actual mesh imagery. Historical full-band verification improved 2.4 GHz S11 from −26.357 dB to −48.315 dB; this is a single-frequency matching objective, not a gain/efficiency/bandwidth optimization.
- Phase-shifter reference: 179.94 degrees at 1.4 GHz, 0.250/0.341 dB state insertion losses. Ideal L/C, calibrated PIN assumptions and assumed pads apply; no new Optim was run on this design. The 40→80 mesh phase change was about 0.87 degrees and is not a strict convergence bound.
- Official AVX 3.9 pF selection, one top-level include and 21-point RF simulation completed. No vendor-model DC completeness is claimed.
- Native window tiling, maximize/restore, and mesh-object loading were exercised. Mesh loading is separate from refinement and solver completion.

Arbitrary PDK conversion, automatic lumped footprints, RFPro/FEM execution, general multi-sweep plans and multi-process task scheduling are outside the supported scope. No example constitutes fabrication, tolerance, or measured-hardware verification.

## Portability and release contents

Examples retain OA geometry and numerical `.sio/.sam/.ds/CSV` data. Machine-specific paths and host metadata in text files and nested solver-log ZIPs are replaced with generic paths/host labels. Run the rebind helper after copying; it does not patch OA binaries or delete sample caches. Generated runtime state and promotional assets are outside the release repository.

Public repository links, paper citations and license attribution remain intentional. See [review notes](REVIEW.zh-CN.md) for the release audit and [changelog](CHANGELOG.md) for changes.

## Repository traffic and contributing

![clones history](https://gist.githubusercontent.com/Lavaxzyh/ec682cff6f482626fa5348189838e66b/raw/clones-history.png)

Traffic collection retains recorded daily history and distinguishes pending observations from zeros; see [traffic definitions](docs/traffic.md). Report issues or propose improvements through GitHub. Future work includes broader PDK/version coverage and wider circuit-design coverage; v0.4.0 includes selected-session native operations.

## License and acknowledgments

[MIT](LICENSE). Keysight and ADS are trademarks of Keysight Technologies; use ADS and vendor data under their applicable licenses. Schematic audit/annotation placement draws on KiCad's bounding-box/candidate-position approach, without including KiCad source code. KiCad is copyright its contributors and licensed under GPL-3.0.
