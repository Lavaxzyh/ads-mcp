"""Build a microstrip schematic and native artwork using only public MCP tools.

Use a NEW workspace. Save/close, open it in ADS GUI, then run Momentum and
inspect the native emModel. No source project is read or overwritten.
"""
import argparse,asyncio,json,os,sys
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
REPO=Path(__file__).resolve().parent.parent
async def run(ws):
    ws=Path(ws).resolve()
    if ws.exists():raise ValueError('Use a new workspace directory')
    env=dict(os.environ);env['ADS_MCP_STATE_FILE']=str(ws.parent/(ws.name+'_state.json'))
    async with stdio_client(StdioServerParameters(command=sys.executable,args=[str(REPO/'mcp_server.py')],env=env)) as (read,write):
        async with ClientSession(read,write) as s:
            await s.initialize()
            async def call(tool_name,**args):
                r=await s.call_tool(tool_name,args);d=r.structuredContent or json.loads(r.content[0].text)
                if r.isError or not d.get('ok'):raise RuntimeError(str(d))
                print(tool_name,json.dumps(d),flush=True);return d
            await call('ads_create_workspace',path=str(ws))
            await call('ads_create_library',name='flow_lib')
            # Initialize library layout technology before placing native artwork.
            await call('ads_create_substrate',lib='flow_lib',name='board',dielectrics=[dict(height_mm=.5,er=2.55)],conductors=[dict(layer='cond',interface=1,thickness_um=17,conductivity_s_per_m=5.8e7)])
            await call('ads_create_schematic',lib='flow_lib',cell='line')
            await call('ads_place_component',lib='ads_tlines',cell='MSUB',x=0,y=3,instance_name='MSub1',params={'H':'.5 mm','Er':'2.55','T':'17 um'})
            await call('ads_place_component',lib='ads_tlines',cell='MLIN',x=0,y=0,instance_name='TL1',params={'W':'1.37743 mm','L':'5 mm'})
            for n in [1,2]:await call('ads_add_design_port',lib='flow_lib',cell='line',name='P'+str(n),number=n,instance='TL1',pin_number=n,angle=180 if n==1 else 0)
            await call('ads_generate_layout',lib='flow_lib',cell='line')
            await call('ads_generate_symbol',lib='flow_lib',cell='line')
            await call('ads_configure_momentum_setup',lib='flow_lib',cell='line',substrate='board',start_ghz=1.37,stop_ghz=1.43,points=3)
            await call('ads_configure_em_model',lib='flow_lib',cell='line')
            await call('ads_configure_momentum_mesh',lib='flow_lib',cell='line',mesh=dict(cells_per_wavelength=40,reference_frequency_ghz=1.6))
            await call('ads_configure_momentum_options',lib='flow_lib',cell='line',plans=[dict(kind='linear',start_ghz=1.37,stop_ghz=1.43,points=3),dict(kind='dc')])
            await call('ads_close_workspace')
    print('Open this workspace in ADS GUI and call ads_run_momentum with its session_id, workspace, and flow_lib/line/em%Setup/emStateFile.xml.')
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('workspace');asyncio.run(run(p.parse_args().workspace))
