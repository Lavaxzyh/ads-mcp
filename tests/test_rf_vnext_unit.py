import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd

from rf_workflows import MatchingEvaluation, MetricCriterion, ParameterTarget
from matching_sweep import evaluate_frame, NetlistEditor
from rf_metrics import rank_key, pareto_cases
from rf_optimizer import LinearConstraint, geometry_failures
from matching_advanced import SearchParameter
from hb_review import evaluate_hb_frame, compression_summary, prepare_hb
from rf_executor import process_alive
from rf_jobs import JobStore


def evaluation(**changes):
    values=dict(start_hz=1e9,stop_hz=2e9,reference_ohms=50,load_r_ohm=50,load_x_ohm=0,
                target_r_ohm=50,target_x_ohm=0,input_port=1,reference_plane="input connector")
    values.update(changes)
    return values


def frame(s21=.5):
    return pd.DataFrame({"S[1,1]":[0j,0j],"S[1,2]":[complex(s21)]*2,"S[2,1]":[complex(s21)]*2,"S[2,2]":[0j,0j]},index=pd.Index([1e9,2e9],name="freq"))


def contract():
    return dict(frequency_target=dict(variable="RFfreq",unit="Hz"),drive_target=dict(variable="Pavs"),harmonic_order=3,
                input_voltage="Vin",input_current="Iin",output_voltage="Vout",output_current="Iout",
                supplies=[dict(voltage="Vdd",current="Idd")],input_reference_plane="input",output_reference_plane="output",
                network_boundary="PA + both matches",model_conditions="synthetic formula test, not ADS evidence",source_termination="50 ohm",load_termination="50 ohm",harmonic_terminations="50 ohm")


def hb_frame():
    data=dict(Vin=[0,1,0,0],Iin=[0,.02,0,0],Vout=[0,10,0,0],Iout=[0,.2,0,0],Vdd=[10,0,0,0],Idd=[.2,0,0,0])
    return pd.DataFrame(data,index=pd.Index([0,1e9,2e9,3e9],name="freq")).astype(complex)


class MetricTests(unittest.TestCase):
    def test_loaded_transducer_gain_uses_source_mismatch(self):
        points,result=evaluate_frame(frame(),evaluation(source_r_ohm=100,source_x_ohm=0,criteria=[dict(name="gain",metric="transducer_gain_db",objective="maximize")]))
        self.assertAlmostEqual(points.transducer_gain_db.iloc[0],10*math.log10(.25*8/9),12)
        self.assertAlmostEqual(points.operating_gain_db.iloc[0],10*math.log10(.25),12)

    def test_missing_source_does_not_silently_assume_fifty(self):
        with self.assertRaises(ValueError):
            MatchingEvaluation.model_validate(evaluation(criteria=[dict(name="gain",metric="transducer_gain_db",objective="maximize")]))

    def test_infeasible_candidate_cannot_win_by_weight(self):
        feasible=dict(case=1,status="done",feasible=True,constraint_violation=0,objective_score=1000,max_error_ohm=10)
        infeasible=dict(case=2,status="done",feasible=False,constraint_violation=.01,objective_score=-1000,max_error_ohm=0)
        self.assertLess(rank_key(feasible),rank_key(infeasible))

    def test_sampled_constraints_and_ripple(self):
        f=frame();f.loc[2e9,"S[2,1]"]=.25
        _,r=evaluate_frame(f,evaluation(criteria=[dict(name="loss",metric="matched_s21_db",aggregate="min",lower=-10),dict(name="flat",metric="matched_s21_db",aggregate="ripple",upper=3)]))
        self.assertFalse(r["feasible"])
        self.assertAlmostEqual(r["criteria"][1]["value"],20*math.log10(2))

    def test_metric_band_outside_dataset_rejected(self):
        with self.assertRaises(ValueError):
            evaluate_frame(frame(),evaluation(criteria=[dict(name="harmonic",metric="matched_s21_db",aggregate="max",upper=-20,start_hz=3e9,stop_hz=4e9)]))

    def test_pareto_retains_tradeoffs_excludes_dominated(self):
        rows=[dict(case=i,status="done",feasible=True,objective_vector=v) for i,v in enumerate(([1,3],[3,1],[4,4],[2,2]),1)]
        rows.append(dict(case=5,status="done",feasible=False,objective_vector=[0,0]))
        self.assertEqual(pareto_cases(rows),[1,2,4])

    def test_geometry_constraint_uses_numeric_units(self):
        targets=[SearchParameter(variable="W1",unit="mm",minimum=.5,maximum=3),SearchParameter(variable="W2",unit="mm",minimum=.5,maximum=3)]
        constraint=LinearConstraint(name="total width",coefficients={"top/W1":1,"top/W2":1},upper=4,unit="mm")
        self.assertFalse(geometry_failures(targets,[2,2],[constraint]))
        self.assertEqual(geometry_failures(targets,[3,2],[constraint])[0]["value"],5)

    def test_netlist_continuation_tolerates_vendor_trailing_spaces(self):
        editor=NetlistEditor('L:L1 a b L=1 nH \\ \n Noise=yes\n')
        self.assertEqual(len(editor.lines),1)
        self.assertIn('Noise=yes',editor.render([ParameterTarget(instance="L1",parameter="L",unit="nH")],[2]))

    def test_nan_scale_rejected(self):
        with self.assertRaises(ValueError):
            MetricCriterion(name="x",metric="matched_s21_db",objective="maximize",scale=float("nan"))


