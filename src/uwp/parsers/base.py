"""Parser contract: the types every site parser implements and receives."""
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from uwp.domain import ErrorKind


@dataclass
class Column:
    """One Excel column produced by a parser."""

    key: str
    title: str
    width: int = 18
    fmt: str | None = None


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
    log: Any
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
    requires_login: bool = False
    settings_model: type[BaseModel] = _EmptySettings
    columns: ClassVar[list[Column]] = []

    @classmethod
    def default_settings(cls) -> dict:
        return cls.settings_model().model_dump()

    @abstractmethod
    async def run(self, ctx: ParseContext) -> ParseResult: ...
