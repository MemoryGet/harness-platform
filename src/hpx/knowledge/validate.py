from pathlib import Path

import yaml

from hpx.dsl.loader import DslError
from hpx.model import CriterionPack


def _normalized(value: str) -> str:
    return "".join(value.split())


def validate_basis(pack: CriterionPack, directory: Path) -> None:
    """Check declared local sources and basis links (V-KB-01..03).

    Publication-date checks require an explicit publish date and belong to the
    publication flow; validation must not guess one from the current clock.
    """
    declarations = {source["id"]: source for source in (pack.knowledge or {}).get("sources", [])}
    sources = {}
    for path in directory.glob("*.yaml"):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise DslError(str(path), f"知识源读取失败：{exc}") from exc
        if isinstance(data, dict) and isinstance(data.get("source"), dict):
            source_id = data["source"].get("id")
            if source_id in sources:
                raise DslError(str(path), f"知识源 id 重复：{source_id}")
            sources[source_id] = data
    for slot in ("input", "process", "quality", "veto"):
        for criterion in getattr(pack.slots, slot):
            for basis in criterion.basis:
                source_id = basis.get("source")
                if source_id not in declarations:
                    raise DslError(criterion.id, f"V-KB-01: 未声明知识源 {source_id}")
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