class HBTests(unittest.TestCase):
    def calculate(self,f=None,c=None,log="HB HB1[1]\nSimulation finished"):
        return evaluate_hb_frame(hb_frame() if f is None else f,contract() if c is None else c,dict(freq_hz=1e9,pavs_dbm=10),log)

    def test_peak_phasor_power_and_pae(self):
        r=self.calculate();self.assertAlmostEqual(r["pout_dbm"],30);self.assertAlmostEqual(r["pae_pct"],49.5)
        self.assertAlmostEqual(r["pin_delivered_w"],.01);self.assertAlmostEqual(r["gain_db"],20)

    def test_nonconvergence_rejected_even_finite_data(self):
        with self.assertRaises(ValueError):self.calculate(log="HB HB1[1]\nfailed to converge\nSimulation finished")

    def test_missing_harmonic_rejected(self):
        with self.assertRaises(ValueError):self.calculate(f=hb_frame().iloc[:-1])

    def test_wrong_probe_direction_rejected(self):
        c=contract();c["output_current_sign"]=-1
        with self.assertRaises(ValueError):self.calculate(c=c)

    def test_missing_supply_not_invented(self):
        with self.assertRaises(ValueError):self.calculate(f=hb_frame().drop(columns="Idd"))

    def test_power_balance_rejected(self):
        f=hb_frame();f.loc[0,"Idd"]=.01
        with self.assertRaises(ValueError):self.calculate(f=f)

    def test_waveform_peak_convention(self):
        c=contract();c["stress_voltage"]="Vout"
        r=self.calculate(c=c);self.assertAlmostEqual(r["voltage_sampled_max"],10);self.assertAlmostEqual(r["voltage_sampled_min"],-10)

    def test_compression_no_extrapolation(self):
        rows=[dict(candidate_id=1,status="done",gain_db=g,conditions=dict(freq_hz=1e9,pavs_dbm=p,bias_values=[])) for p,g in [(0,20),(1,20),(10,19.5)]]
        self.assertEqual(compression_summary(rows)[0]["status"],"not_bracketed")
        rows[-1]["gain_db"]=18
        self.assertAlmostEqual(compression_summary(rows)[0]["p1db_input_dbm"],5.5)


class JobTests(unittest.TestCase):
    def test_pid_reuse_is_not_liveness(self):
        import psutil
        p=psutil.Process()
        self.assertTrue(process_alive(p.pid,p.create_time()))
        self.assertFalse(process_alive(p.pid,p.create_time()-10))

    def test_control_queue_pagination_and_unknown_fields(self):
        with tempfile.TemporaryDirectory() as t, patch.object(JobStore,"ensure_dispatch"):
            store=JobStore(Path(t)/"queue")
            out=Path(t)/"output"
            job=store.submit("search",dict(output_dir=str(out),total_cases=2))
            job_id=job["job_id"]
            self.assertEqual(store.control_job(job_id,"pause")["status"],"paused")
            self.assertEqual(store.control_job(job_id,"resume")["status"],"queued")
            out.mkdir();(out/"cases.json").write_text(json.dumps([dict(case=1,status="done",feasible=True),dict(case=2,status="error",error="solver")]),encoding="utf-8")
            self.assertEqual(store.detail(job_id,failures_only=True)["total"],1)
            with self.assertRaises(ValueError):store.detail(job_id,fields=["secret"])
            self.assertEqual(store.control_job(job_id,"cancel")["status"],"cancelled")
            with self.assertRaises(ValueError):store.control_job(job_id,"resume")


if __name__=="__main__":unittest.main()
