"""One isolated simulator invocation. Internal entry point, not an MCP server."""
import json
from pathlib import Path
import sys
import numpy as np

from ads_engine import ensure_env
from matching_sweep import evaluate_frame
from rf_workflows import write_json
from sparameter_results import export_touchstone, matrix_from_frame


def process_request(request_file):
    request_file = Path(request_file).resolve(strict=True)
    case = request_file.parent
    try:
        request = json.loads(request_file.read_text(encoding="utf-8"))
        ensure_env()
        if request.get("analysis") == "hb":
            from hb_review import run_hb_case
            write_json(case / "result.json", dict(ok=True, **run_hb_case(case, request)))
            return 0
        from keysight.edatoolbox.ads import CircuitSimulator
        from keysight.ads import dataset
        options = {"rel_data_dir": request["data_directory"]} if request["data_directory"] else {}
        CircuitSimulator().run_netlist((case / "input.net").read_text(encoding="utf-8"), output_dir=str(case), working_dir=str(case),
                                      output_file=str(case / "simulator.log"), netlist_file=str(case / "simulator.ckt"),
                                      dataset_name="matching_case", **options)
        files = list(case.glob("*.ds"))
        if len(files) != 1:
            raise ValueError(f"Expected one fresh dataset, got {len(files)}")
        with dataset.open(files[0], mode="r") as ds:
            frames = [ds[k].to_dataframe() for k in ds.keys()]
        frames = [f for f in frames if "S[1,1]" in f.columns]
        if len(frames) != 1 or frames[0].index.nlevels != 1:
            raise ValueError("Expected exactly one single-axis S-parameter varblock")
        frame = frames[0].sort_index()
        points, metrics = evaluate_frame(frame, request["evaluation"])
        f, s = matrix_from_frame(frame)
        np.savez_compressed(case / "sparameters.npz", freq_hz=f, s=s)
        points.to_csv(case / "points.csv", index=False, encoding="utf-8-sig")
        touchstone = export_touchstone(frame, case / "result.s2p", reference_ohms=request["evaluation"]["reference_ohms"])
        write_json(case / "result.json", dict(ok=True, dataset=str(files[0]), points_csv=str(case / "points.csv"),
                                              touchstone=touchstone["touchstone"], raw_sparameters=str(case / "sparameters.npz"), **metrics))
        return 0
    except Exception as exc:
        write_json(case / "result.json", dict(ok=False, error=f"{type(exc).__name__}: {exc}"))
        return 1


def main():
    if sys.argv[1] != "--serve":
        return process_request(sys.argv[1])
    import os
    import time
    import psutil
    folder = Path(sys.argv[2]).resolve(strict=True)
    owner_pid, owner_born = int(sys.argv[3]), float(sys.argv[4])
    while True:
        try:
            if abs(psutil.Process(owner_pid).create_time()-owner_born) > 0.01:
                return 0
        except psutil.NoSuchProcess:
            return 0
        if (folder / "stop").exists():
            return 0
        command = folder / "next.json"
        if command.exists():
            request = json.loads(command.read_text(encoding="utf-8"))
            command.unlink()
            process_request(request["request_file"])
            write_json(folder / "done.json", dict(sequence=request["sequence"]))
        else:
            time.sleep(0.03)


if __name__ == "__main__":
    from rf_executor import native_children_job
    with native_children_job():
        returncode=main()
    raise SystemExit(returncode)
