from pathlib import Path

import yaml
from pydantic import ValidationError

from hpx.model import CriterionPack


class DslError(Exception):
    def __init__(self, path: str, message: str):
        self.path = path
        super().__init__(f"{path}: {message}")


def load_pack(path: Path) -> CriterionPack:
    try:
        with path.open(encoding="utf-8") as stream:
            data = yaml.safe_load(stream)
    except (OSError, yaml.YAMLError) as exc:
        raise DslError(str(path), f"无法读取 YAML：{exc}") from exc
    if not isinstance(data, dict):
        raise DslError(str(path), "顶层必须是对象")
    try:
        return CriterionPack.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(map(str, first["loc"]))
        raise DslError(f"{path}:{location}", first["msg"]) from exc
