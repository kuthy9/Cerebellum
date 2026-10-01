import pytest

from cerebellum.spec.durations import parse_duration


@pytest.mark.parametrize(
    ("value", "seconds"),
    [
        ("500ms", 0.5),
        ("30s", 30.0),
        ("5m", 300.0),
        ("24h", 86400.0),
        ("1d", 86400.0),
        (" 2.5s ", 2.5),
        (3, 3.0),
        (0.25, 0.25),
    ],
)
def test_parse_duration(value, seconds):
    assert parse_duration(value) == pytest.approx(seconds)


@pytest.mark.parametrize("value", ["", "10", "5 minutes", "-1s", -2, True, None, "1w"])
def test_parse_duration_rejects_invalid(value):
    with pytest.raises(ValueError):
        parse_duration(value)
