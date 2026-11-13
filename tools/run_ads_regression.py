"""Run five native ADS 2025 regression cases in separate, fresh task directories."""
import argparse
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
CASES = ("workspace_guard", "native_pins", "series_resistor_sp", "matching_edit", "single_tone_hb")


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def read(path):
    return Path(path).read_text(encoding="utf-8-sig")


def checked(condition, reason):
    if not condition:
        raise ValueError(reason)


@asynccontextmanager
async def server(root, install):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    config = root/"home/hpeesof/config/SystemVue"
    config.mkdir(parents=True)
    (config/"lib.defs").write_text("# isolated regression\n",encoding="utf-8")
    env = dict(os.environ,HPEESOF_DIR=str(install),HOME=str(root/"home"),ADS_MCP_PROFILE="full",
               ADS_MCP_TASK_DIR=str(root),ADS_MCP_WORKROOT=str(root/"jobs"),ADS_MCP_STATE_FILE=str(root/"state.json"),
               MPLCONFIGDIR=str(root/"mpl"),PYTHONUTF8="1",PYTHONDONTWRITEBYTECODE="1")
    params = StdioServerParameters(command=sys.executable,args=["-B",str(REPO/"mcp_server.py")],cwd=str(root),env=env)
    with (root/"server.log").open("w",encoding="utf-8") as log:
        async with stdio_client(params,errlog=log) as (r,w):
            async with ClientSession(r,w) as session:
                await asyncio.wait_for(session.initialize(),60)
                async def call(name, *, expect_ok=True, **args):
                    reply = await session.call_tool(name,args)
                    result = ({"ok":False,"error":reply.content[0].text} if reply.isError else
                              reply.structuredContent or json.loads(reply.content[0].text))
                    if bool(result.get("ok")) != expect_ok:
                        raise ValueError(f"{name}: {result}")
                    return result
                yield call


async def operation(call, task, action, label, payload):
    args = dict(action=action,task_directory=str(task),request_file=write(task/(label+".json"),payload))
    return await call("ads_design_workflow",**args)


def resistor_fixture(task):
    ports = [dict(instance=f"Term{n}",number=n,impedance="50 Ohm",reference_plane=f"R_port_{n}") for n in (1,2)]
    parts = [dict(name="SP1",library="ads_simulation",cell="S_Param",at=[0,-3],
                  parameters=dict(Start="1 GHz",Stop="1.2 GHz",Step="0.1 GHz")),
             dict(name="R1",library="ads_rflib",cell="R",at=[2,0],parameters={"R":"50 Ohm"})]
    for n,x in ((1,0),(2,6)):
        parts.extend([dict(name=f"Term{n}",library="ads_simulation",cell="Term",at=[x,0],angle=-90,
                           parameters={"Num":str(n),"Z":"50 Ohm"}),
                      dict(name=f"G{n}",library="ads_rflib",cell="GROUND",at=[x,-1],angle=-90)])
    return dict(schema_version=1,target=dict(workspace=str(task/"design_wrk"),library="regression_lib",cell="series_r"),
                contract=dict(execution="build_only",testbench="linear_sp",network_boundary="One 50 ohm series resistor",
                              ports=ports,frequency_hz=[1e9,1.2e9,1e8],result_meaning="initial_values_not_simulated"),
                components=parts,wires=[dict(start="Term1:1",end="R1:1"),dict(start="R1:2",end="Term2:1")],
                expected_nets=[dict(name="input",pins=["Term1:1","R1:1"]),dict(name="output",pins=["Term2:1","R1:2"]),
                               dict(name="gnd!",pins=["G1:1","G2:1","Term1:2","Term2:2"])])


async def build(call, task, plan):
    source = write(task/"fixture.json",plan)
    await operation(call,task,"run","build",dict(operation="build",request_id="build",build_request=source))
    state = json.loads(read(task/"task.json"))
    return json.loads(read(state["snapshot"]))


async def guard_case(call, task, snap):
    from workspace_activity import reserve, release
    from design_snapshots import manifest
    ws = Path(snap["source"]["workspace"])
    before = manifest(ws)
    reserve(ws,"regression-guard","owned-test-reservation",task/"guard-state.json",os.getpid())
    try:
        response = await call("ads_set_instance_parameters",expect_ok=False,workspace=str(ws),
                              lib="regression_lib",cell="series_r",instance="R1",parameters={"R":"900 Ohm"})
        checked("reserved by native task regression-guard" in str(response),"Unexpected write-guard error")
    finally:
        release(ws,"owned-test-reservation")
    checked(manifest(ws)==before,"Guarded native files changed")
    return dict(refused=True,files_unchanged=True)


