"""Report export: decide whether a run produces an ``.ods``, write it, prune old ones.

The report is written by the runner right after the rows are stored, because
that is the only place that has the parser columns, the profile and the run id.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from uwp import __version__
from uwp.config import Settings
from uwp.domain import ExportMode, Profile, Proxy, Site
from uwp.ods import prune_reports, report_filename, slugify, write_report
from uwp.parsers.base import BaseParser

log = logging.getLogger(__name__)

# A report is only sent when the site is set to ``changes`` and at least one
# row was new or changed; ``always`` ignores the diff and ``never`` skips it.
def should_export(
    mode: ExportMode | str, new_keys: set[str], changed_keys: set[str]
) -> bool:
    """Whether a run with this diff deserves a report file."""
    mode = ExportMode(str(mode))
    if mode is ExportMode.never:
        return False
    if mode is ExportMode.changes:
        return bool(new_keys or changed_keys)
    return True


def _unique_path(path: Path) -> Path:
    """Avoid overwriting the report of a run started in the same minute."""
    if not path.exists():
        return path
    for index in range(2, 1000):
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    return path


def report_meta(
    *,
    site: Site,
    parser: BaseParser,
    profile: Profile | None,
    proxy: Proxy | None,
    when: datetime,
    tz: str,
    duration_ms: int,
    rows: list[dict],
    new_keys: set[str],
    changed_keys: set[str],
    vanished: int = 0,
) -> dict[str, str]:
    """Metadata sheet contents; proxy credentials are never included."""
    if proxy is None:
        proxy_text = "без прокси"
    else:
        country = f", {proxy.country}" if proxy.country else ""
        proxy_text = f"{proxy.name} ({proxy.scheme}{country})"
    profile_text = "чистый (ротация)" if profile is None else f"{profile.name} ({profile.kind})"
    meta = {
        "Сайт": site.name,
        "Парсер": parser.title,
        "Профиль": profile_text,
        "Прокси": proxy_text,
        "Время": when.astimezone(ZoneInfo(tz)).strftime("%d.%m.%Y %H:%M"),
        "Длительность": f"{duration_ms / 1000:.1f} с",
        "Всего строк": str(len(rows)),
        "Новых": str(len(new_keys)),
        "Изменённых": str(len(changed_keys)),
        "Исчезло": str(vanished),
        "URL": parser.url,
        "Версия": __version__,
    }
    if changed_keys:
        meta["changed_keys"] = ", ".join(sorted(changed_keys))
    return meta


def export_report(
    settings: Settings,
    *,
    site: Site,
    parser: BaseParser,
    rows: list[dict],
    profile: Profile | None,
    proxy: Proxy | None,
    when: datetime,
    duration_ms: int,
    new_keys: set[str],
    changed_keys: set[str],
    vanished: int = 0,
) -> Path | None:
    """Write the run's report when its ``export_mode`` allows it."""
    if not should_export(site.export_mode, new_keys, changed_keys):
        return None
    directory = settings.exports_dir / slugify(site.slug)
    filename = report_filename(
        settings.report_name_template,
        parser.title,
        site.name,
        when.astimezone(ZoneInfo(settings.tz)),
    )
    meta = report_meta(
        site=site,
        parser=parser,
        profile=profile,
        proxy=proxy,
        when=when,
        tz=settings.tz,
        duration_ms=duration_ms,
        rows=rows,
        new_keys=new_keys,
        changed_keys=changed_keys,
        vanished=vanished,
    )
    # The «Новое» column belongs to the change report only.
    flagged = new_keys if ExportMode(str(site.export_mode)) is ExportMode.changes else None
    path = write_report(_unique_path(directory / filename), rows, parser.columns, meta, flagged)
    prune_reports(directory, settings.exports_retention)
    log.info("wrote report %s for site %s", path, site.slug)
    return path
