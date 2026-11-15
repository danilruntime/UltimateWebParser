"""The parser contract itself: ``Column`` defaults and its volatile flag."""
from uwp.parsers.base import Column


def test_a_column_is_tracked_by_default():
    column = Column("title", "Название")

    assert column.volatile is False


def test_a_column_can_be_declared_volatile():
    column = Column("views", "Просмотры", 12, volatile=True)

    assert column.volatile is True
