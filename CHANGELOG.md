# Changelog

## 0.4.0+ads2025.20261030.3 — ADS 2025 contribution branch

- Preserve the upstream v0.4.0 tool names and native workflow foundation; add ADS 2025 compatibility and task-owned GUI support.
- Include the existing Load-pull, matching search, tolerance, bounded HB review, native EM, durable RF jobs and single-schematic workflows developed on this branch.
- Make ADS installation resolution configurable across the three CLI clients and remove maintainer-specific paths from the publication copy.
- Provide synthetic public schematic inputs, explicit validation boundaries and source/runtime privacy guidance.
- Publication checks: 201 unit tests and a real ADS 2025 build/edit/save/reopen smoke test. Historical SP/EM evidence is identified separately; full ADS 2027 regression was not performed.

## Unreleased

### Repository traffic

- Collect traffic every three hours and retain all recorded daily history.
- Merge delayed API observations by date without adding repeated snapshots
  twice; preserve earlier cumulative totals when daily detail is incomplete.
- Distinguish pending data from API-reported zeros and plot cumulative raw
  clone events with collection timestamps.
- Stop before publishing if saved history is unavailable, and render from
  the exact collected snapshot rather than a cached raw Gist response.

## 0.4.0

### Added

- 17 native workflow tools (49 total): inspect/open designs, connected Subnetwork terminals, instance parameters and simulation views, formal cell parameters, native Layout and Dual/Quad/lookalike symbols, vendor library registration, native emModel output/cache policy, mesh/frequency/port configuration, completed-optimizer release, ADS window arrangement and mesh visibility, and full-matrix result comparison.
- GUI circuit simulation and native Optim dispatch through the selected installed ADS bridge, with persisted monitors and archived fresh datasets/logs. Standalone simulation remains the default.
- Two independent native example workspaces: 1.4 GHz reflection phase-shifter cosimulation and complete-layout patch antenna EM optimization. Paper attribution, scope and ideal-component assumptions accompany the phase shifter.
- Official AVX model downloader/provenance and import guide; no vendor kit or paper PDF redistribution.
- Promotional material is prepared separately from the source distribution; `promo/` is not included in the repository.

### Fixed

- Generate Layout from existing schematic artwork instead of treating coordinate-drawn geometry as schematic conversion. Bind Subnetwork terminals to actual instance nets.
- Support complete N-port Touchstone export, including 32-port models, preserving full numerical precision and standard matrix ordering.
- Support one RF linear sweep with optional native discrete DC, internal uncalibrated ports and via substrate mappings.
- Recognize native Momentum error banners, bound configuration-history filenames on Windows, and retain GUI dispatch callbacks until Qt executes them.
- Separate mesh configuration, solver recomputation and mesh display. Configuration changes require workspace reload and a new solve; existing caches are not silently invalidated.
- Rewrite both README tool tables against all 49 registered tools and update examples, N-port export, validation, and capability boundaries.
- Replace machine-specific example metadata and nested solver-log host paths with generic release paths while preserving OA binaries and numerical solver data.
- Store default task state under the user output root instead of a repository `state/` directory; explicit `ADS_MCP_STATE_FILE` still takes precedence.

## 0.3.0 (2026-10-15)

### Added

- Four parameterized construction tools: `ads_create_substrate`,
  `ads_create_layout`, `ads_add_layout_ports`, and
  `ads_configure_momentum_setup`.
- `ads_list_gui_sessions`, `ads_inspect_momentum_setup`, and `ads_run_momentum`:
  seven new MCP tools since v0.2.0, with 32 registered tools in total.
- Bottom-to-top dielectric stacks, conductor mappings and material parameters,
  rectangle/polygon geometry in millimeters, and numbered layout pins.
- Native microwave setup creation from the locally installed RFBoard template,
  with linear sweeps and 50-ohm ground-referenced ports. No vendor templates
  or engineering binaries are included in this repository.
- `demo/build_momentum.py` and a parameterized JSON microstrip example covering
  construction, saved-workspace GUI handoff, solving, and result export.
- Native Momentum execution through a selected ADS GUI session using the
  installed Keysight bridge. Supports newly constructed or existing saved
  local EM Setups.
- Persisted external-run monitors, live frequency progress, duplicate-run
  reservations, fresh log/dataset checks and full linear-grid verification.
- Per-task dataset/log captures so later ADS runs reusing a dataset name do not
  overwrite a completed task's result.
- `demo/run_momentum.py`, workflow/recovery documentation and regression tests.

### Fixed

- Use ADS append mode for edits to preserve saved layout geometry.
- Use the ADS launcher and workspace working directory for GUI startup.
- Check the saved mode and the actual solver log for Momentum microwave mode,
  rather than relying only on the legacy calculation-type field.
- Replace machine-specific installation/workspace paths with `HPEESOF_DIR`,
  the system Program Files default, and a configurable `ADS_MCP_WORKROOT`
  (defaulting to `ads_mcp_demo` under the current user's home directory).
- Integration scripts inherit the configured ADS environment and use the
  interpreter that runs them instead of a fixed repository virtual environment.
- Select the latest completed task's dataset instead of the first task.
- Select S-parameter varblocks in both circuit and Momentum datasets, sort
  their frequency axes, and reject ambiguous or invalid axes.
- Export two-port Touchstone in S11, S21, S12, S22 order.
- Bound data previews to 101 points while retaining both frequency endpoints.
- Plot the dataset's actual frequency band instead of extending to zero.
- Report interrupted in-process circuit jobs as errors after server restart.

### Documentation

- Describe design, simulation and result-processing capabilities without
  presenting absence of GUI as a product feature.
- Document parameterized planar modeling, Momentum's ADS GUI/session requirement,
  and the validated scope, including current port-reference and geometry limits.

### Tag revision

- This v0.3.0 revision includes the four modeling tools alongside the original
  Momentum execution tools. The earlier 28-tool snapshot remains in history at
  commit `33171ab`. Clones holding the initial v0.3.0 tag need to delete that
  local tag and fetch it again after the updated tag is published.

## 0.2.0

- 25 tools covering schematic construction, circuit simulation, optimization,
  schematic audits, annotation placement and result processing.
- LC topology examples and schematic audit workflow.
