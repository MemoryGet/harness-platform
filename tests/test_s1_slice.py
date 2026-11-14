import unittest
from pathlib import Path

from hpx.checkers.rule import check_rule
from hpx.dsl.loader import DslError, load_pack
from hpx.knowledge.validate import validate_basis
from hpx.model.pack import RuleChecker


ROOT = Path(__file__).resolve().parents[1]


class S1SliceTests(unittest.TestCase):
    def test_examples_validate(self):
        for path in sorted((ROOT / "examples").rglob("*.yaml")):
            with self.subTest(path=path):
                self.assertTrue(load_pack(path).metadata.id)

    def test_loudness_pass_fail_and_missing_evidence(self):
        checker = RuleChecker.model_validate(
            {"type": "rule", "fn": "media.probe", "assert": [
                {"field": "audio.integrated_lufs", "op": "within", "value": [-15, -13]}]})
        self.assertTrue(check_rule(checker, {"audio": {"integrated_lufs": -13.6}})[0]["ok"])
        self.assertFalse(check_rule(checker, {"audio": {"integrated_lufs": -11}})[0]["ok"])
        with self.assertRaises(KeyError):
            check_rule(checker, {"audio": {}})

    def test_bad_yaml_reports_location(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as directory:
            path = Path(directory) / "bad.yaml"
            path.write_text("kind: CriterionPack\n", encoding="utf-8")
            with self.assertRaises(DslError) as caught:
                load_pack(path)
            self.assertIn("dsl_version", str(caught.exception))

    def test_knowledge_basis_and_bad_quote(self):
        pack = load_pack(ROOT / "examples/legal-contract-review.yaml")
        validate_basis(pack, ROOT / "knowledge")
        broken = pack.model_copy(deep=True)
        broken.slots.quality[0].basis = [{"source": "kb-cn-contract", "item": "civil-code-577",
                                          "quote": "不存在的原文"}]
        with self.assertRaisesRegex(DslError, "V-KB-03"):
            validate_basis(broken, ROOT / "knowledge")


if __name__ == "__main__":
    unittest.main()
