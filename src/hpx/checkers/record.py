"""JSON input and record evidence collectors."""


def _get(data: dict, path: str):
    value = data
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def input_get(data: dict, key: str) -> dict:
    if not isinstance(data, dict):
        raise ValueError("输入必须是 JSON 对象")
    return {key: _get(data, key)}


def record_field_check(data: dict, path: str) -> dict:
    if not isinstance(data, dict):
        raise ValueError("记录必须是 JSON 对象")
    value = _get(data, path)
    return {"value": value, "length": len(value) if isinstance(value, (str, list, dict)) else 0}
