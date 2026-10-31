"""Bounded, build-only schematic execution. No solver or arbitrary-code endpoint.

A request describes an already-decided circuit. Native transactions protect the
new design; the independently declared pin partition catches opens and shorts.
Existing workspaces are never overwritten. A successful identical request may
be verified again, but changed or incomplete outputs require a new target.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Name = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=80)]
CellName = Annotated[str, Field(pattern=r"^[A-Za-z0-9_][A-Za-z0-9_]*$", max_length=80)]
Pin = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*:[1-9][0-9]*$")]
Value = Annotated[str, Field(min_length=1, max_length=2000)]
Point = tuple[float, float]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Target(Model):
    workspace: str
    library: Name
    cell: CellName


class Port(Model):
    instance: Name
    number: int = Field(ge=1, le=32)
    impedance: Value
    reference_plane: Value


class Contract(Model):
    execution: Literal["build_only"]
    testbench: Literal["linear_sp"]
    network_boundary: Value
    ports: list[Port] = Field(min_length=1, max_length=32)
    # These are explicit input values, not inferred defaults or promises.
    frequency_hz: tuple[float, float, float]
    result_meaning: Literal["initial_values_not_simulated"]


class Component(Model):
    name: Name
    library: Name
    cell: CellName
    at: Point
    angle: float = 0
    enabled: bool = True
    parameters: dict[Name, str | list[str]] = Field(default_factory=dict)
    forms: dict[Name, Value] = Field(default_factory=dict)
    variables: dict[Name, Value] = Field(default_factory=dict)


class Wire(Model):
    start: Pin
    end: Pin
    via: list[Point] = Field(default_factory=list, max_length=16)
    label: str = Field(default="", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$|^$", max_length=80)


class Net(Model):
    name: str = Field(min_length=1, max_length=80)
    pins: list[Pin] = Field(min_length=2, max_length=300)


class Note(Model):
    text: str = Field(min_length=1, max_length=4000)
    at: Point
    height: float = Field(default=0.22, gt=0, le=2)


def hz(value: str) -> float:
    match = re.fullmatch(r"\s*([+\-\d.eE]+)\s*(GHz|MHz|kHz|Hz)?\s*", value, re.I)
    if not match:
        raise ValueError(f"Explicit numeric frequency required: {value!r}")
    result = float(match[1]) * {"ghz": 1e9, "mhz": 1e6, "khz": 1e3, "hz": 1}[str(match[2] or 'Hz').lower()]
    if not math.isfinite(result):
        raise ValueError("Frequency must be finite")
    return result


class BuildPlan(Model):
    schema_version: Literal[1]
    target: Target
    contract: Contract
    components: list[Component] = Field(min_length=1, max_length=150)
    wires: list[Wire] = Field(default_factory=list, max_length=300)
    expected_nets: list[Net] = Field(min_length=1, max_length=300)
    open_pins: list[Pin] = Field(default_factory=list, max_length=300)
    notes: list[Note] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def consistency(self):
        if not Path(self.target.workspace).is_absolute():
            raise ValueError("target.workspace must be absolute")
        parts = {p.name: p for p in self.components}
        if len(parts) != len(self.components):
            raise ValueError("Duplicate instance names")
        variables = [v for p in self.components for v in p.variables]
        if len(variables) != len(set(variables)):
            raise ValueError("Duplicate variables across VAR blocks")
        for part in self.components:
            if part.library == 'ads_simulation' and part.cell not in {'Term','S_Param','MeasEqn','Goal','Optim'}:
                raise ValueError('This flow only configures linear SP; unsupported controller: ' + part.cell)
            if part.variables and (part.library, part.cell) != ("ads_datacmps", "VAR"):
                raise ValueError("variables require ads_datacmps:VAR")
            if part.library == "ads_datacmps" and part.cell == "MeasEqn":
                raise ValueError("ADS 2025 MeasEqn is ads_simulation:MeasEqn; use repeated Meas")
            if set(part.parameters) & set(part.forms):
                raise ValueError("A parameter cannot specify both value and form")
            if "Subst" in part.parameters:
                raise ValueError("Keep Subst at its native default and name the substrate MSub1")
            if part.cell == "MSUB" and part.name != "MSub1":
                raise ValueError("This build flow uses the native MSub1 substrate reference")
            if "OptimType" in part.parameters:
                raise ValueError("Select OptimType through forms, e.g. Gradient")
            for value in part.parameters.values():
                values = value if isinstance(value, list) else [value]
                if not values or len(values) > 128 or any(not v or len(v) > 2000 or '\n' in v or '\r' in v for v in values):
                    raise ValueError("Parameter values must be bounded nonempty single lines")
            if any('\n' in v or '\r' in v for v in part.variables.values()):
                raise ValueError('VAR values must be single lines')
        pins = [p for net in self.expected_nets for p in net.pins] + self.open_pins
        if len(pins) != len(set(pins)):
            raise ValueError("Each pin must belong to exactly one expected net or open_pins")
        if len({n.name for n in self.expected_nets}) != len(self.expected_nets):
            raise ValueError("Duplicate expected net names")
        groups = {p: n.name for n in self.expected_nets for p in n.pins}
        for pin in pins:
            if pin.rsplit(':', 1)[0] not in parts:
                raise ValueError("Unknown instance in expected pin: " + pin)
        for wire in self.wires:
            if wire.start not in groups or wire.end not in groups or groups[wire.start] != groups[wire.end]:
                raise ValueError("Wire endpoints must belong to the same declared expected net")
        terms = {p.name for p in self.components if (p.library, p.cell) == ('ads_simulation', 'Term')}
        if terms != {p.instance for p in self.contract.ports}:
            raise ValueError("contract.ports must cover every simulation Term")
        if len({p.number for p in self.contract.ports}) != len(self.contract.ports):
            raise ValueError("Duplicate port numbers")
        for port in self.contract.ports:
            p = parts[port.instance]
            if p.parameters.get('Num') != str(port.number) or p.parameters.get('Z') != port.impedance:
                raise ValueError("Port Num/Z differs from the declared contract")
        controllers = [p for p in self.components if (p.library, p.cell) == ('ads_simulation', 'S_Param')]
        if len(controllers) != 1:
            raise ValueError("Exactly one linear SP controller is required")
        lo, hi, step = self.contract.frequency_hz
        if not 0 < lo <= hi or step <= 0 or (hi-lo)/step > 1000000:
            raise ValueError("Invalid or excessively large frequency sweep")
        actual = tuple(hz(str(controllers[0].parameters.get(k, ''))) for k in ('Start', 'Stop', 'Step'))
        if any(not math.isclose(a,b,rel_tol=1e-12,abs_tol=1e-6) for a,b in zip(actual,(lo,hi,step))):
            raise ValueError("SP frequency sweep differs from the contract")
        for part in self.components:
            if part.cell == 'Goal' and part.parameters.get('SimInstanceName') != controllers[0].name:
                raise ValueError("Goal must reference the declared SP controller")
            if part.cell == 'Optim' and part.parameters.get('FinalAnalysis', 'None') not in ('None', controllers[0].name):
                raise ValueError("FinalAnalysis must be None or the declared SP controller")
        return self


def load_plan(path: str) -> BuildPlan:
    from schematic_templates import read_request
    return read_request(path)[0]


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def plan_digest(plan: BuildPlan) -> str:
    data=plan.model_dump()
    # Preserve v1 receipts written before explicit activation was supported.
    for part in data['components']:
        if part.get('enabled') is True:part.pop('enabled')
    return digest(json.dumps(data, sort_keys=True, ensure_ascii=False).encode('utf-8'))


def snapshot_fingerprint(folder: Path) -> dict:
    # Includes the OA cell, model definitions and workspace library mapping.
    # Ignore runtime logs/locks/preferences that do not define the circuit.
    return {str(p.relative_to(folder)): digest(p.read_bytes()) for p in sorted(folder.rglob('*'))
            if p.is_file() and '.ads-mcp' not in p.relative_to(folder).parts
            and p.suffix in ('.oa', '.defs', '.cfg', '.py', '.ael')}


def atomic_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding='utf-8')
    os.replace(temp, path)


@contextmanager
def target_lock(workspace: Path):
    workspace.parent.mkdir(parents=True, exist_ok=True)
    key = digest(os.path.normcase(str(workspace)).encode())[:16]
    # The file may remain after a crash; the OS lock, not file existence, owns it.
    with (workspace.parent / ('.ads-build-' + key + '.lock')).open('a+b') as f:
        f.seek(0); f.write(b'0'); f.flush(); f.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError('Another build owns this target; do not restart it') from exc
        try:
            yield
        finally:
            f.seek(0)
            if os.name == 'nt':
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def native_pins(design):
    out = {}
    for inst in design.instances:
        for pin in inst.inst_pins:
            term = pin.inst_term
            if not term.is_numbered or pin.snap_point is None:
                raise ValueError('Unsupported unnumbered/unsnapped pin on ' + inst.name)
            key = inst.name + ':' + str((term.term_number if hasattr(term, "term_number") else term.get_inst_term_number()))
            if key in out:
                raise ValueError('Multiple physical pins for ' + key)
            out[key] = pin
    return out


def partition(groups):
    return sorted(sorted(g) for g in groups)


def brief_checks(checks):
    return {k:v for k,v in checks.items() if k != 'pin_partition'}


def point_on_segment(point, a, b):
    dx,dy=b[0]-a[0],b[1]-a[1]
    cross=(point[0]-a[0])*dy-(point[1]-a[1])*dx
    eps=1e-7
    return abs(cross) <= eps*max(1,abs(dx),abs(dy)) and all(
        min(a[k],b[k])-eps <= point[k] <= max(a[k],b[k])+eps for k in (0,1))


def wire_points(plan, pins):
    """Resolve actual pins once and reject geometrically ambiguous contacts."""
    groups={p:n.name for n in plan.expected_nets for p in n.pins}
    groups.update({p:'open:'+p for p in plan.open_pins})
    routes=[]
    for wire in plan.wires:
        try:
            a,b=pins[wire.start].snap_point,pins[wire.end].snap_point
        except KeyError as exc:
            raise ValueError('Wire references nonexistent native pin ' + str(exc)) from exc
        points=[(float(a.x),float(a.y)),*wire.via,(float(b.x),float(b.y))]
        points=[v for i,v in enumerate(points) if not i or v != points[i-1]]
        for ref,pin in pins.items():
            if groups.get(ref) == groups[wire.start]:
                continue
            xy=(float(pin.snap_point.x),float(pin.snap_point.y))
            if any(point_on_segment(xy,a,b) for a,b in zip(points,points[1:])):
                raise ValueError(f'Wire {wire.start}->{wire.end} touches foreign-net pin {ref}; reroute explicitly')
        routes.append(points)
    return routes


def verify_design(design, plan: BuildPlan) -> dict:
    actual = {i.name: i for i in design.instances}
    if set(actual) != {p.name for p in plan.components}:
        raise ValueError('Instance inventory differs from the plan')
    checks = 0
    for part in plan.components:
        inst = actual[part.name]
        if inst.library_name != part.library or inst.cell_name != part.cell:
            raise ValueError('Wrong model for ' + part.name)
        if inst.is_deactivated == part.enabled:
            raise ValueError('Instance activation differs from plan: ' + part.name)
        if any(abs(a-b) > 1/160 for a,b in zip((inst.origin.x,inst.origin.y),part.at)):
            raise ValueError('Instance placement differs from plan: ' + part.name)
        for key, expected in part.parameters.items():
            value = inst.parameters[key].value
            # StringAndReference values may include their native netlist quotes.
            norm = lambda v: str(v).strip().strip('"')
            if isinstance(expected, list):
                matches = isinstance(value, list) and list(map(norm,value)) == list(map(norm,expected))
            else:
                matches = norm(value) == norm(expected)
            if not matches:
                raise ValueError(f'Parameter readback mismatch: {part.name}.{key}: {value!r} != {expected!r}')
            checks += 1
        for key, form in part.forms.items():
            if inst.parameters[key].form_name != form:
                raise ValueError('Form readback mismatch: ' + part.name + '.' + key)
            checks += 1
        if part.variables and dict(inst.vars) != part.variables:
            raise ValueError('VAR readback mismatch: ' + part.name)
        checks += len(part.variables)
    pins = native_pins(design)
    expected = [n.pins for n in plan.expected_nets] + [[p] for p in plan.open_pins]
    if set(pins) != {p for group in expected for p in group}:
        raise ValueError('Expected nets must cover every actual pin; missing/extra: ' +
                         str(sorted(set(pins) ^ {p for g in expected for p in g})))
    nets = defaultdict(list)
    for ref, pin in pins.items():
        net = pin.inst_term.net
        nets[net.name if net is not None else 'unconnected:' + ref].append(ref)
    if partition(nets.values()) != partition(expected):
        raise ValueError('Electrical connectivity mismatch (open or unintended short): ' +
                         json.dumps({'expected': partition(expected), 'actual': partition(nets.values())}))
    return {'instances': len(actual), 'parameter_checks': checks, 'pins': len(pins),
            'nets': len(nets), 'pin_partition': partition(nets.values()),
            'connectivity_verified': True, 'simulation_run': False, 'optimization_run': False}


def populate(engine, plan: BuildPlan):
    from ads_engine import get_de
    de = get_de(); design = engine._current_design()
    # Field errors roll back this transaction. ADS updates wire connectivity
    # at commit, so electrical verification MUST follow it and precede save.
    # A connectivity failure leaves an explicitly failed, unsaved new target.
    with de.db.Transaction(design, 'Build planned schematic') as tx:
        placed = {}
        for part in plan.components:
            result = engine.place_component(part.library, part.cell, *part.at,
                                            instance_name=part.name, angle=part.angle)
            if result['instance'] != part.name:
                raise ValueError('ADS renamed an instance unexpectedly')
            placed[part.name] = design.find_instance(part.name)
        # Validate all parameter names before changing any values.
        for part in plan.components:
            known = {p.name for p in placed[part.name].parameters}
            unknown = (set(part.parameters) | set(part.forms)) - known
            if unknown:
                raise ValueError(f'Unknown parameters on {part.name}: {sorted(unknown)}')
        for part in plan.components:
            inst = placed[part.name]
            for key, value in part.parameters.items():
                param = inst.parameters[key]
                if isinstance(value, list):
                    while len(param.repeats) < len(value):
                        param.repeats.clone()
                    if len(param.repeats) != len(value):
                        raise ValueError('Repeated parameter count differs: ' + part.name + '.' + key)
                param.value = value
                inst.invoke_item_parameter_changed_callback(key)
            for key, form in part.forms.items():
                choices = {f.name for f in inst.model_def.parameters[key].formset.forms}
                if form not in choices:
                    raise ValueError(f'Unknown form {part.name}.{key}={form}; available: {sorted(choices)}')
                inst.parameters[key].form_name = form
                inst.invoke_item_parameter_changed_callback(key)
            if part.variables:
                for key, value in part.variables.items():
                    inst.vars[key] = value
                # ADS requires at least one VAR entry at all times.
                for key in list(inst.vars):
                    if key not in part.variables:
                        del inst.vars[key]
            inst.update_item_annotation()
            if not part.enabled:inst.deactivate()
        pins = native_pins(design)
        routes = wire_points(plan, pins)
        for wire, points in zip(plan.wires, routes):
            if len(points) > 1:
                w = design.add_wire(points)
                if wire.label:
                    w.add_wire_label(wire.label)
            elif wire.label:
                raise ValueError('A zero-length connection cannot carry a wire label')
        for note in plan.notes:
            design.add_text(de.db_uu.LayerId(231), note.text, note.at, 'Arial', note.height)
        tx.commit()
    checks = verify_design(design, plan)
    netlist = str(design.generate_netlist())
    if not netlist.strip():
        raise ValueError('ADS generated an empty netlist')
    return checks, netlist


def execute(engine, plan: BuildPlan, *, validate_only=False, capture_snapshot=False) -> dict:
    """One bounded build or verified replay; no retries, simulation or GUI use."""
    from ads_engine import get_ads_install_dir, get_de
    started = time.perf_counter(); timings = {}
    workspace = Path(plan.target.workspace).resolve()
    recipe_hash = plan_digest(plan)
    report_path = workspace / '.ads-mcp' / 'build.json'
    for part in plan.components:
        # Actual native library/cell existence is still checked by ADS. This
        # inexpensive check catches a wrong library before workspace creation.
        if not (get_ads_install_dir()/'oalibs/rf'/part.library).is_dir():
            raise ValueError('Fast build accepts installed ADS RF libraries only: ' + part.library)
    if validate_only:
        return {'ok': True, 'status': 'static_preflight_passed', 'plan_hash': recipe_hash,
                'native_validation_pending': True, 'workspace_exists': workspace.exists(),
                'components': len(plan.components), 'wires': len(plan.wires),
                'simulation_run': False, 'optimization_run': False}
    if workspace.exists() and not report_path.is_file():
        # Reject an unrelated existing target before even creating a lock file
        # beside it. The same check is repeated under the lock against races.
        raise ValueError('Existing workspace is not a completed build output; select a new target')
    with engine._op(), target_lock(workspace):
        if engine.workspace_path is not None or get_de().workspace_is_open():
            raise RuntimeError('This MCP owns a workspace. Use schematic_workflow_client.py for an isolated session; do not close another task')
        receipt = None
        if workspace.exists():
            if not report_path.is_file():
                raise ValueError('Existing workspace is not a completed build output; select a new target')
            receipt = json.loads(report_path.read_text(encoding='utf-8'))
            if receipt.get('status') != 'completed' or receipt.get('plan_hash') != recipe_hash:
                raise ValueError('Existing output is incomplete or belongs to another plan; select a new target')
            if receipt.get('files') != snapshot_fingerprint(workspace):
                raise ValueError('Saved design was modified; refusing to overwrite or reuse it')
            if plan_digest(load_plan(str(report_path.parent/'request.json'))) != recipe_hash:
                raise ValueError('Recorded request artifact was modified; refusing to reuse it')
            if digest((report_path.parent/'verified.net').read_text(encoding='utf-8').encode()) != receipt.get('netlist_sha256'):
                raise ValueError('Verified netlist artifact was modified; refusing to reuse it')
        stage = 'workspace'
        try:
            t = time.perf_counter()
            engine.open_workspace(str(workspace)) if receipt else engine.create_workspace(str(workspace))
            timings[stage] = time.perf_counter()-t
            if receipt:
                de = get_de()
                design = de.db_uu.open_design(f'{plan.target.library}:{plan.target.cell}:schematic', de.db_uu.DesignMode.READ_ONLY)
                t = time.perf_counter(); stage = 'replay_verification'
                checks = verify_design(design, plan)
                netlist = str(design.generate_netlist())
                if digest(netlist.encode()) != receipt['netlist_sha256']:
                    raise ValueError('Netlist changed since the completed build')
                timings[stage] = time.perf_counter()-t
                engine.close_workspace()
                return {'ok': True, 'status': 'verified_existing', 'report': str(report_path),
                        'workspace': str(workspace), 'checks': brief_checks(checks),
                        'elapsed_seconds': time.perf_counter()-started, 'timings': timings}
            engine.create_library(plan.target.library)
            # Parameter callbacks for built-in microstrip parts look up cond.
            # Give only this newly-created library the standard ADS technology.
            lib = (getattr(get_de().Library, 'get', None) or get_de().get_open_library)(plan.target.library)
            lib.create_layout_tech_std_ads('millimeter', 1000000, True)
            engine.create_schematic(plan.target.library, plan.target.cell)
            t = time.perf_counter(); stage = 'build_and_check'
            checks, netlist = populate(engine, plan)
            timings[stage] = time.perf_counter()-t
            stage = 'save'; t = time.perf_counter()
            engine.save_schematic()
            engine.close_workspace()
            timings[stage] = time.perf_counter()-t
            stage = 'reopen_verification'; t = time.perf_counter()
            engine.open_workspace(str(workspace))
            de = get_de()
            design = de.db_uu.open_design(f'{plan.target.library}:{plan.target.cell}:schematic', de.db_uu.DesignMode.READ_ONLY)
            saved_checks = verify_design(design, plan)
            saved_netlist = str(design.generate_netlist())
            if netlist != saved_netlist or checks != saved_checks:
                raise ValueError('Saved/reopened circuit differs from the constructed circuit')
            snapshot=None
            if capture_snapshot:
                from design_snapshots import read_design
                from design_capabilities import capture
                snapshot,snapshot_netlist=read_design(design,plan.contract)
                if snapshot_netlist!=saved_netlist or not snapshot['complete']:
                    raise ValueError('Complete native snapshot required for a persistent single-schematic task')
                snapshot['capability_profile']=capture(design,workspace/'.ads-mcp/profiles')
            engine.close_workspace()
            timings[stage] = time.perf_counter()-t
            report_path.parent.mkdir(parents=True, exist_ok=True)
            netlist_path = report_path.parent/'verified.net'
            netlist_path.write_text(netlist, encoding='utf-8')
            atomic_json(report_path.parent/'request.json', plan.model_dump())
            report = {'status': 'completed', 'plan_hash': recipe_hash, 'checks': checks,
                      'contract': plan.contract.model_dump(), 'netlist_sha256': digest(netlist.encode()),
                      'timings': timings, 'elapsed_seconds': time.perf_counter()-started,
                      'files': snapshot_fingerprint(workspace)}
            atomic_json(report_path, report)
            extra={}
            if snapshot is not None:
                from design_snapshots import manifest,file_hash
                snapshot.update(source=plan.target.model_dump(),source_files=manifest(workspace),
                    netlist=str(netlist_path),netlist_sha256=file_hash(netlist_path))
                snapshot_path=report_path.parent/'snapshot.json';atomic_json(snapshot_path,snapshot)
                extra={'snapshot':str(snapshot_path),'snapshot_sha256':file_hash(snapshot_path)}
            return {'ok': True, 'status': 'completed', 'workspace': str(workspace), **extra,
                    'schematic': f'{plan.target.library}:{plan.target.cell}:schematic',
                    'report': str(report_path), 'netlist': str(netlist_path), 'checks': brief_checks(checks),
                    'elapsed_seconds': report['elapsed_seconds'], 'timings': timings}
        except Exception as exc:
            # Failed outputs remain explicit and are never silently retried.
            if receipt is None and workspace.exists():
                atomic_json(report_path, {'status': 'failed', 'stage': stage, 'plan_hash': recipe_hash,
                                         'error': f'{type(exc).__name__}: {exc}', 'timings': timings})
            raise
        finally:
            if engine.workspace_path is not None:
                engine.close_workspace()


def register(mcp, engine, state, dispatch_lock):
    @mcp.tool()
    def ads_build_schematic(request_file: str, action: Literal['validate', 'build'] = 'build') -> dict:
        """Execute an already-decided schematic plan in a NEW isolated workspace; never simulate.

        Read SCHEMATIC-WORKFLOW.zh-CN.md and examples/schematic/output_match.json.
        One call validates, places, wires actual pins, sets conditions, saves and
        reopens once. Unknown fields/parameters or connectivity mismatch fail.
        Existing outputs are verified only for an identical unchanged plan.
        If this server owns a workspace, use the isolated CLI; never close a
        workspace belonging to another task. validate is static only.
        """
        try:
            from schematic_templates import read_request
            plan, expansion = read_request(request_file)
            with dispatch_lock:
                if any(t['status'] == 'running' for t in state.snapshot()['tasks'].values()):
                    raise RuntimeError('This session has a running task; use an isolated client')
                result = execute(engine, plan, validate_only=action == 'validate')
                if expansion.get('template'):
                    result['template'] = expansion['template']
                    result['request_bytes'] = expansion['input_bytes']
                    result['expanded_bytes'] = expansion['expanded_bytes']
                return result
        except Exception as exc:
            return {'ok': False, 'error': f'{type(exc).__name__}: {exc}',
                    'simulation_run': False, 'optimization_run': False}
