# 一次一个原理图：现有功能的精简工作流

本地发布 `0.4.0+ads2025.20261030.3`，上游仍为 v0.4.0，运行于本机 ADS 2025。此版本优化现有建图、修改、检查和线性 SP 的组织方式，没有新增 HB、PDK、多端口或新的射频设计能力。

## 默认使用方式

```txt
使用 ADS MCP 的单原理图工作流完成本次任务。
本次只处理［目标原理图］，目标是［新建／修改指定参数／检查／按指定条件仿真］。
先读取已有条件，绑定一个独立任务目录和一个目标原理图。
复用同一专用会话；按已有模板和必要修改项执行，保存并核对结果。
原工程只读，不自动改变拓扑、板材、器件模型、参考面或端口条件。
默认只返回本次变化、验收结论、关键指标、耗时及详细记录路径。
```

一个任务目录只有一个 `workspace/library/cell/schematic` 身份。新建时绑定；已有工程通过 `import` 复制一次。后续 `edit` 修改同一独立原理图，不再每改一个参数就增加工作区。多个原理图应拆成有顺序的任务。完成任务后退出本任务专用客户端，避免长期占用内存；本流程不打开 GUI 窗口。

优先使用现有 `ads_design_workflow(action="run", task_directory="绝对路径", request_file="绝对路径")`。原有 build/export/plan_patch/apply_patch 等入口保留兼容；需要保留多个完整候选工程时才明确选择原复制工作流。EM 原生接口继续按 EM-WORKFLOW.zh-CN.md 使用，本入口不把任意 EM/GUI 工程自动纳入快速 SP 构建器。

## 简短请求文件

创建一个任务内的新图，沿用已经准备好的短模板输入或完整 BuildPlan：

```json
{"operation":"build","request_id":"create-1","build_request":"<TASK_ROOT>/my_task/short-input.json"}
```

`short-input.json` 中的 target.workspace 必须是该任务目录的独立子目录，library/cell 明确指定。拓扑、板材、尺寸、端口及参考面仍由该输入提供；程序不把历史案例条件作为新工程默认值。建图默认不仿真。

同一张图修改一个变量，不重复提交整图参数：

```json
{"operation":"edit","request_id":"length-1","expected_revision":1,"changes":[{"instance":"VAR1","field":"variables","key":"L_TL1","value":"2.2 mm"}]}
```

原生器件参数使用 `field="parameters"`，枚举选项使用 `field="forms"`。变量名、单位和值必须来自当前工程；上例只展示已验证模板的字段。替换器件可沿用原有 `replacements` 格式，但必须保持原引脚及连接，程序会核对。每次修改都先保存具体变更清单，再修改、保存、重开并核对差异。

读取上次已验证的快照（核对工程内容及环境未变，不重新复制工程）：

```json
{"operation":"inspect"}
```

明确要求重新原生读取时使用 `{"operation":"inspect","fresh":true}`。

读取任务状态或等候已有 SP 任务发生变化：

```json
{"operation":"status","after_revision":-1,"wait_seconds":20}
```

后续 `after_revision` 使用返回的 `job_revision`，不是原理图的 revision。每次最多等待 25 秒；未发生变化返回 `changed=false`，不重复附送长日志。观察超时不是求解失败。

`request_id` 在同一任务中必须唯一标识一次操作；相同 ID、相同输入会返回已执行记录，不重复建图/修改/派发。相同 ID 改了输入会拒绝。失败后先阅读 record，再用新 ID 提交明确修正的输入。不要在未核对原任务前换 ID 再次派发。

## 复用已有客户端，减少重复启动

```powershell
& '<REPO>\.venv\Scripts\python.exe' -B '<REPO>\design_workflow_client.py' --task-dir '<TASK_ROOT>\my_task' --interactive
```

在这个进程中逐行输入：

```json
{"action":"run","request_file":"<TASK_ROOT>/my_task/create.json"}
{"action":"run","request_file":"<TASK_ROOT>/my_task/edit.json"}
{"action":"run","request_file":"<TASK_ROOT>/my_task/inspect.json"}
{"action":"quit"}
```

