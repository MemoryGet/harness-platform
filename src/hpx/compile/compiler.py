"""Deterministic CriterionPack to JSON IR compiler."""

import hashlib
import json
from collections import defaultdict
from pathlib import Path

import yaml

from hpx.dsl.loader import DslError
from hpx.knowledge.validate import validate_basis
from hpx.model import CriterionPack
from hpx.model.pack import GateChecker


SEVERITY_ORDER = {"veto": 0, "block": 1, "warn": 2, "info": 3}


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _topological_steps(pack: CriterionPack):
    by_id = {step.id: step for step in pack.workflow.steps}
    successors = defaultdict(list)
    remaining = {}
    for step in pack.workflow.steps:
        remaining[step.id] = len(step.depends_on)
        for parent in step.depends_on:
            if parent not in by_id:
                raise DslError(step.id, f"依赖了不存在的步骤 {parent}")
            successors[parent].append(step.id)
    if any(len(step.depends_on) != len(set(step.depends_on)) for step in pack.workflow.steps):
        raise DslError(pack.metadata.id, "步骤依赖中有重复 id")
    ready = sorted(step_id for step_id, count in remaining.items() if count == 0)
    result = []
    while ready:
        current = ready.pop(0)
        result.append(by_id[current])
        for child in successors[current]:
            remaining[child] -= 1
            if remaining[child] == 0:
                ready.append(child)
                ready.sort()
    if len(result) != len(by_id):
        raise DslError(pack.metadata.id, "workflow.steps 存在循环依赖")
    return result


def _knowledge_ir(pack: CriterionPack, directory: Path) -> dict:
    declared = {entry["id"]: entry for entry in (pack.knowledge or {}).get("sources", [])}
    local = {}
    for path in directory.glob("*.yaml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("source"), dict):
            local[data["source"]["id"]] = data
    pins = []
    for source_id in sorted(declared):
        snapshot = local.get(source_id, {}).get("snapshot", {})
        pins.append({"source": source_id, "snapshot": snapshot.get("id")})
    bindings = []
    for slot in ("input", "process", "quality", "veto"):
        for criterion in getattr(pack.slots, slot):
            for basis in criterion.basis:
                quote = basis.get("quote") or ""
                bindings.append({"criterion_id": criterion.id, "slot": slot, "source": basis["source"],
                                 "item": basis.get("item"), "path": basis.get("path"),
                                 "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
                                 "required": basis.get("required", True)})
    bindings.sort(key=lambda item: (item["criterion_id"], item["source"], item["item"] or ""))
    return {"pins": pins, "bindings": bindings}


def compile_pack(pack: CriterionPack, knowledge_directory: Path) -> dict:
    if pack.artifact.kind == "composite":
        raise DslError(pack.metadata.id, "复合任务编译尚未实现；不能生成不完整的 IR")
    validate_basis(pack, knowledge_directory)
    steps = _topological_steps(pack)
    order = {step.id: index for index, step in enumerate(steps)}
    checkpoints = []
    gates = []
    for slot in ("input", "process", "quality", "veto"):
        for criterion in getattr(pack.slots, slot):
            checkpoints.append({"criterion_id": criterion.id, "slot": slot, "after": criterion.after,
                                "severity": criterion.severity.value, "applies_when": criterion.applies_when,
                                "checker": criterion.check.model_dump(mode="json", by_alias=True, exclude_none=False)})
            if isinstance(criterion.check, GateChecker):
                gates.append({"criterion_id": criterion.id, "after": criterion.after,
                              "approver_role": criterion.check.approver_role})
    checkpoints.sort(key=lambda item: (order[item["after"]], SEVERITY_ORDER[item["severity"]], item["criterion_id"]))
    gates.sort(key=lambda item: (order[item["after"]], item["criterion_id"]))
    source = pack.model_dump(mode="json", by_alias=True, exclude_none=False)
    checksum = "sha256:" + hashlib.sha256(_canonical(source).encode()).hexdigest()
    return {"ir_version": "0.1", "pack": {"id": pack.metadata.id, "version": pack.metadata.version,
                                            "checksum": checksum},
            "artifact": pack.artifact.model_dump(mode="json", exclude_none=True),
            "graph": {"nodes": [step.model_dump(mode="json", exclude_none=True) for step in steps],
                      "edges": sorted([[parent, step.id] for step in steps for parent in step.depends_on])},
            "checkpoints": checkpoints, "knowledge": _knowledge_ir(pack, knowledge_directory),
            "gates": gates, "eval": pack.eval}
