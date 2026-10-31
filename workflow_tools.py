"""Additional RF tools; registration does not start ADS or touch a workspace."""
import threading
from typing import Literal

from rf_workflows import LoadPullLimits, MatchingEvaluation, SweepParameter, analyze_loadpull
from matching_sweep import prepare_sweep, run_sweep
from matching_targets import build_regions
from matching_advanced import SearchParameter, SearchSettings, ToleranceParameter, ToleranceSettings, prepare_advanced, run_search, run_tolerance


def register(mcp, engine, state, dispatch_lock):
    from rf_job_tools import register as register_jobs
    register_jobs(mcp)
    def launch(kind, prepare, run):
        try:
            with dispatch_lock:
                if any(t["status"] == "running" for t in state.snapshot()["tasks"].values()):
                    raise RuntimeError("A simulation is running; poll it before starting another workflow")
                plan = prepare()
                from rf_jobs import JobStore
                return JobStore().submit({"matching_search":"search","matching_tolerance":"tolerance"}[kind],plan)
        except Exception as exc:
            return dict(ok=False, error=f"{type(exc).__name__}: {exc}")

    @mcp.tool()
    def ads_analyze_loadpull(datasets: list[str], output_dir: str = "", limits: LoadPullLimits | None = None,
                             compare_common: bool = False, reference_plane: str = "", conditions: str = "") -> dict:
        """Read all stored HB1Tone_LoadPull points, export CSV/manifest and optionally rank common loads.

        Reads .ds only; never starts a simulation or edits a project. Original
        probe names/directions are required. Invalid points are retained.
        Limits are optional minima for Pout dBm, PAE percent and Gt dB.
        Common-load comparison requires the same drive/bias/Z0/harmonic order,
        plus caller-supplied reference_plane and model/source/harmonic conditions.
        Uses exact R/X intersection, no interpolation. Numeric validity is not
        proof of HB convergence. Output directory must be new and outside input workspaces.
        """
        try:
            with engine._op():
                result = analyze_loadpull(datasets, output_dir, limits, compare_common, reference_plane, conditions)
            return {"ok": True, **result}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    @mcp.tool()
    def ads_run_matching_sweep(netlist_path: str, parameters: list[SweepParameter], evaluation: MatchingEvaluation,
                                output_dir: str = "", max_cases: int = 64, timeout_seconds: int = 120,
                                stop_on_error: bool = True, data_directory: str = "") -> dict:
        """Sweep a saved two-port SP netlist in new case directories; never edit/write back a schematic.

        Targets are variable, instance+parameter or model+parameter (e.g. MSUB Er/H); scope is an optional
        subcircuit definition name. Values are numeric, with an explicit ADS unit.
        Cartesian combinations are capped (default 64, maximum 256), run serially.
        Evaluation explicitly defines frequency band, real port Z0, complex load,
        input port/reference plane and constant Zin, target_table or region_file.
        Tables default to exact listed frequencies; linear interpolation is opt-in.
        Regions score only matching load-pull frequencies, never interpolate frequencies.
        Rank by worst scored-frequency impedance
        error, then RMS. This is sampled SP analysis, not HB or global optimization.
        Supply literal equal real Z on ports 1 and 2, and one S_Param analysis.
        Returns task_id; poll ads_get_sim_status for progress and output paths.
        """
        try:
            with dispatch_lock:
                if any(t["status"] == "running" for t in state.snapshot()["tasks"].values()):
                    raise RuntimeError("A simulation is running; poll it before starting a sweep")
                plan = prepare_sweep(netlist_path, parameters, evaluation, output_dir, max_cases, timeout_seconds, data_directory)
                from rf_jobs import JobStore
                plan["stop_on_error"]=stop_on_error
                return JobStore().submit("sweep",plan)
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    @mcp.tool()
    def ads_build_loadpull_regions(analysis_manifest: str, limits: LoadPullLimits, output_dir: str = "",
                                    mode: Literal["samples", "triangulated"] = "samples", max_edge_ohm: float | None = None,
                                    reference_plane: str = "", conditions: str = "") -> dict:
        """Build frequency-specific feasible impedance sets from hashed full load-pull CSV exports.

        Read-only analysis, no ADS simulation. Supply the manifest from this
        version of ads_analyze_loadpull and >=1 minimum Pout/PAE/Gt threshold.
        Same drive, bias, Z0 and harmonic order are required; state reference
        plane and model/source/harmonic conditions or inherit them from manifest.
        Default samples mode preserves discrete qualified points. Triangulated
        mode clips local triangles against all thresholds, excludes invalid
        vertices and long edges, and labels the resulting polygons interpolated.
        max_edge_ohm defaults to 2.5 times median nearest-neighbor spacing.
        No interpolation between frequencies; no claim of HB convergence.
        Returns region_file to use in MatchingEvaluation (same plane label).
        """
        try:
            with engine._op():
                return {"ok": True, **build_regions(analysis_manifest, limits, output_dir, mode, max_edge_ohm, reference_plane, conditions)}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    @mcp.tool()
    def ads_search_matching_network(netlist_path: str, parameters: list[SearchParameter], evaluation: MatchingEvaluation,
                                     settings: SearchSettings | None = None, output_dir: str = "", timeout_seconds: int = 120,
                                     data_directory: str = "", cache_dir: str = "", use_cache: bool = True,
                                     stop_on_error: bool = True) -> dict:
        """Search a frozen SP netlist using bounded coarse sampling and local refinement.

        Each parameter has minimum/maximum and optionally allowed_values,
        E12/E24 e_series, OR manufacturing step (anchored at minimum).
        Settings control total budget (includes cache hits), coarse/fine density,
        number of coarse winners to refine and reproducible seed. At most 8
        parameters and 256 evaluated cases. Large Cartesian grids use a bounded
        stratified subset; this does not prove a global optimum.
        Raw complex S data can be cached and rescored for different objectives.
        Cache fingerprints netlist, solver/install/config; external or unclassified
        model dependencies disable caching. All artifacts checked before reuse.
        Returns task_id; poll ads_get_sim_status. No schematic writeback.
        """
        return launch("matching_search", lambda: prepare_advanced("search", netlist_path, parameters, evaluation, settings,
                      output_dir, timeout_seconds, data_directory, cache_dir, use_cache, stop_on_error), run_search)

    @mcp.tool()
    def ads_analyze_matching_tolerance(netlist_path: str, parameters: list[ToleranceParameter], evaluation: MatchingEvaluation,
                                        settings: ToleranceSettings | None = None, output_dir: str = "", timeout_seconds: int = 120,
                                        data_directory: str = "", cache_dir: str = "", use_cache: bool = True,
                                        stop_on_error: bool = False) -> dict:
        """Nominal + two-sided one-at-a-time sensitivity, with optional corners or Monte Carlo.

        Explicit nominal and relative_pct OR absolute tolerance are required.
        model+parameter supports substrate Er/H; instance+parameter supports
        line dimensions and components. Optional physical bounds reject invalid
        intervals; never silently clamp physical variations to nominal grids.
        Monte Carlo assumes independent parameters: uniform ±tolerance, or
        truncated_normal with sigma=tolerance/3 conditioned on ±tolerance.
        Full corners must fit the budget. Every invocation includes nominal and
        2*N OAT cases. Sensitivity ranks max |Zperturbed-Znominal|, avoiding a
        misleading zero derivative at an optimum. Pass fractions include failed
        and unrun requested trials in the denominator; no threshold means no
        pass-fraction claim. This is SP robustness under assumed tolerances,
        not measured manufacturing yield or nonlinear PA performance.
        Returns task_id; poll ads_get_sim_status. No schematic writeback.
        """
        return launch("matching_tolerance", lambda: prepare_advanced("tolerance", netlist_path, parameters, evaluation, settings,
                      output_dir, timeout_seconds, data_directory, cache_dir, use_cache, stop_on_error), run_tolerance)
