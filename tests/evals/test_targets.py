from cerebellum.evals.targets import connector_targets, redact
from cerebellum.spec import load_workflow
from cerebellum.templates import template_path

WORKFLOW = template_path("refund") / "workflow.yaml"
SANDBOX = "http://127.0.0.1:8787"


def by_name(targets):
    return {target.connector: target for target in targets}


def test_the_packaged_workflow_on_the_sandbox_is_isolated():
    targets = by_name(connector_targets(load_workflow(WORKFLOW, env={}), SANDBOX))
    assert targets["orders_db"].isolated and targets["orders_db"].warning is None
    assert targets["orders_db"].where == "fresh sandbox database"
    assert targets["payments"].isolated and targets["payments"].where == f"{SANDBOX} (sandbox)"


def test_localhost_and_a_trailing_slash_still_match_the_sandbox():
    env = {"PAYMENTS_URL": "http://localhost:8787/"}
    targets = by_name(connector_targets(load_workflow(WORKFLOW, env=env), SANDBOX))
    assert targets["payments"].isolated


def test_real_systems_are_named_without_credentials_and_warned_about():
    """Review finding: with ORDERS_DSN / PAYMENTS_URL set, eval silently refunded for real."""
    env = {
        "ORDERS_DSN": "postgresql://shop:s3cret@db.internal:5432/orders",
        "PAYMENTS_URL": "https://payments.example.com",
    }
    targets = by_name(connector_targets(load_workflow(WORKFLOW, env=env), SANDBOX))
    orders, payments = targets["orders_db"], targets["payments"]
    assert not orders.isolated and orders.where == "postgresql://db.internal:5432/orders"
    assert "real PostgreSQL database" in orders.warning and "s3cret" not in orders.warning
    assert not payments.isolated and "not the sandbox" in payments.warning
    assert "fault modes do not apply" in payments.warning


def test_without_a_sandbox_every_rest_target_is_real():
    targets = by_name(connector_targets(load_workflow(WORKFLOW, env={}), None))
    assert not targets["payments"].isolated
    assert "fault modes" not in targets["payments"].warning


def test_redact_drops_credentials_and_hides_key_value_dsns():
    assert redact("postgresql://u:p@h:5432/db") == "postgresql://h:5432/db"
    assert redact("https://api.example.com/v1") == "https://api.example.com/v1"
    assert redact("host=db user=u password=p") == "PostgreSQL (key=value DSN)"
