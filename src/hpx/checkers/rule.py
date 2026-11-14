import operator
import re

from hpx.model.pack import Assertion, RuleChecker


OPS = {"==": operator.eq, "!=": operator.ne, "<": operator.lt, "<=": operator.le,
       ">": operator.gt, ">=": operator.ge}


def field_value(data: dict, path: str):
    value = data
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise KeyError(path)
        value = value[part]
    return value


def evaluate(assertion: Assertion, data: dict) -> dict:
    actual = field_value(data, assertion.field)
    expected = assertion.value
    op = assertion.op
    try:
        if actual is None and expected is not None and op in ("<", "<=", ">", ">=", "within", "min_count"):
            ok = False
        elif op in OPS:
            ok = OPS[op](actual, expected)
        elif op == "within":
            ok = len(expected) == 2 and expected[0] <= actual <= expected[1]
        elif op == "in":
            ok = actual in expected
        elif op == "not_in":
            ok = actual not in expected
        elif op == "contains":
            ok = expected in actual
        elif op == "matches":
            ok = re.search(expected, actual) is not None
        else:
            ok = len(actual) >= expected
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{assertion.field} 无法执行 {op} 比较：{exc}") from exc
    return {"field": assertion.field, "actual": actual, "expected": expected, "ok": bool(ok),
            "message": assertion.message}


def check_rule(checker: RuleChecker, evidence: dict) -> list[dict]:
    return [evaluate(assertion, evidence) for assertion in checker.assertions]
