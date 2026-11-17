"""Localised strings for the bot, one function per former constant.

The words themselves live in :mod:`uwp.i18n.bot`; this module is the facade the
handlers import. Every function takes ``lang`` first, so a handler can never
render a screen without having resolved the admin's language, and a missing key
raises at the call site instead of leaking the other language into the chat.

``esc`` stays here because it is applied to *user data* (site names, proxies,
error text) at the point where the string is composed, not to the translation.
"""

from __future__ import annotations

from html import escape

from uwp import labels
from uwp.i18n import Lang, has_key, t


def esc(value) -> str:
    """Escape a user-supplied string for the HTML parse mode."""
    return escape(str(value), quote=False)


# --- shared ---------------------------------------------------------------


def access_denied(lang) -> str:
    return t(lang, "bot.error.access_denied")


def back(lang) -> str:
    return t(lang, "bot.common.back")


def cancel(lang) -> str:
    return t(lang, "bot.common.cancel")


def delete(lang) -> str:
    return t(lang, "bot.common.delete")


def run_now(lang) -> str:
    return t(lang, "bot.common.run_now")


def settings(lang) -> str:
    return t(lang, "bot.common.settings")


def toggle_on(lang) -> str:
    return t(lang, "bot.common.toggle_on")


def toggle_off(lang) -> str:
    return t(lang, "bot.common.toggle_off")


def add_site(lang) -> str:
    return t(lang, "bot.common.add_site")


def retry(lang) -> str:
    return t(lang, "bot.common.retry")


def history(lang) -> str:
    return t(lang, "bot.common.history")


def new_profile(lang) -> str:
    return t(lang, "bot.common.new_profile")


def new_proxy(lang) -> str:
    return t(lang, "bot.common.new_proxy")


def no_proxy(lang) -> str:
    return t(lang, "bot.common.no_proxy")


def none_word(lang) -> str:
    return t(lang, "bot.common.none")


def check(lang) -> str:
    return t(lang, "bot.common.check")


def login(lang) -> str:
    return t(lang, "bot.common.login")


def login_done(lang) -> str:
    return t(lang, "bot.common.login_done")


def yes(lang) -> str:
    return t(lang, "bot.common.yes")


def no(lang) -> str:
    return t(lang, "bot.common.no")


def param_fallback(lang) -> str:
    return t(lang, "bot.common.param_fallback")


# --- main menu ------------------------------------------------------------


def menu(lang) -> str:
    return t(lang, "bot.menu.title")


def help(lang) -> str:
    return t(lang, "bot.menu.help")


def unknown_command(lang) -> str:
    return t(lang, "bot.menu.unknown_command")


def your_id(lang, value) -> str:
    return t(lang, "bot.menu.your_id", id=esc(value))


# --- sites ----------------------------------------------------------------


def sites_header(lang) -> str:
    return t(lang, "bot.sites.header")


def site_added(lang, name) -> str:
    return t(lang, "bot.sites.added", name=esc(name))


def site_started(lang) -> str:
    return t(lang, "bot.sites.started")


def site_already_running(lang) -> str:
    return t(lang, "bot.sites.already_running")


def site_enabled(lang) -> str:
    return t(lang, "bot.sites.enabled")


def site_disabled(lang) -> str:
    return t(lang, "bot.sites.disabled")


def site_deleted(lang) -> str:
    return t(lang, "bot.sites.deleted")


def confirm_delete(lang, name) -> str:
    return t(lang, "bot.sites.confirm_delete", name=esc(name))


def confirm_delete_yes(lang) -> str:
    """The affirmative on the confirmation screen, not the card's opener."""
    return t(lang, "bot.sites.confirm_delete_yes")


def no_sites(lang) -> str:
    return t(lang, "bot.sites.empty")


def sites_next_line(lang, when) -> str:
    return t(lang, "bot.sites.next_line", when=when)


def sites_list_suffix(lang, when) -> str:
    return t(lang, "bot.sites.list_suffix", when=when)


def choose_parser(lang) -> str:
    return t(lang, "bot.sites.choose_parser")


def need_profile(lang) -> str:
    return t(lang, "bot.sites.need_profile")


def site_not_found(lang) -> str:
    return t(lang, "bot.error.site_not_found")


def profile_not_found(lang) -> str:
    return t(lang, "bot.error.profile_not_found")


def proxy_not_found(lang) -> str:
    return t(lang, "bot.error.proxy_not_found")


def parser_not_found(lang) -> str:
    return t(lang, "bot.error.parser_not_found")


def param_not_found(lang) -> str:
    return t(lang, "bot.error.param_not_found")


def no_parsers(lang) -> str:
    return t(lang, "bot.error.no_parsers")


def status_mark(value) -> str:
    """The glyph in front of a run row; language-independent on purpose."""
    return {
        "ok": "✅",
        "partial": "⚠️",
        "failed": "❌",
        "running": "⏳",
        "canceled": "⏹",
    }.get(str(value), "•")


