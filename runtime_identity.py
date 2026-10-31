"""Read-only runtime identity; distinguish loaded code from later disk updates."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

from version import LOCAL_RELEASE, UPSTREAM_COMMIT, UPSTREAM_VERSION, __version__


def source_inventory(root: Path):
    paths = sorted(root.glob('*.py'))
    paths += [p for p in (root / 'release-validation.json',) if p.is_file()]
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def fingerprint(inventory):
    return hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest()


def tool_name(name):
    return name.rsplit('__', 1)[-1]


class RuntimeIdentity:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.version = __version__
        self.inventory = source_inventory(self.root)
        self.code_sha256 = fingerprint(self.inventory)

    def report(self, registered_tools, *, client_tool_names=None, disabled_tools=None,
               expected_build_id=None):
        disk = source_inventory(self.root)
        changed = sorted(n for n in self.inventory.keys() | disk.keys()
                         if self.inventory.get(n) != disk.get(n))
        registered = sorted(set(map(tool_name, registered_tools)))
        disabled = sorted(set(map(tool_name, disabled_tools or [])))
        expected = set(registered) - set(disabled)
        observed = None if client_tool_names is None else set(map(tool_name, client_tool_names))
        missing = sorted(expected - observed) if observed is not None else None
        extra = sorted(observed - set(registered)) if observed is not None else None
        build_id = f'{LOCAL_RELEASE}:{self.code_sha256[:16]}'
        return {
            'schema_version': 1, 'version': self.version, 'local_release': LOCAL_RELEASE,
            'upstream_version': UPSTREAM_VERSION, 'upstream_commit': UPSTREAM_COMMIT,
            'build_id': build_id, 'code_root': str(self.root),
            'server_pid': os.getpid(), 'server_started_at': self.started_at,
            'python': {'version': platform.python_version(), 'executable': sys.executable},
            'loaded_code_sha256': self.code_sha256, 'disk_code_sha256': fingerprint(disk),
            'files_changed_since_start': changed, 'fresh_server_required': bool(changed),
            'expected_build_matches': None if expected_build_id is None else expected_build_id == build_id,
            'tool_catalog': {'registered': registered, 'registered_count': len(registered),
                             'disabled_by_caller': disabled,
                             'client_observed': client_tool_names is not None,
                             'missing_from_client': missing, 'unknown_in_client': extra,
                             'client_refresh_required': bool(missing or extra) if observed is not None else None},
            'scope': 'Startup file fingerprints identify this process. Client catalog is only assessed when supplied; no sessions are restarted.',
        }
