"""Native two-port emModel testbench construction for explicit SP conditions."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator


class EMVariable(BaseModel):
    model_config = ConfigDict(extra='forbid')
    parameter: str = Field(pattern=r'^[A-Za-z_][A-Za-z0-9_]*$', max_length=128)
    initial: FiniteFloat
    lower: FiniteFloat
    upper: FiniteFloat

    @model_validator(mode='after')
    def bounds(self):
        if not self.lower < self.upper or not self.lower <= self.initial <= self.upper:
            raise ValueError('Each initial value must lie within increasing explicit bounds')
        return self


class EMSGoal(BaseModel):
    model_config = ConfigDict(extra='forbid')
    row: int = Field(ge=1, le=2, strict=True)
    column: int = Field(ge=1, le=2, strict=True)
    comparison: Literal['at_most', 'at_least']
    bound_db: FiniteFloat
    weight: FiniteFloat = Field(default=1., gt=0)


def build_em_testbench(a):
    import hashlib
    import json
    import xml.etree.ElementTree as ET
    from pathlib import Path
    from keysight.ads import de
    from keysight.ads.de import db_uu as db
    if not de.workspace_is_open():
        raise RuntimeError("Open the explicitly selected workspace first")
    ws=Path(de.active_workspace().path).resolve()
    lib=de.get_open_library(a['lib'])
    if not Path(lib.path).resolve().is_relative_to(ws):
        raise ValueError('The testbench library must be local to this workspace')
    source=lib.cell(a['cell'])
    if source.model_def is None:
        raise ValueError('Native cell item definition is missing')
    unknown=set(a['parameters'])-{p.name for p in source.model_def.parameters}
    if unknown:
        raise ValueError('Unknown native model parameters: '+str(sorted(unknown)))
    for view in (a['model'],a['symbol']):
        if not source.view_exists(view):
            raise ValueError('Missing native model/symbol view: '+view)
    folder=Path(de._pde.get_path_to_cellview(a['lib'],a['cell'],a['model'])).resolve()
    if not folder.is_relative_to(ws) or not (folder/'emDataIndex.xml').is_file():
        raise ValueError('A local native emModel with solved data is required')
    state=ET.parse(folder/'emStateFile.xml')
    em_ports=state.findall('.//PortView/Ports/Port')
    if len(em_ports)!=2 or sorted(p.findtext('portNum') for p in em_ports)!=['1','2']:
        raise ValueError('Native EM model setup must contain exactly two ports 1 and 2')
    conditions=[]
    for p in em_ports:
        z=p.findtext('refImpedance','').split(':')
        if len(z)!=2 or float(z[0])!=50. or float(z[1])!=0.:
            raise ValueError('This testbench requires native EM ports with real 50-ohm reference impedance')
        conditions.append({'number':int(p.findtext('portNum')),'name':p.findtext('portName'),
            'feed_type':p.findtext('feedType'),'reference_offset':p.findtext('refoffset'),
            'plus_pins':[n.text for n in p.findall('PlusPinNames/stdItem')],
            'minus_pins':[n.text for n in p.findall('MinusPinNames/stdItem')]})
    sym=db.open_design(f"{a['lib']}:{a['cell']}:{a['symbol']}",db.DesignMode.READ_ONLY)
    if sorted(t.number for t in sym.terms)!=[1,2]:
        raise ValueError('This testbench constructor supports exactly two numbered ports 1 and 2')
    target=f"{a['lib']}:{a['testbench']}:schematic"
    if lib.cell_exists(a['testbench']):
        raise ValueError('Use a new independent testbench cell; existing cells are never replaced')
    output=ws/'.ads-mcp'/'em_testbenches'/a['testbench']
    if output.exists():
        raise ValueError('Testbench evidence directory already exists; use a new name')
    d=db.create_schematic(target)
    def pins(inst):
        result={}
        for p in inst.inst_pins:
            if p.inst_term.is_numbered:
                result[p.inst_term.get_inst_term_number()]=p.snap_point
        return result
    def point(p):return (float(p.x),float(p.y))
    def connect(p,q):
        p,q=point(p),point(q)
        if p!=q:d.add_wire([p,q])
    def ground(p,name):
        return d.add_instance(('ads_rflib','GROUND','symbol'),point(p),name=name)
    dut=d.add_instance((a['lib'],a['cell'],a['symbol']),(0.,0.),name='EM_DUT')
    for key,value in a['parameters'].items():dut.parameters[key].value=value
    dut.specialized_subview=a['model']
    dp=pins(dut)
    ports={}
    for number,sign in ((1,-1),(2,1)):
        p=dp[number]
        term=d.add_instance(('ads_simulation','Term','symbol'),(p.x+sign*3.,p.y),name='Term'+str(number),angle=-90.)
        term.parameters['Num'].value=str(number)
        term.parameters['Z'].value='50 Ohm'
        tp=pins(term);ground(tp[2],'GND'+str(number))
        ports[number]=tp[1]
    if a.get('series_resistance_ohms') is not None:
        p=dp[1]
        resistor=d.add_instance(('ads_rflib','R','symbol'),(p.x-2.,p.y),name='R_series')
        resistor.parameters['R'].value=f"{a['series_resistance_ohms']:.15g} Ohm"
        rp=pins(resistor);connect(ports[1],rp[1]);connect(rp[2],dp[1])
    else:connect(ports[1],dp[1])
    connect(dp[2],ports[2])
    if a.get('shunt_capacitance_pf') is not None:
        p=dp[2]
        cap=d.add_instance(('ads_rflib','C','symbol'),(p.x+1.5,p.y),name='C_shunt',angle=-90.)
        cap.parameters['C'].value=f"{a['shunt_capacitance_pf']:.15g} pF"
        cp=pins(cap);connect(dp[2],cp[1]);ground(cp[2],'GND_C')
    sp=d.add_instance(('ads_simulation','S_Param','symbol'),(0.,4.),name='SP1')
    sp.parameters['Start'].value=f"{a['start_ghz']:.15g} GHz"
    sp.parameters['Stop'].value=f"{a['stop_ghz']:.15g} GHz"
    sp.parameters['Step'].value=f"{(a['stop_ghz']-a['start_ghz'])/(a['points']-1):.15g} GHz"
    d.save_design()
    netlist=str(d.generate_netlist())
    if 'MomCmpt' not in netlist:
        raise RuntimeError('Saved testbench did not netlist the native MomCmpt emModel view')
    output.mkdir(parents=True,exist_ok=False)
    (output/'netlist.log').write_text(netlist,encoding='utf-8')
    result={'design':target,'model':f"{a['lib']}:{a['cell']}:{a['model']}",
            'model_folder':str(folder),'simulation_view':str(dut.specialized_subview),
            'native_em_port_conditions':conditions,
            'parameters':{p.name:str(p.value) for p in dut.parameters},
            'port_reference_ohms':50.,'frequency':{'start_ghz':a['start_ghz'],'stop_ghz':a['stop_ghz'],'points':a['points']},
            'series_resistance_ohms':a.get('series_resistance_ohms'),'shunt_capacitance_pf':a.get('shunt_capacitance_pf'),
            'netlist_file':str(output/'netlist.log'),'netlist_sha256':hashlib.sha256(netlist.encode()).hexdigest(),
            'saved':True,'simulated':False,'boundary':'Two-port linear SP. EM ports are used without added reference-plane shifts or inferred package parasitics.'}
    (output/'manifest.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


def build_em_optimization(a):
    """New native Optim bench. Native iteration budget is not an EM solve cap."""
    import hashlib
    import json
    import xml.etree.ElementTree as ET
    from pathlib import Path
    from keysight.ads import de
    from keysight.ads.de import db_uu as db
    lib=de.get_open_library(a['lib'])
    source=lib.cell(a['cell'])
    names={p.name for p in source.model_def.parameters}
    variables=a['variables']
    if set(v['parameter'] for v in variables)-names:
        raise ValueError('An optimization variable is not a native model parameter')
    model_folder=Path(de._pde.get_path_to_cellview(a['lib'],a['cell'],a['model']))
    options=ET.parse(model_folder/'emDataOptions.xml').find('cellParameters')
    if options is None or options.get('useInterpolation')!='no':
        raise ValueError('Disable native geometry interpolation before a verified EM optimization')
    # Preflight native controller forms before creating any new design.
    optim_model=de.get_open_library('ads_simulation').cell('Optim').model_def
    forms={f.name for f in optim_model.parameters['OptimType'].formset.forms}
    if 'Gradient' not in forms:
        raise ValueError('This ADS version does not expose the tested Gradient form')
    parameters=dict(a['parameters'])
    parameters.update({v['parameter']:format(v['initial'],'.15g') for v in variables})
    result=build_em_testbench(dict(a,parameters=parameters))
    d=db.open_design(result['design'],db.DesignMode.APPEND)
    var=d.add_instance(('ads_datacmps','VAR','symbol'),(0.,-3.),name='VAR_EM_OPT')
    dut=d.find_instance('EM_DUT')
    mapping={}
    for v in variables:
        n='emopt_'+v['parameter']
        var.vars[n]=f"{v['initial']:.15g} opt{{{v['lower']:.15g} to {v['upper']:.15g}}}"
        dut.parameters[v['parameter']].value=n
        mapping[v['parameter']]=n
    for index,spec in enumerate(a['goals'],1):
        goal=d.add_instance(('ads_simulation','Goal','symbol'),(3.,-2.-index),name='EM_GOAL'+str(index))
        goal.parameters['Expr'].value=f"dB(S({spec['row']},{spec['column']}))"
        goal.parameters['SimInstanceName'].value='SP1'
        goal.parameters['Max' if spec['comparison']=='at_most' else 'Min'].value=format(spec['bound_db'],'.15g')
        goal.parameters['Weight'].value=format(spec['weight'],'.15g')
        goal.parameters['RangeVar'].value=['freq']
        goal.parameters['RangeMin'].value=[format(a['start_ghz']*1e9,'.15g')]
        goal.parameters['RangeMax'].value=[format(a['stop_ghz']*1e9,'.15g')]
    opt=d.add_instance(('ads_simulation','Optim','symbol'),(0.,-5.),name='EM_OPT')
    opt.parameters['OptimType'].form_name='Gradient'
    opt.parameters['MaxIters'].value=str(a['max_iterations'])
    opt.parameters['DesiredError'].value='0'
    # This is a quoted analysis-controller name, not a Boolean switch.
    opt.parameters['FinalAnalysis'].value='"SP1"'
    for key in ('SaveSolns','SaveGoals','SaveOptimVars','SaveNominal','SaveAllTrials','EnableCockpit'):
        opt.parameters[key].form_name='Yes'
    # Native ADS rejects simultaneous iteration-only and all-trial recording.
    opt.parameters['SaveAllIterations'].form_name='No'
    # Never back-annotate the source schematic when the cockpit is released.
    opt.parameters['SetBestValues'].form_name='No'
    d.save_design()
    netlist=str(d.generate_netlist())
    folder=Path(result['netlist_file']).parent
    (folder/'initial-netlist.log').write_bytes((folder/'netlist.log').read_bytes())
    (folder/'netlist.log').write_text(netlist,encoding='utf-8')
    result.update(variables=variables,parameter_mapping=mapping,goals=a['goals'],
        optimizer='Gradient',max_iterations=a['max_iterations'],
        budget_kind='native_optimizer_iterations',em_solve_count_cap=None,
        budget_note='A native iteration may evaluate multiple EM geometries. MaxIters is not a maximum number of solves.',
        back_annotation=False,geometry_interpolation=False,
        netlist_sha256=hashlib.sha256(netlist.encode()).hexdigest())
    (folder/'manifest.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


build_em_optimization.__ads_mcp_dependencies__=(build_em_testbench,)


def build_em_candidate(a):
    """Materialize explicit parameters as a new native two-port subnetwork."""
    import hashlib,json
    from pathlib import Path
    from keysight.ads import de
    from keysight.ads.de import db_uu as db
    ws=Path(de.active_workspace().path).resolve()
    lib=de.get_open_library(a['lib'])
    if not Path(lib.path).resolve().is_relative_to(ws):
        raise ValueError('Candidate output must use a local workspace library')
    source=lib.cell(a['cell'])
    if not source.view_exists('schematic') or not source.view_exists(a['symbol']) or not source.view_exists('layout'):
        raise ValueError('Source requires native schematic, symbol and layout views')
    if source.model_def is None:
        raise ValueError('Source native model definition is missing')
    names={p.name for p in source.model_def.parameters}
    if set(a['parameters'])!=names:
        raise ValueError('Supply every native parameter explicitly, without inferred defaults: '+str(sorted(names)))
    symbol=db.open_design(f"{a['lib']}:{a['cell']}:{a['symbol']}",db.DesignMode.READ_ONLY)
    if sorted(t.number for t in symbol.terms)!=[1,2]:
        raise ValueError('Candidate materializer currently supports exactly two numbered ports')
    if lib.cell_exists(a['candidate']):
        raise ValueError('Candidate must be a new independent cell')
    design=f"{a['lib']}:{a['candidate']}:schematic"
    output=ws/'.ads-mcp'/'em_candidates'/a['candidate']
    if output.exists():
        raise ValueError('Candidate evidence already exists; choose a new name')
    d=db.create_schematic(design)
    dut=d.add_instance((a['lib'],a['cell'],a['symbol']),(0.,0.),name='DUT')
    dut.specialized_subview='schematic'
    for name,value in a['parameters'].items():dut.parameters[name].value=format(value,'.15g')
    pins={p.inst_term.get_inst_term_number():p for p in dut.inst_pins if p.inst_term.is_numbered}
    for n in (1,2):
        p=pins[n];net=p.inst_term.net or d.find_or_add_net('P'+str(n))
        p.inst_term.net=net;term=d.add_term(net,'P'+str(n));term.number=n
        d.add_pin(term,d.add_dot(db.LayerId(231),p.snap_point),angle=180. if n==1 else 0.)
    d.save_design()
    # A fixed wrapper still needs a persistent native Subnetwork itemdef for
    # placement as a MomCmpt DUT after its EM model is generated.
    item_definition=design_operation({'operation':'formal_parameters','lib':a['lib'],
        'cell':a['candidate'],'parameters':[],'replace':False})
    netlist=str(d.generate_netlist())
    output.mkdir(parents=True,exist_ok=False)
    (output/'netlist.log').write_text(netlist,encoding='utf-8')
    result={'design':design,'source_cell':a['lib']+':'+a['cell'],
            'item_definition':item_definition,
            'parameters':a['parameters'],'source_simulation_view':'schematic',
            'ports':[{'number':t.number,'name':t.name,'net':t.net.name} for t in d.terms],
            'netlist_sha256':hashlib.sha256(netlist.encode()).hexdigest(),
            'netlist_file':str(output/'netlist.log'),'saved':True,'simulated':False,
            'next_steps':'Generate native layout, verify actual geometry, configure explicit substrate/ports/grid and solve in this new cell. Existing model samples are not copied.',
            'dependency_note':'The new cell preserves a reference to the source native subnetwork; its source definition must remain available and unchanged.'}
    (output/'manifest.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


from design_operations import design_operation
build_em_candidate.__ads_mcp_dependencies__=(design_operation,)