def status_label(lang, value) -> str:
    """Localised name of a run status; the words live in :mod:`uwp.labels`.

    The metadata sheet and the run line read the same keys, so this stays a
    passthrough instead of a second copy of the word table.
    """
    return labels.status(value, lang)


def export_label(lang, mode) -> str:
    """Localised name of an export mode, from the shared catalogue."""
    return labels.export(mode, lang)


def _known(prefix: str, value) -> bool:
    """Whether the enum value is one we translate, instead of raising in ``t``."""
    return has_key(Lang.ru, f"{prefix}.{value}")


def site_card(lang, site, next_run_local: str | None) -> str:
    """One site card: a bold name over a blockquote of its metadata."""
    state = (
        t(lang, "bot.sites.card.state_on")
        if site.enabled
        else t(lang, "bot.sites.card.state_off")
    )
    lines = [
        t(lang, "bot.sites.card.parser", parser=esc(site.parser_key)),
        t(lang, "bot.sites.card.state", state=state),
        t(
            lang,
            "bot.sites.card.interval",
            low=site.interval_min_min,
            high=site.interval_max_min,
        ),
        t(lang, "bot.sites.card.profile", label=profile_label(lang, site)),
        t(lang, "bot.sites.card.export", mode=export_label(lang, site.export_mode)),
    ]
    if next_run_local:
        lines.append(t(lang, "bot.sites.card.next_run", when=next_run_local))
    if site.last_status:
        lines.append(
            t(
                lang,
                "bot.sites.card.last_run",
                status=status_label(lang, site.last_status),
            )
        )
    body = "\n".join(lines)
    return f"<b>{esc(site.name)}</b>\n<blockquote>{body}</blockquote>"


def profile_label(lang, site) -> str:
    if str(site.profile_mode) == "persistent":
        if site.profile_id:
            return t(lang, "bot.sites.profile.persistent", id=site.profile_id)
        return t(lang, "bot.sites.profile.persistent_plain")
    return t(lang, "bot.sites.profile.clean")


# --- settings -------------------------------------------------------------


def settings_title(lang, name) -> str:
    return t(lang, "bot.settings.title", name=esc(name))


def settings_choose(lang) -> str:
    return t(lang, "bot.settings.choose")


def interval_button(lang) -> str:
    return t(lang, "bot.settings.interval_button")


def profile_button(lang) -> str:
    return t(lang, "bot.settings.profile_button")


def proxy_button(lang) -> str:
    return t(lang, "bot.settings.proxy_button")


def export_button(lang) -> str:
    return t(lang, "bot.settings.export_button")


def params_button(lang) -> str:
    return t(lang, "bot.settings.params_button")


def ask_interval(lang) -> str:
    return t(lang, "bot.settings.ask_interval")


def interval_saved(lang, name, low, high) -> str:
    return t(
        lang,
        "bot.settings.interval_saved",
        name=esc(name),
        low=low,
        high=high,
    )


def choose_profile(lang) -> str:
    return t(lang, "bot.settings.choose_profile")


def profile_set(lang, label) -> str:
    return t(lang, "bot.settings.profile_set", label=label)


def choose_proxy(lang) -> str:
    return t(lang, "bot.settings.choose_proxy")


def proxy_set(lang, name) -> str:
    return t(lang, "bot.settings.proxy_set", name=esc(name))


def params_header(lang) -> str:
    return t(lang, "bot.settings.params_header")


def ask_param(lang, title) -> str:
    return t(lang, "bot.settings.ask_param", title=esc(title))


def param_saved(lang, name) -> str:
    return t(lang, "bot.settings.param_saved", name=esc(name))


def export_set(lang, mode) -> str:
    return t(lang, "bot.settings.export_set", mode=mode)


def clean_profile_button(lang) -> str:
    return t(lang, "bot.settings.clean_profile_button")


def need_login_profile(lang) -> str:
    return t(lang, "bot.settings.need_login_profile")


# --- interval and parameter validation ------------------------------------


def interval_number(lang) -> str:
    return t(lang, "bot.settings.interval_number")


def interval_positive(lang) -> str:
    return t(lang, "bot.settings.interval_positive")


def interval_order(lang) -> str:
    return t(lang, "bot.settings.interval_order")


def interval_max(lang, maximum) -> str:
    return t(lang, "bot.settings.interval_max", max=maximum)


def param_not_enum(lang) -> str:
    return t(lang, "bot.settings.param_not_enum")


def param_unknown(lang, name) -> str:
    return t(lang, "bot.settings.param_unknown", name=esc(name))


def param_invalid(lang, raw, location, detail) -> str:
    return t(
        lang,
        "bot.settings.param_invalid",
        raw=esc(raw),
        location=location,
        detail=detail,
    )


def param_allowed(lang, values) -> str:
    return t(lang, "bot.settings.param_allowed", values=values)


def param_int(lang) -> str:
    return t(lang, "bot.settings.param_int")


def param_float(lang) -> str:
    return t(lang, "bot.settings.param_float")


def bound(lang, attr, value) -> str:
    """One localised range hint ("at most 10"); unknown constraints stay silent."""
    return t(lang, f"bot.settings.bound_{attr}", value=value)


