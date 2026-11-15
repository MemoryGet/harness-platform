"""Resolve composite child packs and enforce explicit input contracts."""

from hpx.dsl.loader import DslError
from hpx.model import CriterionPack


def _version(value: str) -> tuple[int, int, int]:
    try:
        parts = [int(part) for part in value.split(".")]
    except ValueError as exc:
        raise DslError(value, "子包版本必须是数字形式的语义版本") from exc
    if len(parts) not in (2, 3):
        raise DslError(value, "子包版本必须是 major.minor 或 major.minor.patch")
    return tuple((parts + [0])[:3])


def _matches(actual: str, constraint: str) -> bool:
    version = _version(actual)
    if constraint.startswith("~"):
        lower = _version(constraint[1:])
        return version[0:2] == lower[0:2] and version >= lower
    if constraint.startswith("^"):
        lower = _version(constraint[1:])
        return version[0] == lower[0] and version >= lower
    return version == _version(constraint)


def resolve_subtasks(parent: CriterionPack, catalog: dict[str, list[CriterionPack]]) -> list[dict]:
    resolved = []
    ids = [subtask.get("id") for subtask in parent.subtasks]
    if len(ids) != len(set(ids)) or any(not item for item in ids):
        raise DslError(parent.metadata.id, "子任务 id 必须唯一且非空")
    parts = {part["slot"]: part for part in parent.artifact.parts}
    producers = {subtask.get("produces") for subtask in parent.subtasks}
    for subtask in parent.subtasks:
        slot = subtask.get("produces")
        if slot not in parts:
            raise DslError(subtask["id"], f"V-COMP-01: 产物槽位不存在：{slot}")
        if subtask.get("on_fail") in ("warn", "continue") and parts[slot].get("required", True):
            raise DslError(subtask["id"], f"V-COMP-02: 可继续失败的子任务不能产出必需槽位 {slot}")
    missing_parts = sorted(slot for slot, part in parts.items() if part.get("required", True) and slot not in producers)
    if missing_parts:
        raise DslError(parent.metadata.id, f"V-COMP-01: 必需产物缺少子任务：{missing_parts}")
    for subtask in parent.subtasks:
        reference = subtask.get("pack", "")
        pack_id, separator, constraint = reference.partition("@")
        candidates = [pack for pack in catalog.get(pack_id, [])
                      if not separator or _matches(pack.metadata.version, constraint)]
        if not candidates:
            raise DslError(subtask["id"], f"PACK_NOT_FOUND: 找不到满足版本约束的子包 {reference}")
        child = max(candidates, key=lambda item: _version(item.metadata.version))
        if child.subtasks:
            raise DslError(subtask["id"], "V-COMP-04: 子包不能再包含子任务")
        required = sorted({key for criterion in child.slots.input for key in criterion.requires_inputs})
        supplied = subtask.get("inputs", {})
        missing = sorted(set(required) - set(supplied))
        if missing:
            raise DslError(subtask["id"], f"V-COMP-03: 子包 {pack_id} 缺少输入映射 {missing}")
        resolved.append({**subtask, "pack_id": pack_id, "pack_version": child.metadata.version,
                         "required_inputs": required})
    by_id = {subtask["id"]: subtask for subtask in resolved}
    dependencies = {item["id"]: set(item.get("depends_on", [])) for item in resolved}
    for item_id, needed in dependencies.items():
        unknown = needed - set(by_id)
        if unknown:
            raise DslError(item_id, f"V-COMP-06: 子任务依赖不存在：{sorted(unknown)}")
    ordered = []
    while dependencies:
        ready = sorted(item_id for item_id, needed in dependencies.items() if not needed)
        if not ready:
            raise DslError(parent.metadata.id, "V-COMP-06: 子任务依赖成环")
        for item_id in ready:
            ordered.append(by_id[item_id])
            del dependencies[item_id]
        for needed in dependencies.values():
            needed.difference_update(ready)
    return ordered
