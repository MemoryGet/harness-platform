import unittest
import shutil
import subprocess
import tempfile
import json
from datetime import date
from pathlib import Path

from hpx.checkers.rule import check_rule
from hpx.checkers.judge import JudgeService, ModelTarget
from hpx.checkers.media import media_probe
from hpx.checkers.schema import check_schema
from hpx.checkers.gate import check_gate
from hpx.checkers.record import input_get, record_field_check
from hpx.dsl.loader import DslError, load_pack
from hpx.dsl.expr import ExpressionError, evaluate_expression, parse_expression
from hpx.knowledge.validate import validate_basis
from hpx.model.pack import GateChecker, JudgeChecker, RuleChecker, SchemaChecker
from hpx.model import CriterionPack
from hpx.compile import compile_pack
from hpx.runtime.store import RunStore
from hpx.runtime.runner import Runner


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

    def test_schema_check_pass_fail_and_strict_mode(self):
        path = ROOT / "schemas/triage_result.json"
        valid = {"ticket_id": "T-1", "route": "技术支撑组", "priority": "P2",
                 "labels": {"category": "技术", "confidence": 0.9},
                 "sla": {"started": True, "deadline": "2026-11-14T12:00:00Z", "minutes_left": 60},
                 "reply_draft": "您好，我们已经收到您的问题并开始处理。"}
        strict = SchemaChecker(type="schema", schema_ref="schemas/triage_result.json")
        self.assertEqual(check_schema(strict, valid, path)["status"], "pass")
        missing = {key: value for key, value in valid.items() if key != "route"}
        self.assertEqual(check_schema(strict, missing, path)["status"], "fail")
        extra = {**valid, "unexpected": True}
        self.assertEqual(check_schema(strict, extra, path)["status"], "fail")
        loose = SchemaChecker(type="schema", schema_ref="schemas/triage_result.json", strict=False)
        self.assertEqual(check_schema(loose, extra, path)["status"], "pass")

    def test_input_and_record_collectors(self):
        self.assertEqual(input_get({"channel": "web"}, "channel"), {"channel": "web"})
        self.assertEqual(input_get({}, "channel"), {"channel": None})
        self.assertEqual(record_field_check({"ticket": {"body": "需要退款处理"}}, "ticket.body"),
                         {"value": "需要退款处理", "length": 6})
        self.assertEqual(record_field_check({}, "ticket.body"), {"value": None, "length": 0})
        pack = load_pack(ROOT / "examples/support-ticket-triage.yaml")
        body = next(item for item in pack.slots.input if item.id == "body_sufficient")
        self.assertTrue(check_rule(body.check, record_field_check(
            {"ticket": {"body": "客户购买的设备无法启动，希望尽快安排技术支持"}}, "ticket.body"))[0]["ok"])
        self.assertFalse(check_rule(body.check, record_field_check({}, "ticket.body"))[0]["ok"])
        channel = next(item for item in pack.slots.input if item.id == "channel_supported")
        self.assertTrue(check_rule(channel.check, input_get({"channel": "web"}, "channel"))[0]["ok"])
        self.assertFalse(check_rule(channel.check, input_get({"channel": "fax"}, "channel"))[0]["ok"])

    def test_veto_cannot_use_judge(self):
        data = load_pack(ROOT / "examples/video-shortform.yaml").model_dump(by_alias=True)
        data["slots"]["veto"][0]["check"] = {
            "type": "judge", "rubric": ["合规"], "pass_if": "score >= 4"}
        with self.assertRaisesRegex(ValueError, "judge 不允许用于 veto"):
            CriterionPack.model_validate(data)

    def test_follow_latest_cannot_be_basis(self):
        pack = load_pack(ROOT / "examples/legal-contract-review.yaml").model_copy(deep=True)
        pack.knowledge["sources"][0]["pin_policy"] = "follow_latest"
        with self.assertRaisesRegex(DslError, "V-KB-04"):
            validate_basis(pack, ROOT / "knowledge")

    def test_gate_waits_for_human_decision(self):
        result = check_gate(GateChecker(type="gate", approver_role="法务负责人",
                                        must_see=["evidence.contract"], prompt="确认？"))
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["approver_role"], "法务负责人")
        self.assertEqual(result["must_see"], ["evidence.contract"])

    def test_knowledge_basis_must_cover_publish_date(self):
        pack = load_pack(ROOT / "examples/legal-contract-review.yaml").model_copy(deep=True)
        for slot in ("input", "process", "quality", "veto"):
            for criterion in getattr(pack.slots, slot):
                criterion.basis = []
        pack.slots.quality[0].basis = [{"source": "kb-cn-contract", "item": "sop-liability-cap-3-1",
                                        "path": "内部审查指引/3.1 责任限额（旧版）"}]
        with self.assertRaisesRegex(DslError, "V-KB-11"):
            validate_basis(pack, ROOT / "knowledge", date(2026, 11, 15))
        validate_basis(pack, ROOT / "knowledge", date(2022, 1, 1))

    def test_applies_when_expression_and_rejected_calls(self):
        expression = "evidence.customer.tier == 'vip' and evidence.risk.sentiment == 'negative'"
        context = {"evidence": {"customer": {"tier": "vip"}, "risk": {"sentiment": "negative"}}}
        self.assertTrue(evaluate_expression(expression, context))
        self.assertFalse(evaluate_expression(expression, {"evidence": {"customer": {"tier": "normal"}}}))
        self.assertTrue(evaluate_expression("input.platform in ['douyin', 'shipinhao']",
                                            {"input": {"platform": "douyin"}}))
        self.assertTrue(evaluate_expression("evidence.risk.legal_threat == true",
                                            {"evidence": {"risk": {"legal_threat": True}}}))
        for unsafe in ("__import__('os').system('echo bad')", "input.items()", "input.x[0]", "input.x + 1"):
            with self.subTest(unsafe=unsafe), self.assertRaises(ExpressionError):
                parse_expression(unsafe)

    def test_compiler_is_deterministic_and_topological(self):
        pack = load_pack(ROOT / "examples/video-shortform.yaml")
        first = compile_pack(pack, ROOT / "knowledge")
        second = compile_pack(pack, ROOT / "knowledge")
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))
        order = {node["id"]: index for index, node in enumerate(first["graph"]["nodes"])}
        for parent, child in first["graph"]["edges"]:
            self.assertLess(order[parent], order[child])
        indices = [order[checkpoint["after"]] for checkpoint in first["checkpoints"]]
        self.assertEqual(indices, sorted(indices))

    def test_compiler_rejects_missing_and_cyclic_steps(self):
        pack = load_pack(ROOT / "examples/video-shortform.yaml").model_copy(deep=True)
        pack.workflow.steps[0].depends_on = ["missing"]
        with self.assertRaisesRegex(DslError, "不存在的步骤"):
            compile_pack(pack, ROOT / "knowledge")
        pack.workflow.steps[0].depends_on = [pack.workflow.steps[-1].id]
        with self.assertRaisesRegex(DslError, "循环依赖"):
            compile_pack(pack, ROOT / "knowledge")

    def test_run_store_persists_tenant_scoped_events(self):
        ir = compile_pack(load_pack(ROOT / "examples/video-shortform.yaml"), ROOT / "knowledge")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runs.sqlite3"
            run_id = RunStore(path).create_run("tenant-a", ir, {"platform": "douyin"}, "test")
            store = RunStore(path)
            self.assertEqual(store.get_run("tenant-a", run_id)["status"], "pending")
            with self.assertRaises(KeyError):
                store.get_run("tenant-b", run_id)
            self.assertEqual(store.append_event("tenant-a", run_id, "step_done", {"step": "ingest"}), 2)
            self.assertEqual([event["seq"] for event in store.events("tenant-a", run_id)], [1, 2])
            self.assertTrue(store.decide_gate("tenant-a", run_id, "release_signoff", "lead", "approve", "ok"))
            self.assertFalse(store.decide_gate("tenant-a", run_id, "release_signoff", "lead", "approve", "ok"))
            with self.assertRaises(ValueError):
                store.decide_gate("tenant-a", run_id, "release_signoff", "lead", "reject", "different")

    def test_runner_resumes_gate_without_reexecuting_steps(self):
        ir = compile_pack(load_pack(ROOT / "examples/video-shortform.yaml"), ROOT / "knowledge")
        with tempfile.TemporaryDirectory() as directory:
            store = RunStore(Path(directory) / "runs.sqlite3")
            calls = []

            def step(node, context):
                calls.append(node["id"])
                return {"ok": True}

            runner = Runner(store, step, lambda check, context: {"status": "pass"})
            run_id = runner.start("tenant-a", ir, {}, triggered_by="test")
            self.assertEqual(store.get_run("tenant-a", run_id)["status"], "awaiting_gate")
            self.assertEqual(runner.advance("tenant-a", run_id), "awaiting_gate")
            self.assertEqual(sum(event["event_type"] == "gate_pending" for event in store.events("tenant-a", run_id)), 1)
            before = list(calls)
            self.assertEqual(runner.resume("tenant-a", run_id, "release_signoff",
                                           "approve", "lead", "looks good"), "done")
            self.assertEqual(calls, before)
            self.assertEqual(runner.advance("tenant-a", run_id), "done")
            self.assertEqual(calls, before)
            self.assertEqual(sum(event["event_type"] == "step_done" for event in store.events("tenant-a", run_id)),
                             len(ir["graph"]["nodes"]))

    def test_runner_veto_aborts_before_gate(self):
        ir = compile_pack(load_pack(ROOT / "examples/video-shortform.yaml"), ROOT / "knowledge")
        with tempfile.TemporaryDirectory() as directory:
            store = RunStore(Path(directory) / "runs.sqlite3")
            runner = Runner(store, lambda node, context: {},
                            lambda check, context: {"status": "fail" if check["criterion_id"] == "ai_disclosure"
                                                    else "pass"})
            run_id = runner.start("tenant-a", ir, {}, triggered_by="test")
            self.assertEqual(store.get_run("tenant-a", run_id)["status"], "aborted")
            self.assertFalse(any(event["event_type"] == "gate_pending"
                                 for event in store.events("tenant-a", run_id)))


if __name__ == "__main__":
    unittest.main()
