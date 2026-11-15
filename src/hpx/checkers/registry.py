"""Named rule evidence collectors; collectors never decide pass or fail."""

import json
from collections.abc import Callable
from pathlib import Path

from hpx.checkers.media import media_probe
from hpx.checkers.record import input_get, record_field_check


RuleCollector = Callable[[Path, dict], dict]
_RULE_FNS: dict[str, RuleCollector] = {}


def rule_fn(name: str):
    def register(function: RuleCollector) -> RuleCollector:
        if name in _RULE_FNS:
            raise ValueError(f"采集函数重复注册：{name}")
        _RULE_FNS[name] = function
        return function
    return register


def get_rule_fn(name: str) -> RuleCollector:
    try:
        return _RULE_FNS[name]
    except KeyError as exc:
        raise ValueError(f"找不到采集函数「{name}」；已注册：{sorted(_RULE_FNS)}") from exc


@rule_fn("media.probe")
def _media(artifact: Path, params: dict) -> dict:
    return media_probe(artifact)


@rule_fn("input.get")
def _input(artifact: Path, params: dict) -> dict:
    data = json.loads(artifact.read_text(encoding="utf-8"))
    return input_get(data, params["key"])


@rule_fn("record.field_check")
def _record(artifact: Path, params: dict) -> dict:
    data = json.loads(artifact.read_text(encoding="utf-8"))
    return record_field_check(data, params["path"])
