"""Renderer abstraction (research topic 35).

The brief's recommended architecture:

    DAW model -> Presentation system -> render primitives -> backend

The renderer does NOT understand Track, Clip, or AutomationNode. It
understands only primitives (rect, text, line, polygon) addressed by
opaque node ids. Backends implement the same interface:

    TkCanvasRenderer   -- tkinter Canvas (the production backend)
    RecordingRenderer  -- records draw commands (tests: proves partial
                          redraws emit commands only for dirty nodes --
                          the brief's section-31 "partial draw-command
                          generation" layer, verified)

Honest scope: tkinter is the only production backend (no GPU on this
machine, and tkinter has no GPU path). The interface is the seam a
future backend would implement -- e.g. an OpenGL renderer consuming
the same primitives. FL Studio documents changed-rectangle painting
and an OpenGL/PBO path; LMMS stays Qt Widgets/QPainter. Pulsegrid's
takeaway is the retained-geometry + renderer-interface split, not a
new rasterizer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class Renderer(ABC):
    """Backend-independent drawing interface. Primitives only."""

    @abstractmethod
    def draw_rect(self, x0: float, y0: float, x1: float, y1: float, *,
                  fill: str = "", outline: str = "", width: int = 1,
                  node_id: str = "") -> None:
        """Filled/outlined rectangle, tagged with node_id."""

    @abstractmethod
    def draw_text(self, x: float, y: float, *, text: str, anchor: str = "w",
                  fill: str = "", font: tuple = ("", 9),
                  node_id: str = "") -> None:
        ...

    @abstractmethod
    def draw_line(self, coords, *, fill: str = "", width: int = 1,
                  node_id: str = "") -> None:
        """Polyline through the flat coords list."""

    @abstractmethod
    def draw_polygon(self, coords, *, fill: str = "", outline: str = "",
                     node_id: str = "") -> None:
        ...

    @abstractmethod
    def delete(self, node_id: str) -> None:
        """Remove everything previously drawn with node_id."""

    @abstractmethod
    def clear(self) -> None:
        """Remove everything."""

    @abstractmethod
    def bbox(self, node_id: str):
        """(x0, y0, x1, y1) bounding box, or None when unknown/absent."""

    @abstractmethod
    def has_node(self, node_id: str) -> bool:
        ...


class TkCanvasRenderer(Renderer):
    """Renderer on top of a tkinter Canvas. node_id <-> canvas tag."""

    def __init__(self, canvas) -> None:
        self._cv = canvas

    def draw_rect(self, x0, y0, x1, y1, *, fill="", outline="", width=1,
                  node_id=""):
        self._cv.create_rectangle(x0, y0, x1, y1, fill=fill,
                                  outline=outline, width=width,
                                  tags=(node_id,) if node_id else ())

    def draw_text(self, x, y, *, text, anchor="w", fill="", font=("", 9),
                  node_id=""):
        self._cv.create_text(x, y, text=text, anchor=anchor, fill=fill,
                             font=font, tags=(node_id,) if node_id else ())

    def draw_line(self, coords, *, fill="", width=1, node_id=""):
        self._cv.create_line(*coords, fill=fill, width=width,
                             tags=(node_id,) if node_id else ())

    def draw_polygon(self, coords, *, fill="", outline="", node_id=""):
        self._cv.create_polygon(*coords, fill=fill, outline=outline,
                                tags=(node_id,) if node_id else ())

    def delete(self, node_id: str) -> None:
        self._cv.delete(node_id)

    def clear(self) -> None:
        self._cv.delete("all")

    def bbox(self, node_id: str):
        return self._cv.bbox(node_id)

    def has_node(self, node_id: str) -> bool:
        return bool(self._cv.find_withtag(node_id))


class RecordingRenderer(Renderer):
    """Records draw commands instead of drawing. For tests.

    Proves the section-31 layering: a partial redraw must record
    commands ONLY for the dirty node ids.
    """

    def __init__(self) -> None:
        self.commands: list[tuple] = []
        self._nodes: set[str] = set()

    def draw_rect(self, x0, y0, x1, y1, *, fill="", outline="", width=1,
                  node_id=""):
        self.commands.append(("rect", node_id, (x0, y0, x1, y1), fill))
        if node_id:
            self._nodes.add(node_id)

    def draw_text(self, x, y, *, text, anchor="w", fill="", font=("", 9),
                  node_id=""):
        self.commands.append(("text", node_id, (x, y), text))
        if node_id:
            self._nodes.add(node_id)

    def draw_line(self, coords, *, fill="", width=1, node_id=""):
        self.commands.append(("line", node_id, tuple(coords)))
        if node_id:
            self._nodes.add(node_id)

    def draw_polygon(self, coords, *, fill="", outline="", node_id=""):
        self.commands.append(("polygon", node_id, tuple(coords)))
        if node_id:
            self._nodes.add(node_id)

    def delete(self, node_id: str) -> None:
        self.commands.append(("delete", node_id))
        self._nodes.discard(node_id)

    def clear(self) -> None:
        self.commands.append(("clear",))
        self._nodes.clear()

    def bbox(self, node_id: str):
        return None  # no geometry retained; tests use node ids

    def has_node(self, node_id: str) -> bool:
        return node_id in self._nodes

    def node_ids_drawn(self) -> set[str]:
        """Node ids that received at least one draw command."""
        return {c[1] for c in self.commands if c[0] in (
            "rect", "text", "line", "polygon") and c[1]}

    def reset(self) -> None:
        self.commands.clear()
