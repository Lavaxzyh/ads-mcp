"""Planar construction input validation without ADS or license access."""
import tempfile
import unittest
import sys
import types
from pathlib import Path
from unittest.mock import Mock, patch

from pydantic import ValidationError

import em_modeling as em


class GeometryTests(unittest.TestCase):
    def test_rectangle_accepts_numeric_coordinates(self):
        shape = em.LayoutShape(kind="rectangle", points_mm=[(0, -1), (5, 1)])
        self.assertEqual(shape.model_dump(mode="json")["points_mm"], [[0.0, -1.0], [5.0, 1.0]])

    def test_rectangle_rejects_bad_corners(self):
        for points in [[(0, 0), (0, 1)], [(1, 0), (0, 1)], [(0, 0), (1, 1), (2, 0)]]:
            with self.subTest(points=points), self.assertRaises(ValidationError):
                em.LayoutShape(kind="rectangle", points_mm=points)

    def test_concave_polygon_is_accepted(self):
        em.LayoutShape(kind="polygon", points_mm=[(0, 0), (3, 0), (1, 1), (0, 3)])

    def test_polygon_rejects_degenerate_or_intersecting_edges(self):
        cases = [
            [(0, 0), (1, 1)],
            [(0, 0), (1, 0), (2, 0)],
            [(0, 0), (1, 0), (0, 1), (0, 0)],
            [(0, 0), (3, 0), (0, 2), (2, 2), (0, 1)],
        ]
        for points in cases:
            with self.subTest(points=points), self.assertRaises(ValidationError):
                em.LayoutShape(kind="polygon", points_mm=points)

    def test_coordinates_reject_nan_infinity_bool_and_strings(self):
        for value in (float("nan"), float("inf"), True, "1", 1e8):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                em.LayoutShape(kind="rectangle", points_mm=[(0, 0), (value, 1)])

    def test_unknown_fields_and_unsafe_layer_names_are_rejected(self):
        for extra in ({"layer": "../other"}, {"arbitrary_code": "print(1)"}):
            with self.subTest(extra=extra), self.assertRaises(ValidationError):
                em.LayoutShape(kind="rectangle", points_mm=[(0, 0), (1, 1)], **extra)

    def test_port_number_is_a_strict_integer(self):
        for number in (True, 1.5, 0, 65):
            with self.subTest(number=number), self.assertRaises(ValidationError):
                em.LayoutPort(name="P1", number=number, x_mm=0, y_mm=0)

    def test_material_parameters_are_physical_finite_values(self):
        for fields in ({"height_mm": 0, "er": 4}, {"height_mm": 1, "er": 0.5},
                       {"height_mm": 1, "er": 4, "loss_tangent": 1},
                       {"height_mm": 1, "er": float("inf")}):
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                em.DielectricLayer(**fields)

    def test_conductor_parameters_reject_invalid_thickness_and_conductivity(self):
        for fields in ({"thickness_um": -1}, {"conductivity_s_per_m": 0}, {"interface": True}):
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                em.ConductorLayer(**fields)


class BuilderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ads-model-unit-")
        self.addCleanup(self.tmp.cleanup)
        self.ws = Path(self.tmp.name).resolve()
        (self.ws / "lib.defs").touch()
        self.modeler = em.EmModeler()
        patcher = patch.object(em, "_model_in_ads", return_value={"created": True})
        self.native = patcher.start()
        self.addCleanup(patcher.stop)

    def build(self, operation, **data):
        return self.modeler.execute(str(self.ws), "lib", operation, **data)

    def substrate(self, **overrides):
        data = {"name": "stack", "dielectrics": [{"height_mm": 1.6, "er": 4.4}],
                "conductors": [{"layer": "cond", "interface": 1}], "ground_bottom": True}
        data.update(overrides)
        return self.build("substrate", **data)

    def test_substrate_inputs_are_normalized_before_native_call(self):
        self.substrate()
        args = self.native.call_args.args[0]
        self.assertEqual(args["workspace"], str(self.ws))
        self.assertEqual(args["dielectrics"][0]["loss_tangent"], 0)
        self.assertIsNone(args["conductors"][0]["conductivity_s_per_m"])

    def test_out_of_stack_interface_is_rejected_before_native_call(self):
        with self.assertRaisesRegex(ValueError, "interfaces"):
            self.substrate(conductors=[{"layer": "cond", "interface": 2}])
        self.native.assert_not_called()

    def test_duplicate_conductor_mappings_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "unique"):
            self.substrate(conductors=[{"layer": "cond"}, {"layer": "cond"}])
        self.native.assert_not_called()

    def test_empty_stack_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "dielectrics"):
            self.substrate(dielectrics=[])
        self.native.assert_not_called()

    def test_empty_geometry_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "shapes"):
            self.build("layout", cell="cell", view="layout", shapes=[])
        self.native.assert_not_called()

    def test_pydantic_objects_can_be_passed_directly(self):
        shape = em.LayoutShape(kind="rectangle", points_mm=[(0, 0), (1, 1)])
        self.build("layout", cell="cell", view="layout", shapes=[shape])
        self.assertEqual(self.native.call_args.args[0]["shapes"][0]["layer"], "cond")

    def test_duplicate_port_names_or_numbers_are_rejected(self):
        base = {"name": "P1", "number": 1, "x_mm": 0, "y_mm": 0}
        for extra in ({"name": "P1", "number": 2}, {"name": "P2", "number": 1}):
            with self.subTest(extra=extra), self.assertRaisesRegex(ValueError, "distinct"):
                self.build("ports", cell="cell", view="layout", ports=[base, {**base, **extra}])
        self.native.assert_not_called()

    def test_unsafe_cell_name_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "ADS name"):
            self.build("layout", cell="../cell", view="layout", shapes=[])
        self.native.assert_not_called()

    def test_non_workspace_is_rejected(self):
        (self.ws / "lib.defs").unlink()
        with self.assertRaisesRegex(ValueError, "lib.defs"):
            self.substrate()
        self.native.assert_not_called()

    def test_setup_rejects_invalid_sweep_and_impedance(self):
        base = {"cell": "cell", "view": "layout", "substrate": "stack", "setup": "emSetup",
                "dataset": "cell_em", "start_ghz": 0.05, "stop_ghz": 4.0, "points": 396, "impedance_ohms": 50.0}
        for change in ({"points": True}, {"points": 1}, {"points": 1.5}, {"start_ghz": 0},
                       {"stop_ghz": float("nan")}, {"start_ghz": 5}, {"impedance_ohms": 75}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.build("setup", **{**base, **change})
        self.native.assert_not_called()

    def test_setup_returns_post_build_inspection(self):
        self.native.return_value = {"setup_file": "new.xml"}
        with patch.object(em, "inspect_setup", return_value={"frequency": {"points": 396}}) as inspect:
            result = self.build("setup", cell="cell", view="layout", substrate="stack", setup="emSetup",
                                dataset="cell_em", start_ghz=0.05, stop_ghz=4.0, points=396, impedance_ohms=50.0)
        self.assertEqual(result["inspection"]["frequency"]["points"], 396)
        inspect.assert_called_once_with(str(self.ws), "new.xml", "emSetup")

    def test_unknown_operation_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown"):
            self.build("execute_python")
        self.native.assert_not_called()


class NativePortTests(unittest.TestCase):
    """A fake native database makes destructive open modes observable."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ads-native-unit-")
        self.addCleanup(self.tmp.cleanup)
        self.ws = Path(self.tmp.name).resolve()
        (self.ws / "lib.defs").touch()
        self.geometry = ["saved rectangle"]
        self.terms = []
        self.layout_view = types.SimpleNamespace(is_layout_view=True)
        cell = types.SimpleNamespace(view_exists=lambda _: True, view=lambda _: self.layout_view)
        self.cell = cell
        lib = types.SimpleNamespace(has_tech=True, cell_exists=lambda _: True, cell=lambda _: cell)
        self.design = types.SimpleNamespace(
            terms=self.terms, meter_to_uu_factor=1000, save_design=Mock(),
            find_or_add_net=lambda name: types.SimpleNamespace(name=name),
            add_dot=lambda lid, point: (lid, point), add_pin=Mock(),
        )
        def add_term(net, name):
            term = types.SimpleNamespace(net=net, name=name, number=0)
            self.terms.append(term)
            return term
        self.design.add_term = add_term
        def open_design(name, mode):
            if mode == "write":
                self.geometry.clear()
                self.terms.clear()
            return self.design
        self.open_design = Mock(side_effect=open_design)
        keysight, ads, de, db, base_db, subst = [types.ModuleType(x) for x in
            ("keysight", "keysight.ads", "keysight.ads.de", "keysight.ads.de.db_uu",
             "keysight.ads.de.db", "keysight.ads.subst")]
        keysight.ads = ads
        ads.de, ads.subst = de, subst
        self.subst = subst
        de.db_uu, de.db = db, base_db
        de.workspace_is_open = lambda: True
        de.active_workspace = lambda: types.SimpleNamespace(path=str(self.ws))
        de.Library = types.SimpleNamespace(get=lambda _: lib)
        de.get_path_to_open_library = Mock(return_value=str(self.ws / "lib"))
        self.de = de
        db.DesignMode = types.SimpleNamespace(APPEND="append", WRITE="write")
        db.open_design = self.open_design
        base_db.LayerId = types.SimpleNamespace(from_name=lambda *_: types.SimpleNamespace(layer=1))
        class Transaction:
            def __init__(self, *_): pass
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def commit(self): pass
        base_db.Transaction = Transaction
        modules = {module.__name__: module for module in (keysight, ads, de, db, base_db, subst)}
        patcher = patch.dict(sys.modules, modules)
        patcher.start()
        self.addCleanup(patcher.stop)

    def add_ports(self):
        return em.EmModeler().execute(str(self.ws), "lib", "ports", cell="cell", view="layout",
                                     ports=[{"name":"P1", "number":1, "x_mm":0, "y_mm":0}])

    def test_appending_ports_preserves_saved_geometry(self):
        self.add_ports()
        self.assertEqual(self.geometry, ["saved rectangle"])
        self.open_design.assert_called_once_with("lib:cell:layout", "append")
        self.assertEqual(self.terms[0].number, 1)
        self.design.save_design.assert_called_once()

    def test_existing_port_collision_is_rejected_without_saving(self):
        self.terms.append(types.SimpleNamespace(name="P1", number=1))
        with self.assertRaisesRegex(RuntimeError, "already exists"):
            self.add_ports()
        self.assertEqual(self.geometry, ["saved rectangle"])
        self.design.save_design.assert_not_called()
        self.design.add_pin.assert_not_called()

    def test_non_layout_view_is_not_opened_for_editing(self):
        self.layout_view.is_layout_view = False
        with self.assertRaisesRegex(RuntimeError, "not a layout"):
            self.add_ports()
        self.open_design.assert_not_called()

    def test_external_library_is_not_modified(self):
        self.de.get_path_to_open_library.return_value = str(self.ws.parent / "external_library")
        with self.assertRaisesRegex(RuntimeError, "local library"):
            self.add_ports()
        self.open_design.assert_not_called()

    def test_workspace_run_reservation_blocks_edits(self):
        locks = self.ws / ".ads-mcp" / "locks"
        locks.mkdir(parents=True)
        (locks / "active.json").write_text("{}")
        with self.assertRaisesRegex(RuntimeError, "reservation"):
            self.add_ports()
        self.open_design.assert_not_called()

    def test_setup_rejects_missing_ground_reference_without_saving(self):
        self.cell.view_exists = lambda name: name == "layout"
        self.subst.substrate_exists = lambda *_: True
        for cover, termination in ((False, False), (True, True)):
            self.subst.open_substrate = lambda *_: types.SimpleNamespace(
                interfaces=[types.SimpleNamespace(is_cover=cover, is_termination=termination)])
            with self.subTest(cover=cover, termination=termination), self.assertRaisesRegex(RuntimeError, "ground cover"):
                em.EmModeler().execute(str(self.ws), "lib", "setup", cell="cell", view="layout",
                                      substrate="stack", setup="emSetup", start_ghz=0.05,
                                      stop_ghz=4.0, points=396, impedance_ohms=50.0)
        self.design.save_design.assert_not_called()


if __name__ == "__main__":
    unittest.main()
