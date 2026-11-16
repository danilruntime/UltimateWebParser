"""The security policy must stay true, not just exist.

``SECURITY.md`` makes promises about settings the code reads. A rename that
skips the document turns it into a lie, and a document nobody links to is
never read — both are cheap to check.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_security_policy_is_linked_from_the_readme():
    """An unlinked SECURITY.md is invisible: GitHub shows it, readers do not."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "SECURITY.md" in readme


def test_security_policy_names_the_escape_hatches_it_documents():
    """These two settings decide whether the limits below apply at all.

    ``UWP_NO_SANDBOX`` disables the renderer sandbox and ``UWP_SECRET_KEY``
    switches passwords to plaintext when absent; a rename would silently
    invalidate the «Известные ограничения» section.
    """
    from uwp.config import Settings

    text = (ROOT / "SECURITY.md").read_text(encoding="utf-8")

    for name in ("UWP_NO_SANDBOX", "UWP_SECRET_KEY", "UWP_PUBLIC_SCHEME"):
        assert name.removeprefix("UWP_").lower() in Settings.model_fields
        assert name in text


def test_security_policy_documents_what_is_out_of_scope():
    """A policy that only lists defences promises more than the code does.

    Both language versions are checked: the English file is the one GitHub
    renders first, and a section dropped from a translation still leaves the
    other one looking complete.
    """
    english = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    russian = (ROOT / "SECURITY.ru.md").read_text(encoding="utf-8")

    assert "Out of scope" in english
    assert "Known limitations" in english
    assert "Вне scope" in russian
    assert "Известные ограничения" in russian
