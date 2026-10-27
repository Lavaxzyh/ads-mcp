"""Fetch the official AVX ADS kit, record provenance, and safely extract locally.

The archive is not redistributed by ads-mcp. Download is explicit.
"""
import argparse,hashlib,io,json,zipfile
from pathlib import Path
from urllib.request import Request,urlopen
URL="https://www.kyocera-avx.com/download/software/ADSNew.zip"

def download(destination,expected_sha256=""):
    dest=Path(destination).resolve()
    if dest.exists():raise ValueError("Use a new destination")
    raw=urlopen(Request(URL,headers={"User-Agent":"ads-mcp/0.4.0"}),timeout=60).read()
    sha=hashlib.sha256(raw).hexdigest()
    if expected_sha256 and sha.lower()!=expected_sha256.lower():raise ValueError("Archive hash differs from the selected version")
    archive=zipfile.ZipFile(io.BytesIO(raw))
    for member in archive.infolist():
        if not (dest/member.filename).resolve().is_relative_to(dest):raise ValueError("Archive path escapes destination")
        if member.external_attr>>16 & 0o170000 == 0o120000:raise ValueError("Archive symlinks are unsupported")
    dest.mkdir(parents=True)
    (dest/"ADSNew.zip").write_bytes(raw)
    archive.extractall(dest)
    manifest={"source":URL,"sha256":sha,"archive_bytes":len(raw)}
    (dest/"vendor-source.json").write_text(json.dumps(manifest,indent=2))
    return manifest

def select_capacitor(kit):
    import re
    p=Path(kit)/"AVX_RF_Components/circuit/models/AVX_Capacitor_cktName.net"
    block=p.read_text().split("define AVX_C0402(",1)[1].split("end AVX_C0402",1)[0]
    files=re.findall(r'"(C0402[^"\n]+\.s2p)"',block)
    name="C0402SE3r9.s2p"
    return {"cell":"AVX_C0402","PartNumber":files.index(name),"data_file":name,"capacitance_pf":3.9}

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("destination");p.add_argument("--sha256",default="")
    a=p.parse_args();print(json.dumps({**download(a.destination,a.sha256),"selection":select_capacitor(a.destination)},indent=2))
