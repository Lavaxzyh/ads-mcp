"""Probe: microstrip schematic -> netlist -> hpeesofsim -> dataset, all headless."""
import os, sys, shutil
from pathlib import Path

os.environ.setdefault("HPEESOF_DIR", r"D:\Program Files\Keysight\ADS2027")

from keysight.ads import de
from keysight.ads.de import db_uu

WS = Path(r"D:\ADS_Project\ads_mcp_probe\probe_wrk")
if WS.exists():
    shutil.rmtree(WS, ignore_errors=True)
WS.parent.mkdir(parents=True, exist_ok=True)

if de.workspace_is_open():
    de.close_workspace()
de.create_workspace(str(WS))
ws = de.open_workspace(str(WS))
de.create_new_library("probe_lib", str(WS / "probe_lib"))
ws.add_library("probe_lib", str(WS / "probe_lib"), de.LibraryMode.NON_SHARED)
sch = db_uu.create_schematic("probe_lib:msim:schematic")
assert sch is not None

txn = de.db.Transaction(sch, "probe microstrip sim")

# --- substrate ---
msub = sch.add_instance(de.LCVName("ads_tlines", "MSUB", "symbol"), (1.0, -3.0), name="MSub1", angle=0.0)
print("MSUB placed:", msub is not None)

# --- microstrip line ---
mlin = sch.add_instance(de.LCVName("ads_tlines", "MLIN", "symbol"), (2.0, 0.0), name="TL1", angle=0.0)
print("MLIN placed:", mlin is not None)

# --- two terms ---
t1 = sch.add_instance(de.LCVName("ads_simulation", "Term", "symbol"), (0.5, 0.0), name="Term1", angle=-90.0)
t2 = sch.add_instance(de.LCVName("ads_simulation", "Term", "symbol"), (3.5, 0.0), name="Term2", angle=-90.0)
print("Terms placed:", t1 is not None and t2 is not None)

# --- grounds for terms ---
sch.add_instance(de.LCVName("ads_rflib", "GROUND", "symbol"), (0.5, -1.0), name="GND1", angle=-90.0)
sch.add_instance(de.LCVName("ads_rflib", "GROUND", "symbol"), (3.5, -1.0), name="GND2", angle=-90.0)

# --- S-parameter controller ---
sp = sch.add_instance(de.LCVName("ads_simulation", "S_Param", "symbol"), (3.5, -3.0), name="SP1", angle=0.0)
print("S_Param placed:", sp is not None)

# --- wiring (main line) ---
sch.add_wire([(0.5, 0.0), (2.0, 0.0)])
sch.add_wire([(3.0, 0.0), (3.5, 0.0)])
txn.commit()

# --- parameter introspection: dump what each instance exposes ---
def dump_params(inst, label):
    try:
        params = inst.parameters
        names = list(params.keys()) if hasattr(params, "keys") else [p.name for p in params]
        print(f"[{label}] params: {names}")
    except Exception as e:
        print(f"[{label}] params introspection failed: {e}")

for inst, label in [(msub, "MSUB"), (mlin, "MLIN"), (sp, "S_Param"), (t1, "Term")]:
    dump_params(inst, label)

# --- set parameters (try/except each so one failure doesn't mask the rest) ---
def try_set(inst, pname, value, label):
    try:
        inst.parameters[pname].value = value
        print(f"[{label}] {pname} = {value} OK")
    except Exception as e:
        print(f"[{label}] set {pname} FAILED: {e}")

try_set(msub, "Er", "4.4", "MSUB")
try_set(msub, "H", "1.6 mm", "MSUB")
try_set(mlin, "W", "1.5 mm", "MLIN")
try_set(mlin, "L", "10 mm", "MLIN")
try_set(sp, "Start", "0.1 GHz", "S_Param")
try_set(sp, "Stop", "5 GHz", "S_Param")
try_set(sp, "Step", "50 MHz", "S_Param")

sch.save_design()

# --- netlist ---
try:
    netlist = sch.generate_netlist()
    text = netlist if isinstance(netlist, str) else str(netlist)
    print("=== NETLIST (first 1200 chars) ===")
    print(text[:1200])
except Exception as e:
    print("generate_netlist FAILED:", e)
    netlist = None

# --- simulate ---
if netlist:
    from keysight.edatoolbox import ads as eda_ads
    out_dir = WS.parent / "sim_out"
    out_dir.mkdir(exist_ok=True)
    sim = eda_ads.CircuitSimulator()
    result = sim.run_netlist(netlist, output_dir=str(out_dir))
    print("=== run_netlist returned:", type(result), str(result)[:300])
    print("output_dir contents:", [p.name for p in out_dir.iterdir()])

    # --- dataset reading ---
    import keysight.ads.dataset as dataset
    ds_files = list(out_dir.glob("*.ds"))
    print("dataset files:", [p.name for p in ds_files])
    if ds_files:
        ds = dataset.open(ds_files[0], mode="r")
        names = ds.list() if hasattr(ds, "list") else None
        print("dataset names:", names)
