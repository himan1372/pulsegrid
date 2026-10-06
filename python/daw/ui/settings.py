"""Settings panel: real, working preferences.

Every control here does something. UI scale and layout reset act
immediately; the unsaved-changes confirmation is honored on exit;
the backend and config-path rows are read-only information.
"""

import os
import tkinter as tk
from tkinter import ttk

from .widgets import Tooltip

SCALES = (("100%", 1.0), ("125%", 1.25), ("150%", 1.5))

# Animation refresh rates, mirroring FL Studio's General options.
# (label, mode) -- mode maps to a _poll_position interval in app.py.
REFRESH_RATES = (("Less smooth", "low"),
                 ("Smooth", "medium"),
                 ("Ultrasmooth", "high"))
REFRESH_TIPS = {
    "low": "Repaint playheads/meters ~15x/sec. Lowest CPU use.",
    "medium": "Repaint ~30x/sec. Good balance.",
    "high": "Repaint ~60x/sec. Smoothest, highest CPU use.",
}


class Settings(ttk.Frame):
    def __init__(self, master, initial_scale, on_scale, on_reset_layout,
                 get_confirm, on_confirm, get_backend, get_config_path,
                 get_refresh, on_refresh, get_plugin_dir, on_plugin_dir,
                 get_multithreaded, on_multithreaded,
                 get_audio_devices, get_audio_selection, on_audio_output,
                 get_buffer_frames, on_buffer_frames,
                 get_input_devices, get_input_selection, on_audio_input):
        """on_scale(factor): apply a UI scale.
        on_reset_layout(): restore the default workspace layout.
        get_confirm()/on_confirm(bool): unsaved-changes prompt preference.
        get_backend()/get_config_path(): read-only info strings.
        get_refresh()/on_refresh(mode): animation refresh-rate preference.
        get_plugin_dir()/on_plugin_dir(path): CLAP plugin folder preference.
        get_audio_devices(): list of {host_id, host_name, device_name,
            is_default} dicts for the output selectors.
        get_audio_selection(): (host_id, device_name); (None, None) means
            "System default".
        on_audio_output(host_id, device_name): apply a new output choice.
        get_buffer_frames()/on_buffer_frames(frames): audio buffer size in
            frames; None = driver default.
        get_input_devices()/get_input_selection()/on_audio_input(...):
            the same trio for the *input* device (FL Studio's separate
            Input selector). Selecting an input only records the choice;
            no input stream is opened in this version.
        """
        super().__init__(master)
        self._on_scale = on_scale
        self._scale_var = tk.DoubleVar(value=initial_scale)
        self._on_refresh = on_refresh
        self._on_plugin_dir = on_plugin_dir

        ttk.Label(self, text="Settings", font=("", 10, "bold"),
                  padding=(8, 4)).pack(anchor="w")

        display = ttk.LabelFrame(self, text="Display", padding=8)
        display.pack(fill="x", padx=8, pady=4)
        ttk.Label(display, text="UI scale:").pack(anchor="w")
        scale_row = ttk.Frame(display)
        scale_row.pack(anchor="w", pady=(4, 0))
        for label, factor in SCALES:
            rb = ttk.Radiobutton(scale_row, text=label, value=factor,
                                 variable=self._scale_var,
                                 command=self._scale_chosen)
            rb.pack(side="left", padx=(0, 10))
            Tooltip(rb, f"Scale the interface to {label}")

        # Animation refresh rate (mirrors FL Studio's General options):
        # controls how often playheads/meters repaint during playback.
        ttk.Label(display, text="Animation refresh rate:").pack(
            anchor="w", pady=(8, 0))
        self._refresh_var = tk.StringVar(value=get_refresh())
        refresh_row = ttk.Frame(display)
        refresh_row.pack(anchor="w", pady=(4, 0))
        for label, mode in REFRESH_RATES:
            rb = ttk.Radiobutton(refresh_row, text=label, value=mode,
                                 variable=self._refresh_var,
                                 command=self._refresh_chosen)
            rb.pack(side="left", padx=(0, 10))
            Tooltip(rb, REFRESH_TIPS[mode])

        # Audio: multithreading (FL-style global switch).
        audio = ttk.LabelFrame(self, text="Audio", padding=8)
        audio.pack(fill="x", padx=8, pady=4)
        self._mt_var = tk.BooleanVar(value=get_multithreaded())
        mt_chk = ttk.Checkbutton(
            audio, text="Multithreaded rendering (use all CPU cores)",
            variable=self._mt_var,
            command=lambda: on_multithreaded(bool(self._mt_var.get())))
        mt_chk.pack(anchor="w")
        Tooltip(mt_chk, "Render track voices in parallel across CPU cores. "
                "Disable if a plugin misbehaves (FL-style per-host opt-out).")

        # Audio output device (FL Studio's Device dropdown / LMMS's Audio
        # Interface + device selectors are the reference designs).
        self._on_audio_output = on_audio_output
        self._get_audio_devices = get_audio_devices
        ttk.Label(audio, text="Audio output:").pack(anchor="w", pady=(8, 0))
        ttk.Label(audio, text="Driver:", foreground="#8b949e",
                  font=("", 9)).pack(anchor="w")
        self._host_var = tk.StringVar()
        self._host_combo = ttk.Combobox(audio, textvariable=self._host_var,
                                        state="readonly", width=28)
        self._host_combo.pack(anchor="w", pady=(2, 0))
        self._host_combo.bind("<<ComboboxSelected>>",
                              lambda _e: self._audio_host_chosen())
        Tooltip(self._host_combo,
                "Audio driver (CPAL host).\n"
                "On Windows this is WASAPI; ASIO is not supported.")
        ttk.Label(audio, text="Device:", foreground="#8b949e",
                  font=("", 9)).pack(anchor="w", pady=(4, 0))
        self._device_var = tk.StringVar()
        self._device_combo = ttk.Combobox(audio, textvariable=self._device_var,
                                          state="readonly", width=28)
        self._device_combo.pack(anchor="w", pady=(2, 0))
        self._device_combo.bind("<<ComboboxSelected>>",
                                lambda _e: self._audio_device_chosen())
        Tooltip(self._device_combo,
                "Output device on the selected driver.\n"
                "\"System default\" follows the OS default playback device.")
        audio_btn_row = ttk.Frame(audio)
        audio_btn_row.pack(anchor="w", pady=(6, 0))
        refresh_btn = ttk.Button(audio_btn_row, text="Refresh",
                                 command=self.refresh_audio_devices)
        refresh_btn.pack(side="left")
        Tooltip(refresh_btn, "Re-scan audio drivers and devices.\n"
                             "Use after plugging in headphones or a headset.")
        self._audio_status = ttk.Label(audio, text="", font=("", 9),
                                       foreground="#8b949e", wraplength=230)
        self._audio_status.pack(anchor="w", pady=(4, 0))
        self._audio_devices = []
        self.refresh_audio_devices()
        self.sync_audio_selection(get_audio_selection())

        # Audio input device (FL Studio's separate Input selector).
        # Selecting an input only records the choice for now; no input
        # stream is opened in this version.
        self._on_audio_input = on_audio_input
        self._get_input_devices = get_input_devices
        ttk.Label(audio, text="Audio input:").pack(anchor="w", pady=(8, 0))
        self._input_var = tk.StringVar()
        self._input_combo = ttk.Combobox(audio, textvariable=self._input_var,
                                         state="readonly", width=28)
        self._input_combo.pack(anchor="w", pady=(2, 0))
        self._input_combo.bind("<<ComboboxSelected>>",
                               lambda _e: self._audio_input_chosen())
        Tooltip(self._input_combo,
                "Input device (microphone / line-in).\n"
                "The choice is saved for future recording support;\n"
                "no audio input is captured in this version.")
        self._input_devices = []
        self.refresh_input_devices()
        self.sync_input_selection(get_input_selection())

        # Buffer size: the real-time lever (FL "Buffer length" analog).
        # Bigger buffer = more time per block (fewer underruns) at the
        # cost of latency.
        self._on_buffer_frames = on_buffer_frames
        ttk.Label(audio, text="Buffer size:", foreground="#8b949e",
                  font=("", 9)).pack(anchor="w", pady=(6, 0))
        self._buffer_var = tk.StringVar()
        self._buffer_combo = ttk.Combobox(audio, textvariable=self._buffer_var,
                                          state="readonly", width=28,
                                          values=["Default (driver)",
                                                  "128 samples",
                                                  "256 samples",
                                                  "512 samples",
                                                  "1024 samples",
                                                  "2048 samples"])
        self._buffer_combo.pack(anchor="w", pady=(2, 0))
        self._buffer_combo.bind("<<ComboboxSelected>>",
                                lambda _e: self._buffer_chosen())
        Tooltip(self._buffer_combo,
                "Audio buffer size.\n"
                "Bigger buffers give the CPU more time per block:\n"
                "fewer clicks/pops under load, but more latency.\n"
                "Applies live (keeps playing); persists in layout.json.")
        self.sync_buffer_frames(get_buffer_frames())

        workspace = ttk.LabelFrame(self, text="Workspace", padding=8)
        workspace.pack(fill="x", padx=8, pady=4)
        self._confirm_var = tk.BooleanVar(value=get_confirm())
        chk = ttk.Checkbutton(
            workspace, text="Ask before discarding unsaved changes",
            variable=self._confirm_var,
            command=lambda: on_confirm(bool(self._confirm_var.get())))
        chk.pack(anchor="w")
        Tooltip(chk, "Show a confirmation dialog when closing or\n"
                     "starting a new project with unsaved edits")
        btn = ttk.Button(workspace, text="Reset layout",
                         command=on_reset_layout)
        btn.pack(anchor="w", pady=(6, 0))
        Tooltip(btn, "Show all panels, reset sizes and UI scale")

        plugins = ttk.LabelFrame(self, text="Plugins", padding=8)
        plugins.pack(fill="x", padx=8, pady=4)
        ttk.Label(plugins, text="CLAP plugin folder:").pack(anchor="w")
        self._plugin_dir_var = tk.StringVar(value=get_plugin_dir())
        dir_row = ttk.Frame(plugins)
        dir_row.pack(fill="x", pady=(4, 0))
        self._plugin_dir_entry = ttk.Entry(dir_row,
                                           textvariable=self._plugin_dir_var)
        self._plugin_dir_entry.pack(side="left", fill="x", expand=True)
        Tooltip(self._plugin_dir_entry,
                "Folder scanned for .clap plugins.\n"
                "Also checks ~/.clap and /usr/lib/clap.\n"
                "Leave empty to use the defaults.")
        browse = ttk.Button(dir_row, text="Browse...",
                            command=self._browse_plugin_dir)
        browse.pack(side="left", padx=(6, 0))
        clear = ttk.Button(dir_row, text="Clear",
                           command=self._clear_plugin_dir)
        clear.pack(side="left", padx=(6, 0))
        Tooltip(browse, "Pick the folder containing your .clap files")
        Tooltip(clear, "Use the default plugin locations")

        system = ttk.LabelFrame(self, text="System", padding=8)
        system.pack(fill="x", padx=8, pady=4)
        ttk.Label(system, text="Audio backend:", foreground="#8b949e",
                  font=("", 9)).pack(anchor="w")
        self._backend_label = ttk.Label(system, text=get_backend(),
                                        font=("", 9))
        self._backend_label.pack(anchor="w", pady=(0, 6))
        Tooltip(self._backend_label,
                "Live audio output. If no device is found the app\n"
                "uses a silent null sink so the UI keeps working.")
        ttk.Label(system, text="Layout file:", foreground="#8b949e",
                  font=("", 9)).pack(anchor="w")
        path_label = ttk.Label(system, text=get_config_path(), font=("", 9),
                               wraplength=220)
        path_label.pack(anchor="w")
        Tooltip(path_label, "Panel layout and preferences are saved here.")

    # -- external sync (the app calls these when state changes elsewhere)

    def sync_scale(self, factor: float) -> None:
        self._scale_var.set(factor)

    def sync_confirm(self, value: bool) -> None:
        self._confirm_var.set(value)

    def sync_refresh(self, mode: str) -> None:
        self._refresh_var.set(mode)

    def sync_plugin_dir(self, path: str) -> None:
        self._plugin_dir_var.set(path or "")

    def refresh_backend(self, backend: str) -> None:
        self._backend_label.config(text=backend)

    def refresh_audio_status(self, text: str) -> None:
        """Show which output is actually active (or why there is none)."""
        self._audio_status.config(text=text)

    # -- audio output selectors -------------------------------------------

    _SYS_DEFAULT = "System default"

    def refresh_audio_devices(self) -> None:
        """Re-scan drivers/devices and rebuild the dropdowns."""
        try:
            self._audio_devices = list(self._get_audio_devices())
        except Exception:
            self._audio_devices = []
        hosts = sorted({d["host_id"] for d in self._audio_devices})
        self._host_labels = [self._SYS_DEFAULT] + hosts
        cur = self._host_var.get()
        self._host_combo["values"] = self._host_labels
        if cur not in self._host_labels:
            cur = self._SYS_DEFAULT
        self._host_var.set(cur)
        self._rebuild_device_combo()
        if not self._audio_devices:
            self.refresh_audio_status(
                "No audio devices found -- playback uses a silent null sink.")

    def sync_audio_selection(self, selection) -> None:
        """Set the dropdowns to (host_id, device_name); None = default."""
        host_id, device_name = selection
        host_label = host_id if host_id else self._SYS_DEFAULT
        if host_label not in self._host_labels:
            self.refresh_audio_devices()
            if host_label not in self._host_labels:
                host_label = self._SYS_DEFAULT
        self._host_var.set(host_label)
        self._rebuild_device_combo()
        dev_label = self._device_label_for(device_name)
        self._device_var.set(dev_label)

    def _devices_for_host(self, host_label):
        if host_label == self._SYS_DEFAULT:
            return []
        return [d for d in self._audio_devices if d["host_id"] == host_label]

    def _device_display(self, dev) -> str:
        name = dev["device_name"]
        return f"{name} (default)" if dev["is_default"] else name

    def _device_label_for(self, device_name):
        """Display label for a device_name (None -> System default)."""
        if not device_name:
            return self._SYS_DEFAULT
        for dev in self._devices_for_host(self._host_var.get()):
            if dev["device_name"] == device_name:
                return self._device_display(dev)
        return self._SYS_DEFAULT

    def _rebuild_device_combo(self) -> None:
        devs = self._devices_for_host(self._host_var.get())
        self._device_labels = [self._SYS_DEFAULT] + [
            self._device_display(d) for d in devs]
        cur = self._device_var.get()
        self._device_combo["values"] = self._device_labels
        if cur not in self._device_labels:
            # Prefer the host's default device, else System default.
            default = next((self._device_display(d) for d in devs
                            if d["is_default"]), self._SYS_DEFAULT)
            cur = default
        self._device_var.set(cur)

    def _current_selection(self):
        host_label = self._host_var.get()
        dev_label = self._device_var.get()
        host_id = None if host_label == self._SYS_DEFAULT else host_label
        device_name = None
        if dev_label != self._SYS_DEFAULT:
            for dev in self._devices_for_host(host_label):
                if self._device_display(dev) == dev_label:
                    device_name = dev["device_name"]
                    break
        return host_id, device_name

    def _audio_host_chosen(self) -> None:
        self._rebuild_device_combo()
        host_label = self._host_var.get()
        if host_label != self._SYS_DEFAULT:
            # New driver: start on its default device (explicit, not the
            # ambiguous "System default" left over from the old driver).
            devs = self._devices_for_host(host_label)
            default = next((self._device_display(d) for d in devs
                            if d["is_default"]), None)
            if default is not None:
                self._device_var.set(default)
        host_id, device_name = self._current_selection()
        self._on_audio_output(host_id, device_name)

    def _audio_device_chosen(self) -> None:
        host_id, device_name = self._current_selection()
        self._on_audio_output(host_id, device_name)

    # -- audio input ------------------------------------------------------

    def refresh_input_devices(self) -> None:
        """Re-scan input devices and rebuild the input dropdown."""
        try:
            self._input_devices = list(self._get_input_devices())
        except Exception:
            self._input_devices = []
        labels = [self._SYS_DEFAULT] + [
            self._device_display(d) for d in self._input_devices]
        cur = self._input_var.get()
        self._input_combo["values"] = labels
        self._input_var.set(cur if cur in labels else self._SYS_DEFAULT)

    def sync_input_selection(self, selection) -> None:
        """Set the input dropdown to (host_id, device_name); None = default."""
        host_id, device_name = selection
        label = self._SYS_DEFAULT
        if host_id is not None or device_name is not None:
            for dev in self._input_devices:
                if dev["host_id"] == host_id and \
                        dev["device_name"] == device_name:
                    label = self._device_display(dev)
                    break
        self._input_var.set(label)

    def _audio_input_chosen(self) -> None:
        label = self._input_var.get()
        host_id, device_name = None, None
        if label != self._SYS_DEFAULT:
            for dev in self._input_devices:
                if self._device_display(dev) == label:
                    host_id, device_name = (dev["host_id"],
                                            dev["device_name"])
                    break
        self._on_audio_input(host_id, device_name)

    # -- buffer size --------------------------------------------------------

    _BUFFER_LABELS = {
        None: "Default (driver)",
        128: "128 samples",
        256: "256 samples",
        512: "512 samples",
        1024: "1024 samples",
        2048: "2048 samples",
    }

    def sync_buffer_frames(self, frames) -> None:
        """Set the dropdown to the current buffer size (None = default)."""
        self._buffer_var.set(self._BUFFER_LABELS.get(frames,
                                                     "Default (driver)"))

    def _buffer_chosen(self) -> None:
        label = self._buffer_var.get()
        frames = next((n for n, lab in self._BUFFER_LABELS.items()
                       if lab == label), None)
        self._on_buffer_frames(frames)

    def _scale_chosen(self) -> None:
        self._on_scale(self._scale_var.get())

    def _refresh_chosen(self) -> None:
        self._on_refresh(self._refresh_var.get())

    def _browse_plugin_dir(self) -> None:
        from tkinter import filedialog
        initial = self._plugin_dir_var.get() or "~"
        path = filedialog.askdirectory(title="Choose CLAP plugin folder",
                                       initialdir=os.path.expanduser(initial))
        if path:
            self._plugin_dir_var.set(path)
            self._on_plugin_dir(path)

    def _clear_plugin_dir(self) -> None:
        self._plugin_dir_var.set("")
        self._on_plugin_dir("")
