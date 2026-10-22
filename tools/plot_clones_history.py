#!/usr/bin/env python3
"""Render all saved daily clones and lifetime totals.

Usage: plot_clones_history.py state.json [output.png]
       plot_clones_history.py --render [output.png]
The workflow uses its exact local snapshot; --render reads the public Gist API.
"""
import argparse
import json
import math
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "DejaVu Sans"]
GIST_ID = "ec682cff6f482626fa5348189838e66b"
API = f"https://api.github.com/gists/{GIST_ID}"
OUT = Path(__file__).resolve().parent.parent / "assets" / "clones-history.png"


def fetch_state() -> dict:
    request = urllib.request.Request(API, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "ads-mcp-traffic"})
    with urllib.request.urlopen(request, timeout=30) as response:
        gist = json.loads(response.read().decode("utf-8"))
    entry = gist["files"]["state.json"]
    if entry.get("truncated"):
        raise ValueError("Gist history is truncated; refusing to render incomplete history")
    return json.loads(entry["content"])


def load_history(data: dict) -> list[dict]:
    raw = data.get("dailyHistory") or []
    source = list(raw.values()) if isinstance(raw, dict) else list(raw)
    by_date = {}
    for record in source:
        day = dict(record)
        key = datetime.fromisoformat(day["date"].replace("Z", "+00:00")).date()
        previous = by_date.get(key)
        if previous and previous.get("clones") is not None:
            day["clones"] = max(previous["clones"], day.get("clones") or 0)
        status = day.get("clonesStatus")
        seen = data.get("lastSeenCloneCounts", {})
        if not day.get("clones") and (status == "pending" or
                                      (status is None and key.isoformat() not in seen)):
            day["clones"] = None
        by_date[key] = day
    if not by_date:
        return []
    first, last = min(by_date), max(by_date)
    days, key = [], first
    while key <= last:
        day = by_date.get(key, {"date": key.isoformat(), "clones": None})
        day["date"] = key.isoformat()
        days.append(day)
        key += timedelta(days=1)
    return days


def plot(days: list[dict], out: Path, *, offset: int = 0, updated_at: str | None = None) -> dict:
    if not days:
        raise ValueError("No saved daily history; refusing to overwrite the curve")
    dates = [datetime.fromisoformat(day["date"]).date() for day in days]
    values = [day.get("clones") for day in days]
    cumulative, running = [], offset
    for value in values:
        if value is None:
            cumulative.append(math.nan)
        else:
            if type(value) is not int or value < 0:
                raise ValueError("Daily clones must be a nonnegative integer or null")
            running += value
            cumulative.append(running)
    fig, ax = plt.subplots(figsize=(10, 5.1), dpi=130)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("#FAFBFD")
    known = [(date, value) for date, value in zip(dates, values) if value is not None]
    ax.bar([date for date, _ in known], [value for _, value in known], width=0.62,
           color="#93C5FD", edgecolor="#3B82F6", linewidth=0.8, label="daily clones", zorder=3)
    ax.plot(dates, cumulative, color="#1D4ED8", marker="o", markersize=4.5,
            linewidth=2.2, label="cumulative clones (all saved history)", zorder=4)
    indices = [i for i, value in enumerate(values) if value is not None]
    stride = max(1, len(indices) // 18)
    for position, i in enumerate(indices):
        if position % stride == 0 or i == indices[-1]:
            ax.annotate(str(cumulative[i]), (dates[i], cumulative[i]), textcoords="offset points",
                        xytext=(0, 8), ha="center", fontsize=8.5, color="#1D4ED8", zorder=5)
    pending = [date for date, value in zip(dates, values) if value is None]
    for i, date in enumerate(pending):
        x = mdates.date2num(date)
        ax.axvspan(x-0.4, x+0.4, facecolor="#E2E8F0", edgecolor="#CBD5E1", alpha=0.65,
                   hatch="///", linewidth=0, zorder=1,
                   label="not yet reported / unavailable" if i == 0 else None)
    ax.set_title("ads-mcp clone history · cumulative recorded total", fontsize=13, fontweight="bold", pad=12)
    ax.set_ylabel("clone events")
    ax.set_ylim(0, max([running, *[value for _, value in known]], default=0) * 1.18 + 1)
    if len(dates) <= 12:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
        ax.xaxis.set_major_locator(mdates.DayLocator(interval=1))
    elif len(dates) <= 31:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
        ax.xaxis.set_major_locator(mdates.DayLocator(interval=2 if len(dates) <= 24 else 3))
    else:
        locator = mdates.AutoDateLocator(minticks=5, maxticks=12)
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    ax.grid(True, axis="y", alpha=0.3, color="#94A3B8")
    ax.legend(fontsize=9, facecolor="white", edgecolor="#CBD5E1", loc="upper left")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    latest = dates[indices[-1]].isoformat() if indices else "none"
    stamp = datetime.fromisoformat(updated_at.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M UTC") if updated_at else "unknown"
    fig.text(0.5, 0.054, f"Latest reported day: {latest}  |  Last collection: {stamp}",
             ha="center", fontsize=8.5, color="#475569")
    scope = "UTC dates · raw clones including automation · pending dates are not zero"
    if offset:
        scope += f" · earlier saved total: {offset}"
    fig.text(0.5, 0.018, scope, ha="center", fontsize=8, color="#64748B")
    fig.autofmt_xdate(rotation=0)
    fig.tight_layout(rect=(0, 0.09, 1, 1))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, facecolor="white")
    plt.close(fig)
    return {"days": len(days), "cumulative_clones": running, "history_offset": offset,
            "pending_dates": len(pending), "latest_reported_day": latest, "png": str(out)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--render", action="store_true", help="Read public Gist instead of a local snapshot")
    parser.add_argument("paths", nargs="*", metavar="PATH")
    args = parser.parse_args()
    if args.render:
        if len(args.paths) > 1:
            parser.error("--render accepts only an optional output path")
        data = fetch_state()
        out = Path(args.paths[0]) if args.paths else OUT
    elif args.paths:
        if len(args.paths) > 2:
            parser.error("Use a state JSON path and an optional output PNG path")
        data = json.loads(Path(args.paths[0]).read_text(encoding="utf-8"))
        out = Path(args.paths[1]) if len(args.paths) == 2 else OUT
    else:
        data, out = fetch_state(), OUT
    days = load_history(data)
    offset = data.get("historyOffsets", {}).get("clones")
    if offset is None:
        offset = max(0, data.get("totalClones", 0) - sum(day.get("clones") or 0 for day in days))
    updated_at = data.get("lastCollectionAt") or max((day.get("capturedAt", "") for day in days), default="")
    print(json.dumps(plot(days, out, offset=offset, updated_at=updated_at), indent=2))


if __name__ == "__main__":
    main()