也可以将这些命令放在 JSON 数组文件里，用既有 `--batch commands.json` 执行。错误会停止批处理；独立 SP 已提交时保留原 job ID，客户端退出不会重新派发它。异步 SP 的等待应在看到提交返回后按状态进行，不在固定批次中猜测完成时间。

## 显式仿真、缓存和安全边界

`operation="simulate"` 使用既有串行 RF 执行器。`simulation` 包含 `authorization="run_linear_sp_on_copy"`、完整 MatchingEvaluation，可选 timeout_seconds（1–120）、data_directory 和 use_cache。频段、参考面、参考阻抗、复数负载和目标均须明确，与当前保存电路合同一致。也可把同样的 simulation 对象显式附在 build/edit/import 请求上，构图验收后才提交。只返回任务 ID，不为等待求解反复展开整个设计上下文。

仅在既有缓存准入规则允许时复用本任务已核验的 SP 结果；键包含完整网表、工作区文件指纹、工程合同、评价条件、模型数据依赖和求解器指纹。未分类模型/文件依赖等保守禁用缓存，不猜测其安全性。`use_cache=false` 要求真实复算；缓存命中明确报告，不能写成新求解。

变更几何、板材、模型、端口、参考面、频率、求解器或依赖会使对应快照/结果不能复用。工程、网表和结果仍按内容核对。只有 ADS 安装及模型目录的环境指纹采用同进程 Windows 变更通知复用；通知异常、目录身份变化或无法监视时重新扫描。缺失、远程或含链接的目录不使用该快速路径。目录变更通知能力依据 [Microsoft 文档](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-findfirstchangenotificationw)；不是依赖过期时间的缓存。已有目录清单对刻意保持身份/大小/时间戳的篡改没有完整保证，未将其描述为安全审计系统。

每个任务和工作区均有锁；运行中的 SP 持有可追溯预留，其他客户端不能修改同一工作区。外部保存改动会被内容指纹拒绝，不自动吸收或覆盖。原工程只在第一次导入时读取/复制，之后只操作独立副本。未保存的外部 GUI 修改不可观察，因此不接管用户正在编辑的工作区。

若原生保存后验收失败，任务标记 needs_recovery，保留当前结果和修改前目标 cell 的文件备份，停止继续修改。不会自动覆盖可能已被外部改动的文件。接口缺口继续使用已有两次同类失败停止规则，不在普通设计任务中自动编写适配器。

若输入在创建工程前已失败，状态为 rejected_before_creation，可修正输入并使用新 request_id；已经产生工程文件的失败仍须核对，不自动重建。修改成功后旧版本的仿真证据保留在历史记录中，不再作为当前原理图的仿真状态。相同请求重放仍核对现有工程未被外部修改，再返回原操作记录。

## 返回内容、证据和计量

默认返回：status、operation、revision、目标原理图、changes 数量、验收摘要、必要的仿真状态、elapsed_seconds 和一个 record 路径。

- `task.json`：唯一目标、当前版本、合同、输入来源、验收/停止条件、最新快照及仿真任务。
- `operations/<request_id>/request.json`：冻结请求和输入指纹。
- 每次操作目录：详细 result.json、快照、网表、变更清单，必要时有 before-native 备份。备份不是额外交付的 ADS 工程。
- `timing.json`：可观测阶段、工具调用和输入/返回字节数；同阶段重叠区间取并集，不把不同阶段简单求和当作总耗时。
- `errors/`：完整错误细节；默认错误摘要最多 600 字符。
- `rf-jobs/` 与操作内 simulation 目录：持久任务、求解日志、原始数据及验证证据。

任务总时长按客户端边界实测；未记录的需求理解、人工等待、模型推理阶段仍标未观测。脚本回归不产生模型请求，无法据此测量真实 input/output/cached tokens；字节数只表示返回内容规模，不能作为 token、费用或总设计速度的同义词。开发和排错另计，不混入日常任务基准。

公开的前后对比和验证边界见 [验证摘要](docs/VALIDATION.zh-CN.md)。优先使用同一专用会话；冷启动仍需初始化 ADS 和重新核对环境，不承诺每个场景都更快。
