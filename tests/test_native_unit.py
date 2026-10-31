"""Behavioral regressions for v0.4 model plans, cache tasks and N-port data."""
import json
import hashlib
import tempfile
import unittest
from pathlib import Path
import numpy as np
import pandas as pd
from pydantic import ValidationError
from em_model import FrequencyPlan, MeshSettings
from session_state import SessionState
from sparameter_results import compare_frames, export_touchstone, matrix_from_frame
import test_momentum_unit as fixture_module
import xml.etree.ElementTree as ET
from momentum import inspect_setup
from types import SimpleNamespace
from unittest.mock import patch, Mock
import sys
from em_model import em_operation
from circuit_runner import CircuitRunner, signature, dispatch_circuit


class NativeTests(unittest.TestCase):
    def test_invalid_frequency_plans_rejected(self):
        for kwargs in [dict(start_ghz=2, stop_ghz=1, points=2), dict(kind="dc", stop_ghz=1), dict(start_ghz=1, stop_ghz=2, points=1)]:
            with self.assertRaises(ValidationError):
                FrequencyPlan(**kwargs)

    def test_mesh_requires_finite_positive_density(self):
        for value in [0, -1, float("nan"), float("inf")]:
            with self.assertRaises(ValidationError):
                MeshSettings(cells_per_wavelength=value)

    def test_external_gui_task_survives_restart_without_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "state.json"
            state = SessionState(p)
            tid = state.create_external_task("gui_circuit", {"session_id": 123, "marker": "original"})
            restored = SessionState(p).task_status(tid)
            self.assertEqual(restored["status"], "running")
            self.assertEqual(restored["monitor"]["marker"], "original")

    def test_nport_touchstone_roundtrip_order(self):
        import skrf
        with tempfile.TemporaryDirectory() as tmp:
            for n in [1, 2, 3, 32]:
                data = {f"S[{i},{j}]": [complex(i*.001, j*.002), complex(i*.003, j*.004)] for i in range(1,n+1) for j in range(1,n+1)}
                frame = pd.DataFrame(data, index=[1e9,2e9])
                target = Path(tmp) / f"test.s{n}p"
                export_touchstone(frame, target)
                network = skrf.Network(str(target))
                _, expected = matrix_from_frame(frame)
                np.testing.assert_allclose(network.s, expected, atol=1e-15)

    def test_comparison_rejects_grid_mismatch(self):
        a = pd.DataFrame({"S[1,1]": [1j, 2j]}, index=[1e9,2e9])
        b = a.copy(); b.index = [1e9,2.01e9]
        with self.assertRaisesRegex(ValueError, "grid"):
            compare_frames(a,b)
        self.assertTrue(compare_frames(a,a)["passed"])


class DCSetupTests(unittest.TestCase):
    def test_native_discrete_dc_plus_rf_grid(self):
        fixture = fixture_module.SetupFixture()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        plans = fixture.main.find("MomSimFreqTable/freqPlanMap")
        plan = ET.SubElement(plans, "MomSimFreqTableDef")
        for tag, value in {"enableFreq":"True", "typeFreq":"3", "startFreq":"0", "startFreqUnit":"GHz"}.items():
            ET.SubElement(plan,tag).text=value
        fixture.save()
        f = inspect_setup(str(fixture.ws),str(fixture.xml))["frequency"]
        self.assertTrue(f["include_dc"])
        self.assertEqual(f["points"],397)


class NativeConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.ws=Path(self.tmp.name);self.folder=self.ws/'lib'/'cell'/'emSetup';self.folder.mkdir(parents=True)
        self.xml=self.folder/'emStateFile.xml'
        self.xml.write_text('<EmSimSetup><MainWindow><EMComponent><OutputEmModel><modelName>old</modelName></OutputEmModel><Preserve>yes</Preserve></EMComponent><EMComponent/><Other>keep</Other></MainWindow></EmSimSetup>')
        lib=SimpleNamespace(path=self.ws/'lib',cell=lambda _:SimpleNamespace(view=lambda _:SimpleNamespace(path=self.folder)))
        de=SimpleNamespace(workspace_is_open=lambda:True,active_workspace=lambda:SimpleNamespace(path=self.ws),Library=SimpleNamespace(get=lambda _:lib))
        ads=SimpleNamespace(de=de)
        p=patch.dict(sys.modules,{'keysight':SimpleNamespace(ads=ads),'keysight.ads':ads,'keysight.ads.de':de});p.start();self.addCleanup(p.stop)

    def test_model_output_is_unique_and_preserves_unrelated_options(self):
        before=self.xml.read_bytes()
        result=em_operation(dict(operation='model',lib='lib',cell='cell',setup='emSetup',model='emModel'))
        main=ET.parse(self.xml).getroot().find('MainWindow')
        self.assertEqual(len(main.findall('EMComponent')),1)
        self.assertEqual(main.findtext('EMComponent/Preserve'),'yes')
        self.assertEqual(main.findtext('Other'),'keep')
        self.assertEqual(main.findtext('EMComponent/OutputEmModel/autoCreateEMModel'),'True')
        self.assertEqual(Path(result['backup']).read_bytes(),before)
        self.assertFalse(result['existing_results_current'])

    def test_unknown_geometry_parameter_does_not_mutate_options(self):
        options=self.folder/'emDataOptions.xml';options.write_text('<emDataViewOptions><cellParameters useInterpolation="no"><parameters><cellParameter name="Wm" resolution="1e-5"/></parameters></cellParameters></emDataViewOptions>')
        before=options.read_bytes()
        with self.assertRaisesRegex(ValueError,'Unknown'):
            em_operation(dict(operation='model_parameters',lib='lib',cell='cell',model='emModel',interpolate_geometry=True,resolutions={'Typo':.1}))
        self.assertEqual(options.read_bytes(),before)


