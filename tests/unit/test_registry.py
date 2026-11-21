"""Tests for the parser contract and the discovery registry."""
import logging
from pathlib import Path

import pytest
from pydantic import BaseModel

from uwp.domain import ErrorKind
from uwp.parsers.base import BaseParser, Column, ParseContext, ParseError, ParseResult
from uwp.parsers.registry import ParserRegistry


def test_discovers_dummy_parser():
    reg = ParserRegistry.discover(package="tests.unit.parsers")
    assert reg.get("dummy").title == "Dummy"
    assert reg.get("dummy").default_settings() == {"limit": 5}


def test_broken_parser_does_not_raise():
    reg = ParserRegistry.discover(package="tests.unit.parsers")
    assert "broken" in reg.broken()
    assert reg.get("broken") is None


def test_broken_parser_records_import_error():
    reg = ParserRegistry.discover(package="tests.unit.parsers")
    assert reg.broken()["broken"] == "RuntimeError: boom"


def test_all_lists_discovered_parsers():
    reg = ParserRegistry.discover(package="tests.unit.parsers")
    assert [p.key for p in reg.all()] == ["dummy"]


def test_init_failure_is_recorded_as_broken():
    reg = ParserRegistry.discover(package="tests.unit.parsers")
    assert reg.broken()["initfail"] == "RuntimeError: init boom"
    assert reg.get("initfail") is None


def test_duplicate_key_keeps_first_and_is_recorded():
    reg = ParserRegistry.discover(package="tests.unit.parsers")
    assert reg.get("dummy").title == "Dummy"
    assert reg.broken()["duplicate"] == "duplicate key 'dummy' from tests.unit.parsers.duplicate.parser"


def test_real_package_discovers_without_raising():
    reg = ParserRegistry.discover()
    assert isinstance(reg, ParserRegistry)


def test_empty_registry_and_register():
    reg = ParserRegistry()
    assert reg.all() == []
    assert reg.get("dummy") is None
    assert reg.broken() == {}

    reg.register(_MinimalParser())
    assert reg.get("minimal").title == "Minimal"
    assert [p.key for p in reg.all()] == ["minimal"]


def test_parse_error_carries_kind_and_message():
    err = ParseError(kind=ErrorKind.login_required, message="need login")
    assert isinstance(err, Exception)
    assert err.kind is ErrorKind.login_required
    assert err.message == "need login"
    assert str(err) == "need login"
    assert err.partial_rows == []


def test_parse_error_keeps_partial_rows():
    rows = [{"title": "a"}]
    err = ParseError(kind=ErrorKind.blocked, message="captcha", partial_rows=rows)
    assert err.partial_rows == rows


def test_parse_result_defaults():
    result = ParseResult(rows=[{"title": "a"}])
    assert result.rows == [{"title": "a"}]
    assert result.meta == {}
    assert result.notes is None


def test_parse_result_meta_is_not_shared():
    first = ParseResult(rows=[])
    second = ParseResult(rows=[])
    first.meta["x"] = 1
    assert second.meta == {}


def test_column_defaults():
    col = Column("title", "Title")
    assert (col.key, col.title, col.width, col.fmt) == ("title", "Title", 18, None)


def test_base_parser_defaults_without_settings_model():
    parser = _MinimalParser()
    assert parser.requires_login is False
    assert parser.default_settings() == {}


def test_login_url_falls_back_to_the_run_url():
    """A third-party parser written before ``auth_url`` must keep working."""
    assert _MinimalParser().auth_url == ""
    assert _MinimalParser().login_url == "https://example.com/"


def test_login_url_prefers_the_declared_auth_page():
    class WithLogin(_MinimalParser):
        auth_url = "https://example.com/login"

    assert WithLogin().login_url == "https://example.com/login"


def test_parse_context_holds_callables(tmp_path: Path):
    ctx = ParseContext(
        page=object(),
        context=object(),
        settings=_Settings(),
        log=logging.getLogger("test"),
        artifacts_dir=tmp_path,
        site=None,
        profile=None,
        known_keys=lambda: {"k"},
        save_artifact=lambda name, data: tmp_path / name,
    )
    assert ctx.known_keys() == {"k"}
    assert ctx.save_artifact("a.txt", b"x") == tmp_path / "a.txt"


def test_base_parser_cannot_be_instantiated():
    with pytest.raises(TypeError):
        BaseParser()


class _Settings(BaseModel):
    limit: int = 1


class _MinimalParser(BaseParser):
    key = "minimal"
    title = "Minimal"
    description = "Smallest possible parser."
    url = "https://example.com/"

    async def run(self, ctx: ParseContext) -> ParseResult:
        return ParseResult(rows=[])
