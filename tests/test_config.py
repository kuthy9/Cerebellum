from pathlib import Path

from cerebellum.config import DEFAULT_MODEL, Settings, has_anthropic_credentials


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