def pin_case(plan, snap):
    want = {frozenset(n["pins"]) for n in plan["expected_nets"]}
    native = {frozenset(n["pins"]) for n in snap["connections"]}
    checked(native==want,"Saved native pin partition differs")
    return {"pins":sum(map(len,native)),"native_groups":[sorted(n) for n in sorted(native,key=lambda s:sorted(s))]}


async def sp_case(call, task):
    import numpy as np
    spec=dict(start_hz=1e9,stop_hz=1.2e9,reference_ohms=50,load_r_ohm=50,load_x_ohm=0,target_r_ohm=50,
              target_x_ohm=0,input_port=1,reference_plane="R_port_1")
    await operation(call,task,"run","sp",dict(operation="simulate",request_id="sp",
                    simulation=dict(authorization="run_linear_sp_on_copy",evaluation=spec,use_cache=False)))
    revision=-1
    while True:
        status=await operation(call,task,"run","poll",dict(operation="status",after_revision=revision,wait_seconds=20))
        if status["status"]=="completed":
            break
        checked(status["status"] in ("queued","running"),"Unexpected SP job status: "+str(status["status"]))
        revision=status["job_revision"]
    evidence=json.loads(read(task/"operations/sp/evidence.json"))
    with np.load(evidence["result"]["raw_sparameters"],allow_pickle=False) as raw:
        checked(np.allclose(raw["freq_hz"],[1e9,1.1e9,1.2e9],rtol=0,atol=1),"Unexpected SP grid")
        error=float(np.max(abs(raw["s"]-np.array([[1/3,2/3],[2/3,1/3]]))))
    checked(error<1e-8,"Complex S differs from analytical 50 ohm series resistor")
    return dict(points=3,max_complex_s_error=error,evidence=str(task/"operations/sp/evidence.json"))


async def matching_case(call, task):
    plan=json.loads(read(REPO/"examples/schematic/output_match_short.json"))
    plan["target"]["workspace"]=str(task/"design_wrk")
    before=await build(call,task,plan)
    old=next(p["variables"]["L_TL1"] for p in before["components"] if p["name"]=="VAR1")
    value="3.25 mm"
    result=await operation(call,task,"run","change",dict(operation="edit",request_id="change",
                           changes=[dict(instance="VAR1",field="variables",key="L_TL1",value=value)]))
    state=json.loads(read(task/"task.json"))
    after=json.loads(read(state["snapshot"]))
    actual=next(p["variables"]["L_TL1"] for p in after["components"] if p["name"]=="VAR1")
    checked(old!=actual==value and result["acceptance"]["saved_reopened"] and
            after["connections"]==before["connections"],"Native matching edit/readback differs")
    return dict(variable="VAR1.L_TL1",before=old,after=actual,net_partition_unchanged=True,snapshot=state["snapshot"])


