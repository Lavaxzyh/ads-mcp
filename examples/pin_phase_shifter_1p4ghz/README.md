# 联合仿真反射式移相器

1.4 GHz、180°单比特，包含原生金属 Layout、焊盘、过孔、集总偏置及 PIN_diode 两态测试台。用于演示原理图 → 原生 Layout → Momentum → emModel → 电路联合仿真。

本示例基于 **“1.4 GHz Low-Cost PIN Diode Phase Shifter for L-Band Radiometer Antenna,” IEEE Access, 2019** 的单比特电路作**大致复现**。[IEEE Xplore](https://ieeexplore.ieee.org/document/8752217/)；DOI：10.1109/ACCESS.2019.2926140。版图、器件模型和部分参数经过调整与调谐。本项目提供的是仿真案例，结果不能当作原论文四比特整机或实测性能。

## 使用

ADS 2027 v6.5.0，Momentum microwave，需相应许可证。先在关闭工程时运行：

```powershell
python examples/rebind_workspace.py examples/pin_phase_shifter_1p4ghz/workspace
```

再在 ADS GUI 中打开 `workspace`，检查下列视图。设置 `HPEESOF_DIR` 并使用具备 ADS/MCP 依赖的 Python：

```powershell
python examples/run_example.py pin_phase_shifter_1p4ghz --action inspect
python examples/run_example.py pin_phase_shifter_1p4ghz --action simulate
```

| 内容 | 视图 |
|---|---|
| 金属源原理图 | `meander14_lib:rtps180_compact80:schematic` |
| 原生版图与模型 | `rtps180_compact80:layout/emSetup/emModel` |
| +5 V 两态之一 | `rtps180_final_forward:schematic` |
| −15 V 两态之一 | `rtps180_final_reverse:schematic` |

模型为32端口；外部RF端口1–2，其余端口用于外接电容、电感、PIN和偏置连接。集总器件的电气模型在测试台接回，金属 Layout 保留其安装焊盘和间隙。每个器件端口的编号以模型中保存的名称及原理图连线为准，不按图形位置猜测。

## 已有结果与建模范围

LP=11.4 nH，C1/C2/C3=47 pF，CHOKE=56 nH，R=470 Ω；主 RF_TEE 的 W1=W2=1.37743 mm。1.4 GHz 两态相差179.94°；正/反偏插损0.250/0.341 dB。25 MHz带内相位误差−2.55°至+2.04°，最差插损0.352 dB、最小回波损耗27.34 dB。

80 cells/波长；DC及1.37–1.43 GHz的61个直接RF点。25 MHz统计使用0.5 MHz电路扫描，部分点由emModel插值。器件模型范围见下表。焊盘尺寸含工程假设，尚未完成实际料号Q/SRF、装配、容差和实测验证。40→80网格的相差变化约0.87°，不能将中心相位误差视作严格收敛精度。

`reference_results` 保存基准CSV、指标和图面。示例使用独立副本，剔除了共存版及中间实验，不再执行移相器优化。实际制造前需按选用封装、板厂工艺和BOM复核。

## 集总器件模型

当前方案包含非理想 PIN 模型，各类集总器件的具体范围如下；参考网表见 `reference_results/rtps180_final_forward.net` 与 `rtps180_final_reverse.net`。

| 部分 | 实际使用的模型与参数 | 建模边界 |
|---|---|---|
| PIN 二极管 | ADS 原生 `PIN_diode`，两态偏置 +5 V / −15 V；`Cj≈0.898 pF`、`Lbond≈0.500 nH`、`Tau≈0.987 μs`，并包含有限电阻及偏置相关响应 | 按论文等效阻抗校准，并非经过厂商验证的完整 BA682 封装模型；`Rs=0`、`Cpack=0` 不代表整个 PIN 模型理想化 |
| PIN 附加 RF 损耗 | 每只二极管外接 `R=2*(VBIAS<0) Ω`，反偏时为 2 Ω | 显式的状态相关等效损耗修正 |
| 电感、电容 | LP=11.4 nH、CHOKE=56 nH、C1/C2/C3=47 pF，采用 ADS 理想 L/C 元件 | 未包含厂商 Q、SRF、ESR/ESL 和封装寄生；电感启用 `Noise=yes` 不等于已加入有限 Q |
| 偏置电阻 | ADS 470 Ω 集总电阻，`Noise=yes` | 包含电阻损耗与噪声，未引入厂商频变或封装寄生模型 |
| 印制电路金属部分 | 走线、安装焊盘及过孔由 Momentum 求解，并以 32 端口 emModel 接回器件 | 焊盘及走线的 EM 效应不等于器件内部封装已经完整建模 |

AVX 0402 3.9 pF 厂商模型接入是另一个独立示例，当前移相器未使用该模型。上述模型范围适用于本示例全部参考曲线及性能指标。
