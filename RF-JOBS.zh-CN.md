# 多指标优化、HB 复核与持久化 RF 任务

新增 `ads_submit_rf_job(workflow="sp", request_file=...)`：对给定网表执行一次原样线性 SP 复核，沿用持久队列和进程隔离，无需扫描变量。输入为 `netlist_path`、`evaluation`、全新 `output_dir`，可选 `timeout_seconds`（1–120）及 `data_directory`。原理图副本与仿真证据衔接见 `DESIGN-WORKFLOW.zh-CN.md`。

本地 ADS 2025 扩展。输入为已导出的网表；仿真发生在新的独立输出目录，不回写原理图。SP 与 HB 是两类不同的证据，SP 达标不代表功放的大信号指标达标。

## 推荐调用方式

1. 将工作流参数写入一个本地 UTF-8 JSON 文件。`examples/rf_jobs/` 提供输入示例。
2. 调用 `ads_submit_rf_job(workflow="optimize", request_file="<TASK_ROOT>/request.json")`。`workflow` 可选 `optimize`、`hb`、`search`、`sweep`、`tolerance`。
3. 保存返回的 `job_id`。调用 `ads_rf_job(action="wait", job_id="rf_...", after_revision=<上次 revision>, wait_seconds=20)`。一次等待最多 25 秒，超时只结束观察，不能因此重复提交。
4. 完成后默认只返回简要结论和输出路径。用 `ads_rf_job(action="detail", job_id="rf_...", limit=5, failures_only=true)` 读取失败或不合格案例。`fields` 可选择 `case,status,parameters,error,feasible,constraint_violation,criteria,max_error_ohm,rms_error_ohm,objective_score,objective_vector,cache_hit,resumed,pout_dbm,pae_pct,gain_db,convergence,harmonics,conditions,directory,dataset,points_csv`。
5. 全量精度与日志保存在 `summary.json`、`cases.json`、各 `case_XXXX` 子目录及 `checkpoints`，不必在每次对话中传回全部数据。

原有 `ads_search_matching_network`、`ads_run_matching_sweep`、`ads_analyze_matching_tolerance` 也已进入该队列；原有 `ads_get_sim_status(task_id)` 继续提供兼容状态与完整结果。为了减少返回体积，新调用优先使用 `ads_rf_job`。

当前连接尚未刷新工具列表时，可直接通过已安装客户端调用同一 MCP：

```powershell
& '<REPO>/.venv/Scripts/python.exe' -B '<REPO>/rf_workflow_client.py' '<TASK_ROOT>/call.json' --ads-root '<ADS_INSTALL>'
```

`call.json` 示例：

```json
{"tool":"ads_submit_rf_job","arguments":{"workflow":"optimize","request_file":"<TASK_ROOT>/optimization.json"}}
```

客户端会自动等到任务完成，输出简要结果。查询/暂停/取消时，把 tool 改为 `ads_rf_job`，arguments 填相应 action 与 job_id。任务保存在同一个 `ADS_MCP_WORKROOT/rf_jobs_v2`；使用不同 workroot 会看到不同队列。

## 多指标约束优化

`optimize` 的基础字段与原有 search 相同：`netlist_path, parameters, evaluation, output_dir, timeout_seconds, data_directory, cache_dir, use_cache, stop_on_error`。`settings` 改为：

| 字段 | 含义 |
|---|---|
| evaluation_budget | 最多评价多少个不同参数组合，包含缓存命中；默认 48，最大 256 |
| simulation_budget | 最多发起多少次 ADS 仿真，失败尝试也计数；默认 32，最大 256 |
| seed | 可复现的初始分层采样种子 |
| initial_fraction | 初始采样占比，默认 0.4 |
| refine_rounds / refine_top | 在优选方案附近缩小范围细化的轮数/方案数 |
| stop_when_feasible | 达到全部硬约束即停止，默认 false |
| wall_seconds | 每次执行在案例之间检查的时间预算；正在执行的案例另受 timeout_seconds 限制 |
| geometry_constraints | 仿真前检查的线性尺寸/参数约束，避免为明显不合格组合运行 ADS |

参数继续支持 minimum/maximum、离散 allowed_values、E12/E24 或制造 step。不会把标称数值自动解释成可生产器件，也不做布局 DRC。

