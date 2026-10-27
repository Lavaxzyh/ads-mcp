# 参数化微带贴片天线 EM 联合优化

馈线、匹配段和贴片同属完整EM区域。VAR → Subnetwork形式参数 → PSN参数化Layout → Momentum重算/缓存 → 原生Optim → 最优尺寸完整频带复算。

## 使用

ADS 2027 v6.5.0，Momentum microwave及电路仿真许可证。先关闭工程并重绑定路径，再从ADS GUI打开副本：

```powershell
python examples/rebind_workspace.py examples/microstrip_patch_em_optimization/workspace
python examples/run_example.py microstrip_patch_em_optimization --action inspect
python examples/run_example.py microstrip_patch_em_optimization --action simulate
# 可选：再次运行天线的自动优化，需要等待完成并处理本次Optimization Cockpit的仿真器占用
python examples/run_example.py microstrip_patch_em_optimization --action optimize
```

| 内容 | 视图 |
|---|---|
| 原理图、源版图、模型 | `full_patch_lib:matched_patch:schematic/layout/emModel/emModelOpt` |
| 最终尺寸实例 | `full_patch_lib:final_geometry:layout` |
| 最终联合仿真 | `full_patch_lib:full_em_final_tb:schematic`，调用 `emModel` |
| 自动优化 | `full_patch_lib:full_em_optimizer_tb:schematic`，调用 `emModelOpt` |

`matched_patch:layout`显示源默认参数，检查最终尺寸应打开`final_geometry:layout`。Wm/Lm为Subnetwork形式参数，`REAL`、`NO_UNIT`，通过`Wm*1 mm`、`Lm*1 mm`转换成微带尺寸；不是名为subnet的数值类型。子电路定义位于`matched_patch/itemdef.py`，`is_sub_design=True`。

## 优化与结果

目标为2.4 GHz处S11≤−40 dB；Wm范围0.4–1.2 mm，Lm范围16–23 mm。优化模型实际求解2.400/2.401 GHz两点，以3 GHz为网格参考频率。几何参数插值关闭，未缓存尺寸通过GUI内原生服务触发Momentum；已有样本可以复用。独立hpeesofsim不具备该GUI服务连接，不应把缺失参数映射或Gemx连接错误当作正常完成。

历史Optim记录：1次迭代、7次函数评估。最终Wm=0.73277 mm，Lm=20.58024 mm；完整2–3 GHz、101点复算，2.4 GHz S11由−26.357 dB改善到−48.315 dB。仅优化单频匹配，未优化效率、增益或带宽；结果不是实测或制造精度保证。

`reference_results`保留历史指标和参数，最新验收报告区分缓存联合复算与实际新EM求解。TermG已保留按连接引脚旋转后的朝向。
