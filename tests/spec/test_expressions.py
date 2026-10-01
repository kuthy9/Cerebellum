import pytest

from cerebellum.errors import TemplateError
from cerebellum.spec.expressions import (
    check_expression,
    check_template,
    eval_condition,
    eval_value,
    is_template,
    render,
)

CTX = {
    "input": {"order_id": "A1001", "amount": 120, "reason": "Arrived BROKEN"},
    "params": {"approval_threshold": 500},
    "steps": {
        "fetch": {
            "output": {"id": "A1001", "amount": 120.0, "items": ["x", "y"]},
            "status": "succeeded",
        },
        "skipped": {"output": None, "status": "skipped"},
    },
}


def test_is_template():
    assert is_template("{{ a }}") and is_template("{% if a %}x{% endif %}")
    assert not is_template("plain text")


def test_single_expression_keeps_native_type():
    assert render("{{ input.amount }}", CTX) == 120
    assert render("{{ steps.fetch.output }}", CTX) == {
        "id": "A1001",
        "amount": 120.0,
        "items": ["x", "y"],
    }
    assert render("  {{ input.amount > 100 }} ", CTX) is True


def test_mixed_string_renders_text_and_json_for_containers():
    assert render("Order {{ input.order_id }}", CTX) == "Order A1001"
    assert render("data={{ steps.fetch.output.items }}", CTX) == 'data=["x", "y"]'
    assert render("{{ input.order_id }}-{{ input.amount }}", CTX) == "A1001-120"


def test_render_recurses_into_dicts_and_lists():
    value = {"a": ["{{ input.amount }}", "x"], "b": 1, "c": None}
    assert render(value, CTX) == {"a": [120, "x"], "b": 1, "c": None}


def test_mapping_keys_win_over_dict_methods():
    assert render("{{ steps.fetch.output.items }}", CTX) == ["x", "y"]


def test_strict_render_of_missing_value_names_the_template():
    with pytest.raises(TemplateError, match=r"steps\.skipped\.output\.id"):
        render("{{ steps.skipped.output.id }}", CTX)
    with pytest.raises(TemplateError, match=r"cannot render 'id=\{\{ input.nope \}\}'"):
        render("id={{ input.nope }}", CTX)


def test_lenient_render_turns_missing_into_none_or_empty():
    assert render("{{ steps.skipped.output.id }}", CTX, strict=False) is None
    assert render("id={{ input.nope }}", CTX, strict=False) == "id="


def test_conditions():
    assert eval_condition("input.amount > params.approval_threshold", CTX) is False
    assert eval_condition("input.amount <= steps.fetch.output.amount", CTX) is True
    assert eval_condition("'broken' in (input.reason or '') | lower", CTX) is True
    assert eval_condition("steps.fetch.status == 'succeeded'", CTX) is True


def test_condition_with_missing_values_is_false():
    assert eval_condition("steps.skipped.output.eligible", CTX) is False
    assert eval_condition("steps.skipped.output.amount > 5", CTX) is False
    assert eval_condition("steps.skipped.output is none", CTX) is True


def test_eval_value_is_lenient():
    assert eval_value("steps.fetch.output.id", CTX) == "A1001"
    assert eval_value("steps.skipped.output.id", CTX) is None


def test_check_expression_reports_syntax_errors():
    check_expression("input.amount > 5")
    with pytest.raises(TemplateError, match="invalid expression"):
        check_expression("input.amount >")


def test_check_template_recurses():
    check_template({"a": ["{{ input.x }}", "plain"], "b": 3})
    with pytest.raises(TemplateError, match="invalid template"):
        check_template({"a": ["{{ input.x "]})


def test_sandbox_blocks_dunder_access():
    assert eval_value("''.__class__.__mro__", {}) is None
