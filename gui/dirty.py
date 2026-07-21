"""Per-pane dirty flags — pure, no ``dearpygui`` import.

Parse handlers ``mark()`` panes; the render loop ``take()`` them. The flag
clears on paint, never on mark, so a pane hidden across many stops is still
dirty when its mode is selected and repaints with current state.
"""

from __future__ import annotations

from typing import Iterable


class DirtySet:
    def __init__(self) -> None:
        self._dirty: set[str] = set()

    def mark(self, pane: str) -> None:
        self._dirty.add(pane)

    def mark_all(self, panes: Iterable[str]) -> None:
        self._dirty.update(panes)

    def take(self, pane: str) -> bool:
        """Test-and-clear. True means the caller must repaint `pane` now."""
        if pane in self._dirty:
            self._dirty.discard(pane)
            return True
        return False

    def pending(self) -> set[str]:
        return set(self._dirty)

    def clear(self) -> None:
        self._dirty.clear()
