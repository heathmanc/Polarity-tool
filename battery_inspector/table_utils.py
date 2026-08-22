from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, TypeVar


T = TypeVar("T")


class ItemTable(Protocol[T]):
    """Small protocol shared by QTableWidget and unit-test doubles."""

    def item(self, row: int, column: int) -> T | None: ...

    def setItem(self, row: int, column: int, item: T) -> None: ...


def ensure_table_item(
    table: ItemTable[T],
    row: int,
    column: int,
    factory: Callable[[], T],
) -> T:
    """Return a cell item, inserting a new item only when the cell is empty.

    Qt owns a ``QTableWidgetItem`` after ``setItem``. Re-inserting that same
    object later generates an ownership warning. Callers should retrieve and
    update the owned item in place, which this helper guarantees.
    """

    item = table.item(row, column)
    if item is None:
        item = factory()
        table.setItem(row, column, item)
    return item
