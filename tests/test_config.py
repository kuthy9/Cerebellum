from pathlib import Path

import pytest

from cerebellum.config import (
    DEFAULT_MODEL,
    DEFAULT_SANDBOX_PORT,
    Settings,
    has_anthropic_credentials,
)
from cerebellum.errors import ConfigError


def test_defaults_when_env_is_empty():
    settings = Settings.from_env({})
    assert settings.home == Path(".cerebellum")
    assert settings.model == DEFAULT_MODEL == "claude-opus-5-5"
    assert settings.force_mock is False
    assert settings.pricing_file is None
    assert settings.sandbox_host == "127.0.0.1"
    assert settings.sandbox_port == 8787
    assert settings.lease_seconds == 30.0
    assert settings.db_path == Path(".cerebellum/cerebellum.db")


def test_env_overrides(tmp_path):
    settings = Settings.from_env(
        {
            "CEREBELLUM_HOME": str(tmp_path),
            "CEREBELLUM_MODEL": "claude-sonnet-5-5",
            "CEREBELLUM_MOCK": "true",
            "CEREBELLUM_PRICING_FILE": str(tmp_path / "prices.json"),
            "CEREBELLUM_SANDBOX_PORT": "9999",
            "CEREBELLUM_LEASE_SECONDS": "5",
        }
    )
    assert settings.home == tmp_path
    assert settings.model == "claude-sonnet-5-5"
    assert settings.force_mock is True
    assert settings.pricing_file == tmp_path / "prices.json"
    assert settings.sandbox_port == 9999
    assert settings.lease_seconds == 5.0


def test_ensure_home_creates_directory(tmp_path):
    settings = Settings.from_env({"CEREBELLUM_HOME": str(tmp_path / "nested" / "home")})
    assert settings.ensure_home().is_dir()


def test_credentials_from_env(tmp_path):
    missing = tmp_path / "no-profile"
    assert has_anthropic_credentials({}, config_dir=missing) is False
    assert has_anthropic_credentials({"ANTHROPIC_API_KEY": "test-key"}, config_dir=missing)
    assert has_anthropic_credentials({"ANTHROPIC_AUTH_TOKEN": "t"}, config_dir=missing)


def test_credentials_from_cli_profile(tmp_path):
    (tmp_path / "config.toml").write_text("profile = 'default'\n")
    assert has_anthropic_credentials({}, config_dir=tmp_path) is True


def test_dashboard_settings_have_defaults_and_env_overrides():
    defaults = Settings.from_env({})
    assert (defaults.ui_host, defaults.ui_port) == ("127.0.0.1", 7400)
    assert defaults.worker_interval == 30.0 and defaults.stream_poll == 0.5
    custom = Settings.from_env(
        {
            "CEREBELLUM_UI_HOST": "0.0.0.0",
            "CEREBELLUM_UI_PORT": "9000",
            "CEREBELLUM_WORKER_INTERVAL": "5",
            "CEREBELLUM_STREAM_POLL": "0.1",
        }
    )
    assert (custom.ui_host, custom.ui_port) == ("0.0.0.0", 9000)
    assert custom.worker_interval == 5.0 and custom.stream_poll == 0.1


NOT_A_NUMBER = ("abc", "nan", "NaN", "inf", "-Infinity", "1e400")


@pytest.mark.parametrize(
    "name, value",
    [
        *[("CEREBELLUM_SANDBOX_PORT", v) for v in (*NOT_A_NUMBER, "8787.5", "0", "-1", "65536")],
        *[("CEREBELLUM_UI_PORT", v) for v in (*NOT_A_NUMBER, "7400.0", "0", "70000")],
        *[("CEREBELLUM_LEASE_SECONDS", v) for v in (*NOT_A_NUMBER, "0", "-5")],
        *[("CEREBELLUM_STREAM_POLL", v) for v in (*NOT_A_NUMBER, "0", "-0.5")],
        *[("CEREBELLUM_WORKER_INTERVAL", v) for v in (*NOT_A_NUMBER, "-1")],
    ],
)
def test_a_bad_numeric_setting_is_a_config_error_naming_it(name, value):
    """Review finding: int()/float() on these variables ended every command in a ValueError
    traceback, and float() let nan, inf and negative seconds through to leases and polling."""
    with pytest.raises(ConfigError) as caught:
        Settings.from_env({name: value})
    assert name in str(caught.value) and repr(value) in str(caught.value)


def test_a_bad_port_says_what_is_expected():
    with pytest.raises(ConfigError) as caught:
        Settings.from_env({"CEREBELLUM_SANDBOX_PORT": "abc"})
    assert str(caught.value) == (
        "CEREBELLUM_SANDBOX_PORT must be an integer from 1 to 65535, got 'abc'"
    )


def test_numeric_settings_accept_their_whole_range():
    edges = Settings.from_env(
        {
            "CEREBELLUM_SANDBOX_PORT": "1",
            "CEREBELLUM_UI_PORT": " 65535 ",
            "CEREBELLUM_LEASE_SECONDS": "0.5",
            "CEREBELLUM_WORKER_INTERVAL": "0",  # 0 turns the dashboard's sweep off
            "CEREBELLUM_STREAM_POLL": "1e-3",
        }
    )
    assert (edges.sandbox_port, edges.ui_port) == (1, 65535)
    assert (edges.lease_seconds, edges.worker_interval, edges.stream_poll) == (0.5, 0.0, 0.001)


def test_an_empty_numeric_setting_means_unset():
    """Like an empty CEREBELLUM_PRICING_FILE or CEREBELLUM_MOCK, an empty value is the default."""
    names = (
        "CEREBELLUM_SANDBOX_PORT",
        "CEREBELLUM_UI_PORT",
        "CEREBELLUM_LEASE_SECONDS",
        "CEREBELLUM_WORKER_INTERVAL",
        "CEREBELLUM_STREAM_POLL",
    )
    assert Settings.from_env(dict.fromkeys(names, "")) == Settings.from_env({})
    assert Settings.from_env({"CEREBELLUM_SANDBOX_PORT": "  "}).sandbox_port == DEFAULT_SANDBOX_PORT


def test_an_empty_text_setting_means_unset():
    """Review finding: an empty CEREBELLUM_UI_HOST (a bare `CEREBELLUM_UI_HOST=` line in .env)
    bound the dashboard to every interface with its Host check off; an empty home, model or
    sandbox host also replaced the default with ''."""
    names = (
        "CEREBELLUM_HOME",
        "CEREBELLUM_MODEL",
        "CEREBELLUM_SANDBOX_HOST",
        "CEREBELLUM_UI_HOST",
    )
    for blank in ("", "  "):
        assert Settings.from_env(dict.fromkeys(names, blank)) == Settings.from_env({}), blank
