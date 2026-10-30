# ADS 2025 从明确要求到可核对工程

本地版本 `0.4.0+ads2025.20261030.3` 日常默认入口为 `ads_design_workflow(action="run")`，先看 [SINGLE-SCHEMATIC.zh-CN.md](SINGLE-SCHEMATIC.zh-CN.md)。一个任务绑定一张独立原理图，后续修改保留同一目标。下文为兼容保留的逐步接口与复制工作流；需要多个完整候选副本时再明确选择它。

本工作流复用原有建图、匹配搜索和持久 RF 队列，新增入口 `ads_design_workflow`。它执行已经明确的工程决定，不自行更换拓扑、板材、目标阻抗、参考面或端口条件。原工程只通过文件读取，ADS 打开的均为新建工程或独立副本。

## 最短调用方式

对 Codex 可以直接说：

> 使用 ads_design_workflow。在一个专用任务会话中，先提取我指定的已保存原理图并核对条件，只在新副本中修改我指定的参数。先保存变更清单，修改后核对实际差异、连接、保存和重新打开的结果。未明确授权时不要运行仿真；遇到接口缺口停止普通设计流程并报告。

当前连接尚未刷新工具目录时，直接使用新安装中的私有客户端，不必关闭其他对话或重启 ADS：

```powershell
& '<REPO>\.venv\Scripts\python.exe' -B '<REPO>\design_workflow_client.py' --task-dir '<TASK_ROOT>\my_task_001' --interactive
```

程序打印 `ready` 后，每行输入一个 JSON，例如：

```json
{"action":"templates"}
{"action":"build","request_file":"<TASK_ROOT>/my_build_request.json"}
{"action":"export","request_file":"<TASK_ROOT>/my_export_request.json"}
{"action":"timing"}
{"action":"quit"}
```

同一进程内连续输入会复用 ADS/MCP 服务。`quit` 只结束该专用会话。也支持 `--batch commands.json`，文件内容为上述命令的 JSON 数组；批处理遇到首个失败即停止。任务目录由操作系统锁保护，第二个进程不能同时占用同一目录。程序不会连接现有 ADS GUI，也不会关闭其他服务的工作区。

直接 MCP 调用包含三个参数：`action`、绝对路径 `task_directory`、可选 `request_file`。文件承载复杂输入，工具返回摘要和完整记录路径。

## 短输入模板

`templates` 列出模板及适用范围；`expand` 展开为完整方案；`validate` 只做静态检查；`build` 建图、验收、保存、重新打开核对。原 `ads_build_schematic` 仍接受完整方案，也接受新模板输入。

目前模板为 `output_match_2stub_sp@1`：双并联开路 stub、阶梯微带、漏极馈电、旁路电容及输出隔直；使用 ADS 内置准静态微带、理想电容和理想直流源。它没有晶体管、HB 或非线性性能承诺。

完整短输入样例：`examples/schematic/output_match_short.json`。必须显式提供：

- `target`：全新输出工作区、库名、单元名。
- `contract`：线性 SP、频率起止和步进、网络边界、两个端口的阻抗和参考面。
- `inputs`：33 项板材、电气尺寸、馈电、目标阻抗和优化器配置值。缺少任意一项都会失败，没有历史板材或尺寸回填。
- `optimizer_enabled`：明确是否启用图中的优化器。`false` 才适用于本工作流的单次 SP 复核；`true` 仅表示保留启用的 Optim 配置，建图本身仍不执行优化。
- `overrides`：可选，修改已有器件的参数、枚举表单或变量；不更换拓扑。端口、频率、板材和目标值应在必填字段中明确修改，不能通过覆盖项绕过契约。

覆盖项示例：

```json
{"instance":"VAR1","field":"variables","key":"L_TL1","value":"3 mm"}
```

公开样例使用 1–1.2 GHz、合成板材与尺寸、50 Ω 目标，优化器关闭；不是新设计的建议、真实功放参数或历史性能基准输入。详见 examples/schematic/README.md。模板端口当前要求显式正实数参考阻抗；阻抗计算由程序从端口 1 的参考阻抗生成，不能另填一个冲突的 Zref。

