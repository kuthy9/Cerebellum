import copy

import pytest
import yaml

from cerebellum.errors import SpecError
from cerebellum.spec import load_workflow, parse_workflow, resolve_params, validate_input
from cerebellum.spec.models import AiStep, HttpStep

BASE = {
    "name": "demo",
    "params": {"threshold": 100},
    "input": {
        "order_id": {"type": "string", "required": True},
        "amount": {"type": "number", "required": True},
        "tier": {"type": "string", "enum": ["gold", "silver"]},
    },
    "connectors": {
        "db": {"type": "postgres", "dsn": "sandbox"},
        "api": {"type": "rest", "base_url": "${API_URL:-http://127.0.0.1:9}"},
    },
    "steps": [
        {
            "id": "load",
            "type": "query",
            "connector": "db",
            "sql": "SELECT 1 AS one",
            "expect": "one",
        },
        {
            "id": "check",
            "type": "validate",
            "needs": ["load"],
            "rules": [{"expr": "steps.load.output.one == 1", "message": "one must be 1"}],
        },
        {
            "id": "call",
            "type": "http",
            "needs": ["check"],
            "connector": "api",
            "method": "POST",
            "path": "/x",
            "body": {"id": "{{ input.order_id }}"},
            "retry": {"max": 2, "base": "200ms"},
            "on_failure": {"fallback": "manual"},
        },
    ],
    "fallbacks": [{"id": "manual", "type": "task", "title": "Handle {{ input.order_id }}"}],
    "output": {"code": "{{ steps.call.output.status }}"},
}


def dump(data):
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def variant(mutate):
    data = copy.deepcopy(BASE)
    mutate(data)
    return dump(data)


def issues_of(text, env=None):
    with pytest.raises(SpecError) as info:
        parse_workflow(text, env=env or {})
    return [str(issue) for issue in info.value.issues]


def test_parse_valid_workflow(tmp_path):
    wf = parse_workflow(dump(BASE), base_dir=tmp_path, env={})
    assert wf.name == "demo"
    assert wf.step_ids == ["load", "check", "call"]
    assert wf.fallback_ids == ["manual"]
    assert wf.is_fallback("manual") and not wf.is_fallback("call")
    assert wf.connectors["api"].base_url == "http://127.0.0.1:9"
    call = wf.step("call")
    assert isinstance(call, HttpStep)
    assert call.retry.base == pytest.approx(0.2)
    assert call.timeout == 30.0
    assert len(wf.digest) == 16
    assert wf.base_dir == str(tmp_path)
    assert wf.source_yaml.startswith("name: demo")


def test_digest_depends_on_text_and_base_dir(tmp_path):
    a = parse_workflow(dump(BASE), base_dir=tmp_path, env={})
    b = parse_workflow(dump(BASE), base_dir=tmp_path / "other", env={})
    assert a.digest != b.digest


def test_load_workflow_from_file(tmp_path):
    path = tmp_path / "wf.yaml"
    path.write_text(dump(BASE), encoding="utf-8")
    wf = load_workflow(path, env={"API_URL": "https://payments.example"})
    assert wf.connectors["api"].base_url == "https://payments.example"
    assert wf.base_dir == str(tmp_path.resolve())


def test_missing_file_is_a_spec_error(tmp_path):
    with pytest.raises(SpecError, match="cannot read file"):
        load_workflow(tmp_path / "missing.yaml")


def test_missing_env_var_without_default():
    text = variant(lambda d: d["connectors"]["api"].update(base_url="${PAYMENTS_URL}"))
    assert issues_of(text) == [
        "connectors.api.base_url: environment variable PAYMENTS_URL is not set"
    ]


def test_invalid_yaml():
    assert issues_of("name: [unclosed")[0].startswith("<yaml>:")


def test_root_must_be_mapping():
    assert issues_of("- a\n- b\n") == ["<root>: workflow must be a YAML mapping"]


def test_structural_error_has_a_path():
    def drop_sql(d):
        del d["steps"][0]["sql"]

    issues = issues_of(variant(drop_sql))
    assert any(issue.startswith("steps[0]") and "sql" in issue for issue in issues)


def test_structural_error_paths_omit_the_union_tag():
    def mutate(d):
        del d["steps"][0]["sql"]
        d["steps"][0]["query"] = "a field named like the tag"
        d["steps"][2]["query"] = "not a mapping"
        d["steps"][2]["query_typo"] = 1
        del d["connectors"]["db"]["dsn"]
        d["fallbacks"][0]["query"] = 1

    issues = issues_of(variant(mutate))
    assert "steps[0].sql: Field required" in issues
    assert "steps[0].query: Extra inputs are not permitted" in issues
    assert "steps[2].query: Input should be a valid dictionary" in issues
    assert "steps[2].query_typo: Extra inputs are not permitted" in issues
    assert "connectors.db.dsn: Field required" in issues
    assert "fallbacks[0].query: Extra inputs are not permitted" in issues
    assert not any(".query.sql" in i or ".http." in i or ".postgres." in i for i in issues)


