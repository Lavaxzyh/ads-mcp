# 参数化 Layout 与原生 Optim

以 [微带贴片天线](../examples/microstrip_patch_em_optimization/README.md) 为准：馈线、匹配段和贴片全放入 EM 区域，不能用“只求解贴片、匹配段仍用解析 MLIN”替代完整金属的优化结果。

`Wm/Lm` 是 Subnetwork 形式参数，数值类型为 `REAL`、单位为 `NO_UNIT`，`ModelDef.is_sub_design=True`；不是名为 subnet 的参数数据类型。`ads_define_cell_parameters` 持久化 `itemdef.py`，`ads_generate_layout(parameterized=true)` 创建 PSN PCell。微带长度/宽度表达式用 `Wm*1 mm`、`Lm*1 mm`，上层 VAR 的裸数按毫米解释。

测试台 DUT 选择 `emModelOpt`；GUI 网表应含 `MomCmpt` 的 `p0="Wm" v0=Wm`、`p1="Lm" v1=Lm`，并指向 `emModelOpt/model.ndx`。完整频带最终复算选择 `emModel`。本地独立网表生成曾丢失 p0/p1 映射，独立 hpeesofsim 也无法提供 GUI Gemx 重算服务，因此这里必须用 GUI 后端。

`ads_configure_em_parameters(interpolate_geometry=false, resolutions={"Wm":1e-5,"Lm":1e-5})` 设置几何样本缓存策略。关闭几何插值后，落在缓存分辨率内的样本仍可复用；不在缓存中的尺寸触发 Momentum。它与同一几何的频率插值不同，也不是每次 Optim 评估都必然重新求解。工具不删除旧样本。更改网格或衬底时应建立新模型名称/独立副本并验证样本对应性。

原生 Optim、目标和 VAR 范围由示例原理图保存。通过 `ads_run_simulation(backend="gui", mode="optimize", ...)` 派发一次，轮询 `ads_get_sim_status`。工具读取新 `.SUMMARY`、`.OPTSOLNVALS`；交互模式没有独立完成日志时，结合新最终数据集和原生 Cockpit 停止状态判定完成。保留最优值及数据副本，桥接超时不会自动重复运行。完成后 `ads_release_optimizer(task_id)` 释放仿真器并请求关闭 Cockpit，保留已记录的最优参数，**不自动改写原理图 VAR**。如 ADS 提示是否更新设计，除非明确要应用最优值，否则选择保留原设计。需显式应用最佳尺寸，再做完整频带复算。

本例历史目标为 2.4 GHz 的 S11≤−40 dB；Wm=0.4–1.2、Lm=16–23。最终 Wm=0.73277 mm、Lm=20.58024 mm，2–3 GHz 复算在 2.4 GHz 得 −48.315 dB。`matched_patch:layout` 是默认几何，最终尺寸打开 `final_geometry:layout`。为最终几何网格图新增 `final_geometry:emSetupMesh`，2.400/2.401 GHz 两点，40 cells/波长、参考 3 GHz，不替代优化模型。
