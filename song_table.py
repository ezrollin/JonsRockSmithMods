"""Pure sorting and selection rules for the song table."""

from __future__ import annotations

from typing import Iterable


SORTABLE_COLUMNS = ("move", "removal", "location", "artist", "song", "favorite", "name", "size")


def removal_row_status(*, checked: bool, queued: bool, f10_marked: bool) -> str:
    """Describe reversible removal intent directly in the visible archive row."""

    states = []
    if queued:
        states.append("Queued after close")
    if f10_marked:
        states.append("F10 mark saved")
    if checked:
        states.append("Checked for removal")
    return "; ".join(states)


def next_sort_state(
    current_column: str | None,
    current_descending: bool,
    requested_column: str,
) -> tuple[str, bool]:
    """First click on a new column sorts ascending; repeated clicks toggle."""

    if requested_column not in SORTABLE_COLUMNS:
        raise ValueError(f"Column is not sortable: {requested_column}")
    if requested_column == current_column:
        return requested_column, not current_descending
    return requested_column, False


def song_sort_key(
    column: str,
    value,
    *,
    checked: bool = False,
    location: str = "Active",
    stable_path: str = "",
) -> tuple[object, str]:
    """Return type-aware sort key with deterministic path tiebreaker."""

    if column not in SORTABLE_COLUMNS:
        raise ValueError(f"Column is not sortable: {column}")
    if column == "move":
        primary: object = 2 if location != "Active" else (1 if checked else 0)
    elif column == "size":
        primary = float(value)
    else:
        primary = str(value).casefold()
    return primary, stable_path.casefold()


def context_selection(clicked_item: str, selected_items: Iterable[str]) -> tuple[str, ...]:
    """Keep an existing group selection when right-clicking inside it."""

    selected = tuple(dict.fromkeys(selected_items))
    if clicked_item in selected:
        return selected
    return (clicked_item,)


def context_actions(locations: Iterable[str]) -> tuple[str, ...]:
    """Offer move/restore only for homogeneous selections."""

    values = tuple(locations)
    if not values:
        return ()
    if all(location == "Active" for location in values):
        return ("toggle_move", "move")
    if all(location == "Do Not Want" for location in values):
        return ("restore",)
    return ()