async def hb_case(call, task, install):
    from matching_sweep import NetlistEditor
    archive=install/"examples/RF_Microwave/ClassC_AmpDesR2_wrk.7zads"
    extract=task/"example"
    extract.mkdir(parents=True)
    seven=install/"bin/7za.exe"
    listed=subprocess.check_output([str(seven),"l","-slt",str(archive)],text=True,errors="replace")
    files=listed.split("----------",1)[-1]
    for line in files.splitlines():
        if line.startswith("Path = "):
            member=PureWindowsPath(line[7:])
            checked(not member.drive and not member.root and ".." not in member.parts,"External member in ADS example archive")
    subprocess.run([str(seven),"x",str(archive),"-o"+str(extract)],check=True,capture_output=True)
    await call("ads_open_workspace",path=str(extract/"ClassC_AmpDesR2_wrk"))
    await call("ads_open_design",lib="ClassC_AmpDesR2_lib",cell="HB1TonePAE_Pswp_ClassC",show=False)
    vendor=(await call("ads_generate_netlist",save=False))["netlist"]
    # This bundled example contains a swept controller. The regression uses one explicit point.
    lines=[]
    for line in NetlistEditor(vendor).lines:
        if line.lstrip().startswith(("SweepPlan:","Tran:","Component:tahb_")):
            continue
        if line.lstrip().startswith("HB:"):
            line=re.sub(r'\s+Sweep(?:Var|Plan)="[^"]*"',"",line)
        lines.append(re.sub(r"^(\s*)global\s+(RFpower\s*=)",r"\1\2",line))
    deck=task/"hb.net"
    deck.write_text("\n".join(lines)+"\n",encoding="utf-8")
    req=json.loads(read(REPO/"examples/rf_jobs/hb-baseline.json"))
    req.update(netlist_path=str(deck),output_dir=str(task/"solve"))
    req["operating_points"]=[dict(freq_hz=935.2e6,pavs_dbm=8,bias_values=[4.8,-1.8])]
    req["contract"]["network_boundary"]="Complete bundled amplifier, fixed matching, bias and drive at the specified point"
    req["contract"]["model_conditions"]="Bundled Generic_GaAsFet Area=10, drain 4.8 V and gate -1.8 V"
    job=await call("ads_submit_rf_job",workflow="hb",request_file=write(task/"request.json",req))
    revision=-1
    while True:
        status=await call("ads_rf_job",action="wait",job_id=job["job_id"],after_revision=revision,wait_seconds=20)
        if status["status"]=="completed":
            break
        checked(status["status"] in ("queued","running"),"HB job failed: "+str(status))
        revision=status["revision"]
    case=json.loads(read(task/"solve/cases.json"))[0]
    checked(case["convergence"]["solver_accepted"] and case["convergence"]["complete_harmonic_grid"],
            "Incomplete harmonic grid or unaccepted native solve")
    checked(abs(case["pout_dbm"]-34.463)<=0.25 and abs(case["pae_pct"]-60.898)<=2.0,
            f'HB regression outside acceptance window: Pout={case["pout_dbm"]}, PAE={case["pae_pct"]}')
    return dict(pout_dbm=case["pout_dbm"],gain_db=case["gain_db"],pae_pct=case["pae_pct"],
                harmonic_order=15,case=str(task/"solve/cases.json"),archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest())


async def run(install, root, report):
    info=read(install/"installer_info.txt")
    installed=re.search(r"(?im)^PRODUCT_NAME=Advanced Design System\s+(\d{4})\b",info)
    checked(installed and installed[1]==report["version"],"Installation product name does not match requested year")
    async def record(key, fn):
        try:
            value=await fn()
            report["cases"][key]=dict(status="passed",**value)
        except Exception as exc:
            report["cases"][key]=dict(status="failed",reason=str(exc))
        write(root/"report.json",report)
    task=root/"resistor"
    async with server(task,install) as call:
        identity=await call("ads_runtime_selfcheck")
        actual=identity["ads"]
        checked(actual["ok"] and Path(actual["hpeesof_dir"]).resolve()==install,"Runtime and requested ADS root differ")
        report.update(product_version=actual["native_product_version"].strip(),python_de_version=actual["python_de_version"],
                      python=sys.version.split()[0],ads_root=str(install))
        plan=resistor_fixture(task)
        try:
            snap=await build(call,task,plan)
        except Exception as exc:
            for key in CASES[:3]:
                report["cases"][key]={"status":"not_run","reason":"Resistor fixture setup failed: "+str(exc)}
        else:
            await record("workspace_guard",lambda:guard_case(call,task,snap))
            try:
                report["cases"]["native_pins"]=dict(status="passed",**pin_case(plan,snap))
            except ValueError as exc:
                report["cases"]["native_pins"]=dict(status="failed",reason=str(exc))
            await record("series_resistor_sp",lambda:sp_case(call,task))
    async with server(root/"matching",install) as call:
        await record("matching_edit",lambda:matching_case(call,root/"matching"))
    async with server(root/"hb",install) as call:
        await record("single_tone_hb",lambda:hb_case(call,root/"hb",install))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ads-root",type=Path,required=True,help="Existing ADS 2025 installation")
    parser.add_argument("--output",type=Path,required=True,help="New, independent regression directory")
    args=parser.parse_args()
    root=args.output.resolve()
    root.mkdir(parents=True,exist_ok=False)
    report=dict(version="2025",cases={case:dict(status="not_run",reason="Not reached") for case in CASES})
    try:
        asyncio.run(run(args.ads_root.resolve(strict=True),root,report))
    except Exception as exc:
        report["setup_error"]=f"{type(exc).__name__}: {exc}"
    report["status"]="passed" if all(c["status"]=="passed" for c in report["cases"].values()) else "incomplete"
    write(root/"report.json",report)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report["status"]=="passed" else 1


if __name__=="__main__":
    raise SystemExit(main())
