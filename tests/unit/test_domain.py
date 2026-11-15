from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from uwp.domain import (
    ErrorKind,
    Event,
    ExportMode,
    Item,
    Job,
    JobKind,
    JobStatus,
    Profile,
    ProfileKind,
    ProfileStatus,
    Proxy,
    ProxyScheme,
    Run,
    RunStatus,
    Site,
    VncSession,
    VncStatus,
)
from uwp.timeutil import utcnow

NOW = datetime(2026, 11, 13, 1, 20, tzinfo=UTC)


def test_site_rejects_interval_min_greater_than_max():
    with pytest.raises(ValidationError):
        Site(id=1, slug="steam", name="Steam", parser_key="steam_deals",
             interval_min_min=90, interval_max_min=60)


def test_site_rejects_interval_below_one():
    with pytest.raises(ValidationError):
        Site(id=1, slug="steam", name="Steam", parser_key="steam_deals",
             interval_min_min=0, interval_max_min=60)


def test_site_defaults():
    s = Site(id=1, slug="steam", name="Steam", parser_key="steam_deals",
             interval_min_min=45, interval_max_min=90)
    assert s.enabled is False and s.export_mode == ExportMode.always
    assert s.profile_mode == ProfileKind.clean and s.max_attempts == 2


def test_site_optional_fields_default():
    s = Site(slug="steam", name="Steam", parser_key="steam_deals",
             interval_min_min=45, interval_max_min=90)
    assert s.id is None
    assert s.parser_settings == {}
    assert s.profile_id is None and s.proxy_id is None
    assert s.next_run_at is None and s.last_run_at is None
    assert s.last_status is None
    assert s.consecutive_failures == 0
    assert s.match_profile_to_proxy_country is False


def test_site_parser_settings_not_shared_between_instances():
    a = Site(slug="a", name="A", parser_key="p", interval_min_min=1, interval_max_min=2)
    b = Site(slug="b", name="B", parser_key="p", interval_min_min=1, interval_max_min=2)
    a.parser_settings["region"] = "ru"
    assert b.parser_settings == {}


def test_site_keeps_enum_instances():
    s = Site(slug="steam", name="Steam", parser_key="steam_deals",
             interval_min_min=45, interval_max_min=90,
             profile_mode="persistent", export_mode="changes")
    assert s.profile_mode is ProfileKind.persistent
    assert s.export_mode is ExportMode.changes


def test_profile_kind_enum_values():
    assert ProfileKind.clean.value == "clean" and ProfileKind.persistent.value == "persistent"


def test_enum_values():
    assert [e.value for e in ProfileStatus] == ["free", "locked", "login", "expired"]
    assert [e.value for e in ProxyScheme] == ["http", "https", "socks5"]
    assert [e.value for e in JobKind] == ["scheduled", "manual"]
    assert [e.value for e in JobStatus] == ["queued", "running", "done", "failed", "canceled"]
    assert [e.value for e in RunStatus] == ["running", "ok", "partial", "failed", "canceled"]
    assert [e.value for e in ErrorKind] == [
        "login_required", "blocked", "transient", "parser_error", "config_error"
    ]
    assert [e.value for e in ExportMode] == ["always", "changes", "never"]
    assert [e.value for e in VncStatus] == [
        "requested", "running", "stopping", "stopped", "failed"
    ]


def test_profile_defaults():
    p = Profile(name="p", kind=ProfileKind.persistent, seed=1,
                platform="windows", brand="Chrome", brand_version="150",
                timezone="Europe/Moscow", locale="ru-RU",
                user_data_dir="/tmp/p")
    assert p.id is None
    assert p.proxy_id is None
    assert p.status == ProfileStatus.free
    assert p.lease_until is None and p.last_used_at is None and p.expires_at is None


def test_proxy_defaults():
    p = Proxy(name="p", scheme=ProxyScheme.http, host="127.0.0.1", port=8080)
    assert p.id is None
    assert p.username is None and p.password is None and p.country is None


def test_job_defaults():
    j = Job(site_id=1, kind=JobKind.scheduled, status=JobStatus.queued, created_at=NOW)
    assert j.id is None
    assert j.priority == 0 and j.attempt == 0
    assert j.claimed_by is None and j.claimed_at is None and j.heartbeat_at is None
    assert j.not_before is None and j.error is None


def test_run_defaults():
    r = Run(job_id=1, site_id=1, started_at=NOW, status=RunStatus.ok)
    assert r.id is None
    assert r.finished_at is None
    assert r.rows_count == 0 and r.new_count == 0
    assert r.report_path is None and r.artifact_dir is None and r.error is None
    assert r.duration_ms is None
    assert r.profile_id is None and r.proxy_id is None


def test_item_defaults():
    i = Item(site_id=1, external_key="app/1", payload={"name": "x"},
             first_seen_at=NOW, last_seen_at=NOW)
    assert i.id is None
    assert i.payload == {"name": "x"}


def test_event_defaults():
    e = Event(kind="run_finished", created_at=NOW)
    assert e.id is None
    assert e.site_id is None and e.run_id is None
    assert e.payload == {}


def test_vnc_session_defaults():
    v = VncSession(profile_id=1, port=6000, display=100, password="pw",
                   status=VncStatus.requested, started_at=NOW, expires_at=NOW)
    assert v.id is None
    assert v.started_by is None
    assert v.last_client_at is None


def test_vnc_session_accepts_last_client_at():
    v = VncSession(profile_id=1, port=6000, display=100, password="pw",
                   status=VncStatus.running, expires_at=NOW, last_client_at=NOW)
    assert v.last_client_at == NOW


def test_pre_insert_timestamps_default_to_utc_now():
    before = utcnow()
    j = Job(id=1, site_id=1, kind=JobKind.scheduled, status=JobStatus.running)
    r = Run(job_id=1, site_id=1, status=RunStatus.ok)
    i = Item(site_id=1, external_key="app/1", payload={})
    e = Event(kind="run_finished")
    v = VncSession(profile_id=1, port=6000, display=100, password="pw",
                   status=VncStatus.running, expires_at=NOW)
    after = utcnow()
    for dt in (j.created_at, r.started_at, i.first_seen_at, i.last_seen_at,
               e.created_at, v.started_at):
        assert dt.tzinfo is UTC
        assert before <= dt <= after


def test_pre_insert_timestamps_are_not_shared():
    a = Job(site_id=1, kind=JobKind.scheduled, status=JobStatus.queued)
    b = Job(site_id=1, kind=JobKind.scheduled, status=JobStatus.queued)
    assert a.created_at is not b.created_at
