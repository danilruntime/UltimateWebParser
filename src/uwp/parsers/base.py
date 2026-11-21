"""Parser contract: the types every site parser implements and receives."""
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from uwp.domain import ErrorKind
from uwp.i18n import Lang, t


@dataclass
class Column:
    """One Excel column produced by a parser."""

    key: str
    title: str
    width: int = 18
    fmt: str | None = None
    # Some values drift on their own between runs (a review count, a live
    # viewer number). They belong in the report, but a change in them is not
    # news, so ``export_mode=changes`` must ignore them. Measured on the live
    # Steam site: review counts moved in 10-13 of 49 rows every 4 minutes,
    # turning ``changes`` into ``always``.
    volatile: bool = False
    # ``title`` is the fallback for a parser written before the catalogue
    # existed: ``title`` stays required, so removing the key later never breaks
    # a third-party parser, and a report written from one keeps its header.
    title_key: str | None = None

    def title_for(self, lang: Lang | str) -> str:
        """The header in ``lang``, or the author's own ``title`` when unkeyed."""
        if self.title_key is None:
            return self.title
        return t(lang, self.title_key)


class ParseError(Exception):
    """Typed parser failure; ``kind`` drives retry and notification policy."""

    def __init__(
        self,
        kind: ErrorKind,
        message: str,
        partial_rows: list[dict] | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.partial_rows = partial_rows if partial_rows is not None else []


@dataclass
class ParseContext:
    """Everything a parser needs for one run, injected by the runner."""

    page: Any
    context: Any
    settings: BaseModel
    log: logging.Logger
    artifacts_dir: Path
    site: Any
    profile: Any
    known_keys: Callable[[], set[str]]
    save_artifact: Callable[[str, bytes], Path]


@dataclass
class ParseResult:
    """Rows collected by a run, plus free-form metadata and a human note."""

    rows: list[dict]
    meta: dict = field(default_factory=dict)
    notes: str | None = None


class _EmptySettings(BaseModel):
    """Settings model for parsers that take no configuration."""


class BaseParser(ABC):
    """Base class for site parsers; discovery requires exactly one subclass per module."""

    key: str
    title: str
    description: str
    url: str
    # The page a VNC login opens. Empty means "the parser has no login form of
    # its own", and ``login_url`` then hands back the run URL. ``url`` itself
    # must stay the run URL: it is what the report's ``URL`` row shows.
    auth_url: str = ""
    requires_login: bool = False
    settings_model: type[BaseModel] = _EmptySettings
    columns: ClassVar[list[Column]] = []
    # Empty means "no catalogue entry": ``title``/``description`` are then shown
    # as written, which is what a parser outside this repository relies on.
    title_key: ClassVar[str] = ""
    description_key: ClassVar[str] = ""

    def title_for(self, lang: Lang | str) -> str:
        """The parser name in ``lang``, or the author's own ``title``."""
        return t(lang, self.title_key) if self.title_key else self.title

    def description_for(self, lang: Lang | str) -> str:
        """The one-line parser blurb in ``lang``, or the author's own text."""
        if self.description_key:
            return t(lang, self.description_key)
        return self.description

    @property
    def login_url(self) -> str:
        """The page a VNC login opens: ``auth_url`` or the run ``url``."""
        return self.auth_url or self.url

    @classmethod
    def default_settings(cls) -> dict:
        return cls.settings_model().model_dump()

    @abstractmethod
    async def run(self, ctx: ParseContext) -> ParseResult: ...
