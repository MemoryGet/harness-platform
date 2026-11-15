from pathlib import Path
from datetime import date

import yaml

from hpx.dsl.loader import DslError
from hpx.model import CriterionPack


def _normalized(value: str) -> str:
    return "".join(value.split())


def _date(value, fallback: date) -> date:
    if value is None:
        return fallback
    return date.fromisoformat(value) if isinstance(value, str) else value


def validate_source(source: dict, path: Path) -> None:
    items = source.get("items", [])
    by_id = {item["id"]: item for item in items}
    if len(by_id) != len(items):
        raise DslError(str(path), "知识条目 id 重复")
    for item in items:
        start = _date(item.get("effective_from"), date.min)
        end = _date(item.get("effective_to"), date.max)
        if start > end:
            raise DslError(str(path), f"V-KB-09: {item['id']} 的生效区间倒置")
        seen = {item["id"]}
        current = item
        while current.get("supersedes"):
            previous_id = current["supersedes"]
            if previous_id not in by_id or previous_id in seen:
                raise DslError(str(path), f"V-KB-09: {item['id']} 的替代关系缺失或成环")
            previous = by_id[previous_id]
            previous_end = _date(previous.get("effective_to"), date.max)
            current_start = _date(current.get("effective_from"), date.min)
            if previous_end >= current_start:
                raise DslError(str(path), f"V-KB-09: {current['id']} 与前版生效时间重叠")
            seen.add(previous_id)
            current = previous
    by_path = {}
    for item in items:
        by_path.setdefault(item.get("path"), []).append(item)
    for item_path, group in by_path.items():
        ordered = sorted(group, key=lambda item: _date(item.get("effective_from"), date.min))
        for previous, current in zip(ordered, ordered[1:]):
            if _date(previous.get("effective_to"), date.max) >= _date(current.get("effective_from"), date.min):
                raise DslError(str(path), f"V-KB-10: 路径 {item_path} 的生效区间重叠")


def validate_basis(pack: CriterionPack, directory: Path, publish_date: date | None = None) -> None:
    """Check declared local sources and basis links (V-KB-01..03).

    V-KB-11 runs only with an explicit publication date; the current clock is
    never used as a substitute.
    """
    declarations = {source["id"]: source for source in (pack.knowledge or {}).get("sources", [])}
    sources = {}
    for path in directory.glob("*.yaml"):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise DslError(str(path), f"知识源读取失败：{exc}") from exc
        if isinstance(data, dict) and isinstance(data.get("source"), dict):
            validate_source(data, path)
            source_id = data["source"].get("id")
            if source_id in sources:
                raise DslError(str(path), f"知识源 id 重复：{source_id}")
            sources[source_id] = data
    for slot in ("input", "process", "quality", "veto"):
        for criterion in getattr(pack.slots, slot):
            grounding = getattr(criterion.check, "grounding", None) or {}
            for source_id in grounding.get("sources", []):
                if source_id not in declarations:
                    raise DslError(criterion.id, f"V-KB-01: grounding 引用了未声明知识源 {source_id}")
            if grounding.get("mode") == "cite_required" and grounding.get("min_citations", 0) < 1:
                raise DslError(criterion.id, "V-KB-07: cite_required 至少需要一条引用")
            for basis in criterion.basis:
                source_id = basis.get("source")
                if source_id not in declarations:
                    raise DslError(criterion.id, f"V-KB-01: 未声明知识源 {source_id}")
                if declarations[source_id].get("pin_policy") == "follow_latest":
                    raise DslError(criterion.id, f"V-KB-04: follow_latest 知识源不能作为判定依据：{source_id}")
                if source_id not in sources:
                    raise DslError(criterion.id, f"V-KB-02: 找不到本地知识源 {source_id}")
                candidates = [item for item in sources[source_id].get("items", [])
                              if (not basis.get("item") or item.get("id") == basis["item"])
                              and (not basis.get("path") or item.get("path") == basis["path"])]
                if len(candidates) != 1:
                    raise DslError(criterion.id, f"V-KB-02: 依据必须唯一匹配：{source_id}/{basis.get('item')}")
                quote = basis.get("quote")
                if quote and _normalized(quote) not in _normalized(candidates[0].get("body", "")):
                    raise DslError(criterion.id, f"V-KB-03: 引文与知识源原文不符：{basis.get('item')}")
                if publish_date is not None:
                    item = candidates[0]
                    start = item.get("effective_from")
                    end = item.get("effective_to")
                    start = date.fromisoformat(start) if isinstance(start, str) else start
                    end = date.fromisoformat(end) if isinstance(end, str) else end
                    if (start and publish_date < start) or (end and publish_date > end):
                        raise DslError(criterion.id, f"V-KB-11: 依据 {item['id']} 在 {publish_date} 未生效")
