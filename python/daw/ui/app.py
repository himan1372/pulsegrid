"""Pulsegrid main window: DAW workspace shell.

Layout (per the UI design playbook):
* Global toolbar: menus + transport + window toggles, hints in the status bar.
* Left dock (tabs): Browser, Plugin Picker, Settings -- panels hide/show
  inside the main window, never as floating windows.
* Editors (center top): Channel Rack (step grid) and Piano Roll tabs.
* Playlist (center bottom): clip timeline with zoom, bar snapping.
* Mixer (right): per-track volume/pan/mute/FX.
Panels resize via paned splitters; visibility, sash positions and UI
scale persist per-user (%APPDATA%\\pulsegrid on Windows,
~/.config/pulsegrid elsewhere).
"""

import copy
import json
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from daw.track_identity import TRACK_COLORS, TRACK_ICONS
from .dirty_regions import DirtyDomain, DirtyTracker

# Structural-edit panels -> semantic dirty domains (research topic 34:
# FL's HW_Dirty_* analog). Tuple panels ("playlist_lane", ...) carry
# geometric detail and are logged by the widget itself.
def _clip_tag(kind: str, index: int) -> str:
    """Canvas tag for a playlist clip (matches playlist_painter tags)."""
    return f"aclip-{index}" if kind == "aclip" else f"clip-{index}"


_PANEL_DOMAINS = {
    "playlist": (DirtyDomain.PLAYLIST_LANES, DirtyDomain.PLAYLIST_HEADERS),
    "mixer": (DirtyDomain.MIXER_STRIPS,),
    "automation": (DirtyDomain.AUTOMATION,),
    "sequencer": (DirtyDomain.SEQUENCER,),
    "pianoroll": (DirtyDomain.PIANOROLL,),
    "browser": (DirtyDomain.BROWSER,),
    "menu": (DirtyDomain.MENU,),
    "debug": (DirtyDomain.DEBUG,),
}

from ..engine_bridge import EngineBridge, EngineError
from ..project import (
    INSTRUMENTS, AudioClip, Clip, Effect, Generator, Note, Pattern,
    PlaylistTrack, Project, ProjectError, SampleAsset, Send,
    blank_channels, empty_project, new_default_project, register_plugin,
)
from ..project import AutomationLane, AutoPoint, parse_auto_param
from ..undo import UndoStack
from .automation import AutomationEditor
from .browser import Browser
from .debug import DebugWindow
from .clap_hosting import (ClapPickerDialog, GeneratorGuiSession,
                            PluginGuiSession, PluginParamDialog)
from .dialogs import show_about, show_error, show_quick_start, show_shortcuts
from .mixer import Mixer
from .pianoroll import PianoRoll
from .piano import PianoKeyboard
from ..live_capture import (CaptureBuffer, KEY_TO_SEMITONE, BASE_MIDI,
                            quantize_beat, QUANTIZE_GRIDS)
from .playlist import Playlist
from .pluginpicker import PluginPicker
from .sequencer import Sequencer
from .settings import Settings
from .transport import TransportBar

APP_TITLE = "Pulsegrid"
EXPORT_LOOPS = 2


def _apply_theme(root: tk.Tk) -> None:
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    bg, panel, fg, accent = "#0d1117", "#161b22", "#e6edf3", "#58a6ff"
    root.configure(bg=bg)
    style.configure(".", background=panel, foreground=fg, fieldbackground="#0d1117")
    style.configure("TFrame", background=panel)
    style.configure("TLabel", background=panel, foreground=fg)
    style.configure("TButton", background="#21262d", foreground=fg, padding=6)
    style.map("TButton", background=[("active", "#30363d"), ("disabled", "#161b22")])
    style.configure("TSpinbox", fieldbackground="#0d1117", foreground=fg,
                    background="#21262d", arrowcolor=fg)
    style.configure("TMenu", background=panel, foreground=fg)
    style.configure("TPanedwindow", background=panel)
    # LabelFrames (Browser sections, Settings groups) must use the dark
    # palette too -- the clam default border renders light/white on the
    # dark background, reading as hollow "ghost" boxes (Windows artifact
    # report, v0.44.0).
    style.configure("TLabelframe", background=panel, foreground=fg,
                    bordercolor="#30363d")
    style.configure("TLabelframe.Label", background=panel, foreground=fg)
    # Notebook tabs + scrollbars in the dark palette as well.
    style.configure("TNotebook", background=bg, bordercolor="#30363d")
    style.configure("TNotebook.Tab", background="#21262d", foreground=fg,
                    padding=(10, 4))
    style.map("TNotebook.Tab",
              background=[("selected", panel), ("active", "#30363d")])
    style.configure("TScrollbar", background="#21262d", troughcolor=bg,
                    bordercolor=bg, arrowcolor=fg)


def _layout_path() -> str:
    # Platform-appropriate per-user config location:
    # %APPDATA%\pulsegrid on Windows, ~/.config/pulsegrid elsewhere.
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        directory = os.path.join(base, "pulsegrid")
    else:
        directory = os.path.join(os.path.expanduser("~"), ".config", "pulsegrid")
    os.makedirs(directory, exist_ok=True)
    return os.path.join(directory, "layout.json")


class PulsegridApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        _apply_theme(root)
        root.title(APP_TITLE)
        root.geometry("1360x940")
        root.minsize(1024, 720)

        self.project: Project = new_default_project()
        self.path: str | None = None
        self.dirty = False
        self.undo = UndoStack()
        # Explicit invalidation layer (research topic 34): semantic
        # dirty domains are logged here on every structural edit;
        # widgets log geometric dirty rects for partial redraws.
        self.dirty_tracker = DirtyTracker()
        self._dnd = None
        # Decoded-sample metadata cache: asset_id -> load_sample info dict
        # (frames, duration_secs, ...). Warmed by on_add_sample, lazily by
        # _sample_info. The engine registry is the source of truth for
        # audio; this is just display metadata.
        self._sample_cache: dict = {}
        self._ui_scale = 1.0
        self.confirm_on_discard = True
        # Animation refresh rate: "low" (~15 Hz), "medium" (~30 Hz),
        # "high" (~60 Hz). Mirrors FL Studio's General options.
        self._refresh_rate = "medium"
        # Multithreaded rendering (FL-style global switch). Default on.
        self._multithreaded = True
        # Custom CLAP plugin folder (empty = defaults: $CLAP_PATH, ~/.clap,
        # /usr/lib/clap). Applied via the CLAP_PATH env var at scan time.
        self._plugin_dir = ""
        self._rack_visible = True
        self._roll_visible = True
        self._playlist_visible = True
        self._clap_scan_cache = None  # lazy list of scanned CLAP plugins
        self._vst3_scan_cache = None  # lazy list of scanned VST3 plugins

        try:
            self.engine = EngineBridge()
        except EngineError as e:
            show_error(root, "Audio engine failed to start", str(e))
            raise SystemExit(1)
        self._push_to_engine(report_errors=False)

        self._build_menu()
        self.transport = TransportBar(
            root, self.toggle_play, self.stop, self.on_tempo,
            on_toggle_browser=self.toggle_browser,
            on_toggle_mixer=self.toggle_mixer,
            on_record=self._on_record_toggle)
        self.transport.pack(side="top", fill="x")
        self.transport.set_tempo(self.project.tempo)
        self.transport.set_backend(self.engine.backend_name())

        ttk.Separator(root, orient="horizontal").pack(fill="x")

        self._build_pattern_bar()

        # -- workspace: FL-style nested panes -----------------------------
        # Top: menu / transport / pattern bar (above). Middle: a vertical
        # workspace paned holding [left dock | center] above the full-width
        # mixer. Center: playlist on top, Channel Rack beside the
        # Piano Roll / Automation / Piano tabs below it -- all visible at
        # once, like a traditional desktop DAW. Every pane is resizable
        # via its sash and show/hide via the View menu.
        self.workspace_paned = ttk.PanedWindow(root, orient="vertical")
        self.workspace_paned.pack(side="top", fill="both", expand=True,
                                   padx=6, pady=6)

        self.main_paned = ttk.PanedWindow(self.workspace_paned,
                                          orient="horizontal")
        self.workspace_paned.add(self.main_paned, weight=1)

        # Left dock: Browser, Plugin Picker and Settings live as tabs in
        # one docked pane -- panels hide/show inside the main window,
        # never as floating windows.
        self.left_notebook = ttk.Notebook(self.main_paned)
        self.main_paned.add(self.left_notebook, weight=0)

        self.browser = Browser(
            self.left_notebook,
            dnd_start=self.dnd_start,
            on_edit_pattern=self.on_edit_pattern,
            on_pattern_action=self.on_pattern_action,
            on_apply_preset=self.apply_preset_to_track,
            on_add_sample=self.on_add_sample,
            on_sample_action=self.on_sample_action)
        self.left_notebook.add(self.browser, text="Browser")
        self.plugins = PluginPicker(
            self.left_notebook, dnd_start=self.dnd_start)
        self.left_notebook.add(self.plugins, text="Plugins")
        self.settings = Settings(
            self.left_notebook,
            initial_scale=self._ui_scale,
            on_scale=self.set_ui_scale,
            on_reset_layout=self.reset_layout,
            get_confirm=lambda: self.confirm_on_discard,
            on_confirm=self._set_confirm_on_discard,
            get_backend=lambda: self.engine.backend_name(),
            get_config_path=_layout_path,
            get_refresh=lambda: self._refresh_rate,
            on_refresh=self._set_refresh_rate,
            get_plugin_dir=lambda: self._plugin_dir,
            on_plugin_dir=self._set_plugin_dir,
            get_multithreaded=lambda: self._multithreaded,
            on_multithreaded=self._set_multithreaded,
            get_audio_devices=self.engine.audio_devices,
            get_audio_selection=self.engine.audio_selection,
            on_audio_output=self._on_audio_output,
            get_buffer_frames=self.engine.buffer_frames,
            on_buffer_frames=self._on_buffer_frames,
            get_input_devices=self.engine.audio_input_devices,
            get_input_selection=self.engine.audio_input,
            on_audio_input=self._on_audio_input)
        self.left_notebook.add(self.settings, text="Settings")

        center = ttk.Frame(self.main_paned)
        self.main_paned.add(center, weight=1)
        self._center = center

        self.center_paned = ttk.PanedWindow(center, orient="vertical")
        self.center_paned.pack(fill="both", expand=True)

        # Editors row: Channel Rack in its own always-visible pane
        # beside the tabbed Piano Roll / Automation / Piano editors.
        self.editors_row = ttk.PanedWindow(self.center_paned,
                                           orient="horizontal")
        self.center_paned.add(self.editors_row, weight=3)

        self.sequencer = Sequencer(self.editors_row, self.on_toggle_step,
                                   self.on_pitch,
                                   on_channel_menu=self.on_channel_menu)
        self.editors_row.add(self.sequencer, weight=1)

        editors = ttk.Notebook(self.editors_row)
        self.editors_row.add(editors, weight=1)
        self.editors = editors

        self.pianoroll = PianoRoll(editors, self.on_pianoroll_commit)
        editors.add(self.pianoroll, text="Piano Roll")
        self.automation = AutomationEditor(editors, self.on_automation_commit,
                                           self.on_automation_clear,
                                           self.ensure_plugin_registered,
                                           dirty_tracker=self.dirty_tracker)
        editors.add(self.automation, text="Automation")
        # Live play piano + capture (score logger).
        self._capture = CaptureBuffer()
        self._quantize_label = "1/16"
        self._piano_base = BASE_MIDI
        # Song-mode capture: T0 anchor (beats) when rec-armed playback starts.
        # On stop, captured notes become a new Pattern + Clip at T0 (FL-style).
        self._record_t0 = None
        # Pending count-in callback id (root.after handle); cancelled by
        # stop() so a stale count-in can never start playback after the
        # user pressed Stop (v0.42.0 transport-safety fix).
        self._count_in_after_id = None
        self.piano = PianoKeyboard(
            editors,
            on_note_on=self._piano_note_on,
            on_note_off=self._piano_note_off,
            on_dump=self._dump_capture,
            on_clear=self._clear_capture,
            get_quantize=lambda: self._quantize_label,
            on_quantize=self._set_quantize)
        editors.add(self.piano, text="Piano")
        self.sequencer.set_pattern(self.project.current_pattern())
        self.pianoroll.set_pattern(self.project.current_pattern())
        self.automation.set_project(self.project)

        self.playlist = Playlist(
            self.center_paned,
            on_place=self.on_place_clip,
            on_move=self.on_move_clip,
            on_delete=self.on_delete_clip,
            on_edit_pattern=self.on_edit_pattern,
            on_add_track=self.on_add_track,
            on_clip_menu=self.on_clip_menu,
            on_edit_audio_clip=self.on_edit_audio_clip,
            peaks_provider=self._clip_peaks,
            on_track_menu=self.on_track_menu,
            dirty_tracker=self.dirty_tracker,
        )
        # Playlist above the editors row (FL-style arrangement-first
        # layout).
        self.center_paned.insert(0, self.playlist, weight=2)
        self.playlist.set_project(self.project)

        self.mixer = Mixer(
            self.workspace_paned,
            on_volume=self.on_mixer_volume,
            on_pan=self.on_mixer_pan,
            on_mute=self.on_mixer_mute,
            on_add_effect=self.on_mixer_add_effect,
            on_effect_param=self.on_mixer_effect_param,
            on_remove_effect=self.on_mixer_remove_effect,
            on_add_plugin=self.on_mixer_add_plugin,
            on_edit_plugin=self.on_mixer_edit_plugin,
            on_open_gui=self.on_mixer_open_gui,
            on_set_generator=self.on_mixer_set_generator,
            on_edit_generator=self.on_mixer_edit_generator,
            on_clear_generator=self.on_mixer_clear_generator,
            on_open_generator_gui=self.on_mixer_open_generator_gui,
            on_add_send=self.on_mixer_add_send,
            on_send_amount=self.on_mixer_send_amount,
            on_send_options=self.on_mixer_send_options,
            on_remove_send=self.on_mixer_remove_send,
            on_vel_track=self.on_mixer_vel_track,
            on_key_track=self.on_mixer_key_track,
            on_save_chain_preset=self.on_mixer_save_chain_preset,
            on_load_chain_preset=self.on_mixer_load_chain_preset,
            on_save_track_preset=self.on_mixer_save_track_preset,
            on_load_track_preset=self.on_mixer_load_track_preset,
            on_add_layer=self.on_mixer_add_layer,
            on_remove_layer=self.on_mixer_remove_layer,
            on_set_layer_mode=self.on_mixer_set_layer_mode,
            on_toggle_layer=self.on_mixer_toggle_layer,
            on_set_layer_gain=self.on_mixer_set_layer_gain,
            on_set_layer_pitch=self.on_mixer_set_layer_pitch,
            on_set_output=self.on_mixer_set_output,
            on_route_only=self.on_mixer_route_only,
            on_add_modulator=self.on_mixer_add_modulator,
            on_edit_modulator=self.on_mixer_edit_modulator,
            on_remove_modulator=self.on_mixer_remove_modulator,
            on_add_assignment=self.on_mixer_add_assignment,
            on_remove_assignment=self.on_mixer_remove_assignment,
        )
        self.workspace_paned.add(self.mixer, weight=0)
        self.mixer.set_project(self.project)
        self.browser.set_project(self.project)

        self.status_var = tk.StringVar(
            value="Ready -- drag a sound from the Browser, click steps to edit, Space to play.")
        status = ttk.Label(root, textvariable=self.status_var, anchor="w",
                           padding=(10, 6), font=("", 9))
        status.pack(side="bottom", fill="x")

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._bind_keys()
        self._apply_saved_layout()
        self._poll_position()
        self._update_title()

    # -- UI construction ------------------------------------------------

    def _build_pattern_bar(self) -> None:
        bar = ttk.Frame(self.root)
        bar.pack(side="top", fill="x", padx=10, pady=(8, 2))
        ttk.Label(bar, text="Pattern:", font=("", 10, "bold")).pack(side="left")
        self.pattern_combo = ttk.Combobox(bar, state="readonly", width=26)
        self.pattern_combo.pack(side="left", padx=8)
        self.pattern_combo.bind("<<ComboboxSelected>>", self._on_pattern_selected)
        ttk.Button(bar, text="+ Add", command=self.pattern_add, width=7).pack(side="left")
        ttk.Button(bar, text="Rename", command=self.pattern_rename, width=8).pack(
            side="left", padx=4)
        ttk.Button(bar, text="Delete", command=lambda: self.pattern_delete(), width=7).pack(
            side="left")
        self._refresh_pattern_combo()

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="New", accelerator="Ctrl+N",
                              command=self.file_new)
        file_menu.add_command(label="Open...", accelerator="Ctrl+O",
                              command=self.file_open)
        file_menu.add_command(label="Save", accelerator="Ctrl+S",
                              command=self.file_save)
        file_menu.add_command(label="Save As...", command=self.file_save_as)
        file_menu.add_separator()
        file_menu.add_command(label="Export arrangement to WAV...", accelerator="Ctrl+E",
                              command=self.export_wav)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.on_close)
        menubar.add_cascade(label="File", menu=file_menu)

        edit_menu = tk.Menu(menubar, tearoff=False)
        edit_menu.add_command(label="Undo", accelerator="Ctrl+Z",
                              command=self.edit_undo)
        edit_menu.add_command(label="Redo", accelerator="Ctrl+Y",
                              command=self.edit_redo)
        edit_menu.add_separator()
        edit_menu.add_command(label="Clear current pattern", command=self.edit_clear)
        menubar.add_cascade(label="Edit", menu=edit_menu)

        view_menu = tk.Menu(menubar, tearoff=False)
        view_menu.add_command(label="Channel Rack",
                              command=self.toggle_channel_rack)
        view_menu.add_command(label="Piano Roll",
                              command=self.toggle_piano_roll)
        view_menu.add_command(label="Playlist",
                              command=self.toggle_playlist)
        view_menu.add_command(label="Mixer", accelerator="Ctrl+M",
                              command=self.toggle_mixer)
        view_menu.add_command(label="Browser", accelerator="Ctrl+B",
                              command=self.toggle_browser)
        view_menu.add_command(label="Plugin Picker", accelerator="Ctrl+P",
                              command=self.toggle_plugins)
        view_menu.add_command(label="Settings",
                              command=self.toggle_settings)
        view_menu.add_separator()
        scale_menu = tk.Menu(view_menu, tearoff=False)
        for pct in (100, 125, 150):
            scale_menu.add_command(label=f"{pct}%",
                                   command=lambda p=pct: self.set_ui_scale(p / 100))
        view_menu.add_cascade(label="UI scale", menu=scale_menu)
        view_menu.add_separator()
        view_menu.add_command(label="Reset layout", command=self.reset_layout)
        view_menu.add_command(label="Debug -- choke verification",
                              command=self.show_debug_window)
        menubar.add_cascade(label="View", menu=view_menu)

        project_menu = tk.Menu(menubar, tearoff=False)
        self._truncate_notes_var = tk.BooleanVar(value=False)
        project_menu.add_checkbutton(
            label="Truncate notes at clip boundaries",
            variable=self._truncate_notes_var,
            command=self.project_toggle_truncate_notes)
        menubar.add_cascade(label="Project", menu=project_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="Quick start guide",
                              command=lambda: show_quick_start(self.root))
        help_menu.add_command(label="Keyboard shortcuts",
                              command=lambda: show_shortcuts(self.root))
        help_menu.add_command(label="About Pulsegrid",
                              command=lambda: show_about(self.root))
        menubar.add_cascade(label="Help", menu=help_menu)

    def _bind_keys(self) -> None:
        self.root.bind("<space>", self._on_space)
        self.root.bind("<Control-n>", lambda _e: self.file_new())
        self.root.bind("<Control-o>", lambda _e: self.file_open())
        self.root.bind("<Control-s>", lambda _e: self.file_save())
        self.root.bind("<Control-e>", lambda _e: self.export_wav())
        self.root.bind("<Control-b>", lambda _e: self.toggle_browser())
        self.root.bind("<Control-p>", lambda _e: self.toggle_plugins())
        self.root.bind("<Control-m>", lambda _e: self.toggle_mixer())
        self.root.bind("<Control-z>", lambda _e: self.edit_undo())
        self.root.bind("<Control-y>", lambda _e: self.edit_redo())
        # Typing-keyboard piano (active when the Piano tab is selected).
        self.root.bind("<KeyPress>", self._on_piano_key, add="+")
        self.root.bind("<KeyRelease>", self._on_piano_key_release, add="+")

    def _piano_active(self, event=None) -> bool:
        """True if typing keys should play the piano."""
        # Not while typing in a text field.
        if event is not None:
            cls = event.widget.winfo_class()
            if cls in ("TEntry", "TCombobox", "TSpinbox", "Text",
                       "Entry", "Spinbox"):
                return False
        # Only when the Piano tab is the selected editor tab.
        try:
            return self.editors.select() == str(self.piano)
        except Exception:
            return False

    def _on_piano_key(self, event) -> None:
        if not self._piano_active(event):
            return
        keysym = event.keysym.lower()
        if keysym not in KEY_TO_SEMITONE:
            return
        # Ignore auto-repeat.
        if getattr(event, "repeat", 0):
            return
        pitch = self._piano_base + KEY_TO_SEMITONE[keysym]
        if 0 <= pitch <= 127:
            self._piano_note_on(pitch, 0.9)

    def _on_piano_key_release(self, event) -> None:
        if not self._piano_active(event):
            return
        keysym = event.keysym.lower()
        if keysym not in KEY_TO_SEMITONE:
            return
        pitch = self._piano_base + KEY_TO_SEMITONE[keysym]
        if 0 <= pitch <= 127:
            self._piano_note_off(pitch)

    def _on_space(self, event) -> None:
        # Don't hijack space while typing in a text field or open combo box.
        cls = event.widget.winfo_class()
        if cls in ("TEntry", "TCombobox", "TSpinbox", "Text", "Entry", "Spinbox"):
            return
        self.toggle_play()

    # -- helpers ----------------------------------------------------------

    def _update_title(self) -> None:
        name = self.project.name
        if self.path:
            name = os.path.basename(self.path)
        star = " *" if self.dirty else ""
        self.root.title(f"{APP_TITLE} -- {name}{star}")

    def _status(self, msg: str) -> None:
        self.status_var.set(msg)

    def _mark_dirty(self) -> None:
        self.dirty = True
        self._update_title()

    def _project_dir(self) -> str | None:
        """Directory of the current project file (None if unsaved)."""
        if self.path:
            return os.path.dirname(os.path.abspath(self.path))
        return None

    def _push_to_engine(self, report_errors: bool = True) -> None:
        try:
            self.engine.push_project(self.project, self._project_dir())
        except EngineError as e:
            if report_errors:
                show_error(self.root, "Engine sync failed", str(e))

    def _confirm_discard(self) -> bool:
        if not self.dirty or not self.confirm_on_discard:
            return True
        return messagebox.askyesno(
            "Unsaved changes",
            "You have unsaved changes. Discard them?",
            parent=self.root,
        )

    def _refresh_pattern_combo(self) -> None:
        names = [p.name for p in self.project.patterns]
        self.pattern_combo["values"] = names
        try:
            idx = next(i for i, p in enumerate(self.project.patterns)
                       if p.id == self.project.selected_pattern)
        except StopIteration:
            idx = 0
        if names:
            self.pattern_combo.current(idx)

    def _refresh_all(self) -> None:
        """Re-sync every view with the current project snapshot."""
        self._refresh_panels("menu", "debug", "sequencer", "pianoroll",
                             "automation", "playlist", "mixer", "browser")

    def _refresh_panels(self, *names: str) -> None:
        """Re-sync only the named panels (avoids full-UI flashing).

        Valid names: "menu", "debug", "sequencer", "pianoroll",
        "automation", "playlist", "mixer", "browser". Every refresh also
        pushes to the engine and updates the title (cheap, non-visual).
        """
        if "menu" in names:
            self._sync_project_menu()
            self._refresh_pattern_combo()
        if "debug" in names and getattr(self, "_debug_window", None) is not None:
            try:
                self._debug_window.refresh_tracks()
            except tk.TclError:
                self._debug_window = None
        pattern = self.project.current_pattern()
        if "sequencer" in names:
            self.sequencer.set_pattern(pattern)
        if "pianoroll" in names:
            self.pianoroll.set_pattern(pattern, self.pianoroll.channel_id)
        if "automation" in names:
            self.automation.set_project(self.project, *self.automation.selection)
        if "playlist" in names:
            self.playlist.set_project(self.project)
        # Targeted lane refresh: ("playlist_lane", track_id[, tags, op]).
        # op "partial": redraw only the tagged clips (dirty-region
        # invalidation); op "delete": remove the tagged items. No tags:
        # full lane redraw.
        for name in names:
            if isinstance(name, tuple) and name[0] == "playlist_lane":
                self.playlist._project = self.project
                tags = name[2] if len(name) > 2 else None
                op = name[3] if len(name) > 3 else "partial"
                if tags and op == "delete":
                    self.playlist.delete_clips(name[1], tags)
                elif tags:
                    self.playlist.refresh_clips(name[1], tags)
                else:
                    self.playlist.refresh_lane(name[1])
        if "mixer" in names:
            self.mixer.set_project(self.project)
        if "browser" in names:
            self.browser.set_project(self.project)
        self._push_to_engine()
        self._update_title()

    def _structural_edit(self, label: str, mutate, panels=None) -> None:
        """Undoable edit via before/after snapshots.

        Snapshot-based (rather than fine-grained closures) so undo can never
        touch a stale object after the project was replaced.

        panels: list of panel names to refresh (see _refresh_panels).
        None (default) refreshes everything -- pass a targeted list to
        avoid flashing unrelated panels.
        """
        before = self.project.snapshot()
        mutate(self.project)
        try:
            self.project.validate()
        except ProjectError as e:
            self.project = before
            show_error(self.root, "Edit failed", str(e))
            return
        after = self.project.snapshot()

        def do():
            self.project = after.snapshot()
            if panels is None:
                self._refresh_all()
            else:
                self._refresh_panels(*panels)

        def undo():
            self.project = before.snapshot()
            if panels is None:
                self._refresh_all()
            else:
                self._refresh_panels(*panels)

        self.undo.execute(label, do, undo)
        self._mark_dirty()
        # Semantic invalidation log (Level 2, FL HW_Dirty_* analog).
        if panels is None:
            for domain in DirtyDomain:
                self.dirty_tracker.whole_domain(
                    domain, note=f"structural edit: {label}")
        else:
            for p in panels:
                if isinstance(p, tuple):
                    continue  # widget logs the geometric event itself
                for domain in _PANEL_DOMAINS.get(p, ()):
                    self.dirty_tracker.whole_domain(
                        domain, note=f"structural edit: {label}")

    def project_toggle_truncate_notes(self) -> None:
        """Project menu: toggle 'Truncate notes at clip boundaries'."""
        value = bool(self._truncate_notes_var.get())

        def mutate(proj):
            proj.truncate_notes = value

        self._structural_edit(
            "Truncate notes at clip boundaries" if value
            else "Let notes ring past clip boundaries",
            mutate)
        self._sync_project_menu()

    def show_debug_window(self) -> None:
        """Open (or focus) the Debug window for silent choke verification."""
        if getattr(self, "_debug_window", None) is not None:
            try:
                self._debug_window.lift()
                self._debug_window.focus_force()
                return
            except tk.TclError:
                pass
        self._debug_window = DebugWindow(
            self.root,
            get_bridge=lambda: self.engine,
            get_project=lambda: self.project,
            get_dirty_tracker=lambda: self.dirty_tracker,
        )
        self._debug_window.refresh_tracks()

    def _sync_project_menu(self) -> None:
        """Reflect the loaded project's settings in the Project menu."""
        self._truncate_notes_var.set(bool(self.project.truncate_notes))

    # -- layout persistence ---------------------------------------------------

    def _apply_saved_layout(self) -> None:
        try:
            with open(_layout_path(), "r", encoding="utf-8") as f:
                layout = json.load(f)
        except (OSError, ValueError):
            # No saved layout: use FL-style first-run proportions once
            # the window exists.
            self.root.after_idle(self._apply_default_sashes)
            return
        if not isinstance(layout, dict):
            self.root.after_idle(self._apply_default_sashes)
            return
        scale = layout.get("ui_scale", 1.0)
        if scale in (1.0, 1.25, 1.5):
            self.set_ui_scale(scale, quiet=True)
        # "left_visible" replaced the older "browser" key; honor both.
        left_visible = layout.get("left_visible", layout.get("browser", True))
        if not left_visible:
            self._set_panel_visible(self.left_notebook, self.main_paned,
                                    self._center, False)
        else:
            tab = layout.get("left_tab", 0)
            if isinstance(tab, int) and 0 <= tab < self.left_notebook.index("end"):
                self.left_notebook.select(tab)
        if not layout.get("mixer", True):
            self._set_panel_visible(self.mixer, self.workspace_paned,
                                    self.main_paned, False)
        confirm = layout.get("confirm_on_discard", True)
        self._set_confirm_on_discard(bool(confirm), quiet=True)
        self._set_refresh_rate(layout.get("refresh_rate", "medium"),
                               quiet=True)
        self._set_plugin_dir(layout.get("plugin_dir", ""), quiet=True)
        # Restore the chosen audio output (v0.42.0). A device that has
        # vanished since is reported, not fatal: the engine falls back to
        # the null sink with the reason in the backend description.
        host_id = layout.get("audio_host")
        device_name = layout.get("audio_device")
        if host_id or device_name:
            try:
                self.engine.set_audio_output(host_id, device_name)
            except EngineError:
                pass
            self.settings.sync_audio_selection(
                self.engine.audio_selection())
        # Restore the buffer size (v0.42.0); invalid values are ignored.
        buffer_frames = layout.get("audio_buffer")
        if isinstance(buffer_frames, int) and buffer_frames > 0:
            try:
                self.engine.set_buffer_frames(buffer_frames)
            except EngineError:
                pass
            self.settings.sync_buffer_frames(self.engine.buffer_frames())
        # Restore the input device choice (v0.43.0).
        in_host = layout.get("audio_input_host")
        in_device = layout.get("audio_input_device")
        if in_host or in_device:
            try:
                self.engine.set_audio_input(in_host, in_device)
            except EngineError:
                pass
            self.settings.sync_input_selection(self.engine.audio_input())
        if not layout.get("channel_rack", True):
            self.toggle_channel_rack()
        if not layout.get("piano_roll", True):
            self.toggle_piano_roll()
        if not layout.get("playlist", True):
            self.toggle_playlist()
        self.root.update_idletasks()
        for key, paned in (("workspace_sash", self.workspace_paned),
                           ("main_sash", self.main_paned),
                           ("center_sash", self.center_paned),
                           ("editors_row_sash", self.editors_row)):
            coords = layout.get(key)
            if isinstance(coords, list):
                try:
                    for i, pos in enumerate(coords):
                        # Very old files stored [x, y] pairs (from the tk
                        # API); take the first coordinate.
                        if isinstance(pos, (list, tuple)):
                            pos = pos[0] if pos else 0
                        paned.sashpos(i, int(pos))
                except (tk.TclError, ValueError, TypeError):
                    pass

    def _apply_default_sashes(self) -> None:
        """First-run sash positions: Channel Rack wide enough to show
        most of the 16-step grid, Playlist above the editors, Mixer as
        a full-width bottom strip."""
        try:
            rw = self.editors_row.winfo_width()
            ch = self.center_paned.winfo_height()
            wh = self.workspace_paned.winfo_height()
        except tk.TclError:
            return
        if rw < 200 or ch < 200 or wh < 200:
            # Window not laid out yet; retry shortly.
            self.root.after(100, self._apply_default_sashes)
            return
        try:
            # ttk.Panedwindow uses sashpos(index, pos): one coordinate
            # (x for horizontal panes, y for vertical).
            self.editors_row.sashpos(0, int(rw * 0.52))
            self.center_paned.sashpos(0, int(ch * 0.38))
            self.workspace_paned.sashpos(0, int(wh * 0.70))
        except tk.TclError:
            pass

    def _save_layout(self) -> None:
        def sash_coords(paned):
            try:
                return [int(paned.sashpos(i))
                        for i in range(len(paned.panes()) - 1)]
            except tk.TclError:
                return []

        host_id, device_name = self.engine.audio_selection()
        try:
            buffer_frames = self.engine.buffer_frames()
        except EngineError:
            buffer_frames = None
        layout = {
            "left_visible": str(self.left_notebook) in self.main_paned.panes(),
            "left_tab": self.left_notebook.index("current"),
            "mixer": str(self.mixer) in self.workspace_paned.panes(),
            "channel_rack": self._rack_visible,
            "piano_roll": self._roll_visible,
            "playlist": self._playlist_visible,
            "ui_scale": self._ui_scale,
            "confirm_on_discard": self.confirm_on_discard,
            "refresh_rate": self._refresh_rate,
            "plugin_dir": self._plugin_dir,
            "audio_host": host_id,
            "audio_device": device_name,
            "audio_buffer": buffer_frames,
            "audio_input_host": self.engine.audio_input()[0],
            "audio_input_device": self.engine.audio_input()[1],
            "workspace_sash": sash_coords(self.workspace_paned),
            "main_sash": sash_coords(self.main_paned),
            "center_sash": sash_coords(self.center_paned),
            "editors_row_sash": sash_coords(self.editors_row),
        }
        try:
            with open(_layout_path(), "w", encoding="utf-8") as f:
                json.dump(layout, f, indent=2)
        except OSError:
            pass  # layout persistence is best-effort

    def _set_panel_visible(self, panel, paned, neighbor, visible: bool) -> None:
        panes = paned.panes()
        if visible and str(panel) not in panes:
            # Reinsert before the neighbor pane to keep the original order.
            try:
                paned.add(panel, before=neighbor)
            except tk.TclError:
                paned.add(panel)
        elif not visible and str(panel) in panes:
            paned.forget(panel)

    def _toggle_left_tab(self, index: int, name: str) -> None:
        """Show the left dock on `index`, or hide it if already there."""
        in_panes = str(self.left_notebook) in self.main_paned.panes()
        if in_panes and self.left_notebook.index("current") == index:
            self._set_panel_visible(self.left_notebook, self.main_paned,
                                    self._center, False)
            self._status(f"{name} hidden.")
        else:
            self._set_panel_visible(self.left_notebook, self.main_paned,
                                    self._center, True)
            self.left_notebook.select(index)
            self._status(f"{name} shown.")

    def toggle_browser(self) -> None:
        self._toggle_left_tab(0, "Browser")

    def toggle_plugins(self) -> None:
        self._toggle_left_tab(1, "Plugin Picker")

    def toggle_settings(self) -> None:
        self._toggle_left_tab(2, "Settings")

    def toggle_mixer(self) -> None:
        visible = str(self.mixer) not in self.workspace_paned.panes()
        self._set_panel_visible(self.mixer, self.workspace_paned,
                                self.main_paned, visible)
        self._status(f"Mixer {'shown' if visible else 'hidden'}.")

    def _editor_insert_pos(self, desired: int) -> int:
        """Tab position that keeps the canonical Piano Roll/Automation/
        Piano order no matter which tabs were hidden first. (The Channel
        Rack is a separate pane now, not a tab.)"""
        return min(desired, self.editors.index("end"))

    def toggle_channel_rack(self) -> None:
        self._rack_visible = not self._rack_visible
        panes = self.editors_row.panes()
        if self._rack_visible and str(self.sequencer) not in panes:
            # Keep the rack left of the tabbed editors.
            self.editors_row.insert(0, self.sequencer, weight=1)
        elif not self._rack_visible and str(self.sequencer) in panes:
            self.editors_row.forget(self.sequencer)
        self._status(f"Channel Rack {'shown' if self._rack_visible else 'hidden'}.")

    def toggle_piano_roll(self) -> None:
        self._roll_visible = not self._roll_visible
        if self._roll_visible:
            self.editors.insert(self._editor_insert_pos(0), self.pianoroll,
                                text="Piano Roll")
        else:
            self.editors.forget(self.pianoroll)
        self._status(f"Piano Roll {'shown' if self._roll_visible else 'hidden'}.")

    def toggle_playlist(self) -> None:
        self._playlist_visible = not self._playlist_visible
        if self._playlist_visible:
            # Back on top, above the editors row.
            self.center_paned.insert(0, self.playlist, weight=2)
        else:
            self.center_paned.forget(self.playlist)
        self._status(f"Playlist {'shown' if self._playlist_visible else 'hidden'}.")

    def _set_confirm_on_discard(self, value: bool, quiet: bool = False) -> None:
        self.confirm_on_discard = value
        self.settings.sync_confirm(value)
        if not quiet:
            self._status("Unsaved-changes prompt "
                         f"{'on' if value else 'off'}.")

    # Poll intervals (ms) per refresh-rate mode.
    _REFRESH_INTERVALS = {"low": 66, "medium": 33, "high": 16}

    def _set_refresh_rate(self, mode: str, quiet: bool = False) -> None:
        if mode not in self._REFRESH_INTERVALS:
            mode = "medium"
        self._refresh_rate = mode
        self.settings.sync_refresh(mode)
        if not quiet:
            self._status(f"Animation refresh rate: {mode}.")

    def _set_multithreaded(self, enabled: bool) -> None:
        """Enable/disable multithreaded rendering (FL-style)."""
        self._multithreaded = bool(enabled)
        try:
            self.engine.set_multithreaded(self._multithreaded)
        except Exception:
            pass  # Engine not ready yet.
        self._status(f"Multithreading {'enabled' if enabled else 'disabled'}.")

    def _on_audio_output(self, host_id: str | None,
                         device_name: str | None) -> None:
        """Apply a new audio output choice from Settings (v0.42.0).

        Switches the backend live: if the transport is playing it keeps
        playing on the new device (position preserved); otherwise the new
        device activates on the next Play. The choice persists in
        layout.json. Failures are reported, never silent.
        """
        try:
            self.engine.set_audio_output(host_id, device_name)
            self.engine.reapply_audio_backend()
        except EngineError as e:
            show_error(self.root, "Audio device switch failed", str(e))
            self.settings.sync_audio_selection(self.engine.audio_selection())
            return
        self._save_layout()
        self._sync_audio_status()
        label = (f"{host_id}: {device_name}" if host_id or device_name
                 else "System default")
        self._status(f"Audio output -> {label}.")

    def _sync_audio_status(self) -> None:
        """Refresh the Settings audio status line with the live backend."""
        try:
            self.settings.refresh_audio_status(
                f"Active: {self.engine.backend_name()}")
        except Exception:
            pass

    def _on_buffer_frames(self, frames: int | None) -> None:
        """Apply a new audio buffer size from Settings (v0.42.0).

        The real-time lever both FL Studio and LMMS expose: a bigger
        buffer gives the CPU more time per block (fewer underruns) at the
        cost of latency. Applies live via reapply_audio_backend().
        """
        try:
            self.engine.set_buffer_frames(frames)
            self.engine.reapply_audio_backend()
        except EngineError as e:
            show_error(self.root, "Buffer size change failed", str(e))
            self.settings.sync_buffer_frames(self.engine.buffer_frames())
            return
        self._save_layout()
        self._sync_audio_status()
        label = f"{frames} samples" if frames else "driver default"
        self._status(f"Audio buffer -> {label}.")

    def _on_audio_input(self, host_id: str | None,
                        device_name: str | None) -> None:
        """Record the user's input-device choice (v0.43.0).

        FL Studio exposes a separate Input selector; we mirror it. The
        choice is stored + persisted for future recording support -- no
        input stream is opened in this version.
        """
        try:
            self.engine.set_audio_input(host_id, device_name)
        except EngineError as e:
            show_error(self.root, "Input device change failed", str(e))
            self.settings.sync_input_selection(self.engine.audio_input())
            return
        self._save_layout()
        label = device_name or "System default"
        self._status(f"Audio input -> {label} (not captured in this version).")

    def _set_plugin_dir(self, path: str, quiet: bool = False) -> None:
        path = (path or "").strip()
        self._plugin_dir = path
        self.settings.sync_plugin_dir(path)
        # The Rust scanner checks CLAP_PATH first, so exporting it here
        # makes the custom folder take effect on the next scan.
        if path:
            os.environ["CLAP_PATH"] = path
        else:
            os.environ.pop("CLAP_PATH", None)
        if not quiet:
            self._status(f"CLAP plugin folder: {path or 'defaults'}.")

    def set_ui_scale(self, factor: float, quiet: bool = False) -> None:
        self._ui_scale = factor
        try:
            self.root.tk.call("tk", "scaling", factor)
        except tk.TclError:
            pass
        self.settings.sync_scale(factor)
        if not quiet:
            self._status(f"UI scale {int(factor * 100)}%.")

    def reset_layout(self) -> None:
        self._set_panel_visible(self.left_notebook, self.main_paned,
                                self._center, True)
        self.left_notebook.select(0)
        if not self._rack_visible:
            self.toggle_channel_rack()
        if not self._roll_visible:
            self.toggle_piano_roll()
        if not self._playlist_visible:
            self.toggle_playlist()
        self._set_panel_visible(self.mixer, self.workspace_paned,
                                self.main_paned, True)
        self.set_ui_scale(1.0, quiet=True)
        self._status("Layout reset to defaults.")

    # -- drag and drop ------------------------------------------------------------

    def dnd_start(self, kind: str, payload: str, label: str, event) -> None:
        """Begin a drag. `kind` is 'instrument', 'pattern', 'effect',
        'preset' or 'sample'."""
        if self._dnd is not None:
            return
        ghost = tk.Toplevel(self.root)
        ghost.wm_overrideredirect(True)
        try:
            ghost.attributes("-topmost", True)
        except tk.TclError:
            pass
        tk.Label(ghost, text=label, bg="#58a6ff", fg="#0d1117",
                 padx=10, pady=5, font=("", 10, "bold")).pack()
        self._dnd = {"kind": kind, "payload": payload, "ghost": ghost}
        self._dnd_move(event)
        self._dnd_motion_id = self.root.bind("<Motion>", self._dnd_move, add="+")
        self._dnd_release_id = self.root.bind("<ButtonRelease-1>", self._dnd_end,
                                              add="+")

    def _dnd_target(self, x_root: int, y_root: int):
        widget = self.root.winfo_containing(x_root, y_root)
        while widget is not None and widget is not self.root:
            channel_id = getattr(widget, "_pulsegrid_channel_id", None)
            if channel_id is not None:
                return ("channel", channel_id, widget)
            track_id = getattr(widget, "_pulsegrid_track_id", None)
            if track_id is not None:
                return ("track", track_id, widget)
            fx_track_id = getattr(widget, "_pulsegrid_fx_track_id", None)
            if fx_track_id is not None:
                return ("fxstrip", fx_track_id, widget)
            widget = widget.master
        return (None, None, None)

    def _dnd_move(self, event) -> None:
        if self._dnd is None:
            return
        # Offset the ghost so the cursor point stays hittable.
        self._dnd["ghost"].wm_geometry(f"+{event.x_root + 16}+{event.y_root + 16}")
        kind, payload = self._dnd["kind"], self._dnd["payload"]
        target, tid, widget = self._dnd_target(event.x_root, event.y_root)
        if target == "channel" and kind == "instrument":
            try:
                ch = next(c for c in self.project.current_pattern().channels
                          if c.id == tid)
                self._status(f"Drop to make '{ch.name}' a {payload}.")
            except StopIteration:
                self._status("")
        elif target == "track" and kind == "pattern":
            beat = self.playlist.beat_at_point(widget, event.x_root)
            try:
                name = self.project.pattern_by_id(payload).name
            except ProjectError:
                name = payload
            self._status(f"Drop to place '{name}' at bar {beat // 4 + 1}, "
                         f"beat {beat % 4 + 1}.")
        elif target == "track" and kind == "sample":
            beat = self.playlist.beat_at_point(widget, event.x_root)
            try:
                name = self.project.sample_by_id(payload).name or payload
            except ProjectError:
                name = payload
            self._status(f"Drop to place audio clip '{name}' at "
                         f"bar {beat // 4 + 1}, beat {beat % 4 + 1}.")
        elif target == "fxstrip" and kind == "effect":
            try:
                tname = next(t for t in self.project.tracks
                             if t.id == tid).name
                self._status(f"Drop to add {payload} to '{tname}'.")
            except StopIteration:
                self._status("")
        elif target == "fxstrip" and kind == "preset":
            try:
                tname = next(t for t in self.project.tracks
                             if t.id == tid).name
                self._status(f"Drop to apply preset '{payload}' to '{tname}'.")
            except StopIteration:
                self._status("")
        else:
            self._status("Drop onto a channel (instruments), a playlist lane "
                         "(patterns), or a mixer strip (effects, presets).")

    def _dnd_end(self, event) -> None:
        if self._dnd is None:
            return
        kind, payload = self._dnd["kind"], self._dnd["payload"]
        # The ghost Toplevel must die even if targeting or the drop
        # itself raises -- a leaked overrideredirect window is a permanent
        # on-screen artifact (Windows ghost-window reports, v0.44.0).
        try:
            target, tid, widget = self._dnd_target(event.x_root, event.y_root)
        finally:
            try:
                self._dnd["ghost"].destroy()
            except tk.TclError:
                pass
            self._dnd = None
            # Remove only the handlers this drag added.
            for seq, fid in (("<Motion>", self._dnd_motion_id),
                             ("<ButtonRelease-1>", self._dnd_release_id)):
                try:
                    self.root.unbind(seq, fid)
                except tk.TclError:
                    pass
        if target == "channel" and kind == "instrument":
            self._drop_instrument(tid, payload)
        elif target == "track" and kind == "pattern":
            beat = self.playlist.beat_at_point(widget, event.x_root)
            self._drop_pattern(tid, beat, payload)
        elif target == "track" and kind == "sample":
            beat = self.playlist.beat_at_point(widget, event.x_root)
            self._drop_sample(tid, beat, payload)
        elif target == "fxstrip" and kind == "effect":
            self.on_mixer_add_effect(tid, payload)
        elif target == "fxstrip" and kind == "preset":
            self.apply_preset_to_track(tid, payload)
        else:
            self._status("Drag cancelled.")

    def _drop_instrument(self, channel_id: str, instrument: str) -> None:
        pattern = self.project.current_pattern()
        try:
            ch = next(c for c in pattern.channels if c.id == channel_id)
        except StopIteration:
            return
        if ch.instrument == instrument:
            self._status(f"{ch.name} is already a {instrument}.")
            return

        def mutate(project, pid=pattern.id):
            pat = project.pattern_by_id(pid)
            c = next(x for x in pat.channels if x.id == channel_id)
            c.instrument = instrument

        self._structural_edit(f"{ch.name} -> {instrument}", mutate)
        self._status(f"{ch.name} is now a {instrument}.")

    def _drop_pattern(self, track_id: str, beat: int, pattern_id: str) -> None:
        try:
            pattern = self.project.pattern_by_id(pattern_id)
        except ProjectError:
            return

        new_idx = len(next(t for t in self.project.tracks
                             if t.id == track_id).clips)

        def mutate(project, tid=track_id):
            track = next(t for t in project.tracks if t.id == tid)
            track.clips.append(Clip(pattern_id=pattern_id, start_beat=beat,
                                    bars=pattern.bars()))

        where = f"bar {beat // 4 + 1}, beat {beat % 4 + 1}"
        self._structural_edit(
            f"place '{pattern.name}' at {where}", mutate,
            panels=[("playlist_lane", track_id, [f"clip-{new_idx}"],
                     "partial"), "automation"])
        self._status(f"Placed '{pattern.name}' at {where}.")

    # -- transport ----------------------------------------------------------

    def _on_record_toggle(self) -> None:
        armed = self.transport.is_rec_armed()
        if armed:
            self._status("Record armed -- press Play for count-in, then perform.")
        else:
            self._status("Record disarmed.")

    def toggle_play(self) -> None:
        try:
            if self.engine.is_playing():
                self.stop()
            else:
                if self.transport.is_rec_armed():
                    self._start_count_in()
                else:
                    self._begin_playback()
        except EngineError as e:
            show_error(self.root, "Playback failed", str(e))

    def _start_count_in(self) -> None:
        """1-bar count-in, then start playback + capture."""
        self._capture.clear()
        self.piano.set_count(0)
        self._status("Count-in... get ready.")
        # 1 bar at current tempo.
        ms_per_beat = 60000.0 / self.project.tempo
        # Defensive: never leave two count-ins pending.
        self._cancel_count_in()
        self._count_in_after_id = self.root.after(
            int(ms_per_beat * 4), self._on_count_in_done)

    def _on_count_in_done(self) -> None:
        """Count-in elapsed: begin playback (unless stop() cancelled us)."""
        self._count_in_after_id = None
        self._begin_playback()

    def _cancel_count_in(self) -> None:
        """Drop a pending count-in so it can never start playback late."""
        if self._count_in_after_id is not None:
            try:
                self.root.after_cancel(self._count_in_after_id)
            except Exception:
                pass
            self._count_in_after_id = None

    def _begin_playback(self) -> None:
        self._push_to_engine()
        # Song-mode capture: remember T0 (placement anchor) before play.
        if self.transport.is_rec_armed():
            try:
                self._record_t0 = float(self.engine.position_beats())
            except Exception:
                self._record_t0 = 0.0
        self.engine.play()
        self.transport.set_backend(self.engine.backend_name())
        self.transport.set_playing(True)
        self.settings.refresh_backend(self.engine.backend_name())
        self._sync_audio_status()
        if self.transport.is_rec_armed():
            self._status("Recording -- play the piano!")
        else:
            self._status(f"Playing -- {self.engine.backend_name()}")

    def stop(self) -> None:
        # A pending count-in must never fire after the user pressed Stop.
        self._cancel_count_in()
        try:
            self.engine.stop()
        except EngineError as e:
            show_error(self.root, "Stop failed", str(e))
        self.transport.set_playing(False)
        self.sequencer.set_playhead(None)
        self.pianoroll.set_playhead(None)
        self.playlist.set_playhead(None)
        self.transport.set_position("Bar 1 | Beat 1.0")
        # Song-mode auto-place: captured notes -> Pattern + Clip at T0.
        if self._record_t0 is not None:
            t0 = self._record_t0
            self._record_t0 = None
            try:
                if self._capture.notes:
                    self._auto_place_capture(t0)
                else:
                    self._status("Stopped -- nothing captured.")
                    return
            except Exception as e:
                # The take failed to place; never let that break the
                # transport -- report it on the status line and stop clean.
                self._status(f"Stopped -- could not place take: {e}")
                return
        self._status("Stopped.")

    def on_tempo(self, bpm: float) -> None:
        if not (20.0 <= bpm <= 300.0):
            self._status("Tempo must be 20-300 BPM.")
            self.transport.set_tempo(self.project.tempo)
            return
        old = self.project.tempo
        if abs(old - bpm) < 1e-9:
            return

        def mutate(project):
            project.tempo = bpm

        self._structural_edit(f"tempo {old:g} -> {bpm:g}", mutate)
        self.transport.set_tempo(self.project.tempo)
        self._status(f"Tempo {bpm:g} BPM.")

    def _pattern_step_at(self, beats: float):
        """Step index of the selected pattern sounding at `beats`, if any."""
        try:
            pattern = self.project.current_pattern()
        except ProjectError:
            return None
        for track in self.project.tracks:
            for clip in track.clips:
                if clip.pattern_id != pattern.id:
                    continue
                start = clip.start_beat
                if start <= beats < start + clip.bars * 4:
                    pat_beats = pattern.steps / 4
                    rel = (beats - start) % pat_beats
                    return int(rel * 4) % pattern.steps
        return None

    def _poll_position(self) -> None:
        try:
            if self.engine.is_playing():
                beats = self.engine.position_beats()
                bar = int(beats // 4) + 1
                beat = beats % 4 + 1
                self.transport.set_position(f"Bar {bar} | Beat {beat:.1f}")
                self.playlist.set_playhead(beats)
                step = self._pattern_step_at(beats)
                self.sequencer.set_playhead(step)
                self.pianoroll.set_playhead(step)
                # Live mixer meters.
                try:
                    self.mixer.set_levels(self.engine.debug_peaks())
                except Exception:
                    pass
            else:
                self.transport.set_playing(False)
                # Decay meters when stopped.
                try:
                    self.mixer.set_levels([])
                except Exception:
                    pass
            # CLAP host callbacks: a plugin's non-parameter state changed
            # (mark_dirty) or it loaded a native preset (loaded/on_error).
            # Poll every frame; the drains are cheap when empty.
            self._poll_plugin_host_events()
        except EngineError:
            pass
        interval = self._REFRESH_INTERVALS.get(self._refresh_rate, 33)
        self.root.after(interval, self._poll_position)

    def _poll_plugin_host_events(self) -> None:
        """Drain CLAP host-callback reports from the engine.

        - mark_dirty(): a plugin's *non-parameter* state changed outside
          the host's view (parameter edits already mark the project dirty
          through the undo system). The project now needs saving again.
        - preset loaded()/on_error(): keep the user informed which native
          preset the plugin has loaded (browser sync).
        - request_restart()/latency.changed(): a plugin needs a restart
          so a structural change (new latency, ports, buffers) can
          apply. Serviced immediately: the slot restarts (state
          preserved), latency is re-queried, PDC is recalculated.
        Called every UI frame; the drains are cheap when empty.
        """
        try:
            dirty = self.engine.take_plugin_dirty_slots()
        except Exception:
            dirty = []
        if dirty:
            self._mark_dirty()
            self._status(
                f"Plugin state changed ({len(dirty)} slot(s)) - project "
                f"needs saving.")
        try:
            events = self.engine.take_preset_events()
        except Exception:
            events = {}
        for ev in events.get("loaded", []):
            loc = ev.get("location", "")
            name = os.path.basename(loc) if loc else "preset"
            self._status(f"Plugin loaded native preset '{name}'.")
        for ev in events.get("errors", []):
            msg = ev.get("message", "")
            self._status(f"Plugin preset load failed: {msg}")
        try:
            restarted = self.engine.process_plugin_restarts()
        except Exception:
            restarted = []
        if restarted:
            self._mark_dirty()
            self._status(
                f"Plugin restart ({len(restarted)} slot(s)): latency "
                f"re-queried, PDC recalculated.")

    # -- pattern management ---------------------------------------------------

    def _on_pattern_selected(self, _event=None) -> None:
        idx = self.pattern_combo.current()
        if 0 <= idx < len(self.project.patterns):
            self.project.selected_pattern = self.project.patterns[idx].id
            pattern = self.project.current_pattern()
            self.sequencer.set_pattern(pattern)
            self.pianoroll.set_pattern(pattern, self.pianoroll.channel_id)
            self._mark_dirty()

    def on_edit_pattern(self, pattern_id: str) -> None:
        """Double-clicked a playlist clip or Browser pattern: edit it."""
        try:
            self.project.pattern_by_id(pattern_id)
        except ProjectError:
            return
        self.project.selected_pattern = pattern_id
        self._refresh_pattern_combo()
        pattern = self.project.current_pattern()
        self.sequencer.set_pattern(pattern)
        self.pianoroll.set_pattern(pattern, self.pianoroll.channel_id)
        if self.editors.index("end") > 0:
            # Editor tabs may be hidden via View; the Channel Rack is a
            # separate pane now and stays visible.
            self.editors.select(0)
        self._mark_dirty()
        self._status(f"Editing pattern '{pattern.name}'.")

    def on_pattern_action(self, action: str, pattern_id: str) -> None:
        if action == "rename":
            self.pattern_rename(pattern_id)
        elif action == "duplicate":
            self.pattern_duplicate(pattern_id)
        elif action == "delete":
            self.pattern_delete(pattern_id)

    def pattern_add(self) -> None:
        def mutate(project):
            pid = project.new_pattern_id()
            n = len(project.patterns) + 1
            project.patterns.append(Pattern(id=pid, name=f"Pattern {n}",
                                            steps=16,
                                            channels=blank_channels(pid)))
            project.selected_pattern = pid

        self._structural_edit("add pattern", mutate)
        self._status(f"Added pattern '{self.project.current_pattern().name}'.")

    def pattern_duplicate(self, pattern_id: str) -> None:
        def mutate(project):
            src = project.pattern_by_id(pattern_id)
            pid = project.new_pattern_id()
            channels = []
            for ch in src.channels:
                dup = copy.deepcopy(ch)
                dup.id = f"{pid}-{ch.id}"
                channels.append(dup)
            project.patterns.append(Pattern(
                id=pid, name=f"{src.name} copy", steps=src.steps,
                channels=channels))
            project.selected_pattern = pid

        self._structural_edit("duplicate pattern", mutate)
        self._status(f"Duplicated as '{self.project.current_pattern().name}'.")

    def pattern_rename(self, pattern_id: str | None = None) -> None:
        pid = pattern_id or self.project.selected_pattern
        try:
            pattern = self.project.pattern_by_id(pid)
        except ProjectError:
            return
        name = simpledialog.askstring("Rename pattern", "Pattern name:",
                                      initialvalue=pattern.name,
                                      parent=self.root)
        if not name or not name.strip() or name == pattern.name:
            return

        def mutate(project, new_name=name.strip()):
            project.pattern_by_id(pid).name = new_name

        self._structural_edit(f"rename pattern -> '{name.strip()}'", mutate)

    def pattern_delete(self, pattern_id: str | None = None) -> None:
        pid = pattern_id or self.project.selected_pattern
        if len(self.project.patterns) <= 1:
            self._status("A project needs at least one pattern.")
            return
        try:
            pattern = self.project.pattern_by_id(pid)
        except ProjectError:
            return
        used = sum(1 for t in self.project.tracks for c in t.clips
                   if c.pattern_id == pid)
        if not messagebox.askyesno(
            "Delete pattern",
            f"Delete pattern '{pattern.name}'?"
            + (f"\n{used} clip(s) using it will be removed from the playlist."
               if used else ""),
            parent=self.root,
        ):
            return

        def mutate(project):
            project.patterns = [p for p in project.patterns if p.id != pid]
            for t in project.tracks:
                t.clips = [c for c in t.clips if c.pattern_id != pid]
            if project.selected_pattern == pid:
                project.selected_pattern = project.patterns[0].id
            self._clamp_automation_beats(project)

        self._structural_edit(f"delete pattern '{pattern.name}'", mutate)
        self._status(f"Deleted pattern '{pattern.name}'.")

    # -- automation helpers ----------------------------------------------------

    @staticmethod
    def _clamp_automation_beats(project) -> None:
        """Drop automation points past the arrangement end.

        Called inside clip mutates: shrinking the arrangement must never
        leave points the engine would reject (which would block playback).
        """
        max_beat = project.arrangement_bars() * 4
        kept = []
        for lane in project.automation:
            lane.points = [p for p in lane.points if p.beat < max_beat]
            if lane.points:
                kept.append(lane)
        project.automation = kept

    # -- playlist edits ---------------------------------------------------------

    def on_place_clip(self, track_id: str, beat: int) -> None:
        pattern = self.project.current_pattern()
        self._drop_pattern(track_id, beat, pattern.id)

    def on_move_clip(self, track_id: str, kind: str, clip_index: int,
                     _old_beat: int, new_beat: int) -> None:
        def mutate(project, tid=track_id):
            track = next(t for t in project.tracks if t.id == tid)
            if kind == "aclip":
                track.audio_clips[clip_index].start_beat = new_beat
            else:
                track.clips[clip_index].start_beat = new_beat
            self._clamp_automation_beats(project)

        where = f"bar {new_beat // 4 + 1}, beat {new_beat % 4 + 1}"
        tag = _clip_tag(kind, clip_index)
        self._structural_edit(f"move clip to {where}", mutate,
                              panels=[("playlist_lane", track_id, [tag],
                                       "partial"), "automation"])

    def on_delete_clip(self, track_id: str, kind: str, clip_index: int) -> None:
        def mutate(project, tid=track_id):
            track = next(t for t in project.tracks if t.id == tid)
            if kind == "aclip":
                del track.audio_clips[clip_index]
            else:
                del track.clips[clip_index]
            self._clamp_automation_beats(project)

        tag = _clip_tag(kind, clip_index)
        self._structural_edit("delete clip", mutate,
                              panels=[("playlist_lane", track_id, [tag],
                                       "delete"), "automation"])
        self._status("Clip deleted.")

    def on_clip_menu(self, track_id: str, kind: str, clip_index: int,
                     x: int, y: int) -> None:
        if kind == "aclip":
            self._audio_clip_menu(track_id, clip_index, x, y)
            return
        try:
            track = next(t for t in self.project.tracks if t.id == track_id)
            clip = track.clips[clip_index]
            name = self.project.pattern_by_id(clip.pattern_id).name
        except (StopIteration, IndexError, ProjectError):
            return
        menu = tk.Menu(self.root, tearoff=False)
        menu.add_command(label=f"Edit pattern '{name}'",
                         command=lambda: self.on_edit_pattern(clip.pattern_id))
        menu.add_command(label="Delete clip",
                         command=lambda: self.on_delete_clip(
                             track_id, "clip", clip_index))
        menu.tk_popup(x, y)

    def _audio_clip_menu(self, track_id: str, clip_index: int,
                         x: int, y: int) -> None:
        try:
            track = next(t for t in self.project.tracks if t.id == track_id)
            clip = track.audio_clips[clip_index]
            name = self.project.sample_by_id(clip.asset_id).name or clip.asset_id
        except (StopIteration, IndexError, ProjectError):
            return
        menu = tk.Menu(self.root, tearoff=False)
        menu.add_command(label=f"Clip properties ({name})...",
                         command=lambda: self.on_edit_audio_clip(track_id,
                                                                 clip_index))
        menu.add_command(label="Delete clip",
                         command=lambda: self.on_delete_clip(
                             track_id, "aclip", clip_index))
        menu.tk_popup(x, y)

    # -- sample assets + audio clips ----------------------------------------

    def on_add_sample(self) -> None:
        """File picker -> register a sample asset (undoable).

        The file is decoded immediately so a bad file fails loudly here,
        before it touches the project. The engine caches the buffer; the
        project stores only the path.
        """
        path = filedialog.askopenfilename(
            title="Add sample",
            filetypes=[("Audio files", "*.wav *.ogg *.flac *.mp3"),
                       ("All files", "*.*")])
        if not path:
            return
        sid = self.project.new_sample_id()
        try:
            info = self.engine.load_sample(sid, path)
        except EngineError as e:
            show_error(self.root, "Could not load sample", str(e))
            return
        self._sample_cache[sid] = info
        # Store project-relative when the file lives under the project
        # directory (keeps the project folder portable).
        store_path = path
        pdir = self._project_dir()
        if pdir:
            try:
                rel = os.path.relpath(path, pdir)
                if not rel.startswith(".."):
                    store_path = rel
            except ValueError:
                pass
        name = os.path.basename(path)

        def mutate(project, sid=sid):
            project.samples.append(
                SampleAsset(id=sid, path=store_path, name=name))

        self._structural_edit(f"add sample '{name}'", mutate,
                              panels=["browser", "playlist"])
        dur = info.get("duration_secs", 0.0)
        self._status(f"Sample '{name}' loaded ({dur:.1f}s). "
                     "Drag it onto a playlist lane to place a clip.")

    def on_sample_action(self, action: str, sample_id: str) -> None:
        if action != "remove":
            return
        try:
            samp = self.project.sample_by_id(sample_id)
        except ProjectError:
            return
        label = samp.name or sample_id
        n_clips = sum(1 for t in self.project.tracks
                      for c in t.audio_clips if c.asset_id == sample_id)

        def mutate(project, sid=sample_id):
            project.samples = [s for s in project.samples if s.id != sid]
            for t in project.tracks:
                t.audio_clips = [c for c in t.audio_clips
                                 if c.asset_id != sid]

        self._structural_edit(f"remove sample '{label}'", mutate,
                              panels=["browser", "playlist"])
        try:
            self.engine.unload_sample(sample_id)
        except EngineError:
            pass
        self._sample_cache.pop(sample_id, None)
        self._status(f"Sample '{label}' removed ({n_clips} clip(s) deleted).")

    def _sample_info(self, asset_id: str) -> dict | None:
        """Decode metadata for an asset, warming the cache on demand."""
        info = self._sample_cache.get(asset_id)
        if info is not None:
            return info
        try:
            samp = self.project.sample_by_id(asset_id)
        except ProjectError:
            return None
        info = self.engine.load_sample(asset_id,
                                       samp.resolve(self._project_dir()))
        self._sample_cache[asset_id] = info
        return info

    def _clip_peaks(self, clip) -> list:
        """Waveform peaks for one audio clip, windowed to its source region.

        Provider for the playlist painter: full-buffer peaks come from the
        engine, then the clip's [offset, offset + length/ratio...] window is
        sliced out (display-only approximation) and reversed for reversed
        clips. Never raises: the painter must always get *something*.
        """
        try:
            info = self._sample_info(clip.asset_id)
        except EngineError:
            return []
        if not info or info.get("duration_secs", 0) <= 0:
            return []
        try:
            peaks = self.engine.sample_peaks(clip.asset_id, 256)
        except EngineError:
            return []
        if not peaks:
            return []
        bps = self.project.tempo / 60.0
        ratio = 2.0 ** ((clip.pitch_semitones + clip.fine_cents / 100.0) / 12.0)
        dur = info["duration_secs"]
        start_s = clip.start_offset_beats / bps
        # Buffer seconds consumed = timeline seconds * ratio (pitch is a
        # resample: it changes duration, like LMMS Sample frequency).
        len_s = clip.length_beats / bps * ratio
        n = len(peaks)
        i0 = max(0, min(n - 1, int(start_s / dur * n)))
        i1 = max(i0 + 1, min(n, int((start_s + len_s) / dur * n)))
        window = peaks[i0:i1]
        if clip.reverse:
            window = window[::-1]
        return window

    def _drop_sample(self, track_id: str, beat: int, asset_id: str) -> None:
        """Place an audio clip from a browser sample drag (undoable)."""
        try:
            info = self._sample_info(asset_id)
        except EngineError as e:
            show_error(self.root, "Sample not ready", str(e))
            return
        if info is None:
            return
        try:
            name = self.project.sample_by_id(asset_id).name or asset_id
        except ProjectError:
            return
        bps = self.project.tempo / 60.0
        length_beats = min(4096.0, max(1.0, info["duration_secs"] * bps))

        new_idx = len(next(t for t in self.project.tracks
                             if t.id == track_id).audio_clips)

        def mutate(project, tid=track_id):
            track = next(t for t in project.tracks if t.id == tid)
            cid = project.new_audio_clip_id()
            track.audio_clips.append(AudioClip(
                id=cid, asset_id=asset_id, start_beat=float(beat),
                length_beats=length_beats))

        self._structural_edit(
            f"place audio clip '{name}'", mutate,
            panels=[("playlist_lane", track_id, [f"aclip-{new_idx}"],
                     "partial")])
        where = f"bar {beat // 4 + 1}, beat {beat % 4 + 1}"
        self._status(f"Audio clip '{name}' placed at {where}.")

    def on_edit_audio_clip(self, track_id: str, clip_index: int) -> None:
        """Per-instance clip properties dialog (FL Clip Properties)."""
        from .dialogs import edit_audio_clip
        try:
            track = next(t for t in self.project.tracks if t.id == track_id)
            clip = track.audio_clips[clip_index]
            name = self.project.sample_by_id(clip.asset_id).name or clip.asset_id
        except (StopIteration, IndexError, ProjectError):
            return
        values = edit_audio_clip(self.root, clip, name)
        if values is None:
            return

        def mutate(project, tid=track_id):
            t = next(t for t in project.tracks if t.id == tid)
            c = t.audio_clips[clip_index]
            c.gain = values["gain"]
            c.pan = values["pan"]
            c.pitch_semitones = values["pitch_semitones"]
            c.fine_cents = values["fine_cents"]
            c.start_offset_beats = values["start_offset_beats"]
            c.length_beats = values["length_beats"]
            c.reverse = values["reverse"]
            c.muted = values["muted"]

        self._structural_edit(
            "edit audio clip", mutate,
            panels=[("playlist_lane", track_id, [f"aclip-{clip_index}"],
                     "partial")])
        self._status("Audio clip updated.")

    def on_add_track(self) -> None:
        def mutate(project):
            from daw.track_identity import default_color_for
            tid = project.new_track_id()
            n = len(project.tracks) + 1
            project.tracks.append(PlaylistTrack(
                id=tid, name=f"Track {n}",
                color_idx=default_color_for(len(project.tracks))))

        self._structural_edit("add track", mutate,
                              panels=["mixer", "playlist", "menu"])
        self._status("Track added -- click it to place clips.")

    # -- track identity (research topic 33) -------------------------------------

    def on_track_menu(self, track_id: str, x: int, y: int) -> None:
        """Right-click menu on a playlist track header.

        Presentation-identity operations (FL Track Mode analog): rename,
        recolor, icon, link/unlink identity. All edits ripple through the
        track's link group and are single undoable edits.
        """
        track = self._mixer_track(self.project, track_id)
        group = self.project.linked_group(track_id)
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label=f"Track: {track.name}",
                         state="disabled")
        menu.add_separator()
        menu.add_command(label="Rename...",
                         command=lambda: self.track_rename(track_id))
        colors = tk.Menu(menu, tearoff=0)
        for i, hexcol in enumerate(TRACK_COLORS):
            colors.add_command(
                label=f"Color {i + 1}",
                background=hexcol,
                command=lambda i=i: self.track_set_color(track_id, i))
        menu.add_cascade(label="Color", menu=colors)
        icons = tk.Menu(menu, tearoff=0)
        icons.add_command(label="(none)",
                          command=lambda: self.track_set_icon(track_id, ""))
        for code in TRACK_ICONS:
            if code:
                icons.add_command(
                    label=f"[{code.upper()}]",
                    command=lambda c=code: self.track_set_icon(track_id, c))
        menu.add_cascade(label="Icon", menu=icons)
        others = [t for t in self.project.tracks if t.id != track_id]
        if others:
            link = tk.Menu(menu, tearoff=0)
            for t in others:
                link.add_command(
                    label=t.name,
                    command=lambda o=t.id: self.track_link(track_id, o))
            menu.add_cascade(label="Link identity with...", menu=link)
        if len(group) > 1:
            menu.add_command(label=f"Unlink identity ({len(group)} linked)",
                             command=lambda: self.track_unlink(track_id))
        menu.tk_popup(x, y)

    def _identity_edit(self, label: str, mutate) -> None:
        self._structural_edit(label, mutate, panels=["playlist", "mixer"])

    def track_rename(self, track_id: str) -> None:
        track = self._mixer_track(self.project, track_id)
        name = simpledialog.askstring("Rename track", "Track name:",
                                      parent=self.root,
                                      initialvalue=track.name)
        if not name or not name.strip():
            return

        def mutate(project, tid=track_id, n=name.strip()):
            project.set_track_identity(tid, name=n)

        self._identity_edit(f"rename track -> '{name.strip()}'", mutate)

    def track_set_color(self, track_id: str, color_idx: int) -> None:
        def mutate(project, tid=track_id, c=color_idx):
            project.set_track_identity(tid, color_idx=c)

        self._identity_edit(f"track color -> {color_idx + 1}", mutate)

    def track_set_icon(self, track_id: str, icon: str) -> None:
        def mutate(project, tid=track_id, ic=icon):
            project.set_track_identity(tid, icon=ic)

        label = f"track icon -> [{icon.upper()}]" if icon else "track icon cleared"
        self._identity_edit(label, mutate)

    def track_link(self, track_id: str, other_id: str) -> None:
        def mutate(project, a=track_id, b=other_id):
            project.link_track_identities([a, b])

        self._identity_edit("link track identities", mutate)
        self._status("Track identities linked -- name/color/icon now ripple.")

    def track_unlink(self, track_id: str) -> None:
        def mutate(project, tid=track_id):
            project.unlink_track_identity(tid)

        self._identity_edit("unlink track identity", mutate)
        self._status("Track identity unlinked.")

    # -- mixer edits ------------------------------------------------------------

    def _mixer_track(self, project, track_id: str):
        return next(t for t in project.tracks if t.id == track_id)

    def on_mixer_volume(self, track_id: str, gain: float) -> None:
        gain = max(0.0, min(2.0, gain))

        def mutate(project, tid=track_id):
            self._mixer_track(project, tid).gain = gain

        self._structural_edit(f"track volume -> {gain:.2f}", mutate,
                              panels=["mixer"])

    def on_mixer_pan(self, track_id: str, pan: float) -> None:
        pan = max(-1.0, min(1.0, pan))

        def mutate(project, tid=track_id):
            self._mixer_track(project, tid).pan = pan

        label = "center" if pan == 0 else f"{'L' if pan < 0 else 'R'}{abs(pan):.2f}"
        self._structural_edit(f"track pan -> {label}", mutate,
                              panels=["mixer"])

    def on_mixer_mute(self, track_id: str, muted: bool) -> None:
        def mutate(project, tid=track_id):
            self._mixer_track(project, tid).muted = muted

        self._structural_edit("mute track" if muted else "unmute track", mutate,
                              panels=["mixer"])
        self._status(f"Track {'muted' if muted else 'unmuted'}.")

    def on_mixer_add_send(self, track_id: str, dest_id: str) -> None:
        def mutate(project, tid=track_id, did=dest_id):
            track = self._mixer_track(project, tid)
            # Avoid duplicates.
            if any(s.to_track_id == did for s in track.sends):
                return
            track.sends.append(Send(to_track_id=did, amount=0.5))

        self._structural_edit("add send", mutate, panels=["mixer"])
        self._status(f"Send added.")

    def on_mixer_send_amount(self, track_id: str, dest_id: str,
                             amount: float) -> None:
        def mutate(project, tid=track_id, did=dest_id, amt=amount):
            track = self._mixer_track(project, tid)
            for s in track.sends:
                if s.to_track_id == did:
                    s.amount = max(0.0, min(1.0, amt))
                    break

        self._structural_edit("send amount", mutate, panels=["mixer"])

    def on_mixer_send_options(self, track_id: str, dest_id: str,
                              options: dict) -> None:
        """Set per-send routing options (tap/pan/sidechain)."""
        def mutate(project, tid=track_id, did=dest_id, opts=options):
            track = self._mixer_track(project, tid)
            for s in track.sends:
                if s.to_track_id == did:
                    if "tap" in opts:
                        if opts["tap"] not in ("pre", "post"):
                            raise ProjectError(
                                f"send: unknown tap '{opts['tap']}'")
                        s.tap = opts["tap"]
                    if "pan" in opts:
                        s.pan = max(-1.0, min(1.0, float(opts["pan"])))
                    if "sidechain" in opts:
                        s.sidechain = bool(opts["sidechain"])
                    break

        self._structural_edit("send options", mutate, panels=["mixer"])

    def on_mixer_vel_track(self, track_id: str, amount: float,
                             mid: float) -> None:
        """Set a track's velocity-tracking amount/middle (FL 3xOsc model).

        Applies to the track's built-in voices; CLAP instruments map
        velocity themselves. Live-updated without rebuild (the engine
        only reads these at note-trigger time).
        """
        def mutate(project, tid=track_id, amt=amount, m=mid):
            track = self._mixer_track(project, tid)
            track.vel_track = max(-1.0, min(1.0, float(amt)))
            track.vel_track_mid = max(0.0, min(1.0, float(m)))

        self._structural_edit("velocity tracking", mutate, panels=["mixer"])

    def on_mixer_key_track(self, track_id: str, amount: float,
                           mid: float) -> None:
        """Set a track's keyboard-tracking amount/middle (FL Channel
        Keyboard Tracker model).

        Applies to the track's built-in voices; CLAP instruments map
        pitch themselves. Live-updated without rebuild (the engine
        only reads these at note-trigger time).
        """
        def mutate(project, tid=track_id, amt=amount, m=mid):
            track = self._mixer_track(project, tid)
            track.key_track = max(-1.0, min(1.0, float(amt)))
            track.key_track_mid = max(0.0, min(127.0, float(m)))

        self._structural_edit("keyboard tracking", mutate, panels=["mixer"])

    def on_mixer_remove_send(self, track_id: str, dest_id: str) -> None:
        def mutate(project, tid=track_id, did=dest_id):
            track = self._mixer_track(project, tid)
            track.sends = [s for s in track.sends if s.to_track_id != did]
            # Drop orphaned send-amount lanes: the route no longer exists
            # and AutomationLane.validate would reject the project.
            project.automation = [
                a for a in project.automation
                if not (a.track_id == tid
                        and a.param == f"send.{did}.amount")]

        self._structural_edit("remove send", mutate, panels=["mixer"])
        self._status(f"Send removed.")

    def on_mixer_set_output(self, track_id: str, dest_id: str) -> None:
        """Set a track's exclusive output route (FL 'route to this track').

        dest_id is a track id or "master". The track's post-FX output is
        mixed into the destination BEFORE its FX chain, and the track no
        longer reaches the Master directly. Parallel sends are untouched
        (unlike FL's 'only', which can also alter existing sends).
        """
        from .dialogs import show_error
        track = self._mixer_track(self.project, track_id)
        if dest_id == track.output:
            return
        if dest_id != "master":
            if dest_id == track_id:
                show_error(self.root, "Cannot route",
                           "A track cannot route to itself.")
                return
            try:
                self.project.track_by_id(dest_id)
            except ProjectError:
                show_error(self.root, "Cannot route",
                           f"Unknown destination '{dest_id}'.")
                return
            if self.project.would_create_output_cycle(track_id, dest_id):
                show_error(self.root, "Cannot route",
                           "That would create a routing cycle.")
                return
        def mutate(project, tid=track_id, did=dest_id):
            self._mixer_track(project, tid).output = did

        self._structural_edit("set output route", mutate, panels=["mixer"])
        dest_name = "Master" if dest_id == "master" else \
            self.project.track_by_id(dest_id).name
        self._status(f"'{track.name}' now routes to {dest_name} only.")

    def on_mixer_route_only(self, dest_id: str, source_ids: list) -> None:
        """FL 'Route selected to this track only': rewrite the output route
        of several source tracks to the destination in ONE undo step.

        Each source's direct Master path is removed; their audio reaches
        Master through the destination (subgroup mode). Existing parallel
        sends are preserved.
        """
        from .dialogs import show_error
        if not source_ids:
            return
        dest = self._mixer_track(self.project, dest_id)
        # Validate everything before mutating (all-or-nothing).
        for sid in source_ids:
            if sid == dest_id:
                show_error(self.root, "Cannot route",
                           "A track cannot route to itself.")
                return
            try:
                self.project.track_by_id(sid)
            except ProjectError:
                show_error(self.root, "Cannot route",
                           f"Unknown track '{sid}'.")
                return
            if self.project.would_create_output_cycle(sid, dest_id):
                show_error(self.root, "Cannot route",
                           "That would create a routing cycle.")
                return
        def mutate(project, did=dest_id, sids=list(source_ids)):
            for sid in sids:
                self._mixer_track(project, sid).output = did

        self._structural_edit(f"route {len(source_ids)} tracks to "
                              f"'{dest.name}' only", mutate,
                              panels=["mixer"])
        self._status(f"{len(source_ids)} track(s) now route to "
                     f"'{dest.name}' only.")

    def on_mixer_save_chain_preset(self, track_id: str) -> None:
        """Save the track's FX chain as a preset (into the preset library)."""
        from ..presets import Preset, save_preset, PRESET_EXTENSION
        from ..content import ensure_user_presets_dir
        from tkinter import filedialog, simpledialog
        track = self._mixer_track(self.project, track_id)
        if not track.effects:
            self._status("No effects to save.")
            return
        name = simpledialog.askstring(
            "Save FX Chain Preset",
            "Preset name:",
            parent=self.root,
            initialvalue=f"{track.name} FX")
        if not name:
            return
        preset = Preset.from_chain(track.effects, name)
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Save FX Chain Preset",
            defaultextension=PRESET_EXTENSION,
            filetypes=[("Pulsegrid presets", f"*{PRESET_EXTENSION}"),
                       ("All files", "*.*")],
            initialdir=ensure_user_presets_dir(),
            initialfile=f"{name}{PRESET_EXTENSION}")
        if path:
            try:
                save_preset(preset, path)
                self.browser.refresh_presets()
                self._status(f"FX chain preset saved: {name}")
            except Exception as e:
                from .dialogs import show_error
                show_error(self.root, "Cannot save preset", str(e))

    def on_mixer_load_chain_preset(self, track_id: str) -> None:
        """Load an FX chain preset onto the track (replaces FX)."""
        from ..presets import load_preset, PRESET_EXTENSION
        from ..content import user_presets_dir
        from tkinter import filedialog
        from .dialogs import show_error
        path = filedialog.askopenfilename(
            parent=self.root,
            title="Load FX Chain Preset",
            initialdir=user_presets_dir(),
            filetypes=[("Pulsegrid presets", f"*{PRESET_EXTENSION}"),
                       ("All files", "*.*")])
        if not path:
            return
        try:
            preset = load_preset(path)
            effects = preset.to_effects()
        except Exception as e:
            show_error(self.root, "Cannot load preset", str(e))
            return
        def mutate(project, tid=track_id, fx=effects):
            self._mixer_track(project, tid).effects = fx
        self._structural_edit("load FX chain preset", mutate,
                              panels=["mixer"])
        self._status(f"FX chain preset loaded: {preset.name}")

    def on_mixer_save_track_preset(self, track_id: str) -> None:
        """Save the track state as a preset (into the preset library)."""
        from ..presets import Preset, save_preset, PRESET_EXTENSION
        from ..content import ensure_user_presets_dir
        from tkinter import filedialog, simpledialog
        track = self._mixer_track(self.project, track_id)
        name = simpledialog.askstring(
            "Save Track Preset",
            "Preset name:",
            parent=self.root,
            initialvalue=f"{track.name}")
        if not name:
            return
        preset = Preset.from_track(track, name)
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Save Track Preset",
            defaultextension=PRESET_EXTENSION,
            filetypes=[("Pulsegrid presets", f"*{PRESET_EXTENSION}"),
                       ("All files", "*.*")],
            initialdir=ensure_user_presets_dir(),
            initialfile=f"{name}{PRESET_EXTENSION}")
        if path:
            try:
                save_preset(preset, path)
                self.browser.refresh_presets()
                self._status(f"Track preset saved: {name}")
            except Exception as e:
                from .dialogs import show_error
                show_error(self.root, "Cannot save preset", str(e))

    def on_mixer_load_track_preset(self, track_id: str) -> None:
        """Load a track preset (replaces generator/FX/gain/pan)."""
        from ..presets import load_preset, PRESET_EXTENSION
        from ..content import user_presets_dir
        from tkinter import filedialog
        from .dialogs import show_error
        path = filedialog.askopenfilename(
            parent=self.root,
            title="Load Track Preset",
            initialdir=user_presets_dir(),
            filetypes=[("Pulsegrid presets", f"*{PRESET_EXTENSION}"),
                       ("All files", "*.*")])
        if not path:
            return
        try:
            preset = load_preset(path)
        except Exception as e:
            show_error(self.root, "Cannot load preset", str(e))
            return
        def mutate(project, tid=track_id, p=preset):
            p.apply_to_track(self._mixer_track(project, tid))
        try:
            self._structural_edit("load track preset", mutate,
                                  panels=["mixer"])
            self._status(f"Track preset loaded: {preset.name}")
        except Exception as e:
            show_error(self.root, "Cannot apply preset", str(e))

    def apply_preset_to_track(self, track_id: str, entry_id: str) -> None:
        """Apply a content-library preset to a mixer track.

        Called by double-clicking / dragging a preset in the Browser, or
        dropping it on a mixer strip. One undo step.
            effect preset -> appended to the track's FX
            chain preset  -> replaces the track's FX
            track preset  -> replaces generator/FX/gain/pan
        """
        from ..content import find_entry
        from .dialogs import show_error
        try:
            entry = find_entry(self.browser._presets, entry_id)
            preset = entry.preset
        except ProjectError as e:
            show_error(self.root, "Cannot apply preset", str(e))
            return
        try:
            if preset.type == "effect":
                fx = preset.to_effect()
                def mutate(project, tid=track_id, fx=fx):
                    self._mixer_track(project, tid).effects.append(fx)
                label = f"apply effect preset '{preset.name}'"
            elif preset.type == "chain":
                fx_list = preset.to_effects()
                def mutate(project, tid=track_id, fx_list=fx_list):
                    self._mixer_track(project, tid).effects = fx_list
                label = f"apply chain preset '{preset.name}'"
            elif preset.type == "track":
                def mutate(project, tid=track_id, p=preset):
                    p.apply_to_track(self._mixer_track(project, tid))
                label = f"apply track preset '{preset.name}'"
            else:
                raise ProjectError(f"unknown preset type '{preset.type}'")
            self._structural_edit(label, mutate, panels=["mixer"])
            self._status(f"Preset applied to track: {preset.name}")
        except Exception as e:
            show_error(self.root, "Cannot apply preset", str(e))

    def on_mixer_add_effect(self, track_id: str, kind: str) -> None:
        try:
            fx = Effect.default(kind)
        except ProjectError as e:
            show_error(self.root, "Cannot add effect", str(e))
            return

        def mutate(project, tid=track_id):
            # Safe to share `fx`: _structural_edit deep-copies the project
            # into before/after snapshots, so stored copies are isolated.
            self._mixer_track(project, tid).effects.append(fx)

        self._structural_edit(f"add {fx.kind} effect", mutate,
                              panels=["mixer", "automation"])
        self._status(f"Added {fx.kind} to track.")

    def on_mixer_effect_param(self, track_id: str, index: int,
                              param: str, value: float) -> None:
        def mutate(project, tid=track_id):
            fx = self._mixer_track(project, tid).effects[index]
            fx.params[param] = value

        self._structural_edit(f"effect {param} -> {value:g}", mutate,
                              panels=["mixer"])

    def on_mixer_remove_effect(self, track_id: str, index: int) -> None:
        def mutate(project, tid=track_id):
            del self._mixer_track(project, tid).effects[index]
            # Automation lanes on the removed effect are dropped; lanes on
            # higher effects shift down one index (same undo step).
            kept = []
            for lane in project.automation:
                if lane.track_id != tid:
                    kept.append(lane)
                    continue
                kind, idx, name = parse_auto_param(lane.param)
                if kind == "fx":
                    if idx == index:
                        continue
                    if idx > index:
                        lane.param = f"fx{idx - 1}.{name}"
                kept.append(lane)
            project.automation = kept

        self._structural_edit("remove effect", mutate,
                              panels=["mixer", "automation"])
        self._status("Effect removed.")

    # -- CLAP plugin hosting ----------------------------------------------

    def _scan_clap_plugins(self) -> list:
        if self._clap_scan_cache is None:
            try:
                self._clap_scan_cache = self.engine.scan_clap_plugins()
            except EngineError as e:
                show_error(self.root, "Plugin scan failed", str(e))
                self._clap_scan_cache = []
        return self._clap_scan_cache

    def ensure_plugin_registered(self, plugin_id: str,
                                 plugin_path: str) -> list:
        """Register a plugin's params; returns the param list.

        Raises EngineError/ProjectError with a user-readable message.
        """
        try:
            params = self.engine.clap_plugin_params(plugin_path, plugin_id)
        except EngineError as e:
            raise EngineError(
                f"cannot read parameters of '{plugin_id}': {e}") from e
        # Find the display name from the last scan (best effort).
        name = plugin_id
        for p in self._scan_clap_plugins():
            if p["id"] == plugin_id:
                name = p["name"]
                break
        register_plugin(plugin_id, name, params)
        return params

    def _scan_vst3_plugins(self) -> list:
        if self._vst3_scan_cache is None:
            try:
                self._vst3_scan_cache = self.engine.scan_vst3_plugins()
            except EngineError as e:
                show_error(self.root, "VST3 scan failed", str(e))
                self._vst3_scan_cache = []
        return self._vst3_scan_cache

    def ensure_vst3_registered(self, plugin_id: str,
                               plugin_path: str) -> list:
        """Register a VST3 plugin's params; returns the param list."""
        try:
            params = self.engine.vst3_plugin_params(plugin_path)
        except EngineError as e:
            raise EngineError(
                f"cannot read parameters of '{plugin_id}': {e}") from e
        name = plugin_id
        for p in self._scan_vst3_plugins():
            if p["plugin_id"] == plugin_id:
                name = p["name"]
                break
        register_plugin(plugin_id, name, params)
        return params

    def on_mixer_add_plugin(self, track_id: str) -> None:
        # Combine CLAP and VST3 plugins; the dicts carry "format".
        plugins = self._scan_clap_plugins() + self._scan_vst3_plugins()

        def picked(plugin: dict) -> None:
            # Validate before touching the project: a broken plugin
            # fails here, loudly, instead of silently in the graph.
            is_vst3 = plugin.get("format") == "vst3"
            try:
                if is_vst3:
                    self.engine.check_vst3_plugin(plugin["path"])
                    params = self.ensure_vst3_registered(
                        plugin["plugin_id"], plugin["path"])
                else:
                    self.engine.check_clap_plugin(plugin["path"], plugin["id"])
                    params = self.ensure_plugin_registered(plugin["id"],
                                                           plugin["path"])
            except (EngineError, ProjectError) as e:
                show_error(self.root, "Cannot add plugin", str(e))
                return
            if is_vst3:
                fx = Effect.vst3(
                    plugin["plugin_id"], plugin["path"],
                    {int(p["id"]): float(p["default"]) for p in params})
            else:
                fx = Effect.plugin(
                    plugin["id"], plugin["path"],
                    {int(p["id"]): float(p["default"]) for p in params})

            def mutate(project, tid=track_id, f=fx):
                self._mixer_track(project, tid).effects.append(f)

            self._structural_edit(f"add plugin {plugin['name']}", mutate)
            self._status(f"Added {plugin['name']} to track.")

        ClapPickerDialog(self.root, plugins, picked,
                         title="Add plugin",
                         heading="Audio effects (CLAP + VST3)")

    def on_mixer_edit_plugin(self, track_id: str, index: int) -> None:
        track = self._mixer_track(self.project, track_id)
        try:
            fx = track.effects[index]
        except IndexError:
            return
        if fx.kind not in ("plugin", "vst3"):
            return
        try:
            if fx.kind == "vst3":
                params = self.ensure_vst3_registered(fx.plugin_id,
                                                     fx.plugin_path)
            else:
                params = self.ensure_plugin_registered(fx.plugin_id,
                                                       fx.plugin_path)
        except (EngineError, ProjectError) as e:
            show_error(self.root, "Cannot open plugin", str(e))
            return

        def on_change(param_id: int, value: float) -> None:
            self.on_mixer_effect_param(track_id, index, str(param_id), value)

        try:
            track_idx = next(i for i, t in enumerate(self.project.tracks)
                             if t.id == track_id)
        except StopIteration:
            return

        def on_open_gui(dialog) -> None:
            PluginGuiSession.open(self, track_idx, index, dialog)

        try:
            fx_latency = self.engine.plugin_latency_samples(
                track_idx, fx_index=index)
        except Exception:
            fx_latency = None
        dlg = PluginParamDialog(
            self.root, f"Plugin: {fx.display_name()}",
            params, fx.params, on_change,
            latency_samples=fx_latency,
            on_open_gui=on_open_gui,
            on_save_preset=lambda: self.on_plugin_save_preset(
                track_idx, fx_index=index),
            on_load_preset=lambda: self._on_preset_loaded_refresh(
                dlg, track_idx, index, None,
                lambda: self.on_plugin_load_preset(
                    track_idx, fx_index=index)),
            on_load_native_preset=lambda: self._on_preset_loaded_refresh(
                dlg, track_idx, index, None,
                lambda: self.on_plugin_load_native_preset(
                    track_idx, fx_index=index)))

    def _on_preset_loaded_refresh(self, dialog, track_idx, fx_index,
                                  layer_index, loader) -> None:
        """Run a preset loader, then refresh the open param dialog's
        sliders from the project (the preset changed the values)."""
        loader()
        try:
            track = self.project.tracks[track_idx]
            if fx_index is not None:
                values = track.effects[fx_index].params
            else:
                values = track.generator_layers[layer_index].generator.params
            dialog.refresh_values(values)
        except (IndexError, AttributeError):
            pass

    def on_mixer_open_gui(self, track_id: str, index: int) -> None:
        """Open a plugin's native floating GUI from the mixer."""
        try:
            track_idx = next(i for i, t in enumerate(self.project.tracks)
                             if t.id == track_id)
        except StopIteration:
            return
        try:
            fx = self.project.tracks[track_idx].effects[index]
        except IndexError:
            return
        if fx.kind != "plugin":
            return
        PluginGuiSession.open(self, track_idx, index)

    # -- track generators (instrument plugins) --------------------------------

    def on_mixer_set_generator(self, track_id: str) -> None:
        """Legacy: pick a CLAP instrument as the track's generator.
        Now delegates to on_mixer_add_layer (adds as a layer)."""
        self.on_mixer_add_layer(track_id)

    def on_mixer_edit_generator(self, track_id: str,
                                layer_idx: int = 0) -> None:
        """Edit a generator layer's parameters (generic panel)."""
        track = self._mixer_track(self.project, track_id)
        if not (0 <= layer_idx < len(track.generator_layers)):
            return
        gen = track.generator_layers[layer_idx].generator
        try:
            if gen.format == "vst3":
                params = self.engine.vst3_plugin_params(gen.plugin_path)
            else:
                params = self.ensure_plugin_registered(gen.plugin_id,
                                                       gen.plugin_path)
        except (EngineError, ProjectError) as e:
            show_error(self.root, "Cannot open instrument", str(e))
            return

        def on_change(clap_id: int, value: float) -> None:
            def mutate(project, tid=track_id, idx=layer_idx):
                layers = self._mixer_track(project, tid).generator_layers
                if 0 <= idx < len(layers):
                    layers[idx].generator.params[str(clap_id)] = value
            self._structural_edit(f"generator param {clap_id} -> {value:g}",
                                  mutate)

        def on_open_gui(dialog) -> None:
            self.on_mixer_open_generator_gui(track_id)

        try:
            track_idx = next(i for i, t in enumerate(self.project.tracks)
                             if t.id == track_id)
        except StopIteration:
            return

        try:
            gen_latency = self.engine.plugin_latency_samples(
                track_idx, layer_index=layer_idx)
        except Exception:
            gen_latency = None
        gen_dlg = PluginParamDialog(
            self.root, f"Generator: {gen.display_name()}",
            params, gen.params, on_change,
            latency_samples=gen_latency,
            on_open_gui=on_open_gui,
            on_save_preset=lambda: self.on_plugin_save_preset(
                track_idx, layer_index=layer_idx),
            on_load_preset=lambda: self._on_preset_loaded_refresh(
                gen_dlg, track_idx, None, layer_idx,
                lambda: self.on_plugin_load_preset(
                    track_idx, layer_index=layer_idx)),
            on_load_native_preset=lambda: self._on_preset_loaded_refresh(
                gen_dlg, track_idx, None, layer_idx,
                lambda: self.on_plugin_load_native_preset(
                    track_idx, layer_index=layer_idx)))

    # -- plugin presets (CLAP state context + preset load) --------------------

    def _apply_plugin_preset_result(self, track_idx: int, fx_index,
                                    layer_index, result: dict,
                                    label: str) -> None:
        """Store a preset load's rescanned params + state blob in the
        project in one undoable edit, then rebuild the audio graph.

        `result` is {"params": {str(clap_id): value}, "state_base64": str}
        from the engine. Per CLAP, rescanned values are NOT automation —
        they are a state sync, so no automation lanes are touched.
        """
        params = result.get("params", {})
        blob = result.get("state_base64", "")

        def mutate(project):
            track = project.tracks[track_idx]
            if fx_index is not None:
                fx = track.effects[fx_index]
                if fx.kind not in ("plugin", "vst3"):
                    raise ProjectError("effect slot is not a plugin")
                fx.params = {str(k): float(v) for k, v in params.items()}
                # VST3 has no opaque state blob in v1; params only.
                if fx.kind == "plugin":
                    fx.state_base64 = blob
            else:
                layer = track.generator_layers[layer_index]
                layer.generator.params = {
                    str(k): float(v) for k, v in params.items()}
                layer.generator.state_base64 = blob

        self._structural_edit(label, mutate, panels=["mixer"])
        self._status(f"{label}.")

    def on_plugin_save_preset(self, track_idx: int, fx_index=None,
                              layer_index=None) -> None:
        """Save the slot's plugin state as a preset file (FOR_PRESET
        state context: "save my state as a reusable preset")."""
        try:
            blob_b64 = self.engine.save_plugin_preset_blob(
                track_idx, fx_index=fx_index, layer_index=layer_index)
        except EngineError as e:
            show_error(self.root, "Save preset failed", str(e))
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".pgpreset",
            filetypes=[("Pulsegrid plugin presets", "*.pgpreset"),
                       ("All files", "*.*")])
        if not path:
            return
        try:
            import base64
            with open(path, "wb") as f:
                f.write(base64.b64decode(blob_b64))
        except Exception as e:
            show_error(self.root, "Save preset failed", str(e))
            return
        self._status(f"Preset saved to {os.path.basename(path)}.")

    def on_plugin_load_preset(self, track_idx: int, fx_index=None,
                              layer_index=None) -> None:
        """Load a .pgpreset file (FOR_PRESET state context) into the
        slot's plugin. One undoable edit."""
        path = filedialog.askopenfilename(
            filetypes=[("Pulsegrid plugin presets", "*.pgpreset"),
                       ("All files", "*.*")])
        if not path:
            return
        try:
            import base64
            with open(path, "rb") as f:
                blob_b64 = base64.b64encode(f.read()).decode("ascii")
            result = self.engine.load_plugin_preset_blob(
                track_idx, blob_b64,
                fx_index=fx_index, layer_index=layer_index)
        except EngineError as e:
            show_error(self.root, "Load preset failed", str(e))
            return
        except Exception as e:
            show_error(self.root, "Load preset failed", str(e))
            return
        self._apply_plugin_preset_result(
            track_idx, fx_index, layer_index, result,
            f"Loaded preset '{os.path.basename(path)}'")

    def on_plugin_load_native_preset(self, track_idx: int, fx_index=None,
                                     layer_index=None) -> None:
        """Ask the plugin to load one of its *native* preset files
        (CLAP_EXT_PRESET_LOAD from_location()). The plugin parses its own
        format; the host then rescans params and captures the state.
        One undoable edit."""
        try:
            supported = self.engine.plugin_supports_preset_load(
                track_idx, fx_index=fx_index, layer_index=layer_index)
        except EngineError:
            supported = False
        if not supported:
            show_error(self.root, "Native presets",
                       "This plugin does not implement native preset "
                       "loading (CLAP_EXT_PRESET_LOAD).")
            return
        path = filedialog.askopenfilename(
            filetypes=[("All files", "*.*")])
        if not path:
            return
        try:
            result = self.engine.plugin_preset_from_location(
                track_idx, path,
                fx_index=fx_index, layer_index=layer_index)
        except EngineError as e:
            show_error(self.root, "Native preset load failed", str(e))
            return
        self._apply_plugin_preset_result(
            track_idx, fx_index, layer_index, result,
            f"Loaded native preset '{os.path.basename(path)}'")

    def on_mixer_clear_generator(self, track_id: str) -> None:
        """Remove all generator layers (back to built-in voices)."""
        def mutate(project, tid=track_id):
            self._mixer_track(project, tid).generator_layers = []

        self._structural_edit("clear generators", mutate)
        self._status("Generators cleared -- built-in voices.")

    def on_mixer_open_generator_gui(self, track_id: str) -> None:
        """Open the generator instrument's native floating GUI."""
        # For layers, opens the first layer's GUI (legacy behavior).
        try:
            track_idx = next(i for i, t in enumerate(self.project.tracks)
                             if t.id == track_id)
        except StopIteration:
            return
        layers = self.project.tracks[track_idx].generator_layers
        if not layers:
            return
        GeneratorGuiSession.open(self, track_idx)

    def on_mixer_add_layer(self, track_id: str) -> None:
        """Add a generator layer to the track (FL Layer-style)."""
        try:
            instruments = self.engine.scan_clap_instruments()
            # VST3 instruments share the same scan; tag the format.
            for v in self.engine.scan_vst3_plugins():
                v = dict(v)
                v.setdefault("format", "vst3")
                instruments.append(v)
        except EngineError as e:
            show_error(self.root, "Scan failed", str(e))
            return
        if not instruments:
            show_error(self.root, "No instruments",
                       "No instrument plugins were found.\n\n"
                       "CLAP: install a .clap plugin into ~/.clap (Linux)\n"
                       "or set CLAP_PATH.\n"
                       "VST3: install a .vst3 plugin into\n"
                       "C:\\Program Files\\Common Files\\VST3 (Windows),\n"
                       "~/.vst3 or /usr/lib/vst3 (Linux).")
            return

        def on_pick(plugin):
            is_vst3 = plugin.get("format") == "vst3"
            try:
                if is_vst3:
                    self.engine.check_vst3_instrument(plugin["path"])
                    params = self.engine.vst3_plugin_params(plugin["path"])
                    # Register display name + params for automation/validation.
                    vst3_id = plugin.get("plugin_id", plugin["path"])
                    register_plugin(vst3_id, plugin["name"], params)
                else:
                    self.engine.check_clap_instrument(plugin["path"],
                                                      plugin["id"])
                    params = self.ensure_plugin_registered(plugin["id"],
                                                           plugin["path"])
            except (EngineError, ProjectError) as e:
                show_error(self.root, "Cannot load instrument", str(e))
                return
            defaults = {str(p["id"]): float(p["default"]) for p in params}
            if is_vst3:
                gen = Generator.vst3(plugin.get("plugin_id", plugin["path"]),
                                     plugin["path"], defaults)
            else:
                gen = Generator.plugin(plugin["id"], plugin["path"], defaults)
            layer = GeneratorLayer(generator=gen)

            def mutate(project, tid=track_id, l=layer):
                self._mixer_track(project, tid).generator_layers.append(l)

            self._structural_edit(
                f"add layer {plugin['name']}", mutate)
            self._status(f"Layer added: {plugin['name']}.")

        ClapPickerDialog(self.root, instruments, on_pick,
                         title="Add generator layer")

    def on_mixer_remove_layer(self, track_id: str, layer_idx: int) -> None:
        """Remove a generator layer."""
        def mutate(project, tid=track_id, idx=layer_idx):
            track = self._mixer_track(project, tid)
            if 0 <= idx < len(track.generator_layers):
                del track.generator_layers[idx]

        self._structural_edit("remove layer", mutate)
        self._status("Layer removed.")

    # -- Modulators --------------------------------------------------------

    def on_mixer_add_modulator(self, track_id: str) -> None:
        """Add a new MSEG modulator to the track."""
        from daw.project import Modulator
        import uuid
        mod = Modulator(
            id=f"mod-{uuid.uuid4().hex[:8]}",
            name=f"Modulator {len(self._mixer_track(self.project, track_id).modulators) + 1}",
            nodes=[[0.0, 0.0], [2.0, 1.0], [4.0, 0.0]],
            loop_enabled=True,
            length_bars=1.0,
            rate_mult=1.0,
            assignments=[],
        )

        def mutate(project, tid=track_id, m=mod):
            self._mixer_track(project, tid).modulators.append(m)

        self._structural_edit(f"add modulator {mod.name}", mutate)
        self._status(f"Modulator added: {mod.name}.")

    def on_mixer_edit_modulator(self, track_id: str, mod_idx: int) -> None:
        """Open the MSEG curve editor for a modulator."""
        from daw.ui.modulators import MSEGCurveEditor
        track = self._mixer_track(self.project, track_id)
        if not (0 <= mod_idx < len(track.modulators)):
            return
        mod = track.modulators[mod_idx]

        def on_change(nodes):
            def mutate(project, tid=track_id, idx=mod_idx, ns=nodes):
                mods = self._mixer_track(project, tid).modulators
                if 0 <= idx < len(mods):
                    mods[idx].nodes = ns
            self._structural_edit(f"edit {mod.name} curve", mutate)

        MSEGCurveEditor(self.root, f"MSEG: {mod.name}",
                        mod.nodes, mod.length_bars, on_change)

    def on_mixer_remove_modulator(self, track_id: str, mod_idx: int) -> None:
        """Remove a modulator."""
        def mutate(project, tid=track_id, idx=mod_idx):
            track = self._mixer_track(project, tid)
            if 0 <= idx < len(track.modulators):
                del track.modulators[idx]

        self._structural_edit("remove modulator", mutate)
        self._status("Modulator removed.")

    def on_mixer_add_assignment(self, track_id: str, mod_idx: int) -> None:
        """Open the assignment dialog to route a modulator to a param."""
        from daw.ui.modulators import ModAssignmentDialog
        from daw.project import ModAssignment
        track = self._mixer_track(self.project, track_id)
        if not (0 <= mod_idx < len(track.modulators)):
            return

        # Build target list: FX plugin slots and generator layers.
        targets = []
        for i, fx in enumerate(track.effects):
            if fx.kind in ("plugin", "vst3"):
                try:
                    if fx.kind == "vst3":
                        params = self.engine.vst3_plugin_params(fx.plugin_path)
                    else:
                        params = self.ensure_plugin_registered(
                            fx.plugin_id, fx.plugin_path)
                    targets.append({
                        "kind": "fx", "index": i,
                        "label": f"FX {i}: {fx.display_name()}",
                        "params": params,
                    })
                except (EngineError, ProjectError):
                    continue
        for i, layer in enumerate(track.generator_layers):
            gen = layer.generator
            try:
                if gen.format == "vst3":
                    params = self.engine.vst3_plugin_params(gen.plugin_path)
                else:
                    params = self.ensure_plugin_registered(
                        gen.plugin_id, gen.plugin_path)
                targets.append({
                    "kind": "gen", "index": i,
                    "label": f"Layer {i}: {gen.display_name()}",
                    "params": params,
                })
            except (EngineError, ProjectError):
                continue

        if not targets:
            show_error(self.root, "No targets",
                       "No plugin parameters available.\n"
                       "Add a plugin effect or generator layer first.")
            return

        def on_add(a_dict):
            a = ModAssignment(**a_dict)
            def mutate(project, tid=track_id, idx=mod_idx, assgn=a):
                mods = self._mixer_track(project, tid).modulators
                if 0 <= idx < len(mods):
                    mods[idx].assignments.append(assgn)
            self._structural_edit("add modulation assignment", mutate)
            self._status("Assignment added.")

        ModAssignmentDialog(self.root, targets, on_add)

    def on_mixer_remove_assignment(self, track_id: str, mod_idx: int,
                                   assign_idx: int) -> None:
        """Remove a modulation assignment."""
        def mutate(project, tid=track_id, mi=mod_idx, ai=assign_idx):
            track = self._mixer_track(project, tid)
            if 0 <= mi < len(track.modulators):
                mods = track.modulators[mi].assignments
                if 0 <= ai < len(mods):
                    del mods[ai]

        self._structural_edit("remove assignment", mutate)
        self._status("Assignment removed.")

    def on_mixer_set_layer_mode(self, track_id: str, mode: str) -> None:
        """Set the layer fan-out mode (all/random/sequential)."""
        if mode not in ("all", "random", "sequential"):
            return

        def mutate(project, tid=track_id, m=mode):
            self._mixer_track(project, tid).layer_mode = m

        self._structural_edit(f"layer mode -> {mode}", mutate)
        self._status(f"Layer mode: {mode}.")

    def on_mixer_toggle_layer(self, track_id: str, layer_idx: int,
                              enabled: bool) -> None:
        """Enable/disable a generator layer."""
        def mutate(project, tid=track_id, idx=layer_idx, en=enabled):
            track = self._mixer_track(project, tid)
            if 0 <= idx < len(track.generator_layers):
                track.generator_layers[idx].enabled = en

        self._structural_edit(
            f"{'enable' if enabled else 'disable'} layer", mutate)

    def on_mixer_set_layer_gain(self, track_id: str, layer_idx: int,
                                gain: float) -> None:
        """Set a layer's gain (0.0-2.0)."""
        gain = max(0.0, min(2.0, gain))

        def mutate(project, tid=track_id, idx=layer_idx, g=gain):
            track = self._mixer_track(project, tid)
            if 0 <= idx < len(track.generator_layers):
                track.generator_layers[idx].gain = g

        self._structural_edit(f"layer gain -> {gain:g}", mutate)

    def on_mixer_set_layer_pitch(self, track_id: str, layer_idx: int,
                                 offset: int) -> None:
        """Set a layer's pitch offset in semitones (-48..48)."""
        offset = max(-48, min(48, offset))

        def mutate(project, tid=track_id, idx=layer_idx, o=offset):
            track = self._mixer_track(project, tid)
            if 0 <= idx < len(track.generator_layers):
                track.generator_layers[idx].pitch_offset = o

        self._structural_edit(f"layer pitch -> {offset:+d}", mutate)

    # -- sequencer / piano roll edits ----------------------------------------------

    def on_toggle_step(self, channel_id: str, step: int) -> None:
        pattern = self.project.current_pattern()
        ch = next(c for c in pattern.channels if c.id == channel_id)
        # Label from pre-mutation state.
        action = "off" if any(n.start == float(step) for n in ch.notes) else "on"

        def mutate(project, pid=pattern.id):
            pat = project.pattern_by_id(pid)
            c = next(x for x in pat.channels if x.id == channel_id)
            # Remove notes starting exactly at `step`; otherwise add a
            # 1-step note at the channel's default pitch. (The piano roll
            # is the precise editor for lengths and chords.)
            before = len(c.notes)
            c.notes = [n for n in c.notes if n.start != float(step)]
            if len(c.notes) == before:
                c.notes.append(Note(start=float(step), length=1.0,
                                    pitch=c.pitch, vel=0.9))

        self._structural_edit(f"{ch.name} step {step + 1} {action}", mutate,
                              panels=["sequencer"])

    def on_pitch(self, channel_id: str, pitch: int) -> None:
        pattern = self.project.current_pattern()
        ch = next(c for c in pattern.channels if c.id == channel_id)
        if ch.pitch == pitch:
            return

        def mutate(project, pid=pattern.id):
            pat = project.pattern_by_id(pid)
            c = next(x for x in pat.channels if x.id == channel_id)
            c.pitch = pitch

        self._structural_edit(f"{ch.name} default pitch -> {pitch}", mutate,
                              panels=["sequencer"])
        self._status(f"{ch.name} default pitch set to MIDI {pitch} "
                     f"(new notes; edit existing notes in the Piano Roll).")

    def on_channel_menu(self, channel_id: str, event) -> None:
        pattern = self.project.current_pattern()
        try:
            ch = next(c for c in pattern.channels if c.id == channel_id)
        except StopIteration:
            return
        menu = tk.Menu(self.root, tearoff=False)
        inst_menu = tk.Menu(menu, tearoff=False)
        for inst in INSTRUMENTS:
            inst_menu.add_command(
                label=inst.capitalize(),
                command=lambda i=inst: self._drop_instrument(channel_id, i))
        menu.add_cascade(label="Instrument", menu=inst_menu)
        menu.add_command(label="Clear notes",
                         command=lambda: self.on_channel_clear_notes(channel_id))
        menu.tk_popup(event.x_root, event.y_root)

    def on_channel_clear_notes(self, channel_id: str) -> None:
        pattern = self.project.current_pattern()

        def mutate(project, pid=pattern.id):
            pat = project.pattern_by_id(pid)
            c = next(x for x in pat.channels if x.id == channel_id)
            c.steps = [False] * pat.steps

        self._structural_edit("clear channel notes", mutate,
                              panels=["sequencer", "pianoroll"])
        self._status("Channel notes cleared.")

    def on_pianoroll_commit(self, channel_id: str, notes: list) -> None:
        pattern = self.project.current_pattern()

        def mutate(project, pid=pattern.id):
            pat = project.pattern_by_id(pid)
            c = next(x for x in pat.channels if x.id == channel_id)
            c.notes = [Note(start=float(n.start), length=float(n.length),
                            pitch=int(n.pitch), vel=float(n.vel))
                       for n in notes]

        self._structural_edit("piano roll edit", mutate,
                              panels=["pianoroll", "sequencer"])

    # -- live play + capture (score logger) ---------------------------------

    def _capture_beat(self) -> float:
        """Current beat for capture timestamps."""
        try:
            if self.engine.is_playing():
                return float(self.engine.position_beats())
        except Exception:
            pass
        # Free-time: wall clock beats at current tempo.
        return time.time() * self.project.tempo / 60.0

    def _piano_note_on(self, pitch: int, velocity: float) -> None:
        self._capture.note_on(pitch, velocity, self._capture_beat())
        self.piano.highlight(pitch, True)
        self.piano.set_count(len(self._capture.notes))

    def _piano_note_off(self, pitch: int) -> None:
        self._capture.note_off(pitch, self._capture_beat())
        self.piano.highlight(pitch, False)

    def _clear_capture(self) -> None:
        self._capture.clear()
        self.piano.set_count(0)
        self._status("Capture cleared.")

    def _set_quantize(self, label: str) -> None:
        self._quantize_label = label
        self.piano.sync_quantize(label)

    def _dump_capture(self) -> None:
        """Write captured notes into the current pattern (with quantize)."""
        if not self._capture.notes:
            self._status("Nothing captured yet -- play the piano first.")
            return
        pattern = self.project.current_pattern()
        if not pattern.channels:
            self._status("No channel in the current pattern.")
            return
        # Target the selected piano-roll channel, else the first.
        channel_id = self.pianoroll.channel_id or pattern.channels[0].id
        grid = dict(QUANTIZE_GRIDS).get(self._quantize_label, 0.25)
        # Align capture start to beat 0 of the pattern.
        t0 = min(n.start_beat for n in self._capture.notes)

        def mutate(project, pid=pattern.id, cid=channel_id):
            pat = project.pattern_by_id(pid)
            ch = next(x for x in pat.channels if x.id == cid)
            for n in self._capture.notes:
                start = quantize_beat(n.start_beat - t0, grid)
                length = max(grid or 0.25, quantize_beat(n.length_beats, grid)
                             if grid else n.length_beats)
                # Clamp into the pattern.
                if start < 0 or start >= pat.steps:
                    continue
                ch.notes.append(Note(start=float(start),
                                     length=float(min(length, pat.steps - start)),
                                     pitch=int(n.pitch),
                                     vel=float(n.velocity)))
            ch.notes.sort(key=lambda n: (n.start, n.pitch))

        count = len(self._capture.notes)
        self._structural_edit(f"dump {count} captured notes", mutate,
                              panels=["pianoroll", "sequencer"])
        self._capture.clear()
        self.piano.set_count(0)
        self._status(f"Dumped {count} notes to '{pattern.name}'.")

    def _auto_place_capture(self, t0: float) -> None:
        """Song-mode auto-place (FL-style).

        Captured notes become a new Pattern; a Clip referencing it is
        placed on the first track at the recording-start position T0.
        Notes are stored relative to T0; the clip holds the absolute
        placement. One undo step.

        The take channel uses the "lead" instrument (v0.42.0 fix): it is
        the pitched voice, so captured piano notes sound at their
        recorded pitches. (v0.19.0-v0.41.0 used instrument="keys", which
        is not a valid instrument -- every take failed validation and
        was silently discarded.)
        """
        from ..project import Channel, Clip, Note, Pattern
        grid = dict(QUANTIZE_GRIDS).get(self._quantize_label, 0.25)
        # Notes relative to T0, in beats; drop notes before T0 (count-in).
        rel = []
        for n in self._capture.notes:
            rb = n.start_beat - t0
            if rb < -0.001:
                continue
            rel.append((max(0.0, rb), n))
        if not rel:
            self._capture.clear()
            self.piano.set_count(0)
            self._status("Stopped -- no notes after record start.")
            return
        # Extent of recorded data -> clip length (whole bars).
        max_end = max(rb + n.length_beats for rb, n in rel)
        if grid:
            max_end = max(max_end, grid)
        bars = max(1, int(-(-max_end // 4)))  # ceil to bars
        steps = bars * 16
        # Build notes in steps (4 steps per beat), quantized.
        notes = []
        for rb, n in rel:
            start_beats = quantize_beat(rb, grid) if grid else rb
            start_steps = start_beats * 4.0
            if grid:
                len_beats = max(grid, quantize_beat(n.length_beats, grid))
            else:
                len_beats = n.length_beats
            length_steps = max(1.0, len_beats * 4.0)
            if start_steps < 0 or start_steps >= steps:
                continue
            length_steps = min(length_steps, steps - start_steps)
            notes.append(Note(start=float(start_steps),
                              length=float(length_steps),
                              pitch=int(n.pitch),
                              vel=float(n.velocity)))
        if not notes:
            self._capture.clear()
            self.piano.set_count(0)
            self._status("Stopped -- no notes fit in the new pattern.")
            return
        notes.sort(key=lambda n: (n.start, n.pitch))

        def mutate(project, t0b=t0):
            # New pattern.
            pid = project.new_pattern_id()
            pat = Pattern(id=pid,
                          name=f"Recorded take {len(project.patterns) + 1}",
                          steps=steps,
                          channels=[Channel(id=f"{pid}-keys",
                                            name="Keys",
                                            instrument="lead",
                                            pitch=60,
                                            notes=notes)])
            project.patterns.append(pat)
            # Clip at T0 on the first track.
            if not project.tracks:
                raise ProjectError("no tracks to place the recorded clip")
            track = project.tracks[0]
            start_beat = int(t0b)  # floor to whole beat
            track.clips.append(Clip(pattern_id=pid,
                                    start_beat=start_beat,
                                    bars=bars))
            track.clips.sort(key=lambda c: c.start_beat)

        count = len(notes)
        bar = int(t0 // 4) + 1
        self._structural_edit(
            f"record {count} notes at bar {bar}", mutate,
            panels=["playlist", "pianoroll", "sequencer"])
        self._capture.clear()
        self.piano.set_count(0)
        self._status(
            f"Recorded {count} notes -> new pattern placed at bar {bar}.")

    def on_automation_commit(self, track_id: str, param: str,
                             points: list, interp: str = "linear",
                             tension: float = 0.5, lfo=None) -> None:
        """Commit automation points and/or the lane's curve shape.

        interp/tension select the lane's curve evaluator (research
        topic 30); per-point tangents (research topic 36) ride along
        on the points; lfo is the lane's LFO modulation layer (or
        None). All are validated by AutomationLane/LfoSettings.
        """
        import copy
        lfo_copy = copy.deepcopy(lfo) if lfo is not None else None

        def mutate(project, tid=track_id):
            pts = sorted(points, key=lambda p: p.beat)
            lanes = project.lanes_for(tid, param)
            if pts:
                new_pts = [AutoPoint(p.beat, p.value, p.in_tan, p.out_tan)
                           for p in pts]
                if lanes:
                    lanes[0].points = new_pts
                    lanes[0].interp = interp
                    lanes[0].tension = max(0.0, min(1.0, float(tension)))
                    lanes[0].lfo = copy.deepcopy(lfo_copy)
                else:
                    project.automation.append(AutomationLane(
                        id=project.new_automation_id(), track_id=tid,
                        param=param, points=new_pts, interp=interp,
                        tension=max(0.0, min(1.0, float(tension))),
                        lfo=copy.deepcopy(lfo_copy)))
            else:
                # Deleting the last point removes the lane.
                project.automation = [
                    a for a in project.automation
                    if not (a.track_id == tid and a.param == param)]

        self._structural_edit("automation edit", mutate,
                              panels=["automation"])

    def on_automation_clear(self, track_id: str, param: str) -> None:
        if not self.project.lanes_for(track_id, param):
            return

        def mutate(project, tid=track_id):
            project.automation = [
                a for a in project.automation
                if not (a.track_id == tid and a.param == param)]

        self._structural_edit("clear automation lane", mutate,
                              panels=["automation"])
        self._status("Automation lane cleared.")

    # -- edit menu ------------------------------------------------------------

    def edit_undo(self) -> None:
        label = self.undo.undo()
        if label:
            self._mark_dirty()
            self._status(f"Undid: {label}")
        else:
            self._status("Nothing to undo.")

    def edit_redo(self) -> None:
        label = self.undo.redo()
        if label:
            self._mark_dirty()
            self._status(f"Redid: {label}")
        else:
            self._status("Nothing to redo.")

    def edit_clear(self) -> None:
        pattern = self.project.current_pattern()

        def mutate(project, pid=pattern.id):
            pat = project.pattern_by_id(pid)
            for ch in pat.channels:
                ch.steps = [False] * pat.steps

        self._structural_edit(f"clear pattern '{pattern.name}'", mutate)
        self._status(f"Pattern '{pattern.name}' cleared.")

    # -- file menu --------------------------------------------------------------

    def _replace_project(self, project: Project, path: str | None) -> None:
        self.stop()
        self.project = project
        self.path = path
        self.dirty = False
        self.undo.clear()
        self.transport.set_tempo(project.tempo)
        self._refresh_all()
        self._update_title()

    def file_new(self) -> None:
        if not self._confirm_discard():
            return
        self._replace_project(empty_project(), None)
        self._status("New empty project -- drag a sound from the Browser, then build a pattern.")

    def file_open(self) -> None:
        if not self._confirm_discard():
            return
        path = filedialog.askopenfilename(
            parent=self.root, title="Open project",
            filetypes=[("Pulsegrid projects", "*.pulsegrid.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            project = Project.load(path)
        except ProjectError as e:
            show_error(self.root, "Could not open project", str(e))
            return
        self._replace_project(project, path)
        self._status(f"Opened {os.path.basename(path)}.")

    def _write_project(self, path: str) -> bool:
        try:
            self._refresh_plugin_state_blobs()
            self.project.save(path)
        except ProjectError as e:
            show_error(self.root, "Could not save project", str(e))
            return False
        self.path = path
        self.dirty = False
        self._update_title()
        self._status(f"Saved {os.path.basename(path)}.")
        return True

    def _refresh_plugin_state_blobs(self) -> None:
        """Pull live CLAP state blobs from the engine into the project.

        Called at save time so each plugin's opaque state (including
        non-parameter state the host never sees) is preserved in the
        JSON project as Base64. A failure here never blocks saving:
        parameter values are always saved regardless.
        """
        try:
            blobs = self.engine.save_plugin_states()
        except Exception:
            return
        tracks = self.project.tracks
        for b in blobs:
            try:
                track_idx = int(b["track"])
                plugin_id = b["plugin_id"]
                blob = b["state_base64"]
            except (KeyError, TypeError, ValueError):
                continue
            if not (0 <= track_idx < len(tracks)) or not blob:
                continue
            track = tracks[track_idx]
            fx_index = b.get("fx_index")
            layer_index = b.get("layer_index")
            try:
                if fx_index not in (None, ""):
                    fx = track.effects[int(fx_index)]
                    if fx.kind == "plugin" and fx.plugin_id == plugin_id:
                        fx.state_base64 = blob
                elif layer_index not in (None, ""):
                    layer = track.generator_layers[int(layer_index)]
                    if layer.generator.plugin_id == plugin_id:
                        layer.generator.state_base64 = blob
            except (IndexError, TypeError, ValueError):
                continue

    def file_save(self) -> None:
        if self.path:
            self._write_project(self.path)
        else:
            self.file_save_as()

    def file_save_as(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Save project",
            defaultextension=".pulsegrid.json",
            filetypes=[("Pulsegrid projects", "*.pulsegrid.json"), ("All files", "*.*")],
        )
        if path:
            self._write_project(path)

    def export_wav(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Export arrangement to WAV",
            defaultextension=".wav",
            filetypes=[("WAV audio", "*.wav"), ("All files", "*.*")],
        )
        if not path:
            return
        self._export_dialog(path)

    def _export_dialog(self, path: str) -> None:
        """Non-blocking export: the render runs on a worker thread (the
        Rust side releases the GIL during DSP), with a real progress bar
        and a Cancel button. The UI stays responsive throughout."""
        win = tk.Toplevel(self.root)
        win.title("Exporting WAV...")
        win.transient(self.root)
        win.resizable(False, False)
        win.grab_set()
        win.protocol("WM_DELETE_WINDOW", self._export_cancel)
        frame = ttk.Frame(win, padding=24)
        frame.pack()
        ttk.Label(frame, text="Rendering WAV...",
                  font=("", 11, "bold")).pack(anchor="w")
        label = ttk.Label(frame, text="Starting...", foreground="#8b949e")
        label.pack(anchor="w", pady=(6, 8))
        bar = ttk.Progressbar(frame, mode="determinate", length=340)
        bar.pack()
        ttk.Button(frame, text="Cancel",
                   command=self._export_cancel).pack(anchor="e", pady=(12, 0))

        # Snapshot the arrangement on the UI thread; the worker only
        # reads this immutable dict, so edits made mid-render can't
        # corrupt it.
        arrangement = EngineBridge.arrangement_dict(self.project,
                                                    self._project_dir())
        self._export_win = win
        self._export_label = label
        self._export_bar = bar
        self._export_path = path
        self._export_cancel_flag = threading.Event()
        self._export_queue: queue.Queue = queue.Queue()
        thread = threading.Thread(target=self._export_worker,
                                  args=(arrangement, path), daemon=True)
        thread.start()
        self._export_poll()

    def _export_worker(self, arrangement: dict, path: str) -> None:
        q = self._export_queue
        cancel = self._export_cancel_flag

        def progress(done, total):
            q.put(("progress", done, total))
            return not cancel.is_set()

        try:
            completed = self.engine.render_wav_threaded(
                arrangement, path, EXPORT_LOOPS, progress)
            q.put(("done", completed))
        except Exception as e:  # EngineError and anything unexpected
            q.put(("error", str(e)))

    def _export_poll(self) -> None:
        if self._export_win is None:
            return
        try:
            while True:
                msg = self._export_queue.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    _, done, total = msg
                    self._export_bar["maximum"] = total
                    self._export_bar["value"] = done
                    self._export_label.config(
                        text=f"Rendering... block {done} of {total}")
                elif kind == "done":
                    self._export_finish(bool(msg[1]))
                    return
                elif kind == "error":
                    self._export_finish(False, msg[1])
                    return
        except queue.Empty:
            pass
        self.root.after(50, self._export_poll)

    def _export_cancel(self) -> None:
        if self._export_win is None:
            return
        self._export_cancel_flag.set()
        self._export_label.config(text="Cancelling...")

    def _export_finish(self, completed: bool, error: str | None = None) -> None:
        win, path = self._export_win, self._export_path
        self._export_win = None
        win.destroy()
        if error is not None:
            show_error(self.root, "Export failed", error)
        elif not completed:
            try:
                os.remove(path)
            except OSError:
                pass
            self._status("Export cancelled -- partial file removed.")
        else:
            self._status(f"Exported {EXPORT_LOOPS} arrangement loops to "
                         f"{os.path.basename(path)}.")

    def on_close(self) -> None:
        if self._confirm_discard():
            self._save_layout()
            # Destroy any open native plugin GUIs first.
            for session in list(PluginGuiSession._active.values()):
                try:
                    session.close()
                except Exception:
                    pass
            for session in list(GeneratorGuiSession._active.values()):
                try:
                    session.close()
                except Exception:
                    pass
            try:
                self.engine.stop()
            except EngineError:
                pass
            self.root.destroy()
