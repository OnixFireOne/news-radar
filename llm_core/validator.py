"""
Lightweight structural validation for parsed LLM JSON responses.

Not a full JSON Schema implementation (no new dependency) — just enough
to catch a malformed/incomplete response before it reaches business logic.
Schemas are declared as typed FieldSpec objects, not a bare dict, so both
the schema and the check are mypy-checkable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

FieldType = Literal["string", "number", "integer", "boolean", "array", "object"]

_PY_TYPES: dict[FieldType, tuple[type, ...]] = {
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


@dataclass(frozen=True)
class FieldSpec:
    """One expected field in a JSON response: name, JSON type, required-ness."""

    name: str
    type: FieldType
    required: bool = True


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    errors: list[str]


def validate_fields(data: dict[str, object], fields: list[FieldSpec]) -> ValidationResult:
    """Check that `data` has the expected fields with the expected JSON types."""
    errors: list[str] = []
    for f in fields:
        if f.name not in data:
            if f.required:
                errors.append(f"missing required field: {f.name}")
            continue

        value = data[f.name]
        if f.type == "integer" and isinstance(value, bool):
            # bool is a subclass of int in Python — reject it explicitly.
            errors.append(f"field '{f.name}' expected integer, got bool")
            continue

        expected_types = _PY_TYPES[f.type]
        if not isinstance(value, expected_types):
            errors.append(f"field '{f.name}' expected {f.type}, got {type(value).__name__}")

    return ValidationResult(ok=not errors, errors=errors)
