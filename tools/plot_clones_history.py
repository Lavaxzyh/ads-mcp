#!/usr/bin/env python3
"""Plot the clones history curve from the ghtraf badge Gist.

Data source: the public badge Gist's state.json (permanent daily history,
beyond GitHub's 14-day traffic window).

Modes:
  python plot_clones_history.py [state.json] [out.png]   # local file -> local png
  python plot_clones_history.py --gist                    # fetch gist, render, upload back

--gist mode needs GIST_ID and GITHUB_TOKEN (a PAT with gist scope) in env.
In GitHub Actions the workflow provides both.
"""
import base64
import json
import os
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "DejaVu Sans"]

GIST_ID = "ec682cff6f482626fa5348189838e66b"
PNG_NAME = "clones-history.png"
OUT = Path(__file__).resolve().parent.parent / "assets" / PNG_NAME
API = "https://api.github.com"


# ---------------- gist IO ----------------

def _request(method: str, url: str, token: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        url,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        data=json.dumps(body).encode() if body is not None else None,
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def fetch_state(gist_id: str, token: str) -> dict:
    gist = _request("GET", f"{API}/gists/{gist_id}", token)
    return json.loads(gist["files"]["state.json"]["content"])


def upload_png(gist_id: str, token: str, png: Path) -> None:
    content = base64.b64encode(png.read_bytes()).decode()
    _request("PATCH", f"{API}/gists/{gist_id}", token,
             {"files": {PNG_NAME: {"content": content}}})


# ---------------- data + plot ----------------

def load_history(data: dict) -> list[dict]:
    raw = data.get("dailyHistory") or {}
    days = list(raw.values()) if isinstance(raw, dict) else list(raw)
    days.sort(key=lambda d: d.get("date", ""))
    return days


def plot(days: list[dict], out: Path) -> dict:
    dates = [datetime.fromisoformat(d["date"].replace("Z", "+00:00")).date() for d in days]
    clones = [int(d.get("clones", 0)) for d in days]
    uniq = [int(d.get("uniqueClones", d.get("clones", 0))) for d in days]

    cum, running = [], 0
    for u in uniq:
        running += u
        cum.append(running)

    fig, ax = plt.subplots(figsize=(9, 4.6), dpi=130)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("#FAFBFD")

    ax.bar(dates, clones, width=0.62, color="#93C5FD", edgecolor="#3B82F6",
           linewidth=0.8, label="daily clones", zorder=3)
    ax.plot(dates, cum, color="#1D4ED8", marker="o", markersize=4.5, linewidth=2.2,
            label="cumulative unique cloners", zorder=4)
    for x, y in zip(dates, cum):
        ax.annotate(str(y), (x, y), textcoords="offset points", xytext=(0, 8),
                    ha="center", fontsize=9, color="#1D4ED8", zorder=5)

    ax.set_title("ads-mcp clones history (ghtraf · beyond GitHub's 14-day window)",
                 fontsize=13, fontweight="bold", pad=12)
    ax.set_ylabel("clones")
    ax.set_ylim(0, max(cum) * 1.18 + 1)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=1))
    ax.grid(True, axis="y", alpha=0.3, color="#94A3B8")
    ax.legend(fontsize=10, facecolor="white", edgecolor="#CBD5E1", loc="upper left")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.autofmt_xdate(rotation=0)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, facecolor="white")
    plt.close(fig)
    return {"days": len(days), "cumulative_unique": cum[-1] if cum else 0,
            "total_clones": sum(clones), "png": str(out)}


def main() -> None:
    if "--gist" in sys.argv:
        gist_id = os.environ.get("GIST_ID", GIST_ID)
        token = os.environ["GITHUB_TOKEN"]
        data = fetch_state(gist_id, token)
        info = plot(load_history(data), OUT)
        upload_png(gist_id, token, OUT)
        info["uploaded_to_gist"] = gist_id
        print(json.dumps(info, indent=1))
        return
    src = sys.argv[1] if len(sys.argv) > 1 else None
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else OUT
    if src:
        data = json.loads(Path(src).read_text(encoding="utf-8"))
    else:
        data = fetch_state(GIST_ID, os.environ["GITHUB_TOKEN"])
    print(json.dumps(plot(load_history(data), dst), indent=1))


if __name__ == "__main__":
    main()
