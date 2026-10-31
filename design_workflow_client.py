"""Private reusable ADS MCP session. JSON-line input or a bounded command batch.

python design_workflow_client.py --task-dir <new-task-dir> --batch commands.json
python design_workflow_client.py --task-dir <task-dir> --interactive
Commands: {"action":"build", "request_file":"..."}; {"action":"quit"}.
The same process handles consecutive requests; it never attaches to an ADS GUI
or restarts another client. Workspace ownership is checked again on the server.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from ads_engine import get_ads_install_dir
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from schematic_flow import target_lock
from workflow_trace import Trace


async def run(task_directory, ads_root, batch=None, interactive=False):
    root = Path(task_directory).resolve()
    trace = Trace(root)
    if not (Path(ads_root)/'bin/hpeesofsim.exe').is_file():
        raise ValueError('Specify the ADS installation with --ads-root')
    source = Path(__file__).resolve().parent
    # A process/OS lock, not a guessed timeout or stale PID, owns this task.
    with target_lock(root/'private-session'):
        root.mkdir(parents=True, exist_ok=True)
        config = root/'home/hpeesof/config/SystemVue'
        config.mkdir(parents=True, exist_ok=True)
        (config/'lib.defs').write_text('# private task session\n', encoding='utf-8')
        with trace.span('materials', 'read batch command file'):
            commands = json.loads(Path(batch).read_text(encoding='utf-8-sig')) if batch else []
            if not isinstance(commands, list) or len(commands)>200:
                raise ValueError('Batch must contain at most 200 commands')
        env = dict(os.environ, HPEESOF_DIR=str(Path(ads_root).resolve()), HOME=str(root/'home'),
                   ADS_MCP_TASK_DIR=str(root), ADS_MCP_STATE_FILE=str(root/'session-state.json'),
                   ADS_MCP_WORKROOT=str(root/'rf-jobs'), MPLCONFIGDIR=str(root/'mplconfig'),
                   PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
        total = time.perf_counter()
        started = trace.begin('service_startup', 'native initialization and MCP handshake')
        with (root/'server.log').open('a', encoding='utf-8') as errors:
          async with stdio_client(StdioServerParameters(command=sys.executable,
                  args=[str(source/'mcp_server.py')], cwd=str(root), env=env), errlog=errors) as (rd, wr):
            async with ClientSession(rd, wr) as session:
                await asyncio.wait_for(session.initialize(), timeout=60)
                trace.end(started)
                print(json.dumps({'status':'ready','task_directory':str(root),'session_reused_for_next_commands':True}), flush=True)
                index, failed = 0, False
                while True:
                    if interactive:
                        raw = await asyncio.to_thread(sys.stdin.readline)
                        if not raw: break
                        command = json.loads(raw)
                    elif index < len(commands):
                        command = commands[index]; index += 1
                    else: break
                    if command.get('action') == 'quit': break
                    if set(command)-{'action','request_file'}:
                        raise ValueError('Commands contain only action and request_file')
                    args = dict(command, task_directory=str(root))
                    t = time.perf_counter()
                    try:
                        response = await asyncio.wait_for(session.call_tool('ads_design_workflow',args), timeout=300)
                    except asyncio.TimeoutError:
                        print(json.dumps({'ok':False,'status':'observation_timeout','task_directory':str(root),
                            'request_file':command.get('request_file'),'next':'Inspect task.json and the same original request/job; no automatic redispatch or cancellation.'}),flush=True)
                        failed=True
                        break
                    text = '\n'.join(c.text for c in response.content if c.type=='text')
                    result = json.loads(text) if not response.isError else {'ok':False,'error':text}
                    trace.call('ads_design_workflow:'+str(command.get('action')),time.perf_counter()-t,
                               len(json.dumps(args).encode()),len(text.encode('utf-8')),result.get('ok',False))
                    print(json.dumps(result, ensure_ascii=False), flush=True)
                    if not result.get('ok'):
                        failed = True
                        # Ordinary batches stop at the first failed operation.
                        # Interactive callers can inspect and submit a corrected input.
                        if not interactive: break
                shutdown = trace.begin('service_shutdown', 'MCP shutdown')
        trace.end(shutdown)
        summary = trace.summary()
        summary['session_elapsed_seconds'] = time.perf_counter()-total
        print(json.dumps({'status':'closed','timing':summary}), flush=True)
        return 1 if failed else 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--task-dir', required=True)
    p.add_argument('--ads-root', default=str(get_ads_install_dir()))
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument('--batch'); group.add_argument('--interactive', action='store_true')
    a=p.parse_args()
    return asyncio.run(run(a.task_dir,a.ads_root,a.batch,a.interactive))


if __name__ == '__main__':
    try: raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({'ok':False,'error':f'{type(exc).__name__}: {exc}'}),flush=True)
        raise SystemExit(1)
