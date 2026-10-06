"""Track presentation identity (research topic 33).

A strip-level presentation object, in the brief's architectural sense: the
persistent presentation/interaction identity of a track or mixer strip --
name, color, icon -- separated from the audio model (gain/pan/mutes/FX/
routing, which live on PlaylistTrack and in the engine graph).

Two ideas from the FL Studio vs LMMS research land here:

1. Extracted identity. FL exposes per-surface presentation identity
   (Channel Rack channel, Playlist track header, Mixer track) with
   persistent name/color/icon; LMMS binds a concrete TrackView to the
   Track model. Pulsegrid's analog: TrackIdentity is pure data (no
   tkinter, no engine). The playlist header and the mixer strip are two
   presentation surfaces that both read the SAME identity object -- the
   audio model never knows how it is painted.

2. Identity linking (FL Track Mode analog). FL's Track Mode links an
   instrument channel, a playlist track and a mixer track so that a
   name/color/icon change on one member ripples to the others. Pulsegrid
   tracks are unified (one track = one playlist lane + one mixer strip,
   like LMMS's Track -> TrackView), so the linking applies ACROSS tracks:
   linked tracks share presentation identity -- renaming/recoloring one
   ripples to every member of the link group. Edits are write-through
   copies (no shared references), so JSON serialization stays trivial.

What this is NOT (honest scope): this is a Level-1 extraction -- a
presentation-state object plus pure geometry helpers (see
ui/strip_presentation.py). It is not the full
MODEL -> PRESENTATION -> GEOMETRY -> RENDERER -> INTERACTION-CONTROLLER
split the brief sketches for LMMS's future; the tkinter widgets still
own interaction. Instrument-logo derivation (LMMS TrackLabelButton
reading the plugin descriptor) is documented as research, not adapted:
icons here are user-set from a fixed ASCII-safe set (the v0.8.1 lesson:
no Unicode in widget text for distributed builds).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Persistent per-track strip colors. Same 8-hue palette the playlist uses
# for patterns (playlist_painter.PATTERN_COLORS); defined here so the
# model layer does not import UI painter modules.
TRACK_COLORS = [
    "#f778ba", "#79c0ff", "#7ee787", "#ffa657", "#d2a8ff",
    "#ff9e64", "#56d4dd", "#e3b341",
]

# ASCII-safe icon codes shown as small badges on the playlist header and
# the mixer strip. "" means "no icon". Kept short and uppercase on render.
TRACK_ICONS = [
    "", "drm", "bas", "key", "gtr", "vox", "syn", "str", "brs", "fx",
    "mix", "aud",
]


@dataclass
class TrackIdentity:
    """Persistent presentation identity of one track.

    Shown on every surface (playlist header, mixer strip). Audio state
    (gain/pan/mute/FX/routing/automation) is NOT here -- that lives on
    PlaylistTrack and in the engine.
    """
    name: str = "Track"
    color_idx: int = 0  # index into TRACK_COLORS
    icon: str = ""      # "" or a key from TRACK_ICONS

    def validate(self, track_id: str) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError(f"track '{track_id}': identity name must be "
                             f"a non-empty string")
        if not isinstance(self.color_idx, int) or not (
                0 <= self.color_idx < len(TRACK_COLORS)):
            raise ValueError(f"track '{track_id}': color_idx "
                             f"{self.color_idx!r} out of range")
        if self.icon not in TRACK_ICONS:
            raise ValueError(f"track '{track_id}': unknown icon "
                             f"{self.icon!r}")

    def to_dict(self) -> dict:
        return {"name": self.name, "color_idx": self.color_idx,
                "icon": self.icon}

    @classmethod
    def from_dict(cls, d: dict, fallback_name: str = "Track",
                  fallback_color: int = 0) -> "TrackIdentity":
        d = d or {}
        name = d.get("name", fallback_name)
        color_idx = d.get("color_idx", fallback_color)
        icon = d.get("icon", "")
        # Clamp defensively: a hand-edited project file must not crash load.
        if not isinstance(color_idx, int) or not (
                0 <= color_idx < len(TRACK_COLORS)):
            color_idx = fallback_color % len(TRACK_COLORS)
        if icon not in TRACK_ICONS:
            icon = ""
        return cls(name=str(name), color_idx=color_idx, icon=icon)

    def copy(self) -> "TrackIdentity":
        return TrackIdentity(name=self.name, color_idx=self.color_idx,
                             icon=self.icon)


def default_color_for(position: int) -> int:
    """Default color for the Nth track (0-based).

    Matches the old positional mixer accent colors, so projects created
    before persistent colors existed keep their look after migration.
    """
    return position % len(TRACK_COLORS)


def normalize_link_groups(groups: list[list[str]],
                          track_ids: set[str]) -> list[list[str]]:
    """Clean identity link groups: drop unknown/duplicate ids, drop
    singletons (a 1-track group links nothing), dedupe identical groups,
    keep first-seen order."""
    seen: set[tuple[str, ...]] = set()
    out: list[list[str]] = []
    for g in groups:
        ids = [tid for tid in dict.fromkeys(g) if tid in track_ids]
        if len(ids) < 2:
            continue
        key = tuple(ids)
        if key in seen:
            continue
        seen.add(key)
        out.append(ids)
    return out


def linked_members(groups: list[list[str]], track_id: str) -> list[str]:
    """All track ids sharing identity with track_id (including itself),
    or [track_id] when unlinked."""
    for g in groups:
        if track_id in g:
            return list(g)
    return [track_id]
