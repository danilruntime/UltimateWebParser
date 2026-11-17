"""Enum words come from the catalogue, once: the chat and the report agree."""
from uwp import labels
from uwp.domain import ErrorKind, ExportMode, ProfileKind, RunStatus
from uwp.i18n import Lang, catalog_keys, t


def test_a_run_status_is_translated():
    assert labels.status(RunStatus.ok, Lang.ru) == "успешно"
    assert labels.status(RunStatus.ok, Lang.en) == "ok"
    assert labels.status("retrying", Lang.en) == "retrying"


def test_the_report_sheet_and_the_chat_use_the_same_word():
    """The chat label and the report label are one lookup, not two.

    ``texts.status_label``/``export_label`` delegate to ``labels``; this test
    pins that the delegation is still in place, so the report cannot drift from
    the run line. It does *not* prove the words are right — the literal
    assertions above do that.
    """
    from uwp.bot import texts

    for value in RunStatus:
        assert labels.status(value, Lang.en) == texts.status_label(Lang.en, value)
        assert labels.status(value, Lang.ru) == texts.status_label(Lang.ru, value)
    for mode in ExportMode:
        assert labels.export(mode, Lang.en) == texts.export_label(Lang.en, mode)
        assert labels.export(mode, Lang.ru) == texts.export_label(Lang.ru, mode)


def test_every_enum_value_has_a_key_in_both_languages():
    """A new ``ErrorKind`` that nobody translates must fail here, not in a chat."""
    keys = catalog_keys()
    for prefix, values in (
        ("report.status", RunStatus),
        ("report.export", ExportMode),
        ("report.kind", ProfileKind),
        ("report.error", ErrorKind),
    ):
        for value in values:
            key = f"{prefix}.{value}"
            assert key in keys[Lang.ru], f"{key} is missing"
            assert key in keys[Lang.en], f"{key} is missing"
            assert t(Lang.en, key) != t(Lang.ru, key), f"{key} was not translated"


def test_an_unknown_value_stays_as_it_is():
    assert labels.status("weird", Lang.en) == "weird"
    assert labels.kind("weird", Lang.en) == "weird"
    assert labels.error("weird", Lang.en) == "weird"
    assert labels.export("weird", Lang.en) == "weird"


def test_the_english_and_russian_kind_words_differ():
    assert labels.kind(ProfileKind.persistent, Lang.ru) == "постоянный"
    assert labels.kind(ProfileKind.persistent, Lang.en) == "persistent"
    assert labels.kind(ProfileKind.clean, Lang.en) == "clean"
    assert labels.error(ErrorKind.login_required, Lang.en) == "login required"