## 提取、变更预览、独立副本修改

`export` 请求：

```json
{
  "source":{"workspace":"<WORKSPACE_ROOT>/example_wrk","library":"example_lib","cell":"output_match"},
  "contract":{
    "execution":"build_only","testbench":"linear_sp",
    "network_boundary":"这里填写明确的网络边界",
    "ports":[
      {"instance":"Term1","number":1,"impedance":"50 Ohm","reference_plane":"这里填写输入参考面"},
      {"instance":"Term2","number":2,"impedance":"50 Ohm","reference_plane":"这里填写输出参考面"}
    ],
    "frequency_hz":[2200000000,2600000000,10000000],
    "result_meaning":"initial_values_not_simulated"
  }
}
```

上述值也必须按实际工程替换。契约可以省略以进行只读提取；省略时参考面等语义信息标为未知，不能直接进行同条件仿真复核。

程序先复制文件并重定位工作区内库路径，再以只读模式打开副本。返回的 `snapshot` 包含器件、原生参数类型及值、枚举表单、变量、引脚、网络分组、导线几何、仿真控制器和缺失项；`snapshot_sha256` 用于后续核对。无法读取的字段记录在 `unreadable`，不会替换为猜测值。

`plan_patch` 请求使用刚返回的快照路径和哈希：

```json
{
  "snapshot":"填写 export 返回的 snapshot",
  "snapshot_sha256":"填写 export 返回的 snapshot_sha256",
  "destination":"<TASK_ROOT>/revision_001_wrk",
  "changes":[
    {"instance":"VAR1","field":"variables","key":"L_TL1","value":"3 mm"},
    {"instance":"C_HF","field":"parameters","key":"C","value":"110 pF"}
  ]
}
```

返回 `review`、`review_sha256` 和前后变更清单；此时尚未创建输出工程。`apply_patch` 的请求为：

```json
{"review":"填写 review 路径","review_sha256":"填写 review_sha256"}
```

成功后返回新快照，可直接作为下一次修改的源。每版都用新的目的目录，避免覆盖已有版本。原始文件、预览文件、快照或源工程发生改变时会拒绝执行。修改前后会核对所有可读字段、引脚位置和电气连接，再保存并重新打开核对。

也支持显式 `replacements`：

```json
{"instance":"C_HF","library":"ads_rflib","cell":"C","parameters":{"C":"120 pF"},"forms":{}}
```

目前限于内置 RF 器件，替换后必须保持相同引脚编号和位置，不自动重接线。控制器/VAR 替换、层次内部改图、镜像后需重布线等不属于此操作。修改端口或仿真条件时必须同时给出明确的 `contract_after`，否则原契约核对失败。

## 能力档案

`export` 同时生成按实际 ADS 版本、安装位置及所用模型库区分的档案。记录原生元件、引脚、参数类型、单位类别、可用枚举和本次实际读到的表单，以及已确认的兼容处理。

`capabilities` 请求为 `{"profile":"档案路径"}`。核心二进制 SHA256、API/安装/模型库文件清单变化后，档案被标为 `invalidated_reextract_required`，必须重新提取。清单使用文件大小及纳秒修改时间；不声称识别恶意保持大小和时间戳的改动。

“API 中存在的枚举”与“已经实际写入并验收的枚举”有区别。具体赋值和器件替换的成功证据以 build/revision 验收记录为准，不将全部 API 元数据宣称为已经逐项测试。

## 匹配结果落图与同条件 SP 复核

已有 `ads_submit_rf_job` 的 search/sweep/optimize 结果继续使用，无需重做搜索。先 `export` 对应的原理图，再调用 `plan_candidate`：

```json
{
  "snapshot":"快照路径","snapshot_sha256":"快照哈希",
  "search_directory":"已完成搜索的输出目录",
  "case":1,
  "destination":"<TASK_ROOT>/selected_case_wrk"
}
```

必须显式选定 `case`。程序检查搜索源网表、案例实际输入、参数单位、原理图和搜索条件是否匹配，并生成参数映射及变更清单。不能把来源不同的搜索结果直接套到另一张图。

