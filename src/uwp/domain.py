"""Domain enums and plain pydantic models mirroring the SQLite rows."""
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from uwp.timeutil import utcnow


class ProfileKind(StrEnum):
    clean = "clean"
    persistent = "persistent"


class ProfileStatus(StrEnum):
    free = "free"
    locked = "locked"
    login = "login"
    expired = "expired"


class ProxyScheme(StrEnum):
    http = "http"
    https = "https"
    socks5 = "socks5"


class JobKind(StrEnum):
    scheduled = "scheduled"
    manual = "manual"


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"
    canceled = "canceled"


class RunStatus(StrEnum):
    running = "running"
    ok = "ok"
    partial = "partial"
    failed = "failed"
    canceled = "canceled"


class ErrorKind(StrEnum):
    login_required = "login_required"
    blocked = "blocked"
    transient = "transient"
    parser_error = "parser_error"
    config_error = "config_error"


class ExportMode(StrEnum):
    always = "always"
    changes = "changes"
    never = "never"


class VncStatus(StrEnum):
    requested = "requested"
    running = "running"
    stopping = "stopping"
    stopped = "stopped"
    failed = "failed"


class _Model(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    # ``id`` is ``None`` for rows that have not been inserted yet; repositories fill it
    # from the SQLite autoincrement on insert.


class Proxy(_Model):
    id: int | None = None
    name: str
    scheme: ProxyScheme
    host: str
    port: int
    username: str | None = None
    password: str | None = None
    country: str | None = None


class Profile(_Model):
    id: int | None = None
    name: str
    kind: ProfileKind
    seed: int
    # Open sets: the spec defines no closed enum for platform/brand/brand_version.
    platform: str
    brand: str
    brand_version: str
    timezone: str
    locale: str
    user_data_dir: str
    proxy_id: int | None = None
    status: ProfileStatus = ProfileStatus.free
    lease_until: datetime | None = None
    last_used_at: datetime | None = None
    expires_at: datetime | None = None


class Site(_Model):
    id: int | None = None
    slug: str
    name: str
    parser_key: str
    parser_settings: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = False
    profile_mode: ProfileKind = ProfileKind.clean
    profile_id: int | None = None
    proxy_id: int | None = None
    interval_min_min: int
    interval_max_min: int
    next_run_at: datetime | None = None
    export_mode: ExportMode = ExportMode.always
    max_attempts: int = 2
    last_run_at: datetime | None = None
    last_status: str | None = None
    consecutive_failures: int = 0
    match_profile_to_proxy_country: bool = False

    @model_validator(mode="after")
    def _check_interval(self) -> "Site":
        if self.interval_min_min < 1 or self.interval_max_min < 1:
            raise ValueError("interval bounds must be >= 1 minute")
        if self.interval_min_min > self.interval_max_min:
            raise ValueError("interval_min_min must not exceed interval_max_min")
        return self


class Job(_Model):
    id: int | None = None
    site_id: int
    kind: JobKind
    status: JobStatus
    priority: int = 0
    attempt: int = 0
    claimed_by: str | None = None
    claimed_at: datetime | None = None
    heartbeat_at: datetime | None = None
    not_before: datetime | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class Run(_Model):
    id: int | None = None
    job_id: int
    site_id: int
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    status: RunStatus
    rows_count: int = 0
    new_count: int = 0
    report_path: str | None = None
    artifact_dir: str | None = None
    error: str | None = None
    duration_ms: int | None = None
    profile_id: int | None = None
    proxy_id: int | None = None


class Item(_Model):
    id: int | None = None
    site_id: int
    external_key: str
    payload: dict[str, Any]
    first_seen_at: datetime = Field(default_factory=utcnow)
    last_seen_at: datetime = Field(default_factory=utcnow)


class Event(_Model):
    id: int | None = None
    # Open set: event kinds are free-form strings, not a closed enum in the spec.
    kind: str
    site_id: int | None = None
    run_id: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)


class VncSession(_Model):
    id: int | None = None
    profile_id: int
    port: int
    display: int
    password: str
    token: str
    status: VncStatus
    started_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    started_by: int | None = None
    # Updated from the websockify connection count; ``None`` means "no idle stop".
    last_client_at: datetime | None = None
