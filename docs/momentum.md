# Native Momentum execution workflow

The three execution tools run a **saved native EM Setup** through an
ADS GUI session. They use the locally installed Keysight `bin/ads-mcp.exe`
bridge and the AEL `deem_run_em_setup` path verified on ADS 2027 (v6.5.0).
Keysight's executable, XML templates, OA designs and licenses are not included
in this repository.

Set `HPEESOF_DIR` to the local ADS installation directory if it is not under
the system `ProgramFiles/Keysight/ADS2027` default. The GUI bridge and circuit
tools use the same installation setting. `ADS_MCP_WORKROOT` controls circuit
example outputs; it does not change the existing workspace passed to Momentum.

v0.4.0 also provides four [planar EM modeling tools](em-modeling.md)
to create substrates, layouts, ports and setups. Build and save the project in
the MCP workspace, close it, then open it in ADS GUI for this execution flow.
The version contains 49 MCP tools in total, including the native Layout,
emModel, GUI-window and result-comparison tools documented in [v040-tools.md](v040-tools.md).

## Prepare the project

1. Create and save the layout, substrate, ports and native EM Setup using the
   modeling tools, or use an existing ADS design.
2. Open the workspace in the ADS GUI. Use a library inside the workspace,
   with simple library/cell/view names (letters, digits and underscores).
3. Use native calculation type `5`, `SimSetupOverview/simulator=0` (microwave),
   and one enabled linear RF sweep, optionally with a separate discrete DC point. This is
   the saved configuration tested with the Momentum microwave solver on ADS 2027.
   Other native calculation types and nonlinear/multiple sweeps are rejected.
4. Enable dataset output, disable reuse of previous results, and use local,
   immediate execution. The saved layout must be linked to that EM Setup.
5. If this MCP server opened a workspace, call `ads_close_workspace` before
   using Momentum. The GUI session owns the design for this workflow.

Construction tools accept explicit planar geometry and stack parameters;
automatic inference from arbitrary schematics/PDKs and RFPro/FEM execution
are not supported. Setup inspection is a configuration check, not a port or mesh accuracy assessment.
RF results can differ from circuit simulation because the physical models differ.

## Tool sequence

```python
ads_list_gui_sessions()
# Select the returned session whose workspace_path is the intended project.

ads_inspect_momentum_setup(
    workspace="<WORKSPACE>",
    setup_file="<SETUP_XML>",
)

ads_run_momentum(
    session_id=12345,  # use a discovered ID, not this placeholder
    workspace="<WORKSPACE>",
    setup_file="<SETUP_XML>",
)
# -> task_id

ads_get_sim_status(task_id="momentum_<returned-id>")
# Repeat until status == "done" or "error".

ads_get_results()        # latest completed task's dataset
ads_plot_sparams()
ads_export_touchstone()
```

`setup_file` must point to the **canonical EM Setup cellview**, identified by
its sibling `eesof_em_setup.file`. Do not use the generated XML copy under
`simulation/`. ADS encodes uppercase letters in OA directory names, e.g.
`emSetup` is stored in `em%Setup`.

Inspection rejects a setup whose saved workspace or local layout path points
elsewhere. After copying an engineering project, rebind and save the copied
setup in ADS before dispatch. The tools do not silently rewrite paths, change
the GUI workspace or save another design.

## Completion and frequency grid

Launching returns a task ID after the GUI accepts the command. Completion
requires a changed Momentum log identifying **microwave mode**, successful solver completion markers, and a
changed output dataset; copied/stale completed results do not qualify. The
dataset must contain the configured number of points and match the complete
linear frequency grid within **1 Hz**. No interpolation is used.

ADS writes to the output name configured in the native setup and can overwrite
its previous dataset. Use an engineering copy when the original must remain
untouched. After verified completion, the tools capture the dataset and log
under `.ads-mcp/results/<task_id>/` and return those per-task paths. The result
also contains `source_dataset` and `source_log` for the native output locations.
Subsequent runs reusing the same ADS dataset name cannot overwrite the captured
result of an earlier task.

The live test uses **0.05–4.00 GHz, 396 points, 0.01 GHz step**. These are example
settings, not hard-coded limits. Dataset summaries select an S-parameter
varblock (`data` for Momentum, commonly `SP1.SP` for circuit simulation) and
support single, unique frequency axes. Multidimensional sweeps must be selected
or reduced before result processing. Touchstone export supports complete N-port
matrices, requires the matching `.sNp` extension, and assumes 50-ohm reference impedance.

## Restart, uncertain dispatch and recovery

Momentum executes in ADS, independently of the MCP server. Use one MCP server
per state file. Keep the same state JSON file (default
`<USER_OUTPUT>/.ads-mcp/session_state.json`, or set `ADS_MCP_STATE_FILE`)
and continue polling the same task ID after restarting the server. Monitoring
does not redispatch a run. Interrupted in-process circuit tasks are reported
as errors on restart; they cannot be resumed this way.

Each run exclusively creates a JSON reservation under `.ads-mcp/locks/` in
the workspace (the inspector returns its `run_lock` path). ADS can rebuild the
generated simulation directory, so monitoring files are kept outside it. The
reservation prevents another server/client from dispatching the same setup
concurrently. It is removed after verified completion.

A GUI bridge timeout can mean the command was accepted before its response
was lost. Such calls return `dispatch_uncertain`; keep polling the returned
task and inspect ADS. The tools never automatically retry or kill the solver.
If no fresh log appears, or the log stops changing, polling reports
`attention_required`.

After a solver error or uncertain interrupted run, the reservation may remain.
**Inspect ADS and stop/finish that run first.** Only after confirming that the
solver is no longer active, remove that run's reservation JSON at the reported
`run_lock` path. Poll its task once more if it was still running; it will become an error and permit another
task. Removing the reservation is a recovery action, not cancellation.

## Reproducible command-line example

Run the demo using the same Python environment as the MCP server:

```bat
.venv\Scripts\python.exe demo\run_momentum.py --workspace <WORKSPACE> --setup-file <SETUP_XML> --session-id 12345 --state-file <STATE_JSON>
```

Add `--resume-task momentum_<returned-id>` with the same state file to monitor
an already dispatched task. The demo reads the result and exports PNG and
Touchstone output only after verified completion.

## 本版能力边界

v0.4.0 包含 49 个工具，新增原生 Layout、emModel、GUI 窗口和结果比较工具。
会话发现、配置检查和启动三个求解工具继续
复用状态查询、数据读取、绘图和 Touchstone 导出。

版图、衬底、端口和原生 EM Setup 可由建模工具创建，也可使用已有工程。
自动建模完成后先保存并关闭 MCP 工作区，再在 ADS GUI 中打开工程求解。
检查对象是 cellview 内的 `emStateFile.xml`，不是 `simulation` 下的生成副本。
复制工程后应在 ADS 中重新绑定工程路径；工具会拒绝残留的旧路径。

完成判定同时检查新日志中的微波模式、新数据集及完整频点网格，容差 1 Hz，不插值。
MCP 服务重启后可使用同一状态文件和 task_id 继续轮询。桥接超时不代表
仿真未启动，因此不会自动重试或终止 ADS。错误恢复前应先在 ADS 中确认
求解器已停止，再清理相应运行锁；具体步骤见上文。
