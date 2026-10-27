# v0.4.0 新工具与调用边界

17个新工具，合计49个；旧工具名称与 standalone 默认调用保持兼容。

| 工具 | 功能 |
|---|---|
| ads_open_design | 检查已有视图，可在指定GUI中打开 |
| ads_add_design_port | 在实例引脚实际net上添加Subnetwork层次端口 |
| ads_set_instance_parameters | 修改已有参数并显式选择仿真视图 |
| ads_define_cell_parameters | 生成REAL/NO_UNIT的持久化Subnetwork形式参数 |
| ads_generate_layout | 从保存的原理图原生生成/更新Layout，可设置PSN |
| ads_generate_symbol | Dual/Quad矩形或Layout lookalike符号 |
| ads_import_library | 注册本地厂商lib.defs |
| ads_configure_em_model | 设置原生自动创建/更新模型输出 |
| ads_inspect_em_model | 检查原生文件、参数、样本缓存与几何插值策略 |
| ads_configure_em_parameters | 设置几何插值/缓存分辨率，不清缓存 |
| ads_configure_momentum_mesh | cells/波长、边缘网格、参考频率 |
| ads_configure_momentum_options | 单RF线性+可选DC计划、端口feed类型 |
| ads_list_windows | 当前ADS进程窗口、设计标识与屏幕几何 |
| ads_arrange_windows | setGeometry拼铺、showMaximized逐窗最大化、恢复 |
| ads_set_mesh_visibility | 指定Layout窗口的Momentum Mesh显示开关 |
| ads_release_optimizer | 释放已完成Optim的仿真器，不应用参数 |
| ads_compare_sparameters | 同频率网格完整复数N端口比较，无插值 |

`ads_run_simulation` 新增 `backend="gui"`、`mode="optimize"`、`session_id/workspace/design`；原默认仍为 standalone simulate。`ads_export_touchstone` 支持1/2/3/32等完整N端口矩阵，默认扩展名依实际矩阵推断。显式扩展名不匹配会拒绝导出。

GUI操作通过本机官方 `ads-mcp.exe` 桥接，在选定 ADS 进程主线程执行固定内部函数，参数以JSON传递；不向MCP暴露任意脚本执行端点。工作区不匹配会拒绝，工具不替用户切换工程。布局窗口按进程内`winId`定位；设计重复打开时应传窗口ID。几何是Qt逻辑像素，包含多屏可用区域原点。

仿真期间拒绝编辑；GUI任务可重启后继续轮询。桥接超时表示派发结果不确定，必须检查原任务，不能自动重派。`ads_release_optimizer`仅处理已完成且结果已归档的Optim任务；后续运行不应绕过尚在运行的任务。

支持范围不包括自动BOM、任意集总封装生成、所有ADS版本适配、任意多段/自适应频率计划、自动全局制造约束或实测保证。PIN焊盘与模型范围见具体示例。
