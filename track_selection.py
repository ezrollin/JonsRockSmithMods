"""Small selection model for explicit per-track checkbox controls."""


class TrackCheckboxSelection:
    def __init__(self, count: int) -> None:
        if type(count) is not int or count < 0:
            raise ValueError("Track count must be a non-negative integer")
        self.count = count
        self._selected: set[int] = set()

    def toggle(self, index: int) -> bool:
        self._validate(index)
        if index in self._selected:
            self._selected.remove(index)
            return False
        self._selected.add(index)
        return True

    def select_all(self) -> None:
        self._selected = set(range(self.count))

    def clear(self) -> None:
        self._selected.clear()

    def contains(self, index: int) -> bool:
        self._validate(index)
        return index in self._selected

    def selected_indices(self) -> tuple[int, ...]:
        return tuple(sorted(self._selected))

    def _validate(self, index: int) -> None:
        if type(index) is not int or not 0 <= index < self.count:
            raise IndexError("Track index is out of range")
