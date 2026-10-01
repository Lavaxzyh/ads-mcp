"""Step-by-step narrated demo: build the SI-LPF from scratch, one call at a time.

Each step prints the engine call it makes and the verbatim result, so the
console transcript reads as a build log of the circuit construction.
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ads_engine import AdsEngine  # noqa: E402

WS = Path(r"D:\ADS_Project\ads_mcp_demo\si_lpf_final2_wrk")
OUT = Path(r"D:\ADS_Project\ads_mcp_demo\sim_out_step")

step_no = 0

def step(title: str) -> None:
    global step_no
    step_no += 1
    print(f"\n{'='*70}\nSTEP {step_no}: {title}\n{'='*70}", flush=True)
    time.sleep(0.4)

def show(result: dict) -> None:
    print("  ->", result, flush=True)


engine = AdsEngine()

step("确认 ADS 环境可用")
show(engine.check_installed())

step("创建工作区(相当于 GUI 里 File > New Workspace)")
if WS.exists():
    shutil.rmtree(WS)
show(engine.create_workspace(str(WS)))

step("创建设计库 si_lpf_lib(相当于右键 library > New Library)")
show(engine.create_library("si_lpf_lib"))

step("新建空白原理图 si_lpf_lib:lpf5(相当于 New Cell > Schematic)")
show(engine.create_schematic("si_lpf_lib", "lpf5"))

step("定义 VAR 变量:Wh/Wl = 高/低阻抗线宽,Lh/Ll = 高/低阻抗线长")
for n, v in (("Wh", "0.2 mm"), ("Wl", "8 mm"), ("Lh", "23 mm"), ("Ll", "6.5 mm")):
    show(engine.add_var(n, v))

step("放置 6 段微带线 MLIN(ads_tlines 库)——阶梯阻抗滤波器的主体")
print("  布局: Term1 -[TL1 hi]-[TL2 lo]-[TL3 hi]-[TL4 lo]-[TL5 hi]-[TL6 lo]- Term2")
print("  窄线(Wh)~电感 L,宽线(Wl)~电容 C,参数引用刚才的 VAR 变量")
X = [0.5, 2.0, 3.5, 5.0, 6.5, 8.0]
SEG = [("Wh", "Lh"), ("Wl", "Ll"), ("Wh", "Lh"), ("Wl", "Ll"), ("Wh", "Lh"), ("Wl", "Ll")]
for x, (w, l), name in zip(X, SEG, ["TL1", "TL2", "TL3", "TL4", "TL5", "TL6"]):
    show(engine.place_component("ads_tlines", "MLIN", x, 0.0, instance_name=name, params={"W": w, "L": l}))

step("放置输入/输出端口 Term1、Term2(ads_simulation 库,自动带地)")
show(engine.add_term("Term1", 0.0, 0.0))
show(engine.add_term("Term2", 9.5, 0.0))

step("连线:把端口与 6 段线的首尾逐个接通(7 段导线)")
edges = [(0.0, X[0])] + [(X[i] + 1.0, X[i + 1]) for i in range(5)] + [(X[-1] + 1.0, 9.5)]
for x1, x2 in edges:
    show(engine.add_wire([[x1, 0.0], [x2, 0.0]]))

step("放置基板 MSUB(自动命名 MSub1):FR4,Er=4.4,H=1.6mm")
print("  所有 MLIN 的 Subst 默认引用 MSub1,所以基板实例名不能改")
show(engine.place_component(
    "ads_tlines", "MSUB", 5.0, -2.5,
    params={"Er": "4.4", "H": "1.6 mm", "TanD": "0.02", "Cond": "5.8e7"},
))

step("放置 S 参数控制器 SP1:0.05-6 GHz,步进 25 MHz")
show(engine.place_component(
    "ads_simulation", "S_Param", 9.5, -2.5, instance_name="SP1",
    params={"Start": "0.05 GHz", "Stop": "6 GHz", "Step": "25 MHz"},
))

step("确认图上的完整实例清单")
show(engine.list_instances())

step("保存并生成网表(网表 = 引擎看到的真实电路)")
engine.save_schematic()
netlist = engine.generate_netlist()["netlist"]
keep = [ln for ln in netlist.splitlines()
        if ln.strip() and not ln.strip().startswith(";") and not ln.startswith((" ", "\t"))]
print("\n".join("  " + ln for ln in keep)[:1500])
print(f"\n  ... (网表共 {len(netlist)} 字符)")

step("后台提交 hpeesofsim 仿真")
if OUT.exists():
    shutil.rmtree(OUT)
result = engine.run_simulation(str(OUT), netlist)
print("  ->", result)

step("读取 S 参数结果")
res = engine.get_results(result["dataset"])
s11, s21 = res["summary"]["S1,1"], res["summary"]["S1,2"]
cutoff = s21.get("first_below_-3dB_hz", 0)
print(f"  S11 范围: {s11['db_min']} .. {s11['db_max']} dB")
print(f"  S21 通带: {s21['db_at_fmin']} dB @ 起始频点")
print(f"  S21 -3dB 截止: {cutoff/1e9:.3f} GHz(设计目标 1 GHz)")
print(f"  S21 阻带: {s21['db_at_fmax']} dB @ 6 GHz")

step("导出交付物:Touchstone .s2p + 曲线 PNG")
show(engine.export_touchstone(result["dataset"], str(WS.parent / "step_demo.s2p")))
show(engine.plot_sparams(result["dataset"], str(WS.parent / "step_demo_sparams.png"), ["S[1,1]", "S[1,2]"]))

print(f"\n{'='*70}\nDEMO COMPLETE — 打开 GUI 查看这张真实生成的原理图\n{'='*70}", flush=True)
show(engine.open_in_gui(str(WS)))
