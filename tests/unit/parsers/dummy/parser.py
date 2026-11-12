"""Fixture parser that imports cleanly and defines exactly one BaseParser subclass."""
from typing import ClassVar

from pydantic import BaseModel

from uwp.parsers.base import BaseParser, Column, ParseContext, ParseResult


class DummySettings(BaseModel):
    limit: int = 5


class DummyParser(BaseParser):
    key = "dummy"
    title = "Dummy"
    description = "Fixture parser used by the registry tests."
    url = "https://example.com/"
    settings_model = DummySettings
    columns: ClassVar[list[Column]] = [Column("title", "Title")]

    async def run(self, ctx: ParseContext) -> ParseResult:
        return ParseResult(rows=[])
