"""Observable wall-clock intervals, not guessed model/thinking durations."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time
import uuid

from schematic_flow import atomic_json, target_lock

PHASES = ('materials', 'preparation', 'configuration', 'service_startup', 'build',
          'acceptance', 'rework', 'simulation', 'service_shutdown')


class Trace:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.path = self.directory / 'timing.json'

    def _read(self):
        if self.path.is_file(): return json.loads(self.path.read_text(encoding='utf-8'))
        return {'schema_version': 1, 'spans': [], 'calls': [], 'failures': [],
                'model_token_usage': {'status':'unobserved','input_tokens':None,'output_tokens':None,'cached_tokens':None},
                'scope': 'Observed wall-clock intervals; nested spans must not be summed. '
                         'Intervals may contain waiting; no inference of model CPU/thinking time.',
                'unobserved': list(PHASES)}

    def _update(self, fn):
        self.directory.mkdir(parents=True, exist_ok=True)
        with target_lock(self.path):
            data = self._read(); result = fn(data); atomic_json(self.path, data)
            return result

    def begin(self, phase, label='', observer='program'):
        if phase not in PHASES: raise ValueError('Unknown trace phase: ' + phase)
        if observer not in ('program', 'caller_boundary'): raise ValueError('Invalid observer')
        token = uuid.uuid4().hex
        def add(data):
            data['spans'].append({'id': token, 'phase': phase, 'label': label,
                                 'observer': observer, 'pid': os.getpid(), 'start_utc': datetime.now(timezone.utc).isoformat(),
                                 'start_monotonic_ns': time.monotonic_ns(), 'status': 'running'})
            if phase in data['unobserved']: data['unobserved'].remove(phase)
        self._update(add)
        return token

    def end(self, token, outcome='completed'):
        def close(data):
            found = next((s for s in data['spans'] if s['id'] == token), None)
            if found is None or found['status'] != 'running': raise ValueError('Unknown or closed trace span')
            elapsed = (time.monotonic_ns()-found['start_monotonic_ns'])/1e9
            if elapsed < 0: raise ValueError('Monotonic clock reset; span cannot be measured')
            found.update(end_utc=datetime.now(timezone.utc).isoformat(), elapsed_seconds=elapsed,
                         status=outcome, measurement='measured_boundary_elapsed')
        self._update(close)

    @contextmanager
    def span(self, phase, label=''):
        token = self.begin(phase, label)
        try: yield
        except BaseException:
            self.end(token, 'failed'); raise
        else: self.end(token)

    def call(self, tool, elapsed, input_bytes, output_bytes, ok):
        self._update(lambda d: d['calls'].append(dict(tool=tool, elapsed_seconds=elapsed,
                     input_bytes=input_bytes, output_bytes=output_bytes, ok=ok)))

    def native_timings(self, timings):
        def add(d):
            for label, elapsed in timings.items():
                phase = 'acceptance' if label in ('reopen_verification','replay_verification') else 'build'
                d.setdefault('native_durations',[]).append({'phase':phase,'label':label,
                    'elapsed_seconds':elapsed,'measurement':'measured_perf_counter',
                    'overlaps_parent_call':True})
                if phase in d['unobserved']: d['unobserved'].remove(phase)
        self._update(add)

    def check_retry(self, request_hash, implementation_hash):
        data = self._read()
        failures = [f for f in data['failures'] if f['request_hash'] == request_hash and
                    f['implementation_hash'] == implementation_hash]
        if len(failures) >= 2:
            raise RuntimeError('STOP_REPEATED_FAILURE: same unchanged operation failed twice. '
                               'Inspect the recorded gap; no implicit adapter development or further retry.')
        gaps={}
        for f in data['failures']:
            if f['implementation_hash']==implementation_hash and f.get('gap_code'):
                gaps[f['gap_code']]=gaps.get(f['gap_code'],0)+1
        if any(v>=2 for v in gaps.values()):
            raise RuntimeError('STOP_REPEATED_INTERFACE_GAP: repeated native adapter gap; report it as tool-development work before a new design attempt')

    def failure(self, request_hash, implementation_hash, error):
        found=re.search(r'\b(UNADAPTED_PARAMETER|UNADAPTED_ENUM|UNADAPTED_INTERFACE)\b',str(error))
        self._update(lambda d: d['failures'].append(dict(request_hash=request_hash,
                     implementation_hash=implementation_hash, error=str(error),
                     gap_code=found[1] if found else None,
                     utc=datetime.now(timezone.utc).isoformat(), auto_retry=False)))

    def summary(self):
        d = self._read()
        def union(intervals):
            total=0.;end=None
            for start,stop in sorted(intervals):
                total+=max(0.,stop-max(start,end if end is not None else start));end=max(stop,end if end is not None else stop)
            return total
        phases={}
        for phase in PHASES:
            intervals=[(s['start_monotonic_ns']/1e9,s['start_monotonic_ns']/1e9+s['elapsed_seconds'])
                       for s in d['spans'] if s['phase']==phase and 'elapsed_seconds' in s]
            if intervals:phases[phase]=union(intervals)
        return {'trace': str(self.path), 'calls': len(d['calls']),
                'input_bytes': sum(c['input_bytes'] for c in d['calls']),
                'output_bytes': sum(c['output_bytes'] for c in d['calls']),
                'phase_elapsed_seconds':phases,'phase_note':'Per-phase observed interval union; different phases may overlap and must not be summed into end-to-end time.',
                'model_token_usage':d.get('model_token_usage',{'status':'unobserved'}),
                'byte_measurement':'UTF-8 payload proxy only; not model tokens or billing',
                'unobserved': d['unobserved'],
                'open_spans': [s['id'] for s in d['spans'] if s['status'] == 'running']}
