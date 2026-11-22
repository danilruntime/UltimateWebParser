"""Application settings loaded from ``UWP_*`` environment variables and ``.env``."""
import contextlib
import os
from collections.abc import Mapping
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from uwp.i18n import Lang, normalize

BROWSER_VERSION = "148.0.7778.215"
BROWSER_SHA256 = "70d239830332e5820aa34dfcb284161cac0429eee25da642830afe04bda717f4"


def browser_url_for(version: str) -> str:
    """The release archive URL of a fingerprint-chromium version."""
    return (
        "https://github.com/adryfish/fingerprint-chromium/releases/download/"
        f"{version}/ungoogled-chromium-{version}-1-x86_64_linux.tar.xz"
    )


BROWSER_URL = browser_url_for(BROWSER_VERSION)


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
    admin_ids: list[int] = Field(default_factory=list)
    # Proxy for the Telegram Bot API only. Without it a blocked api.telegram.org
    # makes start_polling fail with TelegramNetworkError and the container
    # restart-loops. ``socks5://host:port`` also needs aiohttp-socks.
    telegram_proxy: str | None = None
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
    # ``https`` when a TLS reverse proxy fronts the VNC band; noVNC itself
    # speaks plain HTTP and is bound to loopback by default.
    public_scheme: str = "http"
    # Inside a bridged container websockify must listen on 0.0.0.0 for Docker's
    # port publishing to reach it; compose publishes the band on the host's
    # 127.0.0.1. With ``network_mode: host`` there is no publishing, so the
    # overlay sets this to 127.0.0.1 — otherwise noVNC lands on every host
    # interface.
    vnc_listen_host: str = "0.0.0.0"
    secret_key: str | None = None
    report_name_template: str = "{parser_title}_{site_name}_{timestamp}"
    # Language of everything a human reads when no per-admin choice exists:
    # the CLI, the fallback for the bot, and reports unless overridden below.
    default_language: str = "ru"
    # Reports are written by the worker, which has no admin context, so their
    # language is a separate deployment setting. None means default_language.
    report_language: str | None = None
    # A restart must not replay commands sent while the bot was down.
    drop_pending_updates: bool = True
    exports_retention: int = 50
    artifacts_retention: int = 20
    vnc_history_days: int = 7
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

    @model_validator(mode="after")
    def _browser_url_follows_the_version(self) -> "Settings":
        """Keep the archive URL and the version in step.

        ``UWP_BROWSER_VERSION`` used to move the version alone, so the
        installer downloaded the pinned 148 archive, verified the pinned 148
        hash and printed 150 — a success message for a build it never fetched.
        A URL that still equals the default follows the version; any other URL
        is a deliberate mirror override and is left alone.
        """
        if self.browser_version != BROWSER_VERSION and self.browser_url == BROWSER_URL:
            self.browser_url = browser_url_for(self.browser_version)
        return self

    @field_validator("public_scheme", mode="before")
    @classmethod
    def _check_public_scheme(cls, value: object) -> object:
        """A typo like ``htps`` must not produce a dead link silently."""
        if isinstance(value, str):
            value = value.strip().lower()
            if value not in ("http", "https"):
                raise ValueError("UWP_PUBLIC_SCHEME must be http or https")
            return value
        return value

    @field_validator("telegram_proxy", mode="before")
    @classmethod
    def _blank_proxy_is_none(cls, value: object) -> object:
        """``UWP_TELEGRAM_PROXY=`` in .env means "no proxy", not an empty URL."""
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @field_validator("report_language", mode="before")
    @classmethod
    def _normalize_report_language(cls, value: object) -> object:
        """``UWP_REPORT_LANGUAGE=`` means the default language; a code is normalized."""
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return None
            lang = normalize(value)
            if lang is None:
                raise ValueError(
                    f"UWP_REPORT_LANGUAGE={value!r} is not supported (ru, en)"
                )
            return lang.value
        return value

    @field_validator("default_language", mode="before")
    @classmethod
    def _normalize_default_language(cls, value: object) -> object:
        """A locale copied from a client (``en-US``) is not a typo; ``de`` is."""
        if isinstance(value, str):
            lang = normalize(value)
            if lang is None:
                raise ValueError(
                    f"UWP_DEFAULT_LANGUAGE={value!r} is not supported (ru, en)"
                )
            return lang.value
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
    def report_lang(self) -> Lang:
        """The language reports are written in: explicit setting, else the default."""
        return Lang(self.report_language or self.default_language)

    @property
    def profiles_dir(self) -> Path:
        return self.data_dir / "profiles"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"

    @property
    def downloads_dir(self) -> Path:
        """Where the VNC login browser saves files an admin asked for.

        Chromium's own default is the XDG "Downloads" folder, which does not
        exist in the container and cannot be chosen through the "Save as"
        dialog while kiosk mode hides it.
        """
        return self.data_dir / "downloads"


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
        # ``call_default_factory=True``: a plain ``get_default()`` returns
        # ``PydanticUndefined`` for fields declared with ``default_factory``
        # (``admin_ids``), which would leak the sentinel into the constructor.
        defaults = {
            name: field.get_default(call_default_factory=True)
            for name, field in Settings.model_fields.items()
        }
        settings = Settings(_env_file=None, **{**defaults, **explicit})
    for directory in (
        settings.data_dir,
        settings.profiles_dir,
        settings.exports_dir,
        settings.artifacts_dir,
        settings.downloads_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        # ``mkdir(mode=...)`` only applies to the directory it creates and is
        # filtered by the umask; a pre-existing world-readable folder keeps its
        # mode, so every run re-asserts it. Profiles hold live session cookies.
        with contextlib.suppress(OSError):
            os.chmod(directory, 0o700)
    return settings
