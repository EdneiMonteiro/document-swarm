"""Validate data against a bounded, offline subset of JSON Schema 2020-12.

Only the keywords listed in ``KEYWORDS`` are honoured.  An unknown keyword is an
error rather than a silently ignored constraint, so a schema cannot promise a
restriction that this validator does not actually apply.  References resolve
locally; nothing is fetched over the network.
"""

from __future__ import annotations

import json
import re
from typing import Any

from scripts.checks.common import InputError

KEYWORDS = frozenset({
    "$schema", "$id", "$comment", "$defs", "$ref", "title", "description",
    "type", "enum", "const", "properties", "required", "additionalProperties",
    "propertyNames", "dependentRequired", "minProperties", "maxProperties",
    "items", "minItems", "maxItems", "uniqueItems",
    "minLength", "maxLength", "pattern", "minimum", "maximum",
    "allOf", "oneOf",
})
MAX_DEPTH = 40


def _is(value: Any, name: str) -> bool:
    if name == "integer":
        return type(value) is int
    if name == "number":
        return type(value) in (int, float)
    if name == "boolean":
        return type(value) is bool
    if name == "string":
        return type(value) is str
    if name == "object":
        return type(value) is dict
    if name == "array":
        return type(value) is list
    if name == "null":
        return value is None
    raise InputError(f"unsupported schema type: {name}")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _resolve(reference: Any, root: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(reference, str) or not reference.startswith("#/$defs/"):
        raise InputError(f"only local #/$defs references are supported: {reference!r}")
    name = reference[len("#/$defs/"):]
    target = root.get("$defs", {}).get(name)
    if not isinstance(target, dict):
        raise InputError(f"unresolved schema reference: {reference}")
    return target


def validate(data: Any, schema: dict[str, Any], *, root: dict[str, Any] | None = None,
             path: str = "$", depth: int = 0) -> None:
    """Raise :class:`InputError` describing the first constraint *data* violates."""
    if depth > MAX_DEPTH:
        raise InputError(f"{path}: schema nesting exceeds the supported depth")
    if not isinstance(schema, dict):
        raise InputError(f"{path}: schema must be an object")
    root = schema if root is None else root
    unknown = set(schema) - KEYWORDS
    if unknown:
        raise InputError(f"{path}: unsupported schema keywords {sorted(unknown)}")
    if "$ref" in schema:
        validate(data, _resolve(schema["$ref"], root), root=root, path=path, depth=depth + 1)

    declared = schema.get("type")
    if declared is not None:
        names = declared if isinstance(declared, list) else [declared]
        if not any(_is(data, name) for name in names):
            raise InputError(f"{path}: expected {' or '.join(names)}")
    if "const" in schema and _canonical(data) != _canonical(schema["const"]):
        raise InputError(f"{path}: must equal {schema['const']!r}")
    if "enum" in schema:
        allowed = {_canonical(item) for item in schema["enum"]}
        if _canonical(data) not in allowed:
            raise InputError(f"{path}: {data!r} is not one of {schema['enum']}")
    for keyword in ("allOf", "oneOf"):
        branches = schema.get(keyword)
        if branches is None:
            continue
        if not isinstance(branches, list) or not branches:
            raise InputError(f"{path}: {keyword} requires a non-empty list")
        failures = []
        for index, branch in enumerate(branches):
            try:
                validate(data, branch, root=root, path=path, depth=depth + 1)
            except InputError as exc:
                failures.append(f"[{index}] {exc}")
        if keyword == "allOf" and failures:
            raise InputError(failures[0])
        if keyword == "oneOf" and len(failures) != len(branches) - 1:
            detail = "; ".join(failures) if failures else "matched more than one branch"
            raise InputError(f"{path}: must match exactly one supported variant: {detail}")

    if type(data) is str:
        if "minLength" in schema and len(data) < schema["minLength"]:
            raise InputError(f"{path}: shorter than {schema['minLength']} characters")
        if "maxLength" in schema and len(data) > schema["maxLength"]:
            raise InputError(f"{path}: longer than {schema['maxLength']} characters")
        if "pattern" in schema and not re.search(schema["pattern"], data):
            raise InputError(f"{path}: does not match {schema['pattern']}")
    if type(data) in (int, float) and type(data) is not bool:
        if "minimum" in schema and data < schema["minimum"]:
            raise InputError(f"{path}: below the minimum {schema['minimum']}")
        if "maximum" in schema and data > schema["maximum"]:
            raise InputError(f"{path}: above the maximum {schema['maximum']}")
    if type(data) is list:
        if "minItems" in schema and len(data) < schema["minItems"]:
            raise InputError(f"{path}: requires at least {schema['minItems']} items")
        if "maxItems" in schema and len(data) > schema["maxItems"]:
            raise InputError(f"{path}: allows at most {schema['maxItems']} items")
        if schema.get("uniqueItems") and len({_canonical(item) for item in data}) != len(data):
            raise InputError(f"{path}: items must be unique")
        if "items" in schema:
            for index, item in enumerate(data):
                validate(item, schema["items"], root=root, path=f"{path}[{index}]", depth=depth + 1)
    if type(data) is dict:
        for key in schema.get("required", []):
            if key not in data:
                raise InputError(f"{path}: missing required property {key!r}")
        if "minProperties" in schema and len(data) < schema["minProperties"]:
            raise InputError(f"{path}: requires at least {schema['minProperties']} properties")
        if "maxProperties" in schema and len(data) > schema["maxProperties"]:
            raise InputError(f"{path}: allows at most {schema['maxProperties']} properties")
        properties = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for key, item in data.items():
            if "propertyNames" in schema:
                validate(key, schema["propertyNames"], root=root, path=f"{path}.{key}", depth=depth + 1)
            if key in properties:
                validate(item, properties[key], root=root, path=f"{path}.{key}", depth=depth + 1)
            elif extra is False:
                raise InputError(f"{path}: unsupported property {key!r}")
            elif isinstance(extra, dict):
                validate(item, extra, root=root, path=f"{path}.{key}", depth=depth + 1)
        for key, dependents in schema.get("dependentRequired", {}).items():
            if key in data:
                for required in dependents:
                    if required not in data:
                        raise InputError(f"{path}: {key!r} also requires {required!r}")
