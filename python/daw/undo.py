"""Minimal undo/redo for pattern edits (command pattern)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class _Command:
    label: str
    do: Callable[[], None]
    undo: Callable[[], None]


class UndoStack:
    def __init__(self, limit: int = 100):
        self._undo: list[_Command] = []
        self._redo: list[_Command] = []
        self._limit = limit

    def execute(self, label: str, do: Callable[[], None], undo: Callable[[], None]) -> None:
        do()
        self._undo.append(_Command(label, do, undo))
        if len(self._undo) > self._limit:
            del self._undo[: len(self._undo) - self._limit]
        self._redo.clear()

    def undo(self) -> str | None:
        if not self._undo:
            return None
        cmd = self._undo.pop()
        cmd.undo()
        self._redo.append(cmd)
        return cmd.label

    def redo(self) -> str | None:
        if not self._redo:
            return None
        cmd = self._redo.pop()
        cmd.do()
        self._undo.append(cmd)
        return cmd.label

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()
