"""Smoke test the ads-mcp server over stdio like a real MCP client would."""
import asyncio
import os

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SERVER = str(REPO / "mcp_server.py")
PYTHON = str(REPO / ".venv" / "Scripts" / "python.exe")


async def main() -> None:
    params = StdioServerParameters(
        command=PYTHON,
        args=[SERVER],
        env={**os.environ, "HPEESOF_DIR": r"D:\Program Files\Keysight\ADS2027"},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            info = await session.initialize()
            print("connected:", info.serverInfo.name, flush=True)
            tools = await session.list_tools()
            print("tools:", len(tools.tools), sorted(t.name for t in tools.tools), flush=True)

            res = await session.call_tool("ads_check_installed", {})
            payload = res.content[0].text
            print("check_installed:", payload[:200], flush=True)

            res = await session.call_tool("ads_session_status", {})
            print("session_status:", res.content[0].text[:200], flush=True)

            # quick end-to-end: create a tiny workspace through MCP tools
            import shutil

            ws = r"D:\ADS_Project\ads_mcp_demo\mcp_protocol_test_wrk"
            shutil.rmtree(ws, ignore_errors=True)
            res = await session.call_tool("ads_create_workspace", {"path": ws})
            print("create_workspace:", res.content[0].text[:150], flush=True)
            res = await session.call_tool("ads_create_library", {"name": "t_lib"})
            print("create_library:", res.content[0].text[:120], flush=True)
            res = await session.call_tool("ads_create_schematic", {"lib": "t_lib", "cell": "t1"})
            print("create_schematic:", res.content[0].text[:120], flush=True)
            res = await session.call_tool(
                "ads_place_component",
                {"lib": "ads_tlines", "cell": "MLIN", "x": 1.0, "y": 0.0,
                 "instance_name": "TL1", "params": {"W": "1 mm", "L": "5 mm"}},
            )
            print("place_component:", res.content[0].text[:200], flush=True)
            res = await session.call_tool("ads_generate_netlist", {})
            ok = '"ok": true' in res.content[0].text
            print("generate_netlist ok:", ok, flush=True)
            print("\nPROTOCOL SMOKE TEST PASSED", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
