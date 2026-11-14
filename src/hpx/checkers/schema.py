"""JSON Schema artifact checks."""

import copy
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker, SchemaError

from hpx.model.pack import SchemaChecker


def _allow_extra_properties(node):
    if isinstance(node, dict):
        node.pop("additionalProperties", None)
        for value in node.values():
            _allow_extra_properties(value)
    elif isinstance(node, list):
        for value in node:
            _allow_extra_properties(value)


def check_schema(checker: SchemaChecker, artifact: object, schema_path: Path) -> dict:
    """Return pass/fail for the artifact; invalid schemas raise an error."""
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    if not checker.strict:
        schema = copy.deepcopy(schema)
        _allow_extra_properties(schema)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(artifact), key=lambda item: (list(map(str, item.path)), item.message))
    return {"status": "fail" if errors else "pass", "errors": [
        {"path": "/" + "/".join(map(str, error.path)), "message": error.message}
        for error in errors]}