`evaluation` 保留目标阻抗/目标表/Load-pull 区域、参考面、频段和端口条件；新增 `source_r_ohm,source_x_ohm,criteria`。计算 transducer_gain_db 时必须明确源阻抗，不能默认为 50 Ω。

每个 criterion 包含 `name,metric,aggregate,lower,upper,objective,target,scale,weight`。lower/upper 是硬约束；objective 为 minimize/maximize/target/none；只有 target 类型填写 target。scale 是该指标的归一化尺度，weight 是目标权重。不同单位不要直接裸相加。

| metric | 定义与边界 |
|---|---|
| impedance_error_ohm | 到目标阻抗或阻抗区域的距离，仅在已有明确目标的频率评价 |
| input_return_loss_db | −20log10|S11|，相对于网表的实数参考阻抗；不是失配负载下的实际输入回波 |
| output_return_loss_db | −20log10|S22|，相对于网表参考阻抗 |
| matched_s21_db | 20log10|S21|，匹配参考端口条件 |
| transducer_gain_db | 实际指定源/负载阻抗下的负载功率 / 源可用功率，10log10(GT) |
| operating_gain_db | 指定负载下的负载功率 / 网络净接受输入功率，10log10(GP)；输入净接受功率不正时不可用 |

aggregate 支持 min/max/mean/rms/ripple（最大值减最小值）。默认采用 evaluation 频段，也可在单个 criterion 中设置 start_hz/stop_hz，用于通带、带外抑制或谐波频带。网表必须实际仿真覆盖这些频段，工具不外推。

排序先看全部约束是否合格；不合格方案按归一化违约量排序；合格方案再按加权目标排序。summary 同时保留可行 Pareto 候选。算法为分层初始采样加多轮局部细化，不保证全局最优。没有找到合格方案时，任务可以完成，但 feasible_cases 为 0，不能将 best_case 当作合格设计。

geometry_constraints 形式为 `{"name":"总线宽","coefficients":{"top/W1":1,"top/W2":1},"upper":4,"unit":"mm"}`。系数作用于参数声明单位下的数值，不自动换算；调用方必须保证量纲一致。只支持线性表达式，不执行任意代码。

容差分析在设置了 criteria 硬约束时，会同时检查这些约束；失败/未运行的样本不计为通过。统计通过比例仍不等于实际制造良率。

## HB 大信号复核

`hb` 需要一个完整、可独立运行的单音 HB 模板网表，且不带内置 Sweep/Optim/Transient 等其他控制器。工具负责逐个改变频率、可用输入功率与指定偏置。谐波阶数来自模板并与 contract 对照，不会悄悄修改。

必填字段为 `netlist_path, contract, operating_points`，输出目录必须新建。可选字段为 `candidates_json,candidate_ids,mappings,data_directory,dependency_files,timeout_seconds,stop_on_error`。

- contract.frequency_target：顶层变量，单位 Hz；HB 控制器 Freq[1] 必须直接引用它。
- contract.drive_target：顶层无单位变量，数值表示 dBm，模板源应通过 dbmtow 使用它。
- contract.bias_targets：偏置参数的列表，顺序对应每个 operating_point.bias_values。
- 每个 operating_point 为 `{"freq_hz":935200000,"pavs_dbm":10,"bias_values":[4.8,-2.75]}`。
- 明确 input_voltage/input_current、output_voltage/output_current。电流方向默认 +1；可用 input_current_sign/output_current_sign 改为 −1。必须对应真实网表上的探针和参考面。
- supplies 是直流电压/电流探针对；delivered_current_sign 表示电流送入被测电路的方向。所有供电都应纳入。
- 明确 input_reference_plane、output_reference_plane、network_boundary、model_conditions、source_termination、load_termination、harmonic_terminations。这些说明是调用方提供的条件，不会假称工具已识别全部物理连接。
- limits 支持 min_pout_dbm、min_pae_pct、min_gt_db；没有阈值时只能核算指标，不能声明符合设计规范。
- stress_voltage/stress_current 可选，用于从谐波重建 4096 个等相位波形样本的最小/最大值；不代表器件额定值检查或连续时间严格极值。

