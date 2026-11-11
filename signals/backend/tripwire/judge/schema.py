"""Build the structured output schema for a signal's judge."""


def _object_schema(properties: dict) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _field_schema(field: dict) -> dict:
    field_type = field["type"]
    nullable = field.get("nullable", False)

    if field_type == "enum":
        schema = {"type": "string", "enum": list(field["choices"])}
        if nullable:
            schema = {"anyOf": [schema, {"type": "null"}]}
    else:
        schema = {"type": [field_type, "null"] if nullable else field_type}

    if field.get("description"):
        schema["description"] = field["description"]

    return schema


def build_judge_schema(output_schema: list[dict]) -> dict:
    """Compile a signal version's field definitions into the judge response schema."""
    fields = {
        field["name"]: _field_schema(field)
        for field in output_schema
    }
    finding = _object_schema({
        "severity": {"type": "string", "enum": ["info", "warning", "critical"]},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "fields": _object_schema(fields),
    })
    return _object_schema({
        "analysis": {"type": "string"},
        "findings": {"type": "array", "items": finding},
    })
