"""Discovery registry for site parsers."""
import importlib
import inspect
import pkgutil

from uwp.parsers.base import BaseParser


class ParserRegistry:
    """Holds discovered parsers; broken ones are recorded instead of raised."""

    def __init__(self) -> None:
        self._parsers: dict[str, BaseParser] = {}
        self._broken: dict[str, str] = {}

    @classmethod
    def discover(cls, package: str = "uwp.parsers") -> "ParserRegistry":
        """Import every ``<package>.<name>.parser`` module and register its parser."""
        registry = cls()
        pkg = importlib.import_module(package)
        for info in pkgutil.iter_modules(pkg.__path__):
            if not info.ispkg:
                continue
            name = info.name
            try:
                module = importlib.import_module(f"{package}.{name}.parser")
            except Exception as exc:  # noqa: BLE001 - any import failure marks the parser broken
                registry._broken[name] = f"{type(exc).__name__}: {exc}"
                continue
            classes = [
                obj
                for obj in vars(module).values()
                if inspect.isclass(obj) and issubclass(obj, BaseParser) and obj is not BaseParser
            ]
            if len(classes) != 1:
                registry._broken[name] = (
                    f"expected exactly one BaseParser subclass, found {len(classes)}"
                )
                continue
            try:
                parser = classes[0]()
            except Exception as exc:  # noqa: BLE001 - a failing constructor marks the parser broken
                registry._broken[name] = f"{type(exc).__name__}: {exc}"
                continue
            if parser.key in registry._parsers:
                registry._broken[name] = f"duplicate key '{parser.key}' from {module.__name__}"
                continue
            registry._parsers[parser.key] = parser
        return registry

    def register(self, parser: BaseParser) -> None:
        """Register a parser instance directly (used by tests and tooling)."""
        self._parsers[parser.key] = parser

    def get(self, key: str) -> BaseParser | None:
        return self._parsers.get(key)

    def all(self) -> list[BaseParser]:
        return list(self._parsers.values())

    def broken(self) -> dict[str, str]:
        return dict(self._broken)
