"""Offline contract and search tests; no CODE V process or COM activation.

The small callback interpreter covers the generated IF/assignment/SAV subset.
It does not implement, validate or emulate the proprietary optical solver.
"""

from copy import deepcopy
import json
from pathlib import Path
import random
import re
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import grouped_glass as ge


class SyntheticCodeV:
    """Known two-material landscape: neither single change helps; the pair does."""

    def __init__(self, native, *, bad_native=False, stale=False, joint_failure=False, native_gain=False):
        self.native = native.resolve()
        self.bad_native = bad_native
        self.stale = stale
        self.joint_failure = joint_failure
        self.native_gain = native_gain
        materials = {(surface, 1): ("", "") for surface in range(7)}
        materials.update({(1, 1): ("TEST", "A0"), (3, 1): ("TEST", "B0"), (5, 1): ("TEST", "FIXED")})
        self.current = {"materials": materials, "score": 10.0, "valid": False, "geometry": 0}
        self.files = {}
        self.variables = {}
        self.commands = []
        self.joint_start_geometries = []

    def _optimize(self):
        self.current["geometry"] += 1
        a = self.current["materials"][(1, 1)][1]
        b = self.current["materials"][(3, 1)][1]
        self.current["score"] = 2.0 if (a, b) == ("A1", "B1") else (10.0 if (a, b) == ("A0", "B0") else 12.0)
        self.current["valid"] = not self.stale

    def EvaluateExpression(self, expression):
        if expression.startswith("^"):
            return str(self.variables[expression[1:]])
        if expression == "(NUM S)":
            return "6"
        if expression == "(NUM Z)":
            return "1"
        if expression == "(OPT ERF)":
            return "1" if self.current["valid"] else "-1"
        if expression == "(AUT.ERF ALL)":
            return str(self.current["score"])
        match = re.fullmatch(r"\(GLA S(\d+) Z(\d+)( CAT)?\)", expression)
        if match:
            surface, zoom = map(int, match.groups()[:2])
            return "'" + self.current["materials"][(surface, zoom)][0 if match[3] else 1] + "'"
        raise AssertionError(expression)

    def _expression(self, expression):
        expression = re.sub(r"\(GLA S\d+ Z\d+(?: CAT)?\)|\(OPT ERF\)|\(AUT\.ERF ALL\)",
                            lambda match: repr(ge.cv_string(self.EvaluateExpression(match[0])))
                            if "GLA" in match[0] else self.EvaluateExpression(match[0]), expression)
        expression = re.sub(r"\^([A-Za-z_0-9]+)", lambda match: repr(self.variables[match[1]]), expression)
        expression = expression.replace("<>", "!=")
        expression = re.sub(r"(?<![<>=!])=(?!=)", "==", expression)
        expression = expression.replace(" AND ", " and ").replace(" OR ", " or ")
        return eval(expression, {"__builtins__": {}, "UPCASE": str.upper})

    def _callback(self, path):
        text = path.read_text(encoding="ascii").replace("&\n", "").replace("&\r\n", "")
        active, stack = True, []
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith(("!", "GBL ")):
                continue
            if line.startswith("IF "):
                condition = bool(self._expression(line[3:])) if active else False
                stack.append((active, condition))
                active = active and condition
            elif line == "ELS":
                parent, condition = stack[-1]
                active = parent and not condition
            elif line == "END IF":
                active = stack.pop()[0]
            elif active and line.startswith("^"):
                name, value = line.split("==", 1)
                self.variables[name.strip()[1:]] = self._expression(value.strip())
            elif active:
                self.Command(line)
        if stack:
            raise AssertionError("unclosed generated callback block")

    def Command(self, command):
        self.commands.append(command)
        paths = re.findall(r'"([^\"]*)"', command)
        if command.startswith("RES "):
            path = str(Path(paths[0]))
            if path in self.files:
                self.current = deepcopy(self.files[path])
            elif Path(path).name != "source.len":
                raise AssertionError(f"missing synthetic snapshot {path}")
        elif command.startswith("SAV "):
            path = Path(paths[0])
            self.files[str(path)] = deepcopy(self.current)
            path.write_text("SYNTHETIC TEST SNAPSHOT; NOT AN OPTICAL MODEL", encoding="ascii")
        elif command.startswith("GBL "):
            for name in re.findall(r"\^(\w+)", command):
                self.variables.setdefault(name, 0)
        elif command.startswith("^"):
            name, value = command.split("==", 1)
            self.variables[name.strip()[1:]] = float(value)
        elif command.startswith("GLA "):
            surface = int(re.search(r"S(\d+)", command)[1])
            code = command.split()[-1]
            name, catalog = code.rsplit("_", 1)
            self.current["materials"][(surface, 1)] = (catalog, name)
            self.current["valid"] = False
        elif command.startswith("IN ") and Path(paths[0]).name == "aut.seq":
            self.joint_start_geometries.append(self.current["geometry"])
            if self.joint_failure and self.current["materials"][(1, 1)][1] == "A1":
                return "Error: synthetic untraceable trial"
            self._optimize()
        elif command.startswith("IN ") and Path(paths[0]) == self.native:
            callback = Path(paths[2])
            before = deepcopy(self.current)
            if self.bad_native:
                self.current["materials"][(1, 1)] = ("TEST", "FORBIDDEN")
                self.current.update(score=1.0, valid=True)
                self._callback(callback)
            if self.native_gain:
                self.current["materials"][(1, 1)] = ("TEST", "A1")
                self.current.update(score=8.0, valid=True)
                self._callback(callback)
                self.native_gain = False
            # End on a worse trial deliberately: an outer runner must restore its checkpoint.
            self.current = deepcopy(before)
            self.current.update(score=50.0, valid=True)
        elif command.startswith("IN "):
            pass
        else:
            raise AssertionError(command)
        return ""


class GroupedGlassTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="grouped_glass_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cv = self.root / "CODEV"
        (self.cv / "macro").mkdir(parents=True)
        (self.cv / "glass").mkdir()
        self.native = self.cv / "macro/GlassExpert.seq"
        self.native.write_bytes(b"ORIGINAL MACRO PLACEHOLDER - MUST NOT CHANGE")
        (self.cv / "glass/TEST.xml").write_text("<Catalog><Name>TEST</Name><Glasses>" + "".join(
            f"<Glass><GlassName>{name}</GlassName></Glass>" for name in ["A0", "A1", "B0", "B1", "FIXED"])
            + "</Glasses></Catalog>", encoding="utf-8")
        (self.root / "source.len").write_bytes(b"SOURCE LENS PLACEHOLDER - MUST NOT CHANGE")
        (self.root / "aut.seq").write_text("AUT\nEFT TA\nGO\n", encoding="ascii")
        (self.root / "A.dat").write_text("Catalog\tGlass\nTEST\tA0\nTEST\tA1\n", encoding="ascii")
        (self.root / "B.dat").write_text("Catalog\tGlass\nTEST\tB0\nTEST\tB1\n", encoding="ascii")
        self.config = {"codev_root": str(self.cv), "lens": "source.len", "aut": "aut.seq",
                       "groups": [{"name": "A", "surfaces": [1], "candidates": "A.dat"},
                                  {"name": "B", "surfaces": [3], "candidates": "B.dat"}],
                       "passes": 3, "joint_trials": 1, "joint_pairs": [[1, 3]], "seed": 2}
        self.config_file = self.root / "config.json"

    def prepare(self):
        self.config_file.write_text(json.dumps(self.config), encoding="utf-8")
        return ge.prepare(self.config_file, self.root / "job")

    def run_synthetic(self, **kwargs):
        plan = self.prepare()
        application = SyntheticCodeV(self.native, **kwargs)
        result = ge.run_job(self.root / "job/job.json", application)
        return result, application, plan

    def test_prepare_creates_separate_lists_and_correct_surface_inputs(self):
        plan = self.prepare()
        a = (self.root / "job/assets/group_01.inputs.dat").read_text()
        b = (self.root / "job/assets/group_02.inputs.dat").read_text()
        self.assertIn("Surfaces:\t1\n", a)
        self.assertIn("User Catalog:\tassets/group_01.dat\n", a)
        self.assertIn("Surfaces:\t3\n", b)
        self.assertIn("User Catalog:\tassets/group_02.dat\n", b)
        self.assertEqual(plan["status"], "prepared_offline_not_optically_verified")

    def test_overlapping_groups_fail_before_output(self):
        self.config["groups"][1]["surfaces"] = [1]
        with self.assertRaisesRegex(ValueError, "重叠"):
            self.prepare()
        self.assertFalse((self.root / "job").exists())

    def test_uninstalled_material_fails_before_output(self):
        (self.root / "B.dat").write_text("Catalog\tGlass\nTEST\tMISSING\n")
        with self.assertRaisesRegex(ValueError, "TEST/MISSING"):
            self.prepare()
        self.assertFalse((self.root / "job").exists())

    def test_joint_and_native_only_constraints_cannot_silently_differ(self):
        self.config["native_settings"] = {"Absorption Factor": "0.8"}
        with self.assertRaisesRegex(ValueError, "联合换料"):
            self.prepare()

    def test_relative_weight_rebasing_is_explicitly_rejected(self):
        self.config["native_settings"] = {"Relative Weight": "0.9"}
        with self.assertRaisesRegex(ValueError, "重置基准"):
            self.prepare()

    def test_joint_move_finds_cooperation_missed_by_single_group_improvement(self):
        result, application, _ = self.run_synthetic()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["final_score"], 2.0)
        native_events = [e for e in result["events"] if e["type"] == "native_group"]
        self.assertTrue(all(e["score"] == 10.0 for e in native_events[:2]))
        self.assertTrue(any(e["type"] == "joint" and e.get("accepted") for e in result["events"]))
        self.assertEqual(application.current["materials"][(5, 1)], ("TEST", "FIXED"))
        self.assertFalse(any(command.startswith(("FRZ", "GLC")) for command in application.commands))

    def test_native_last_trial_is_not_treated_as_accepted_solution(self):
        self.config["joint_trials"] = 0
        result, _, _ = self.run_synthetic()
        self.assertEqual(result["final_score"], 10.0)
        self.assertEqual(len([e for e in result["events"] if e["type"] == "pass"]), 1)

    def test_generated_callback_preserves_a_valid_native_improvement(self):
        self.config["joint_trials"] = 0
        result, application, _ = self.run_synthetic(native_gain=True)
        self.assertEqual(result["final_score"], 8.0)
        self.assertEqual(application.current["materials"][(1, 1)], ("TEST", "A1"))

    def test_generated_callback_rejects_native_material_outside_group(self):
        self.config["joint_trials"] = 0
        result, application, _ = self.run_synthetic(bad_native=True)
        self.assertEqual(result["final_score"], 10.0)
        self.assertTrue(all(e["rejected_captures"] == 1 for e in result["events"] if e["type"] == "native_group"))
        self.assertEqual(application.current["materials"][(1, 1)], ("TEST", "A0"))

    def test_stale_aut_is_a_failure_not_a_completed_search(self):
        self.prepare()
        with self.assertRaisesRegex(ge.CandidateRejected, "过期"):
            ge.run_job(self.root / "job/job.json", SyntheticCodeV(self.native, stale=True))
        summary = json.loads((self.root / "job/results/summary.json").read_text())
        self.assertEqual(summary["status"], "failed")

    def test_failed_joint_trial_rolls_back_to_accepted_materials(self):
        result, application, _ = self.run_synthetic(joint_failure=True)
        self.assertEqual(result["final_score"], 10.0)
        rejected = [e for e in result["events"] if e["type"] == "joint" and "rejection" in e]
        self.assertEqual(len(rejected), 1)
        self.assertEqual(application.current["materials"][(1, 1)], ("TEST", "A0"))

    def test_macro_source_lens_and_aut_are_not_modified(self):
        paths = [self.native, self.root / "source.len", self.root / "aut.seq", self.root / "A.dat", self.root / "B.dat"]
        before = {path: path.read_bytes() for path in paths}
        self.run_synthetic()
        self.assertEqual(before, {path: path.read_bytes() for path in paths})

    def test_changed_prepared_asset_is_rejected_before_commands(self):
        self.prepare()
        (self.root / "job/assets/aut.seq").write_text("AUT\nGO\n! changed")
        application = SyntheticCodeV(self.native)
        with self.assertRaisesRegex(ValueError, "准备文件已改变"):
            ge.run_job(self.root / "job/job.json", application)
        self.assertEqual(application.commands, [])

    def test_candidate_sampling_is_legal_bounded_and_reproducible(self):
        groups = self.prepare()["groups"]
        state = {(1, 1): ("TEST", "A0"), (3, 1): ("TEST", "B0")}
        actual = list(ge.joint_candidates(groups, state, [[1, 3]], 1, random.Random(9)))
        again = list(ge.joint_candidates(groups, state, [[1, 3]], 1, random.Random(9)))
        self.assertEqual(actual, again)
        self.assertEqual(actual, [{1: ge.Glass("TEST", "A1"), 3: ge.Glass("TEST", "B1")}])

    def test_multiple_zooms_must_use_same_material_at_each_target_surface(self):
        groups = self.prepare()["groups"]
        state = {(1, 1): ("TEST", "A0"), (1, 2): ("TEST", "A1"),
                 (3, 1): ("TEST", "B0"), (3, 2): ("TEST", "B0")}
        with self.assertRaisesRegex(ge.CandidateRejected, "组态"):
            ge.check_materials(state, groups)

    def test_every_sibling_joint_trial_starts_from_the_same_parent(self):
        xml = self.cv / "glass/TEST.xml"
        xml.write_text(xml.read_text().replace("</Glasses>", "<Glass><GlassName>A2</GlassName></Glass>"
                                               "<Glass><GlassName>B2</GlassName></Glass></Glasses>"))
        with (self.root / "A.dat").open("a") as stream:
            stream.write("TEST\tA2\n")
        with (self.root / "B.dat").open("a") as stream:
            stream.write("TEST\tB2\n")
        self.config.update(passes=1, joint_trials=4)
        result, application, _ = self.run_synthetic()
        self.assertEqual(len([e for e in result["events"] if e["type"] == "joint"]), 4)
        self.assertEqual(application.joint_start_geometries[1:], [1, 1, 1, 1])

    def test_irrecoverable_aut_sentinel_is_never_a_valid_score(self):
        application = SyntheticCodeV(self.native)
        application.current.update(score=ge.IRRECOVERABLE_ERF, valid=True)
        session = ge.Session(application, self.root / "test.log")
        with self.assertRaisesRegex(ge.CandidateRejected, "可用评价"):
            session.score()


if __name__ == "__main__":
    unittest.main(verbosity=2)
