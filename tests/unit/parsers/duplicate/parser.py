"""Fixture parser that collides with the dummy parser's key; discovery must keep the first."""
from uwp.parsers.base import BaseParser, ParseContext, ParseResult


class DuplicateParser(BaseParser):
    key = "dummy"
    title = "Duplicate"
    description = "Fixture parser that reuses the dummy key."
    url = "https://example.com/"

    async def run(self, ctx: ParseContext) -> ParseResult:
        return ParseResult(rows=[])