复核 SP 搜索候选时，candidates_json 使用搜索输出的 cases.json，candidate_ids 明确选择案例，mappings 将源参数键映射到 HB 网表中的 ParameterTarget。例如将 `top/Lmatch` 映射为 `ClassC_Amp_wBothMatches/L2.L`，并声明 nH。scale 可用于显式单位换算。该映射只证明数值代入正确，网络边界的一致性仍需调用方核对。

基波功率使用峰值相量的 0.5×Re(V×I*)；直流功耗使用 ΣVdc×Idc；PAE=(Pout−Pin_delivered)/Pdc；gain_db=Pout_dBm−Pavs_dBm，另提供 delivered_gain_db。保留各谐波功率与 dBc。输入反向净功率、供电符号异常、非有限数据、缺少谐波和功率平衡失败都会拒绝该案例。

完整数据集不是收敛证明。当前接受条件包括原生模拟器正常返回、HB 日志完成且无 Warning/Error/非收敛/容差异常、频率和谐波轴完整、探针有限及功率自洽。输出明确记为 solver_accepted，不宣称已经完成谐波阶数收敛研究、稳定性分析或实测验证。

P1dB 仅在最低两个采样输入功率的增益相差不超过 0.2 dB，且后续点夹住 1 dB 压缩交叉时给出线性插值值；否则说明基线不足或未夹住，不外推。Class-C 示例可能不具有可用的小信号基线。

HB 不启用仿真缓存。模板、候选表和 dependency_files 的哈希在派发前后核对；外部模型与数据依赖必须明确提供，未声明的外部文件变化不能由工具完整识别。candidate_review 以“该候选全部工作点都达标”为判断依据；best_case 只是一个工作点。

## 长任务与故障恢复

- SQLite WAL 保存队列、请求、状态、进度和结果。独立调度进程使任务在 MCP 客户端退出后继续执行。同一 workroot 的 RF 任务串行运行。
- Windows 通过本机 WMI 以当前账户启动隐藏的独立进程，避免 MCP SDK 的进程组随连接关闭而退出。不安装服务、计划任务，不请求管理员身份。依赖本环境已有的 psutil 与 pywin32；WMI 权限不足会明确报错。
- pause：完成当前案例并保存校验信息后暂停，释放队列。resume：重新核对源码、ADS 核心/配置、源网表及已完成结果，再复用案例；不会把等待超时当作失败重跑。
- cancel：只终止该任务登记的进程及其子进程，使用 PID 和创建时间核对身份。不会按进程名批量结束 ADS。
- 调度进程异常退出后，只有确认它及登记的原生工作进程已经退出才标记 interrupted，需显式 resume。仍活着的子进程不能再次派发。
- 已完成案例写入独立 checkpoint，避免每个案例都重写整个列表；最终生成 cases.json 与排序表。失败及中断目录保留，恢复不覆盖原尝试。
- 每次仿真在启动前先持久化预算计数，中断尝试也占用 simulation_budget。若剩余预算不够，不会借恢复任务超出预算；需要增加预算时，应创建新的请求。HB 的 256 上限限制独立工作点数，显式恢复造成的额外尝试会单独计数。
- 每个任务复用一个 Python 仿真宿主；各次 hpeesofsim 仍独立、串行。缓存继续使用内容哈希和原始复数 S 参数，目标变化时重新评分。
- 全局桌面 ADS/GUI 仿真不属于这个 RF 队列；本扩展不会终止这些进程，也不声称对其他 workroot 或原作者 GUI/Momentum 工具有跨队列资源锁。
- 不实现自动无条件重试；错误、失败、取消、中断、暂停、完成各自区分。完成但指标不达标也会明确报告。

## 借鉴来源

采用约束优先和 Pareto 支配的通用思路，参考 [Optuna 的约束比较实现](https://github.com/optuna/optuna/blob/master/optuna/samplers/nsgaii/_constraints_evaluation.py)；任务状态与持久化句柄参考 [RQ Job 文档](https://python-rq.org/docs/jobs/) 和 [MCP Tasks 扩展](https://tasks.extensions.modelcontextprotocol.io/)。当前是本 MCP 自带任务工具，不假定客户端已支持原生 Tasks 扩展；无需 Redis、Optuna 或云端服务。加速和返回体积以本机同条件测试为准，不把某个算法名称当作性能保证。
