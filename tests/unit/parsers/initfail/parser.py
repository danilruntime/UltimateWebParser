"""Fixture parser that imports cleanly but raises in ``__init__``; discovery must record it."""
from uwp.parsers.base import BaseParser, ParseContext, ParseResult


class InitFailParser(BaseParser):
    key = "initfail"
    title = "InitFail"
    description = "Fixture parser whose constructor fails."
    url = "https://example.com/"

    def __init__(self) -> None:
        raise RuntimeError("init boom")

    async def run(self, ctx: ParseContext) -> ParseResult:
        return ParseResult(rows=[])
