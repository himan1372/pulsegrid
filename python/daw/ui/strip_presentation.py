"""Strip-level presentation objects (research topic 33, Level-1 extraction).

The brief's architectural sketch for LMMS's future:

    MODEL -> PRESENTATION STATE -> GEOMETRY -> RENDERER
                                          <-> INTERACTION CONTROLLER

This module implements the PRESENTATION STATE and GEOMETRY layers for
Pulsegrid's track strips as pure data (no tkinter):

* StripPresentation: one per track -- the strip-level presentation
  object. It carries the presentation identity (name/color/icon, shared
  with the playlist header surface), selection state, live status
  (mute, generator/FX counts, latency) and the live meter peak.
* strip_geometry(): pure function resolving a StripPresentation to
  pixel geometry (strip width, accent bar height, badge size...).

The audio model (PlaylistTrack) never knows about pixels; the engine
never sees any of this. The tkinter widgets (mixer strips, playlist
headers) are the renderers + interaction controllers -- a full
renderer/controller split is future work (honest scope: Level 1 only).

FL Studio parallel: each surface (playlist header, mixer strip) reads
the same TrackIdentity, like FL's Channel/Playlist/Mixer surfaces
sharing name/color/icon under Track Mode. LMMS parallel: TrackView's
role as the object binding a Track model to its visual representation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from daw.track_identity import TRACK_COLORS, TrackIdentity

# -- geometry constants (pixels) -------------------------------------------

STRIP_WIDTH = 176
ACCENT_BAR_HEIGHT = 4
ICON_BADGE_FONT_SIZE = 8
HEADER_CHIP_WIDTH = 10


@dataclass
class StripPresentation:
    """Presentation state for one track strip. Pure data, no tkinter.

    Built from the model by build_strip_presentations(); read by the
    mixer strip builder and the playlist header builder. Live fields
    (peak) are updated by the meter loop without rebuilding.
    """
    track_id: str
    identity: TrackIdentity
    selected: bool = False
    muted: bool = False
    has_generator: bool = False
    fx_count: int = 0
    latency_samples: int = 0
    peak: float = 0.0  # live meter level 0..1, updated by the meter loop

    @property
    def color(self) -> str:
        """Resolved hex color for the accent bar / header chip."""
        return TRACK_COLORS[self.identity.color_idx % len(TRACK_COLORS)]

    @property
    def badge_text(self) -> str:
        """Icon badge text, e.g. '[DRM]'. Empty when no icon is set."""
        return f"[{self.identity.icon.upper()}]" if self.identity.icon else ""

    @property
    def display_name(self) -> str:
        return self.identity.name


def build_strip_presentations(tracks, selected_ids: set[str] | None = None,
                              latency_by_track: dict[str, int] | None = None,
                              ) -> list[StripPresentation]:
    """Build one StripPresentation per track from model objects.

    tracks: PlaylistTrack list. latency_by_track: optional track id ->
    latency samples (from the engine bridge).
    """
    selected_ids = selected_ids or set()
    latency_by_track = latency_by_track or {}
    out = []
    for t in tracks:
        out.append(StripPresentation(
            track_id=t.id,
            identity=t.identity,  # snapshot of the track's identity; both
                                  # surfaces (playlist header, mixer strip)
                                  # rebuild from the same model track on
                                  # every refresh, so they always agree --
                                  # FL Track Mode's "one identity, many
                                  # surfaces" without shared references
                                  # (serialization stays trivial)
            selected=t.id in selected_ids,
            muted=t.muted,
            has_generator=bool(t.generator_layers),
            fx_count=len(t.effects),
            latency_samples=int(latency_by_track.get(t.id, 0)),
        ))
    return out


def strip_geometry(pres: StripPresentation,
                   compact: bool = False) -> dict:
    """Pure geometry for a strip (mixer) or header (playlist) surface.

    Returns plain values; the widgets apply them. No tkinter here.
    """
    return {
        "strip_width": STRIP_WIDTH if not compact else STRIP_WIDTH - 40,
        "accent_bar_height": ACCENT_BAR_HEIGHT,
        "accent_color": pres.color,
        "badge_text": pres.badge_text,
        "badge_font_size": ICON_BADGE_FONT_SIZE,
        "header_chip_width": HEADER_CHIP_WIDTH,
        "name": pres.display_name,
        "muted": pres.muted,
        "selected": pres.selected,
    }
