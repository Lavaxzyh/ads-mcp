"""Typed operations transported into the selected native ADS GUI process."""
from __future__ import annotations

import inspect
import json
from pathlib import Path


def call_native(operation, arguments: dict, session_id: int | None = None,
                workspace: str = "", timeout: float = 55):
    """Execute our own operation on the ADS main thread, locally or via bridge.

    Operation is an internal Python callable, never a user-supplied code string.
    Arguments cross the bridge as serialized data. Remote calls do not switch
    workspaces; a mismatch fails before the operation runs.
    """
    if session_id is None:
        if workspace:
            from keysight.ads import de
            if not de.workspace_is_open() or Path(de.active_workspace().path).resolve() != Path(workspace).resolve():
                raise RuntimeError("Open the requested workspace in the MCP server first")
        return operation(arguments)
    if type(session_id) is not int or session_id <= 0:
        raise ValueError("session_id must be a positive integer")
    from ads_engine import get_ads_install_dir
    from momentum import OfficialAdsClient
    payload = json.dumps({"arguments": arguments, "workspace": workspace}, ensure_ascii=True)
    dependencies="\n".join(inspect.getsource(fn) for fn in getattr(operation,'__ads_mcp_dependencies__',()))
    code = f'''import json
from pathlib import Path
from keysight.ads import de
_request = json.loads({payload!r})
if _request["workspace"]:
    if not de.workspace_is_open() or Path(de.active_workspace().path).resolve() != Path(_request["workspace"]).resolve():
        raise RuntimeError("Selected ADS GUI uses another workspace; no workspace was switched")
{dependencies}
{inspect.getsource(operation)}
_answer = {operation.__name__}(_request["arguments"])
print("ADS_NATIVE_JSON:" + json.dumps(_answer, ensure_ascii=True))
'''
    with OfficialAdsClient(get_ads_install_dir() / "bin" / "ads-mcp.exe", timeout=timeout) as client:
        connected = client.call("connect_session", {"session_id": session_id})
        if not connected.get("connected") or connected.get("mode") != "remote":
            raise RuntimeError("Could not connect to the selected ADS GUI session")
        result = client.call("execute_python", {"code": code})
    if result.get("mode") != "remote":
        raise RuntimeError("Operation requires execution in the selected GUI process")
    for line in reversed(result.get("stdout", "").splitlines()):
        if line.startswith("ADS_NATIVE_JSON:"):
            return json.loads(line[len("ADS_NATIVE_JSON:"):])
    raise RuntimeError("ADS did not complete the operation: " + str(result.get("stderr", result))[-2500:])