# --- profiles -------------------------------------------------------------


def profiles_header(lang) -> str:
    return t(lang, "bot.profiles.header")


def no_profiles(lang) -> str:
    return t(lang, "bot.profiles.empty")


def ask_profile_name(lang) -> str:
    return t(lang, "bot.profiles.ask_name")


def profile_created(lang, name) -> str:
    return t(lang, "bot.profiles.created", name=esc(name))


def profile_deleted(lang) -> str:
    return t(lang, "bot.profiles.deleted")


def profile_done(lang, name) -> str:
    return t(lang, "bot.profiles.done", name=esc(name))


def profile_card(lang, profile) -> str:
    status = (
        t(lang, f"bot.profile.status.{profile.status}")
        if _known("bot.profile.status", profile.status)
        else str(profile.status)
    )
    body = "\n".join(
        [
            t(lang, "bot.profiles.card.state", status=status),
            t(
                lang,
                "bot.profiles.card.fingerprint",
                platform=profile.platform,
                brand=profile.brand,
                version=profile.brand_version,
            ),
            t(
                lang,
                "bot.profiles.card.timezone",
                timezone=profile.timezone,
                locale=profile.locale,
            ),
        ]
    )
    return f"<b>👤 {esc(profile.name)}</b>\n<blockquote>{body}</blockquote>"


# --- proxies --------------------------------------------------------------


def proxies_header(lang) -> str:
    return t(lang, "bot.proxies.header")


def no_proxies(lang) -> str:
    return t(lang, "bot.proxies.empty")


def ask_proxy(lang) -> str:
    return t(lang, "bot.proxies.ask")


def proxy_added(lang) -> str:
    return t(lang, "bot.proxies.added")


def proxy_deleted(lang) -> str:
    return t(lang, "bot.proxies.deleted")


def proxy_check(lang, check) -> str:
    """The verdict of a proxy reachability check, in the admin's language.

    The service returns a structured :class:`~uwp.services.proxies.ProxyCheck`;
    the sentence, and the check mark, are built here so an English chat never
    shows a Russian reason. This string goes to ``callback.answer(show_alert=True)``,
    where Telegram has no parse mode: escaping would show ``&amp;`` for an
    ``OSError`` detail that contained ``&``.
    """
    key = "bot.proxies.check_ok" if check.ok else "bot.proxies.check_failed"
    return t(lang, key, text=check.text(lang))


def proxy_card(lang, proxy) -> str:
    auth = (
        ", " + t(lang, "bot.proxies.card.user", name=esc(proxy.username))
        if proxy.username
        else ""
    )
    country = (
        "\n" + t(lang, "bot.proxies.card.country", country=esc(proxy.country))
        if proxy.country
        else ""
    )
    body = (
        f"{esc(proxy.scheme)}://{esc(proxy.host)}:{proxy.port}{auth}{country}"
    )
    return f"<b>🌍 {esc(proxy.name)}</b>\n<blockquote>{body}</blockquote>"


# --- run history ----------------------------------------------------------


def runs_header(lang) -> str:
    return t(lang, "bot.runs.header")


def no_runs(lang) -> str:
    return t(lang, "bot.runs.empty")


def run_line(lang, mark, when, name, status, rows) -> str:
    return t(
        lang,
        "bot.runs.line",
        mark=mark,
        when=when,
        name=esc(name),
        status=status,
        rows=rows,
    )


def run_site_name(lang, site_id) -> str:
    return t(lang, "bot.runs.site", id=site_id)


def run_history_line(lang, mark, when, status, rows, new) -> str:
    return t(
        lang,
        "bot.runs.history_line",
        mark=mark,
        when=when,
        status=status,
        rows=rows,
        new=new,
    )


# --- VNC login ------------------------------------------------------------


def vnc_session(lang, info, remaining) -> str:
    return t(
        lang,
        "bot.vnc.session",
        url=esc(info.url),
        password=esc(info.password),
        remaining=remaining,
    )


def vnc_login_button(lang) -> str:
    return t(lang, "bot.vnc.login_button")


def vnc_done_button(lang) -> str:
    return t(lang, "bot.vnc.done_button")


def vnc_extend_button(lang) -> str:
    return t(lang, "bot.vnc.extend_button")


def vnc_finished(lang) -> str:
    return t(lang, "bot.vnc.finished")


def vnc_extended(lang, remaining) -> str:
    return t(lang, "bot.vnc.extended", remaining=remaining)


# --- interface language ---------------------------------------------------


def language_screen(lang) -> str:
    return t(lang, "bot.language.screen")


def language_option(lang, code) -> str:
    """The label on a language button; the current one is ticked in the keyboard."""
    return t(lang, f"bot.language.{code}")


def language_saved(lang) -> str:
    return t(lang, "bot.language.saved")


def language_unknown(lang) -> str:
    return t(lang, "bot.language.unknown")


def language_unavailable(lang) -> str:
    return t(lang, "bot.language.unavailable")


def language_save_failed(lang) -> str:
    """The repo existed but the write raised: storage failure, not wiring."""
    return t(lang, "bot.language.save_failed")
