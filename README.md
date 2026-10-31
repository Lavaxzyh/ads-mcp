# ads-mcp — ADS 2025 power-amplifier workflows

[简体中文](README.zh-CN.md) · [Upstream project](https://github.com/Lavaxzyh/ads-mcp) · [MIT license](LICENSE)

This is the **ADS 2025 adaptation and RF/PA workflow branch** of [Lavaxzyh/ads-mcp](https://github.com/Lavaxzyh/ads-mcp), maintained on **`feature/power-amplifier`**. It builds on the author's v0.4.0 native schematic/Layout/Momentum/emModel foundation, preserves all **49 upstream tool names**, and registers **64 tools** in total.

**Branch release: `0.4.0+ads2025.20261030.3`**. This is a branch-specific version, not a new upstream release. Implementation ancestry is upstream commit `72e2793`; the contribution branch was prepared from `0181a5b` (the subsequent upstream change only updates a README badge).

The focus is practical ADS 2025 compatibility, explicit engineering inputs, repeatable PA-related analysis, and a compact **one task / one schematic** workflow. This is not an autonomous PA designer or an official Keysight product.

## Adaptations and enhancements

| Area | Branch capability | Boundary |
|---|---|---|
| ADS 2025 | Native API fixes, Python 3.12/PySide2, authenticated task-owned GUI bridge | Windows ADS 2025 is the validation target; arbitrary user GUI sessions are not taken over |
| Original operations | Schematic, native Layout/PSN, lookalike, Momentum, emModel and selected cosimulation paths | Retained tool names do not certify every operation on every ADS release |
| Load-pull | All stored points, explicit-condition comparison, Pout/PAE/gain filtering, frequency-specific impedance regions | Stored samples and interpolation are distinguished; numerical validity is not HB convergence |
| Matching | Sweeps, frequency-dependent targets, bounded coarse/fine search, nominal-value/manufacturing constraints, conservative caching | Explicit two-port SP; no automatic nonlinear-PA performance claim |
| Tolerance | OAT sensitivity, corners and seeded Monte Carlo | Specified independent-parameter assumptions; not measured production yield |
| Existing HB review | Explicit supported-template operating-point/candidate review | Requires supplied models, probes, terminations and authorization; not an arbitrary HB/PDK generator |
| Single schematic | Short templates, one-time import, same-copy edits, change review, save/reopen checks and compact receipts | Originals remain read-only in this workflow; engineering conditions are not silently replaced |
| Durable execution | Stable IDs, task/workspace locks, status-change waits, RF checkpoints and bounded retries | Timeout does not automatically cancel or resubmit a task |
| Self-check | Local version, loaded/disk fingerprints, actual catalog and environment | No automatic restart of other sessions |

The official ADS 2027 bridge route remains where applicable. **This branch has not been fully regression-tested on ADS 2027**; upstream main retains the author's original validated environment. See [validation and limitations](docs/VALIDATION.zh-CN.md).

## Installation

Use Windows and a licensed ADS 2025 installation. Native validation used ADS 2025 `610.shp`, ADS Python 3.12.2 and PySide2 5.15.10. Native Keysight packages must match the bundled interpreter and are not redistributed. Momentum/GUI operations require the corresponding license and a task-owned session.

Replace placeholders with your own absolute paths; keep outputs outside the checkout.

```powershell
git clone --branch feature/power-amplifier https://github.com/Lavaxzyh/ads-mcp.git
cd ads-mcp
& '<ADS_INSTALL>/tools/python/python.exe' -m venv --system-site-packages .venv
& '.venv/Scripts/python.exe' -m pip install -r requirements.txt
```

Until this contribution is merged, use the contributor's fork URL or reviewed archive; do not assume upstream already includes this code.

```json
{
  "mcpServers": {
    "ads-mcp": {
      "command": "<REPO>/.venv/Scripts/python.exe",
      "args": ["-B", "<REPO>/mcp_server.py"],
      "env": {
        "HPEESOF_DIR": "<ADS_INSTALL>",
        "ADS_MCP_WORKROOT": "<OUTPUT_ROOT>",
        "ADS_MCP_STATE_FILE": "<OUTPUT_ROOT>/.ads-mcp/session_state.json"
      }
    }
  }
}
```

`HPEESOF_DIR` takes precedence; the generic fallback is Program Files/Keysight/ADS2025. All three CLI clients use the same resolver. Test-only dependencies are in `requirements-test.txt`.

## Daily use

Tell the assistant which schematic and inputs to use, the requested changes and whether simulation is authorized. Read existing information, bind one independent task and schematic, reuse its dedicated session, then report changes, acceptance, timing and evidence paths.

Use `ads_design_workflow(action="run", task_directory=..., request_file=...)`: build/import once, then edit the same owned copy. Requests and stable-ID rules are in [SINGLE-SCHEMATIC.zh-CN.md](SINGLE-SCHEMATIC.zh-CN.md).

```powershell
& '<REPO>/.venv/Scripts/python.exe' -B '<REPO>/design_workflow_client.py' --task-dir '<TASK_ROOT>' --ads-root '<ADS_INSTALL>' --interactive
```

Run `ads_runtime_selfcheck` when starting or updating. An existing process may still have old code; use a new dedicated client or reconnect that idle client without restarting unrelated sessions.

## Documentation and verification

- [One schematic](SINGLE-SCHEMATIC.zh-CN.md), [extraction/reviewed copies](DESIGN-WORKFLOW.zh-CN.md), [full BuildPlan](SCHEMATIC-WORKFLOW.zh-CN.md).
- [Load-pull/scans](RF-WORKFLOWS.zh-CN.md), [targets/search/tolerance](RF-ADVANCED.zh-CN.md), [RF jobs and HB review](RF-JOBS.zh-CN.md).
- [ADS 2025 native EM and owned GUI](EM-WORKFLOW.zh-CN.md).
- [Validation and efficiency](docs/VALIDATION.zh-CN.md), [privacy](docs/PRIVACY.zh-CN.md), [contributing](CONTRIBUTING.md).

Run `python -B tools/run_unit_tests.py`. This publication passed **201 unit tests** and a **real ADS 2025 synthetic build/edit/save/reopen check**. Historical SP/EM evidence is reported separately. Original upstream examples retain their provenance and are not all revalidated on this branch.

In the preceding local efficiency release, a prepared build + two edits + rejected edit took median 8.12 s versus 9.52 s with a reused session (n=3); calls fell from 7 to 4 and returned UTF-8 text from 5,525 to 2,158 bytes. Cold startup did not improve. These are bounded scripted measurements, **not complete RF-design time or actual model-token savings**. The public fixture uses different synthetic example values and does not reproduce that historical timing workload.

## Privacy and attribution

No private PA projects, device kits, credentials, licenses, chat history, virtualenvs or local validation logs are included. New schematic examples use synthetic values. Runtime outputs may contain paths, model names and engineering data; review them separately before sharing.

Credit for the original architecture and native ADS workflows goes to **Lavaxzyh and upstream contributors**. This branch adds ADS 2025 adaptation and the workflows above. The [MIT license](LICENSE) is preserved; Keysight software and third-party models remain subject to their own licenses.