class CircuitMonitorTests(unittest.TestCase):
    def test_qt_callback_is_retained_until_dispatch_and_records_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            class Window:
                def isVisible(self):return True
                def windowTitle(self):return 'tb [lib:tb:schematic] (Schematic):7'
                def findChildren(self,_):return [action]
                def raise_(self):pass
                def activateWindow(self):pass
            action=SimpleNamespace(objectName=lambda:'Simulate',isEnabled=lambda:True)
            w=Window();app=SimpleNamespace(topLevelWidgets=lambda:[w],processEvents=lambda:None,activeWindow=lambda:w)
            timer=Mock();de=SimpleNamespace(ael=SimpleNamespace(db_get_design_context_from_name=Mock(),de_analyze=Mock(),
                api_set_current_window_by_seq_num=Mock(),de_get_current_design_context=Mock(),db_get_design_name=Mock(return_value='lib:tb:schematic')))
            ael=SimpleNamespace(decl=SimpleNamespace(APPEND_DSN_MODE=2,READ_ONLY_DSN_MODE=0))
            modules={'keysight':SimpleNamespace(ads=SimpleNamespace(de=de,ael=ael)),'keysight.ads':SimpleNamespace(de=de,ael=ael),
                     'PySide6':SimpleNamespace(),'PySide6.QtWidgets':SimpleNamespace(QApplication=SimpleNamespace(instance=lambda:app),QMainWindow=Window),
                     'PySide6.QtGui':SimpleNamespace(QAction=object),'PySide6.QtCore':SimpleNamespace(QTimer=SimpleNamespace(singleShot=timer)),
                     'shiboken6':SimpleNamespace(isValid=lambda _:True)}
            marker=Path(tmp)/'dispatch.json'
            with patch.dict(sys.modules,modules):
                dispatch_circuit(dict(design='lib:tb:schematic',mode='simulate',marker=str(marker)))
                self.assertEqual(len(app._ads_mcp_dispatches),1)
                timer.call_args.args[1]()
                de.ael.de_analyze.assert_called_once()
                de.ael.api_set_current_window_by_seq_num.assert_called_once_with(7)
                self.assertFalse(app._ads_mcp_dispatches)
            self.assertTrue(json.loads(marker.read_text())['dispatched'])

    def test_preexisting_dataset_and_log_do_not_complete_new_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws=Path(tmp);data=ws/'data';data.mkdir();ds=data/'tb.ds';ds.write_bytes(b'old')
            log=data/'logFile.txt';log.write_text('Total stopwatch time')
            state=SessionState(ws/'state.json')
            tid=state.create_external_task('gui_circuit',dict(workspace=str(ws),session_id=1,design='lib:tb:schematic',cell='tb',mode='simulate',folder=str(ws),marker=str(ws/'missing.json'),started=0,datasets_before={str(ds.resolve()):signature(ds)},logs_before={str(log.resolve()):signature(log)}))
            reader=Mock();result=CircuitRunner().poll(state,tid,reader)
            self.assertEqual(result['status'],'running');reader.assert_not_called()

    def test_release_retains_best_values_without_applying_to_design(self):
        with tempfile.TemporaryDirectory() as tmp:
            state=SessionState(Path(tmp)/'state.json')
            tid=state.create_external_task('gui_circuit',dict(session_id=1,workspace=tmp,mode='optimize',design='lib:tb:schematic',folder=tmp))
            with self.assertRaises(ValueError):CircuitRunner().release(state,tid)
            ds=Path(tmp)/'result.ds';ds.write_bytes(b'unit dataset')
            state.update_external_task(tid,status='done',result={'optimal_parameters':{'Wm':.7},'dataset':str(ds),'dataset_sha256':hashlib.sha256(ds.read_bytes()).hexdigest()})
            with patch('circuit_runner.call_native',return_value={'simulator_released':True,'schematic_parameters_modified':False,'cockpit_closed':[True],'cockpit_close_required':False}) as native:
                result=CircuitRunner().release(state,tid)
                self.assertEqual(result['optimal_parameters'],{'Wm':.7})
                self.assertFalse(result['schematic_parameters_modified'])
                CircuitRunner().release(state,tid)
                self.assertEqual(native.call_count,1)

    def test_release_resumes_pending_close_without_releasing_simulator_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            state=SessionState(Path(tmp)/'state.json')
            tid=state.create_external_task('gui_circuit',dict(session_id=1,workspace=tmp,mode='optimize',design='lib:tb:schematic',folder=tmp))
            ds=Path(tmp)/'result.ds';ds.write_bytes(b'unit dataset')
            state.update_external_task(tid,status='done',result={'optimal_parameters':{'Wm':.7},'dataset':str(ds),'dataset_sha256':hashlib.sha256(ds.read_bytes()).hexdigest()})
            Path(tmp,'optimizer-release.json').write_text(json.dumps({'design':'lib:tb:schematic','simulator_released':True,'cockpit_closed':[False],'cockpit_close_required':True}),encoding='utf-8')
            with patch('circuit_runner.call_native',return_value={'simulator_released':True,'cockpit_closed':[True],'cockpit_close_required':False}) as native:
                CircuitRunner().release(state,tid)
                self.assertTrue(native.call_args.args[1]['already_released'])


if __name__ == "__main__":
    unittest.main()