def test_duplicate_ids():
    text = variant(lambda d: d["fallbacks"].append({"id": "load", "type": "task", "title": "x"}))
    assert "fallbacks[1].id: duplicate step id 'load'" in issues_of(text)


def test_unknown_dependency():
    text = variant(lambda d: d["steps"][1].update(needs=["nope"]))
    assert "steps[1].needs: unknown step 'nope'" in issues_of(text)


def test_dependency_on_fallback_is_rejected():
    text = variant(lambda d: d["steps"][1].update(needs=["manual"]))
    assert "steps[1].needs: 'manual' is a fallback step and cannot be a dependency" in issues_of(
        text
    )


def test_cycle_is_rejected():
    text = variant(lambda d: d["steps"][0].update(needs=["call"]))
    assert any(issue.startswith("steps: dependency cycle") for issue in issues_of(text))


def test_unknown_fallback():
    text = variant(lambda d: d["steps"][2].update(on_failure={"fallback": "ghost"}))
    assert any("unknown fallback 'ghost'" in issue for issue in issues_of(text))


def test_fallback_restrictions():
    def mutate(d):
        d["fallbacks"][0].update(needs=["load"], when="true")

    issues = issues_of(variant(mutate))
    assert "fallbacks[0].needs: fallback steps cannot declare needs" in issues
    assert "fallbacks[0].when: fallback steps cannot declare when" in issues


def test_fallback_used_twice():
    text = variant(lambda d: d["steps"][1].update(on_failure={"fallback": "manual"}))
    assert any("already used by step 'check'" in issue for issue in issues_of(text))


def test_connector_type_mismatch():
    text = variant(lambda d: d["steps"][0].update(connector="api"))
    expected = "steps[0].connector: step type 'query' needs a 'postgres' connector, 'api' is 'rest'"
    assert expected in issues_of(text)


def test_unknown_connector():
    text = variant(lambda d: d["steps"][0].update(connector="warehouse"))
    assert "steps[0].connector: unknown connector 'warehouse'" in issues_of(text)


def test_templated_sql_is_rejected():
    text = variant(
        lambda d: d["steps"][0].update(sql="SELECT * FROM t WHERE id = '{{ input.order_id }}'")
    )
    assert any("bind parameters" in issue for issue in issues_of(text))


def test_bad_expression_reports_path():
    text = variant(lambda d: d["steps"][1]["rules"][0].update(expr="steps.load.output >"))
    issues = issues_of(text)
    assert any(issue.startswith("steps[1].rules[0].expr: invalid expression") for issue in issues)


def test_approval_restrictions():
    def mutate(d):
        d["steps"].append(
            {
                "id": "gate",
                "type": "approval",
                "needs": ["check"],
                "title": "ok?",
                "show": ["ghost"],
                "retry": {"max": 1},
            }
        )

    issues = issues_of(variant(mutate))
    assert "steps[3]: approval steps cannot declare retry or on_failure" in issues
    assert "steps[3].show: unknown step 'ghost'" in issues


def test_ai_schema_is_checked_and_strictified():
    ai_step = {
        "id": "judge",
        "type": "ai",
        "needs": ["check"],
        "prompt": "Judge {{ input.order_id }}",
        "output_schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}},
    }
    wf = parse_workflow(variant(lambda d: d["steps"].append(ai_step)), env={})
    judge = wf.step("judge")
    assert isinstance(judge, AiStep)
    assert judge.output_schema["additionalProperties"] is False

    bad = dict(ai_step, output_schema={"type": "string"})
    issues = issues_of(variant(lambda d: d["steps"].append(bad)))
    assert "steps[3].output_schema: output_schema must have type: object at the root" in issues


def test_output_templates_are_checked():
    text = variant(lambda d: d["output"].update(code="{{ broken "))
    assert any(issue.startswith("output.code: invalid template") for issue in issues_of(text))


def test_validate_input_accepts_valid_data():
    wf = parse_workflow(dump(BASE), env={})
    data = {"order_id": "A1", "amount": 12.5, "tier": "gold", "extra": True}
    assert validate_input(wf, data) == data


def test_validate_input_reports_every_problem():
    wf = parse_workflow(dump(BASE), env={})
    with pytest.raises(SpecError) as info:
        validate_input(wf, {"amount": "120", "tier": "bronze"})
    assert [str(i) for i in info.value.issues] == [
        "input.order_id: is required",
        "input.amount: expected number, got str '120'",
        "input.tier: must be one of ['gold', 'silver']",
    ]


def test_validate_input_rejects_bool_for_number_and_non_objects():
    wf = parse_workflow(dump(BASE), env={})
    with pytest.raises(SpecError, match="expected number, got bool"):
        validate_input(wf, {"order_id": "A1", "amount": True})
    with pytest.raises(SpecError, match="must be a JSON object"):
        validate_input(wf, ["not", "an", "object"])


def test_resolve_params():
    wf = parse_workflow(dump(BASE), env={})
    assert resolve_params(wf) == {"threshold": 100}
    assert resolve_params(wf, {"threshold": 900}) == {"threshold": 900}
    with pytest.raises(SpecError, match="params.unknown: is not declared"):
        resolve_params(wf, {"unknown": 1})
