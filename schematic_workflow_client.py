"""Build a decided circuit through one isolated MCP session (no simulation).

ADS-venv-python schematic_workflow_client.py plan.json --ads-root <ADS_INSTALL>
Add --validate for static input validation only, without creating a workspace.
"""
import argparse
import asyncio
import os
import json
from pathlib import Path
from ads_engine import get_ads_install_dir
import sys
from rf_workflow_client import run


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan')
    parser.add_argument('--validate', action='store_true')
    parser.add_argument('--ads-root', default=str(get_ads_install_dir()))
    args = parser.parse_args()
    request = {'tool': 'ads_build_schematic', 'arguments': {
        'request_file': str(Path(args.plan).resolve(strict=True)),
        'action': 'validate' if args.validate else 'build'}}
    try:
        code = asyncio.run(run(request, args.ads_root))
    except Exception as exc:
        def causes(error):
            if isinstance(error, BaseExceptionGroup):
                return [s for child in error.exceptions for s in causes(child)]
            return [f'{type(error).__name__}: {error}']
        print(json.dumps({'ok': False, 'errors': causes(exc)}, ensure_ascii=False), flush=True)
        code = 1
    raise SystemExit(code)
