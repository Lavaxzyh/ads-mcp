"""Versioned structural recipes; electrical choices have no historical defaults."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
from typing import Literal

from pydantic import Field
from schematic_flow import Model, Target, Contract, BuildPlan, Name, Value

ROOT = Path(__file__).resolve().parent / 'templates'


class Override(Model):
    instance: Name
    field: Literal['parameters', 'forms', 'variables']
    key: Name
    value: str | list[str]


class TemplateRequest(Model):
    schema_version: Literal[1]
    kind: Literal['template']
    template: str
    target: Target
    contract: Contract
    optimizer_enabled: bool
    inputs: dict[str, Value]
    overrides: list[Override] = Field(default_factory=list, max_length=100)


def definition(template_id: str) -> dict:
    if template_id != 'output_match_2stub_sp@1':
        raise ValueError('Unsupported template; list templates before designing a new topology')
    data = json.loads((ROOT / 'output_match_2stub_sp.v1.json').read_text(encoding='utf-8'))
    actual = hashlib.sha256(json.dumps(data['plan'], sort_keys=True).encode()).hexdigest()
    if data['plan_sha256'] != actual:
        raise ValueError('Template structure changed without a new verification/version')
    return data


def catalog() -> list[dict]:
    d = definition('output_match_2stub_sp@1')
    return [{k: v for k, v in d.items() if k != 'plan'}]


def expand(payload: dict) -> tuple[BuildPlan, dict]:
    request = TemplateRequest.model_validate(payload)
    template = definition(request.template)
    required = set(template['required_inputs'])
    if required != set(request.inputs):
        raise ValueError(f'Template inputs mismatch: missing={sorted(required-set(request.inputs))}, unknown={sorted(set(request.inputs)-required)}')
    ports = request.contract.ports
    if [(p.instance, p.number) for p in ports] != [('Term1', 1), ('Term2', 2)]:
        raise ValueError('This template requires Term1/1 and Term2/2 in that order')
    numeric_ports = []
    for p in ports:
        match = re.fullmatch(r'\s*([\d.eE+\-]+)\s*(?:Ohm)?\s*', p.impedance, re.I)
        if not match or not 0 < float(match[1]) < float('inf'):
            raise ValueError('This template requires explicit positive real SP reference impedances in Ohm')
        numeric_ports.append(match[1])
    automatic = {'port1': ports[0].impedance, 'port2': ports[1].impedance,
                 'Zref': numeric_ports[0]}
    lo, hi, step = request.contract.frequency_hz
    automatic.update(Start=f'{lo:.15g} Hz', Stop=f'{hi:.15g} Hz', Step=f'{step:.15g} Hz',
                     RangeMin=f'{lo:.15g}', RangeMax=f'{hi:.15g}')

    def resolve(value):
        if isinstance(value, dict):
            if set(value) == {'$input'}: return request.inputs[value['$input']]
            if set(value) == {'$derived'}: return automatic[value['$derived']]
            return {k: resolve(v) for k, v in value.items()}
        if isinstance(value, list): return [resolve(v) for v in value]
        return value

    plan = resolve(copy.deepcopy(template['plan']))
    plan['target'] = request.target.model_dump()
    plan['contract'] = request.contract.model_dump()
    parts = {c['name']: c for c in plan['components']}
    parts['OPT1']['enabled'] = request.optimizer_enabled
    changes, seen = [], set()
    # Contract and substrate/target fields are explicit inputs, not override
    # shortcuts. A topology change requires another verified recipe/full plan.
    protected = set(template['protected_override_fields'])
    for change in request.overrides:
        key = f'{change.instance}.{change.field}.{change.key}'
        if key in seen or key in protected:
            raise ValueError('Duplicate or contract-protected override: ' + key)
        seen.add(key)
        values = parts.get(change.instance, {}).get(change.field, {})
        if change.key not in values:
            raise ValueError('Override must name an existing template field: ' + key)
        old = values[change.key]
        values[change.key] = change.value
        changes.append({'path': key, 'before': old, 'after': change.value})
    plan['notes'] = [{'text': 'Explicit-input output match; INITIAL VALUES, NOT SIMULATED.\n'
                             'Ideal capacitors/supply, quasi-static microstrip. No transistor or HB verification.',
                      'at': [0, 10.5], 'height': .22},
                     {'text': '\n'.join(f'Port {p.number}: {p.reference_plane}; Z={p.impedance}' for p in ports),
                      'at': [0, -19.7], 'height': .22}]
    result = BuildPlan.model_validate(plan)
    return result, {'template': request.template, 'structure_sha256': template['plan_sha256'],
                    'applicability': template['applicability'], 'overrides': changes,
                    'input_bytes': len(json.dumps(payload, ensure_ascii=False).encode('utf-8')),
                    'expanded_bytes': len(result.model_dump_json().encode('utf-8'))}


def read_request(path: str) -> tuple[BuildPlan, dict]:
    p = Path(path).resolve(strict=True)
    if p.stat().st_size > 512000: raise ValueError('Build request exceeds 512 KB')
    payload = json.loads(p.read_text(encoding='utf-8-sig'))
    if payload.get('kind') == 'template': return expand(payload)
    return BuildPlan.model_validate(payload), {'template': None, 'input_bytes': p.stat().st_size}