`materialize` 使用返回的 review：

```json
{"review":"预览路径","review_sha256":"预览哈希","simulation":"verify_same_conditions","timeout_seconds":120}
```

`simulation: "none"` 只生成并验收副本；`verify_same_conditions` 明确授权一次 SP 复核。它复用现有持久 RF 队列，比较实际频率网格和全部复数 S 参数，默认绝对误差上限 `1e-9`，并保存具体差值。副本建图成功但仿真不一致时，整体不会标为完成。

也可单独调用 `simulate`：提供快照及哈希、`authorization: "run_linear_sp_on_copy"`、完整的既有 `MatchingEvaluation`、可选数据目录和 1–120 秒的单案例超时。SP 请求新增对应的 `ads_submit_rf_job(workflow="sp", request_file=...)`，不需要人为构造一个无意义的扫描变量。

这里只复核线性 SP。端口 2 的参考阻抗与评价用的实际负载阻抗不是同一个字段，评价仍使用既有工具明确的负载条件。没有把 SP 匹配、传输效率或结构检查升级为功放 PAE、压缩或谐波性能。

## 版本比较

`compare` 请求包含 `left/left_sha256`、`right/right_sha256`。返回器件、参数、变量、位置、连接及契约的实际差异。没有仿真证据时只给结构结果。

需要 RF 指标比较时，另提供 `left_evidence/left_evidence_sha256`、`right_evidence/right_evidence_sha256`，均来自 `simulate`。快照归属、数据哈希、仿真环境、端口、参考面、评价设置和频率网格一致才计算指标；否则解释为何退回结构比较。逐频点 CSV 保留阻抗等评价列的前后值、差值，以及四个 S 参数的实部和虚部。

## 完整耗时记录与停止条件

私有客户端记录服务启动、请求往返、退出、实际调用次数、请求/返回字节数。服务记录资料读取、配置展开、建图、重新打开验收、返工与显式仿真区间。任务目录下 `timing.json` 保存原始记录。

程序无法回溯模型此前读取资料、思考方案或等待用户的准确时间。需要从任务开始记录时，调用者可先用边界标记：

```powershell
& '<REPO>\.venv\Scripts\python.exe' '<REPO>\workflow_trace_cli.py' --task-dir '<TASK_ROOT>\my_task_001' begin preparation --label '方案准备区间'
# 保存返回的 span_id，区间结束时传给 end：
& '<REPO>\.venv\Scripts\python.exe' '<REPO>\workflow_trace_cli.py' --task-dir '<TASK_ROOT>\my_task_001' end '返回的 span_id'
```

这只实测两个标记之间的墙钟时间，可能含等待，不等于模型计算时间。未标记阶段保留 `unobserved`，未闭合区间保留 `running`。嵌套区间与原生子阶段重叠，不能简单求和。

每个操作只尝试一次，不自动换拓扑或编写临时适配器。相同请求在同一实现下失败两次会阻止继续重试；不同请求连续触及同类原生适配缺口也会停止。工程输入错误应修正输入；接口缺口应另作为明确授权的工具开发处理。批处理不会在失败后偷偷继续后续步骤。

## 当前边界

- 只读取已经保存的原理图；ADS GUI 中未保存的编辑无法观测，不宣称已经包含。
- 提取为顶层标准方案，不展开层次内部。未解码字段显式标记；存在无法核对的原生字段时拒绝宣称完整增量验收。
- 工作区内部库可复制；指向工作区外部的用户库、未解析变量路径、符号链接/目录联接需要显式依赖处理，目前会停止。不会为绕过问题修改原库。
- 快照最多 25000 个源文件；任意外部模型的隐藏依赖、任意自定义代码及所有 ADS 元件组合不在已验证范围。
- 输出目录必须全新，不能覆盖用户或其他会话已有目录。原生检查有少量保存前后窗口，独立私有输出加操作系统锁是主要隔离手段，不以此宣称能拦截不遵守锁的外部程序的每一次瞬间写入。
- 真实耗时、验证版本和未覆盖项见 [公开验证摘要](docs/VALIDATION.zh-CN.md)；原始本机记录不随源码分享。
