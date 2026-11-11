"""Checks a signal definition and lists every problem, so the caller can fix them all at once."""

import re

from ..filters import FilterError, matches

FIELD_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
FIELD_TYPES = {"string", "number", "boolean", "enum"}
FIELD_KEYS = {"name", "type", "description", "choices", "nullable"}
STATUSES = {"success", "error"}


def blank(value):
    return not isinstance(value, str) or not value.strip()


def problems(name, instruction, output_schema, filters, critical_guidance):
    """Every problem with a definition, as readable sentences. Empty list = valid."""
    found = []
    if blank(name):
        found.append("name must not be blank")
    if blank(instruction):
        found.append("instruction must not be blank")
    if critical_guidance is not None and blank(critical_guidance):
        found.append("critical_guidance must be left out or not blank")
    found += field_problems(output_schema)
    found += filter_problems(filters)
    return found


def field_problems(fields):
    if not fields:
        return ["output_schema needs at least one field"]
    found, seen = [], set()
    for i, f in enumerate(fields):
        where = f"output_schema[{i}]"
        if not isinstance(f, dict):
            found.append(f"{where} must be an object")
            continue
        if unknown := set(f) - FIELD_KEYS:
            found.append(f"{where} has unknown keys: {', '.join(sorted(unknown))}")
        name = f.get("name")
        if not isinstance(name, str) or not FIELD_NAME.match(name):
            found.append(f"{where}.name must start with a letter or _ and use only letters, digits and _")
        elif name in seen:
            found.append(f"{where}.name '{name}' is used twice")
        else:
            seen.add(name)
        if blank(f.get("description")):
            found.append(f"{where}.description must not be blank")
        if not isinstance(f.get("nullable", False), bool):
            found.append(f"{where}.nullable must be true or false")
        kind, choices = f.get("type"), f.get("choices")
        if kind not in FIELD_TYPES:
            found.append(f"{where}.type must be one of: {', '.join(sorted(FIELD_TYPES))}")
        elif kind == "enum":
            if (not isinstance(choices, list) or not choices or any(blank(c) for c in choices)
                    or len(set(choices)) != len(choices)):
                found.append(f"{where}.choices must be a list of different, non-blank strings")
        elif choices is not None:
            found.append(f"{where}.choices is only allowed when type is enum")
    return found


def filter_problems(filters):
    found = []
    for i, f in enumerate(filters):
        if not isinstance(f, dict):
            found.append(f"filters[{i}] must be an object")
            continue
        try:
            matches([f], {"status": "success"})
        except FilterError:
            found.append(f"filters[{i}] is not a supported filter (only status = success or error)")
            continue
        if f.get("value") not in STATUSES:
            found.append(f"filters[{i}].value must be 'success' or 'error'")
    return found


def normalize_fields(fields):
    """The stored form of a valid field list: nullable always present, choices only on enums."""
    out = []
    for f in fields:
        field = {"name": f["name"], "type": f["type"], "description": f["description"].strip(),
                 "nullable": f.get("nullable", False)}
        if f["type"] == "enum":
            field["choices"] = f["choices"]
        out.append(field)
    return out