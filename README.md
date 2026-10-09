# ads-mcp

![Clones](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/Lavaxzyh/ec682cff6f482626fa5348189838e66b/raw/clones.json) ![Views](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/Lavaxzyh/ec682cff6f482626fa5348189838e66b/raw/views.json) ![Installs](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/Lavaxzyh/ec682cff6f482626fa5348189838e66b/raw/installs.json)

**An open, headless-first MCP server for Keysight ADS** — build RF/microwave schematics, netlist, simulate, and read S-parameters without ever opening the GUI.

English · [简体中文](README.zh-CN.md)

![Python](https://img.shields.io/badge/Python-3.14_(ADS_bundled)-3776AB?logo=python&logoColor=white)
![MCP](https://img.shields.io/badge/Protocol-MCP_stdio-8A2BE2)
![ADS](https://img.shields.io/badge/Tested_on_Keysight_ADS_2027-red)
![License](https://img.shields.io/badge/License-MIT-green)
![Tools](https://img.shields.io/badge/Tools-25-4c1)

> Not affiliated with or endorsed by Keysight Technologies. Requires your own licensed ADS installation — this repo ships no Keysight code or binaries.

## Why This Exists

ADS 2027 ships an official MCP server (`bin\ads-mcp.exe`): closed-source, and built as a **thin generic REPL** — one `execute_python` tool over the full ADS Python API. Powerful, but the agent must already know the API and write code for every step.

**ads-mcp** takes the opposite approach: the design workflow itself is encoded as **25 named, schema-documented tools** with guardrails, so an agent that has never seen the ADS Python API can run the full loop — schematic → netlist → `hpeesofsim` → S-parameters — conversationally.

| | Official `ads-mcp.exe` | This project |
|---|---|---|
| Source | obfuscated binary | open, auditable |
| Philosophy | thin REPL over full API | structured domain tools |
| Agent needs ADS Python? | yes | no |
| Workflow guardrails | — | substrate naming, param protection, param tables |
| Long simulations | — | task id + polling, no blocked tool calls |
| Session state | in-memory | persisted JSON state machine |
| Works without GUI | local session, license checkout | headless automation mode, always |

Both are stdio MCP servers; register both side by side if you like — this project's 25 tools cover the design loop, the official `execute_python` covers everything else.

## Highlights

- **Headless-first** — the ADS GUI never needs to open; open it only at the end to admire the result (`ads_open_in_gui`).
- **25 domain tools** — every step of the microstrip design loop is a named tool with a documented schema; see the tool surface below.
- **Guardrails built in** — substrate auto-naming, string-reference-parameter protection, authoritative component/parameter tables via `ads_browse_components`.
- **Async simulation** — `ads_run_simulation` returns a task id immediately; poll with `ads_get_sim_status`. No 10-minute tool calls, no killed solves.
- **Persistent session state** — a JSON state machine (idle → building → simulating → analyzing) survives server restarts and records every task.
- **Real exports** — Touchstone `.s2p`, matplotlib PNG plots, dataset → pandas.
- **Topology library** — Butterworth/Chebyshev LC ladders (LPF/HPF/BPF/BSF) synthesized from g-parameters and built headlessly, every cell verified against theory.
- **Schematic audit** — KiCad-style overlap audit (`ads_audit_schematic`) plus annotation auto-placement (`ads_autolabel`) keep delivered schematics clean: zero body collisions, no dangling wire ends, no text-on-symbol grazes.

## Quick Start

Prerequisites: Windows with a licensed Keysight ADS installation — verified end-to-end on ADS 2027 (v6.5.0); uses the `keysight.ads.de` Python API that Keysight has provided since ADS 2025U2, so earlier versions are untested. The native DE engine is cp314-only — the ADS-bundled Python 3.14 is required.

```bat
git clone https://github.com/<you>/ads-mcp.git
cd ads-mcp

:: venv on the ADS-bundled interpreter (reuses the ADS Python packages)
"C:\Program Files\Keysight\ADS2027\tools\python\python.exe" -m venv --system-site-packages .venv
.venv\Scripts\python.exe -m pip install "mcp<2" matplotlib pandas
```

### MCP Client Config

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

Restart your client (Claude Desktop / ZCode / Cursor / …) — the 25 `ads_*` tools appear.

Try it without an agent:

```bat
.venv\Scripts\python.exe demo\step_by_step.py
```

This narrates a complete filter build (15 steps), simulates, exports, and opens the result in the ADS GUI.

## Tool Surface

### Environment & session (5)

| Tool | What it does |
|---|---|
| `ads_check_installed` | Verify the ADS install, `hpeesofsim.exe`, and the headless DE engine. First call of a session. |
| `ads_create_workspace` | Create an ADS workspace at a path and open it (opens instead of failing if it exists). |
| `ads_open_workspace` | Open an existing workspace. |
| `ads_close_workspace` | Close the current workspace. |
| `ads_session_status` | Snapshot of session state: state-machine phase, open workspace, current schematic, past simulation tasks. |

### Schematic construction (9)

| Tool | What it does |
|---|---|
| `ads_create_library` | Create a design library inside the open workspace and attach it. |
| `ads_create_schematic` | Create/open schematic cell `lib:cell` as the current design — subsequent placement tools act on it. |
| `ads_browse_components` | Enumerate component libraries/cells with authoritative parameter names (microstrip family in `ads_tlines`, controllers/ports in `ads_simulation`, VAR in `ads_datacmps`). Call before placing unfamiliar parts. |
| `ads_place_component` | Place an instance at (x, y) with optional name/rotation/parameter dict. MSUB is auto-named `MSub1`; string-reference params (`Subst`) are refused to protect netlist quoting. |
| `ads_add_wire` | Add a polyline wire, optional net label. |
| `ads_add_var` | Define a schematic variable in the VAR block; component parameters reference it by name. |
| `ads_add_term` | Place an S-parameter port (Term, 50 Ω) with its ground; auto-numbers the port from the name. |
| `ads_add_ground` | Place a ground symbol. |
| `ads_list_instances` | List everything placed on the current schematic, to confirm the build. |

### Simulation (3)

| Tool | What it does |
|---|---|
| `ads_generate_netlist` | Save the schematic and return the netlist text — the most reliable mid-build sanity check. |
| `ads_run_simulation` | Save → netlist → run `hpeesofsim` in a background thread; returns a `task_id` immediately. |
| `ads_get_sim_status` | Poll a simulation task; on completion carries the dataset (`.ds`) path. |

### Optimization (2)

| Tool | What it does |
|---|---|
| `ads_add_goal` | Add an optimization goal: expression (e.g. `dB(S(1,2))`), keep-above (`min`) or keep-below (`max`) bound, evaluation band, weight. |
| `ads_add_optim` | Place the optimization controller (gradient/hpVMO, random, quasinewton, …); auto-collects every variable that has an opt range and every goal. |
`ads_add_var` gains optional `opt_min`/`opt_max` — variables with a range become optimization variables.



### Schematic audit (2)

| Tool | What it does |
|---|---|
| `ads_audit_schematic` | KiCad-style overlap audit of a saved or current schematic: pairwise instance bounding boxes (body vs annotation text) plus wire segments, graded by severity (body-body / body-text / text-text / text-wire / body-wire) with connectivity-aware exemptions and dangling-wire-endpoint (ERC-style) detection. |
| `ads_autolabel` | Auto-places instance annotations to clear text-level collisions: for each clash, tries candidate sides (right/left/up/down, least-displacement first) and moves only the annotation — never the component. |

### Results & export (4)

| Tool | What it does |
|---|---|
| `ads_get_results` | Read a dataset: per-S-parameter dB min/max/band-edge, first −3 dB crossing (cutoff hint), downsampled magnitude/phase preview. |
| `ads_export_touchstone` | Export S-parameters as a standard `.s2p` (HZ S RI R 50). |
| `ads_plot_sparams` | Plot \|S\| dB vs frequency (selectable columns) to a PNG. |
| `ads_open_in_gui` | Open the current workspace in the ADS GUI (detached process, survives server restarts). |

Current MCP registration: **25 tools**.

## Example Workflow

Design a 5-element step-impedance microstrip low-pass filter on FR4, ~1 GHz cutoff:

```text
ads_check_installed()
ads_create_workspace(path="~/ads_mcp_demo/lpf_wrk")
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
ads_plot_sparams(columns=["S[1,1]", "S[1,2]"])
ads_export_touchstone()
ads_open_in_gui()                                       # admire the schematic
```

The full narrated version of exactly this build lives in [`demo/step_by_step.py`](demo/step_by_step.py).

## Requirements

- Windows with a licensed Keysight ADS installation — verified end-to-end on ADS 2027 (v6.5.0); uses the `keysight.ads.de` Python API provided by Keysight since ADS 2025U2; earlier versions untested
- ADS-bundled Python 3.14 (for the `keysight.ads.de` native engine); wheels for result reading (`keysight.ads.dataset` cp310–cp314) ship in the ADS wheelhouse
- An MCP client (Claude Desktop, ZCode, Cursor, or any stdio-MCP host)

## Verification

End-to-end acceptance (MCP-level, `tests/test_full_flow.py`) on ADS 2027:

- ✅ 25 tools registered and callable over stdio
- ✅ conversational build: 13 instances placed, netlist verified, simulation submitted/polled via task id
- ✅ SI-LPF response: passband −0.08 dB, −3 dB cutoff **1.025 GHz** (design target 1 GHz), stopband −38 dB @ 6 GHz
- ✅ Touchstone + PNG exports, GUI hand-off
- ✅ LC topology library: 8 filters (LPF/HPF/BPF/BSF × Chebyshev/Butterworth) built and verified — Chebyshev LPF in-band ripple 0.10 dB, Butterworth cutoff 1.00 GHz, 0 schematic collisions per cell

## Roadmap

- [ ] Parameter sweeps & MeasEqn tools
- [x] Optimizer hooks (geometry → spec closed-loop tuning)
- [x] Schematic audit & annotation auto-placement (KiCad eeschema-inspired)
- [ ] EM (Momentum) flow
- [ ] Multi-session registry (attach/audit concurrent ADS processes)
- [ ] API doc search tool (complements the official server's `search_docs`)
- [ ] Linux support where ADS supports it

## Community

Issues and PRs welcome — the codebase is small on purpose: three modules, no framework. If you build a design flow on top (synthesis, PCB co-sim, load-pull…), a demo script contribution is the best PR.

## Acknowledgments

The overlap audit (`ads_audit_schematic`) and label auto-placement (`ads_autolabel`) follow the approach of KiCad's eeschema field auto-placement (`eeschema/autoplace_fields.cpp`): bounding-box collision detection with severity-graded candidate placement. The algorithm idea was referenced, not copied — no KiCad code is included, and this project remains MIT. KiCad is © its contributors, licensed under GPL-3.0.

## License

[MIT](LICENSE) — Keysight and ADS are trademarks of Keysight Technologies; this project only calls the public Python API shipped with your licensed installation.
