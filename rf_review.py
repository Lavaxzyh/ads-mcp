"""File-based RF postprocessing on saved evidence; analysis never starts a solver."""
import json
from pathlib import Path
from typing import Literal


def register(mcp, *, compact_default=False):
    @mcp.tool()
    def ads_review_rf(action: Literal["common_load", "smith_overlay","hb_power_sweep","doherty","stability","em_comparison"],
                      request_file: str, detail: Literal["summary","full"] | None = None) -> dict:
        """Analyze archived RF results. For common_load and em_comparison, summary returns key
        values and a full report path. Workflow defaults to summary. See PA-WORKFLOWS.zh-CN.md."""
        try:
            payload = json.loads(Path(request_file).read_text(encoding="utf-8-sig"))
            compact = detail == "summary" or (detail is None and compact_default)
            if action == "common_load":
                from loadpull_selection import select
                return select(payload, compact=compact)
            if action == "hb_power_sweep":
                from pa_sweep import review
                return review(payload)
            if action == "doherty":
                from doherty_analysis import analyze
                return analyze(payload)
            if action == "stability":
                from twoport_review import stability
                return stability(payload)
            if action == "em_comparison":
                from twoport_review import em_comparison
                return em_comparison(payload, compact=compact)
            from smith_overlay import render
            return render(payload)
        except (OSError, ValueError, KeyError) as exc:
            return {"ok":False, "error":f"{type(exc).__name__}: {exc}"}
