from cerebellum.spec.schemas import (
    minimal_instance,
    schema_problems,
    strictify,
    validation_errors,
)

SCHEMA = {
    "type": "object",
    "required": ["eligible", "risk", "tags"],
    "properties": {
        "eligible": {"type": "boolean"},
        "risk": {"type": "string", "enum": ["low", "medium", "high"]},
        "tags": {
            "type": "array",
            "items": {"type": "object", "properties": {"k": {"type": "string"}}},
        },
        "score": {"type": "number", "minimum": 0.5},
    },
}


def test_strictify_adds_additional_properties_recursively_without_mutating():
    strict = strictify(SCHEMA)
    assert strict["additionalProperties"] is False
    assert strict["properties"]["tags"]["items"]["additionalProperties"] is False
    assert "additionalProperties" not in SCHEMA


def test_strictify_keeps_explicit_value():
    assert strictify({"type": "object", "additionalProperties": True})["additionalProperties"]


def test_schema_problems():
    assert schema_problems(SCHEMA) == []
    assert schema_problems({"type": "string"}) == [
        "output_schema must have type: object at the root"
    ]
    assert schema_problems({"type": "object", "properties": {"a": {"type": 5}}})


def test_validation_errors_lists_paths():
    errors = validation_errors(strictify(SCHEMA), {"eligible": "yes", "risk": "low", "tags": []})
    assert errors == ["eligible: 'yes' is not of type 'boolean'"]
    assert validation_errors(SCHEMA, {"eligible": True, "risk": "low", "tags": []}) == []


def test_minimal_instance_satisfies_schema():
    value = minimal_instance(strictify(SCHEMA))
    assert value == {"eligible": False, "risk": "low", "tags": []}
    assert validation_errors(strictify(SCHEMA), value) == []
    assert minimal_instance({"type": "integer", "minimum": 3}) == 3
    assert minimal_instance({"type": "string", "minLength": 2}) == "xx"
    assert minimal_instance({"anyOf": [{"type": "null"}, {"type": "string"}]}) is None
