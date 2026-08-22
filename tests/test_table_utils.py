from battery_inspector.table_utils import ensure_table_item


class FakeTable:
    def __init__(self) -> None:
        self.cells: dict[tuple[int, int], object] = {}
        self.insertions = 0

    def item(self, row: int, column: int):
        return self.cells.get((row, column))

    def setItem(self, row: int, column: int, item: object) -> None:
        self.insertions += 1
        self.cells[(row, column)] = item


def test_existing_table_item_is_updated_without_reinsertion() -> None:
    table = FakeTable()
    original = object()
    table.setItem(2, 3, original)

    returned = ensure_table_item(table, 2, 3, object)

    assert returned is original
    assert table.insertions == 1


def test_missing_table_item_is_inserted_once() -> None:
    table = FakeTable()

    first = ensure_table_item(table, 1, 4, object)
    second = ensure_table_item(table, 1, 4, object)

    assert second is first
    assert table.insertions == 1
