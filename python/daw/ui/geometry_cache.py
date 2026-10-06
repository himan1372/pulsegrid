"""Retained geometry cache (research topic 35).

The brief's retained-mode core: presentation objects keep their visual
representation alive between frames, so moving one clip does not
reconstruct every other clip:

    clip moved -> ClipPresentation.bounds = newBounds
                  dirty(oldBounds); dirty(newBounds)

LaneGeometryCache retains one ClipDraw (the presentation object: bounds,
waveform geometry, label layout, color, selection state) per clip tag.
On each sync it diffs the freshly built presentation state against the
retained one and reports exactly which node ids are added / removed /
changed / unchanged. Unchanged ClipDraw objects are returned by
identity (the same object, not a copy) -- the renderer then only
regenerates draw commands for the dirty ids.

Pure data + logic. No tkinter, no renderer. The widget owns one cache
per lane.
"""

from __future__ import annotations

from dataclasses import dataclass, field


def _peaks_fingerprint(peaks) -> tuple:
    """Cheap content fingerprint for waveform peaks.

    The widget rebuilds the peak list on every draw-state build, so
    object identity would mark every audio clip dirty every frame.
    Sampling up to 16 buckets (+length +endpoints) is O(1) and
    deterministic for identical content.
    """
    if not peaks:
        return ()
    n = len(peaks)
    stride = max(1, n // 16)
    return (n, peaks[0], peaks[-1],
            tuple(peaks[i] for i in range(0, n, stride)))


def _clip_key(clip) -> tuple:
    """Identity of a clip's visual representation.

    Two ClipDraws with equal keys look identical; the retained object
    can be reused without regenerating draw commands.
    """
    return (clip.x0, clip.x1, clip.color, clip.label, clip.selected,
            clip.kind, clip.muted, clip.reversed,
            _peaks_fingerprint(clip.peaks))


@dataclass
class LaneGeometryCache:
    """Retained per-lane clip geometry. One instance per playlist lane."""
    _retained: dict = field(default_factory=dict)  # tag -> ClipDraw
    _warm: bool = False

    @property
    def warm(self) -> bool:
        """True once the cache has seen a full lane state."""
        return self._warm

    def sync(self, clips) -> dict:
        """Diff fresh ClipDraws against retained geometry.

        Returns {"added": [...], "removed": [...], "changed": [...],
        "unchanged": [...]} as lists of clip tags. Retained objects for
        unchanged clips keep their identity.
        """
        new_keys = {c.tag: _clip_key(c) for c in clips}
        old_tags = set(self._retained)
        new_tags = set(new_keys)
        added = sorted(new_tags - old_tags)
        removed = sorted(old_tags - new_tags)
        changed, unchanged = [], []
        for tag in sorted(new_tags & old_tags):
            if new_keys[tag] == _clip_key(self._retained[tag]):
                unchanged.append(tag)
            else:
                changed.append(tag)
        # Retain the new objects for added/changed; keep old for unchanged.
        for c in clips:
            if c.tag in added or c.tag in changed:
                self._retained[c.tag] = c
        for tag in removed:
            del self._retained[tag]
        self._warm = True
        return {"added": added, "removed": removed, "changed": changed,
                "unchanged": unchanged}

    def get(self, tag: str):
        """The retained ClipDraw for a tag, or None."""
        return self._retained.get(tag)

    def drop(self, tags) -> None:
        """Forget retained geometry (e.g. after tagged items are deleted)."""
        for tag in tags:
            self._retained.pop(tag, None)

    def invalidate(self) -> None:
        """Cold reset: next sync treats everything as added."""
        self._retained.clear()
        self._warm = False
