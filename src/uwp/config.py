"""Application settings loaded from ``UWP_*`` environment variables and ``.env``."""
from collections.abc import Mapping
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

BROWSER_VERSION = "150.0.7871.186"
BROWSER_SHA256 = "48eff860053efd9ad498d51370f0a1f59f983df39c02a72ad2a8682cdead4055"
BROWSER_URL = (
    "https://github.com/adryfish/fingerprint-chromium/releases/download/"
    f"{BROWSER_VERSION}/ungoogled-chromium-{BROWSER_VERSION}-1-x86_64_linux.tar.xz"
)


class _CsvEnvSource(EnvSettingsSource):
    def prepare_field_value(self, field_name, field, value, value_is_complex):
        if field_name == "admin_ids" and isinstance(value, str):
            return value
        return super().prepare_field_value(field_name, field, value, value_is_complex)


class _CsvDotEnvSource(DotEnvSettingsSource):
    def prepare_field_value(self, field_name, field, value, value_is_complex):
        if field_name == "admin_ids" and isinstance(value, str):
            return value
        return super().prepare_field_value(field_name, field, value, value_is_complex)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="UWP_", env_file=".env", extra="ignore")

    bot_token: str = ""
    admin_ids: list[int] = []
    data_dir: Path = Path("data")
    tz: str = "Europe/Moscow"
    browser_path: Path | None = None
    browser_version: str = BROWSER_VERSION
    browser_sha256: str = BROWSER_SHA256
    browser_url: str = BROWSER_URL
    max_parallel_runs: int = 1
    run_timeout: int = 300
    page_timeout_ms: int = 30_000
    navigation_timeout_ms: int = 60_000
    heartbeat_interval: int = 15
    orphan_threshold: int = 90
    vnc_port_min: int = 6000
    vnc_port_max: int = 6100
    vnc_ttl_minutes: int = 60
    vnc_idle_minutes: int = 20
    public_host: str = "localhost"
    secret_key: str | None = None
    report_name_template: str = "{parser_title}_{site_name}_{timestamp}"
    exports_retention: int = 50
    event_ttl_days: int = 3
    headless: bool = True
    no_sandbox: bool = False
    log_level: str = "INFO"

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _split_admin_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Run the CSV validator before pydantic-settings tries to JSON-decode the raw
        # ``UWP_ADMIN_IDS=1,2`` value from the environment or ``.env``.
        return (
            init_settings,
            _CsvEnvSource(settings_cls),
            _CsvDotEnvSource(settings_cls),
            file_secret_settings,
        )

    @property
    def db_path(self) -> Path:
        return self.data_dir / "uwp.db"

    @property
    def profiles_dir(self) -> Path:
        return self.data_dir / "profiles"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Build settings from an explicit ``UWP_*`` mapping (tests) or the environment/``.env``.

    The data directories are created if missing.
    """
    if env is None:
        settings = Settings()
    else:
        # Explicit values win over the process environment; fields absent from ``env``
        # fall back to the class defaults, so tests never depend on the real environment.
        explicit = {key[4:].lower(): value for key, value in env.items() if key.startswith("UWP_")}
        defaults = {name: field.get_default() for name, field in Settings.model_fields.items()}
        settings = Settings(_env_file=None, **{**defaults, **explicit})
    for directory in (
        settings.data_dir,
        settings.profiles_dir,
        settings.exports_dir,
        settings.artifacts_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    return settings
