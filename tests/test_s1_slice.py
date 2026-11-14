import unittest
import shutil
import subprocess
import tempfile
from pathlib import Path

from hpx.checkers.rule import check_rule
from hpx.checkers.judge import JudgeService, ModelTarget
from hpx.checkers.media import media_probe
from hpx.dsl.loader import DslError, load_pack
from hpx.knowledge.validate import validate_basis
from hpx.model.pack import JudgeChecker, RuleChecker


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

    def test_judge_rotation_failover_and_real_low_score(self):
        class Backend:
            def __init__(self):
                self.calls = []

            def score(self, target, checker, artifact):
                self.calls.append(target.name)
                if target.name == "primary":
                    raise RuntimeError("unavailable")
                return {"score": 2, "model_version": "backup-v1"}

        backend = Backend()
        service = JudgeService([ModelTarget("primary", "a"), ModelTarget("backup", "b")], backend)
        checker = JudgeChecker(type="judge", rubric=["完整性"], pass_if="score >= 4", samples=1)
        first = service.run(checker, "artifact")
        self.assertEqual(first["status"], "fail")
        self.assertEqual(backend.calls, ["primary", "backup"])
        self.assertEqual(first["raw_scores"], [2])
        service.run(checker, "artifact")
        self.assertEqual(backend.calls, ["primary", "backup", "backup"])

    def test_judge_all_models_unavailable_is_error(self):
        class Down:
            def score(self, target, checker, artifact):
                raise RuntimeError("down")

        service = JudgeService([ModelTarget("openai", "a")], Down())
        checker = JudgeChecker(type="judge", rubric=["完整性"], pass_if="score >= 4", samples=1)
        self.assertEqual(service.run(checker, "artifact")["status"], "error")

    def test_judge_uses_median_and_rejects_ungrounded_output(self):
        class Scores:
            def __init__(self):
                self.values = iter([3, 5, 4])

            def score(self, target, checker, artifact):
                return {"score": next(self.values), "model_version": "test-v1"}

        checker = JudgeChecker(type="judge", rubric=["完整性"], pass_if="score >= 4", samples=3)
        service = JudgeService([ModelTarget("test", "a")], Scores())
        result = service.run(checker, "artifact")
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["score"], 4)
        checker.grounding = {"mode": "cite_required", "min_citations": 1}
        service = JudgeService([ModelTarget("test", "a")],
                               type("Ungrounded", (), {"score": lambda self, *args: {
                                   "score": 5, "citations": [], "uncited_claims": ["claim"]}})())
        self.assertEqual(service.run(checker, "artifact")["status"], "fail")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "requires FFmpeg")
    def test_media_probe_reads_real_audio_and_video(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.mp4"
            subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i",
                            "color=c=black:s=320x240:r=25", "-f", "lavfi", "-i",
                            "sine=frequency=1000:sample_rate=48000", "-af", "volume=7dB", "-t", "2",
                            "-c:v", "mpeg4", "-c:a", "aac", "-y", str(path)], check=True)
            data = media_probe(path)
            self.assertEqual(data["video"]["width"], 320)
            self.assertGreater(data["video"]["duration_sec"], 1)
            self.assertTrue(-15 <= data["audio"]["integrated_lufs"] <= -13)


if __name__ == "__main__":
    unittest.main()
