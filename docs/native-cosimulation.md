# 原理图到原生 EM 联合仿真

这条链路使用 ADS 自身微带器件的 artwork，不是另行按坐标重绘一个外观相似的版图。ADS 2027 v6.5.0 / Windows 为当前验证平台。

1. 创建金属源原理图。使用微带元件、衬底引用及焊盘/间隙结构，接入 Subnetwork 端子。`ads_add_design_port` 将端子绑定到具体实例引脚的实际 net；仿真 `TermG` 与层次端子不是同一功能。坐标看似重合也不能证明连接。
2. `ads_generate_layout` 使用 `layout.edit.update_design_from(source)` 生成原生 Layout。保存源图、核对单位、端口和连接；RF_TEE 连续主线的 W1/W2 应一致。更新已有 Layout 需显式 `update=true`。对于无 artwork 的集总器件，不能假设自动生成真实封装/焊盘。
3. `ads_create_substrate` 定义实际介质、金属和过孔映射；`ads_configure_momentum_setup` 创建本地原生 Setup。已有端口可用 `ads_configure_momentum_options` 设置 Auto/TML/None feed；器件内部安装端口的去嵌应逐一检查。DC 为单独离散点，不是 RF 线性步进。
4. `ads_generate_symbol` 独立生成 Dual/Quad 或 lookalike。`keysight.ads.de.experimental.generate_symbol.SymbolGenerator` 是矩形 Dual/Quad 符号生成工具类，不生成 EM 数据。lookalike 使用原生 AEL。
5. `ads_configure_em_model` 在现有 Setup 中保留唯一 `EMComponent`，设置 `OutputEmModel` v2.1 的 autoCreate/autoUpdate。保存、关闭并重开工作区，再 `ads_run_momentum`。这是配置加原生仿真的自动生成路线，不是凭空创建可用 EM 数据的 Python 类。
6. `ads_get_sim_status` 必须完成，并检查实际模型文件：`eesof_em_model.file`、`model.ndx`、`emDataIndex.xml`、`data.*.sio/.sam`。`ads_inspect_em_model` 报告文件与样本，但存在视图不等于数值验收。
7. 测试台将器件电气模型接回金属模型对应端口，通过 `ads_set_instance_parameters(..., simulation_view="emModel")` 明确使用视图。生成网表应出现指向本地 `model.ndx` 的 `MomCmpt`。仿真、比较全部复数 S 项和同一频率网格；`ads_compare_sparameters` 不插值。

固定 EM 数据可在 standalone 后端使用；带 Wm/Lm 等参数并需要新 EM 样本的模型使用 `ads_run_simulation(backend="gui", session_id=..., workspace=..., design="lib:cell:schematic")`。GUI 会话必须已打开目标工程，MCP 所拥有的工作区必须先关闭；工具不自动切换用户工程。

网格修改工具仅保存 Setup 并备份配置，要求工作区重载、新求解和缓存对应性检查。`ads_set_mesh_visibility` 使用原生加载/清除动作管理显示对象，并统计 `MomentumMesh` 对象数量；不会保存 Layout，也不能替代几何样本对应性检查。宣传网格图来自实际求解产生的 `proj.gdf`，金属来自 `proj_a`。

大型案例见 [联合仿真反射式移相器](../examples/pin_phase_shifter_1p4ghz/README.md)。最小原生转换示例见 `demo/build_native_line.py`，不覆盖已有工程。
