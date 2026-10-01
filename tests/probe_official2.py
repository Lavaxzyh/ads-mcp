"""Dump the official ads-mcp.exe tools/list + instructions in full."""
import json
import subprocess
import threading
import time

EXE = r"D:\Program Files\Keysight\ADS2027\bin\ads-mcp.exe"
proc = subprocess.Popen([EXE], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
lines = []

def pump():
    for line in proc.stdout:
        lines.append(line)

threading.Thread(target=pump, daemon=True).start()

def send(obj):
    proc.stdin.write(json.dumps(obj) + "\n")
    proc.stdin.flush()
    time.sleep(0.4)

send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2024-11-05", "capabilities": {},
    "clientInfo": {"name": "probe", "version": "0"}}})
send({"jsonrpc": "2.0", "method": "notifications/initialized"})
send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
time.sleep(5)
proc.kill()

for line in lines:
    msg = json.loads(line)
    if msg.get("id") == 1:
        instr = json.loads(msg["result"]["instructions"])
        print("=== INSTRUCTIONS(content) ===")
        print(instr.get("content", "")[:2600])
    if msg.get("id") == 2:
        tools = msg["result"]["tools"]
        print(f"\n=== OFFICIAL TOOLS ({len(tools)}) ===")
        for t in tools:
            desc = t["description"].split("\n")[0][:150]
            print(f"- {t['name']}: {desc}")
