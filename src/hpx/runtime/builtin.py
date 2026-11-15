"""Small local runtime adapters for deterministic built-in nodes and checks."""

from pathlib import Path

from hpx.checkers.media import media_probe
from hpx.checkers.record import input_get, record_field_check
from hpx.checkers.rule import check_rule
from hpx.model.pack import RuleChecker
from hpx.runtime.deepseek import DeepSeekHeadlessExecutor


def execute_step(node: dict, context: dict) -> dict:
    if node["uses"] == "tool.probe_media":
        source = context["input"].get("raw_footage")
        if not isinstance(source, str) or not source:
            raise ValueError("tool.probe_media 需要输入 raw_footage 文件路径")
        return media_probe(Path(source))
    if node["uses"].startswith("agent."):
        return DeepSeekHeadlessExecutor(
            Path("/workspace/.dsh/node_modules/.bin/dsh"),
            Path("/workspace/.hpx/dsh-runs") / context["run_id"],
            Path("/workspace/.dsh-home"),
        )(node, context)
    raise NotImplementedError(f"尚未实现节点执行器：{node['uses']}")


def execute_check(checkpoint: dict, context: dict) -> dict:
    checker = checkpoint["checker"]
    if checker["type"] != "rule":
        raise NotImplementedError(f"运行时尚未实现检查器：{checker['type']}")
    rule = RuleChecker.model_validate(checker)
    if rule.fn == "media.probe":
        values = context["evidence"].get("probe")
        if values is None:
            return {"status": "error", "reason": "缺少 probe 证据"}
    elif rule.fn == "input.get":
        values = input_get(context["input"], rule.params["key"])
    elif rule.fn == "record.field_check":
        values = record_field_check(context["input"], rule.params["path"])
    else:
        raise NotImplementedError(f"运行时尚未实现采集函数：{rule.fn}")
    try:
        assertions = check_rule(rule, values)
    except (KeyError, ValueError) as exc:
        return {"status": "error", "reason": str(exc)}
    return {"status": "pass" if all(row["ok"] for row in assertions) else "fail",
            "assertions": assertions}
