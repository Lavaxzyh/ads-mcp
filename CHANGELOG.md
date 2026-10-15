# Changelog

## 0.3.0 (Unreleased)

### Added

- `ads_list_gui_sessions`, `ads_inspect_momentum_setup`, and `ads_run_momentum`:
  28 registered MCP tools in total.
- Native Momentum execution through a selected ADS GUI session using the
  installed Keysight bridge. Requires an existing saved local EM Setup.
- Persisted external-run monitors, live frequency progress, duplicate-run
  reservations, fresh log/dataset checks and full linear-grid verification.
- Per-task dataset/log captures so later ADS runs reusing a dataset name do not
  overwrite a completed task's result.
- `demo/run_momentum.py`, workflow/recovery documentation and regression tests.

### Fixed

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
- Document Momentum's ADS GUI/session requirement and the current validated
  scope; do not claim automatic layout/substrate/setup generation or RFPro/FEM.

## 0.2.0

- 25 tools covering schematic construction, circuit simulation, optimization,
  schematic audits, annotation placement and result processing.
- LC topology examples and schematic audit workflow.
