"""Per-region dirty flags: the explicit invalidation layer (research topic 34).

The brief's terminology, preserved:

* Level 1 -- boolean dirty:   "something changed"
* Level 2 -- semantic dirty:   "the mixer display needs refresh"
  (FL publicly exposes this level through its MIDI scripting
  OnRefresh/HW_Dirty_* flags; LMMS has Model::dataChanged() signals)
* Level 3 -- geometric dirty:  "these pixels need repainting"
  (Qt gives this to LMMS through QWidget::update(rect) -> QRegion)

And the brief's section-31 warning: dirty REGION (invalidation) is not
partial GEOMETRY recomputation is not partial DRAW-COMMAND generation.
They are three separate optimization layers.

What this module implements, honestly framed:

* DirtyDomain: Level-2 semantic dirty categories -- Pulsegrid's analog
  of FL's HW_Dirty_Mixer_Display / HW_Dirty_Patterns / ... flags.
* DirtyRect: Level-3 geometric rectangles (union, intersection, area).
  Widgets compute these from model changes (e.g. a clip's old + new
  bounds) and use them to skip regenerating draw commands for
  unaffected canvas items -- partial INVALIDATION and partial DRAW
  generation. Pixel-level repainting of the dirty region remains the
  toolkit's (tkinter's) job, exactly as Qt's backing store does it for
  LMMS.
* DirtyTracker: the central log. Semantic events are recorded by the
  app's structural-edit path; geometric events are recorded by the
  widgets that compute dirty rects. The Debug window renders the recent
  event history, so the invalidation layer is observable.

Pure data + logic. No tkinter, no engine.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, auto


class DirtyDomain(Enum):
    """Semantic dirty categories (FL HW_Dirty_* analog).

    A domain says WHAT KIND of UI state changed, not which pixels.
    """
    PLAYLIST_LANES = auto()    # clip blocks on playlist lanes
    PLAYLIST_HEADERS = auto()  # track header strips (name/color/icon)
    MIXER_STRIPS = auto()      # mixer strip controls
    MIXER_METERS = auto()      # mixer peak meters (continuous)
    AUTOMATION = auto()        # automation envelope editor
    SEQUENCER = auto()         # step sequencer grid
    PIANOROLL = auto()         # piano roll editor
    TRANSPORT = auto()         # transport bar / playhead state
    BROWSER = auto()           # content browser
    DEBUG = auto()             # debug window
    MENU = auto()              # menus / title bar
    STATUS = auto()            # status bar message


@dataclass(frozen=True)
class DirtyRect:
    """One geometric dirty region: half-open [x0, x1) x [y0, y1)."""
    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self) -> None:
        if self.x1 < self.x0 or self.y1 < self.y0:
            raise ValueError(f"inverted dirty rect {self}")

    @property
    def area(self) -> float:
        return max(0.0, self.x1 - self.x0) * max(0.0, self.y1 - self.y0)

    def intersects(self, other: "DirtyRect") -> bool:
        return (self.x0 < other.x1 and other.x0 < self.x1
                and self.y0 < other.y1 and other.y0 < self.y1)

    def union(self, other: "DirtyRect") -> "DirtyRect":
        return DirtyRect(min(self.x0, other.x0), min(self.y0, other.y0),
                         max(self.x1, other.x1), max(self.y1, other.y1))

    def padded(self, px: float) -> "DirtyRect":
        return DirtyRect(self.x0 - px, self.y0 - px,
                         self.x1 + px, self.y1 + px)

    def contains(self, x: float, y: float) -> bool:
        return self.x0 <= x < self.x1 and self.y0 <= y < self.y1


def merge_rects(rects: list[DirtyRect]) -> list[DirtyRect]:
    """Merge overlapping rects (Qt backing-store style coalescing)."""
    rects = list(rects)
    merged: list[DirtyRect] = []
    for r in rects:
        for i, m in enumerate(merged):
            if r.intersects(m):
                merged[i] = m.union(r)
                break
        else:
            merged.append(r)
    # One more pass: unions can create new overlaps.
    if len(merged) != len(rects):
        return merge_rects(merged)
    return merged


@dataclass
class DirtyEvent:
    """One recorded invalidation: semantic domain + geometric regions."""
    domain: DirtyDomain
    rects: list[DirtyRect] = field(default_factory=list)
    note: str = ""
    t: float = field(default_factory=time.time)

    @property
    def whole_domain(self) -> bool:
        """No rects: the entire domain was invalidated (Level 1/2 only)."""
        return not self.rects

    @property
    def merged_area(self) -> float:
        return sum(r.area for r in merge_rects(self.rects))


class DirtyTracker:
    """Central invalidation log.

    The app records semantic (whole-domain) events on every structural
    edit; widgets record geometric events when they compute dirty rects
    for partial redraws. `recent()` feeds the Debug window's
    invalidation view.
    """

    def __init__(self, max_events: int = 200) -> None:
        self._events: list[DirtyEvent] = []
        self._max = max_events

    def mark_dirty(self, domain: DirtyDomain,
                   rects: list[DirtyRect] | None = None,
                   note: str = "") -> DirtyEvent:
        ev = DirtyEvent(domain=domain, rects=list(rects or []), note=note)
        self._events.append(ev)
        del self._events[:-self._max]
        return ev

    def whole_domain(self, domain: DirtyDomain, note: str = "") -> DirtyEvent:
        """Level-1/2 invalidation: the whole domain needs refresh."""
        return self.mark_dirty(domain, None, note)

    def is_dirty(self, domain: DirtyDomain) -> bool:
        return any(e.domain is domain for e in self._events)

    def recent(self, n: int = 30) -> list[DirtyEvent]:
        return self._events[-n:]

    def clear(self) -> None:
        self._events.clear()

    def event_count(self) -> int:
        return len(self._events)
