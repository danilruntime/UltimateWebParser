"""Report export: decide whether a run produces an ``.ods``, write it, prune old ones.

The report is written by the runner right after the rows are stored, because
that is the only place that has the parser columns, the profile and the run id.
Its language is the deployment-wide ``settings.report_lang``: the worker has no
admin in front of it, so a per-admin language would be a guess.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from uwp import __version__, labels
from uwp.config import Settings
from uwp.domain import ExportMode, Profile, Proxy, Site
from uwp.i18n import DEFAULT_LANG, Lang, t
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
    lang: Lang = DEFAULT_LANG,
) -> dict[str, str]:
    """Metadata sheet contents; proxy credentials are never included."""
    if proxy is None:
        proxy_text = t(lang, "report.meta.no_proxy")
    else:
        country = f", {proxy.country}" if proxy.country else ""
        proxy_text = f"{proxy.name} ({proxy.scheme}{country})"
    profile_text = (
        t(lang, "report.meta.clean_profile")
        if profile is None
        else f"{profile.name} ({labels.kind(profile.kind, lang)})"
    )
    meta = {
        t(lang, "report.meta.site"): site.name,
        t(lang, "report.meta.parser"): parser.title_for(lang),
        t(lang, "report.meta.profile"): profile_text,
        t(lang, "report.meta.proxy"): proxy_text,
        t(lang, "report.meta.when"): when.astimezone(ZoneInfo(tz)).strftime(
            # A language's date shape travels with its words: ``13.11.2026`` in
            # an English sheet reads as an ambiguous month/day to most readers.
            "%Y-%m-%d %H:%M" if Lang(lang) is Lang.en else "%d.%m.%Y %H:%M"
        ),
        t(lang, "report.meta.duration"): t(
            lang, "report.meta.duration_value", seconds=duration_ms / 1000
        ),
        t(lang, "report.meta.rows"): str(len(rows)),
        t(lang, "report.meta.new"): str(len(new_keys)),
        t(lang, "report.meta.changed"): str(len(changed_keys)),
        t(lang, "report.meta.vanished"): str(vanished),
        t(lang, "report.meta.url"): parser.url,
        t(lang, "report.meta.version"): __version__,
    }
    if changed_keys:
        # A machine key, not a label: the report reader greps for it and the
        # runner writes it back into ``changed`` detection.
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
    lang: Lang | None = None,
) -> Path | None:
    """Write the run's report when its ``export_mode`` allows it."""
    if lang is None:
        lang = settings.report_lang
    if not should_export(site.export_mode, new_keys, changed_keys):
        return None
    directory = settings.exports_dir / slugify(site.slug)
    filename = report_filename(
        settings.report_name_template,
        parser.title_for(lang),
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
        lang=lang,
    )
    # «Новое» describes the data, not the mode: ``always`` still writes a full
    # snapshot, but every snapshot says which rows arrived since the last run.
    path = write_report(
        _unique_path(directory / filename),
        rows,
        parser.columns,
        meta,
        new_keys,
        lang=lang,
    )
    prune_reports(directory, settings.exports_retention)
    log.info("wrote report %s for site %s", path, site.slug)
    return path
