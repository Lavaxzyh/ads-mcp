"""Rebind native XML paths after extracting/moving a CLOSED example workspace.

Usage: python examples/rebind_workspace.py examples/.../workspace
Does not patch OA binaries, delete caches, or remove live ADS lock files.
"""
import argparse
from pathlib import Path
import xml.etree.ElementTree as ET


def rebind(workspace):
    ws=Path(workspace).resolve(strict=True)
    if not (ws/"lib.defs").is_file():raise ValueError("Not an ADS workspace")
    locks=list(ws.rglob("*.cdslck"))+list(ws.rglob("*.lck"))
    if locks:raise RuntimeError("Close this workspace in ADS before rebinding; inspect leftover lock files manually")
    old=set()
    for p in ws.rglob("emStateFile.xml"):
        for n in ET.parse(p).getroot().findall(".//MomLayout/workspaceText"):
            if n.text and Path(n.text).resolve()!=ws:old.add(n.text)
    changed=[]
    for p in ws.rglob("*"):
        if p.is_file() and p.suffix in {".xml",".ndx"}:
            text=p.read_text(encoding="utf-8");new=text
            for original in sorted(old,key=len,reverse=True):
                new=new.replace(original,str(ws)).replace(original.replace("\\","/"),str(ws).replace("\\","/"))
            if new!=text:p.write_text(new,encoding="utf-8");changed.append(str(p.relative_to(ws)))
    return {"workspace":str(ws),"updated":changed}

if __name__=="__main__":
    import json
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("workspace")
    print(json.dumps(rebind(parser.parse_args().workspace),indent=2))
